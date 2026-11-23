import base64
from types import SimpleNamespace

import pymupdf
import pytest
from fastapi.testclient import TestClient

from privydoc import api, detection
from privydoc.documents import export_pdf, redact, validate


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(detection, "local_nlp", lambda: None)
    with TestClient(api.app) as session:
        yield session


def upload(client, text):
    response = client.post("/api/review", content=text.encode("utf-8"), headers={"Content-Type": "text/plain"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()


def release(client, review, decisions, manual=()):
    return client.post("/api/release", headers={"X-Review-Token": review["token"]},
                       json={"decisions": decisions, "manual": list(manual)})


def test_text_release_and_summary_only_see_sanitized_text(client, monkeypatch):
    text = ("Name: Jane Doe\nJ. Doe prepared the final project review for the team. "
            "Jane sent it to jane@example.org. Internal reference: ZX99.")
    review = upload(client, text)
    names, email = review["groups"]
    assert [item["text"] for item in names["mentions"]] == ["Jane Doe", "J. Doe", "Jane"]
    assert email["kind"] == "Email"
    original_summarizer = api.summarize

    def checked_summary(sanitized):
        assert all(secret not in sanitized for secret in ("Jane", "Doe", "example.org", "ZX99"))
        return original_summarizer(sanitized)

    monkeypatch.setattr(api, "summarize", checked_summary)
    assert release(client, review, {}).status_code == 422
    response = release(client, review, {names["id"]: "redact", email["id"]: "redact"}, ["ZX99"])
    assert response.status_code == 200
    result = response.json()
    assert result["text"].count("[REDACTED]") == 5
    assert "final project review" in result["summary"]
    assert result["pdf"] and not result["pdf_error"]
    assert release(client, review, {}).status_code == 404


def test_shared_surname_requires_separate_review(client):
    review = upload(client, "Name: Jane Chen\nName: Mei Chen\nChen signed the report.")
    assert [group["kind"] for group in review["groups"]] == ["Person", "Person", "Ambiguous name"]
    assert review["groups"][2]["mentions"][0]["text"] == "Chen"


def test_optional_ner_path(monkeypatch):
    text = "Mira Patel prepared the report."
    entity = SimpleNamespace(label_="PERSON", text="Mira Patel", start_char=0, end_char=10)
    monkeypatch.setattr(detection, "local_nlp", lambda: lambda _: SimpleNamespace(ents=[entity]))
    [[mention]] = detection.detect(text)
    assert (mention.start, mention.end, mention.kind, mention.source) == (0, 10, "Person", "spaCy")


def test_pdf_release_drops_original_pdf_objects(client):
    marker = "ORIGINAL_SECRET_8921"
    with pymupdf.open() as source:
        page = source.new_page()
        page.insert_text((50, 60), f"Contact: Jane Doe\n{marker}\nThis project report is ready for public release.")
        page.add_text_annot((30, 30), marker)
        source.set_metadata({"author": marker})
        source.embfile_add("secret.txt", marker.encode())
        data = source.tobytes()
    review = client.post("/api/review", content=data, headers={"Content-Type": "application/pdf"}).json()
    result = release(client, review, {0: "redact"}, [marker]).json()
    payload = base64.b64decode(result["pdf"])
    with pymupdf.open(stream=payload, filetype="pdf") as output:
        assert marker.encode() not in payload
        assert marker not in output[0].get_text()
        assert "Jane Doe" not in output[0].get_text()
        assert "ready for public release" in output[0].get_text()
        assert output.embfile_count() == 0
        assert not output.metadata.get("author")
        assert list(output[0].annots()) == []
        assert output[0].get_pixmap().width > 0


def test_unrepresentable_pdf_has_verified_txt_fallback(client):
    review = upload(client, "資料の確認。This document contains a multilingual sentence.")
    result = release(client, review, {}).json()
    assert result["pdf"] is None
    assert "verified TXT" in result["pdf_error"]
    assert result["text"].startswith("資料")


def test_repeated_normalized_value_blocks_release(client):
    review = upload(client, "Project Harbor\nThis record also says Project\nHarbor.")
    response = release(client, review, {}, ["Project Harbor"])
    assert response.status_code == 422
    assert "still appears" in response.json()["detail"]


def test_invalid_uploads_and_expiry(client, monkeypatch):
    assert client.post("/api/review", content=b"123").status_code == 415
    assert client.post("/api/review", content=b"bad", headers={"Content-Type": "application/pdf"}).status_code == 422
    assert client.post("/api/review", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    monkeypatch.setattr(api, "MAX_BYTES", 12)
    assert client.post("/api/review", content=b"a" * 13, headers={"Content-Type": "text/plain"}).status_code == 413
    review = upload(client, "Sample.")
    api.reviews[review["token"]].expires = 0
    assert release(client, review, {}).status_code == 404


def test_manual_overlaps_and_discard(client):
    review = upload(client, "ababa")
    assert release(client, review, {}, ["missing"]).status_code == 422
    assert release(client, review, {}, ["aba"]).json()["text"] == "[REDACTED]"
    review = upload(client, "Example.")
    assert client.delete("/api/review", headers={"X-Review-Token": review["token"]}).status_code == 204
    assert release(client, review, {}).status_code == 404


def test_export_validation_fails_closed():
    sanitized, removed = redact("Jane Doe and jane\nDOE", [(0, 8)])
    with pytest.raises(ValueError, match="still appears"):
        validate(sanitized, removed)
    with pytest.raises(ValueError, match="still appears"):
        export_pdf(sanitized, removed)
