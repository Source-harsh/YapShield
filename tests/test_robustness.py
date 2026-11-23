import base64

import pymupdf
import pytest
from fastapi.testclient import TestClient

from privydoc import api, detection, documents


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(detection, "local_nlp", lambda: None)
    with TestClient(api.app) as session:
        yield session


def upload(client, data, content_type="application/pdf"):
    return client.post("/api/review", content=data, headers={"Content-Type": content_type})


def release(client, review, payload):
    headers = {"X-Review-Token": review["token"]}
    preview = client.post("/api/preview", headers=headers, json=payload)
    assert preview.status_code == 200, preview.text
    headers["X-Preview-Receipt"] = preview.json()["receipt"]
    response = client.post("/api/release", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_mixed_page_layouts_release_verified_reflow(client):
    with pymupdf.open() as source:
        for index, (width, height, rotation) in enumerate([(595, 842, 0), (792, 612, 0), (900, 700, 90)]):
            page = source.new_page(width=width, height=height)
            page.set_rotation(rotation)
            page.insert_text((40, 50), "Quarterly record")
            page.insert_text((40, 95), "Column left")
            page.insert_text((290, 95), "Column right")
            page.insert_text((40, 120), f"Row Alpha {index}")
            page.insert_text((290, 120), f"Row Beta {index}")
            page.draw_rect((35, 75, 420, 135))
            page.insert_text((40, 190), f"Contact: reviewer{index}@example.org")
            page.insert_text((40, 350), "End of record")
        response = upload(client, source.tobytes())
    assert response.status_code == 200, response.text
    review = response.json()
    assert review["text"].count("Quarterly record") == review["text"].count("End of record") == 3
    assert all(f"Row {column} {index}" in review["text"] for column in ("Alpha", "Beta") for index in range(3))
    result = release(client, review, {"decisions": {group["id"]: "redact" for group in review["groups"]}, "manual": []})
    assert result["text"].count("[REDACTED]") == 3
    with pymupdf.open(stream=base64.b64decode(result["pdf"]), filetype="pdf") as output:
        assert output.page_count == 1
        assert not output[0].get_drawings()
        assert " ".join(output[0].get_text().split()) == " ".join(result["text"].split())


def test_embedded_unicode_pdf_has_verified_txt_fallback(client):
    with pymupdf.open() as source:
        source.new_page().insert_text((40, 60), "資料の確認", fontname="japan")
        review = upload(client, source.tobytes()).json()
    result = release(client, review, {"decisions": {}, "manual": []})
    assert result["pdf"] is None
    assert "verified TXT" in result["pdf_error"]
    assert base64.b64decode(result["txt"]).decode().strip() == "資料の確認"


@pytest.mark.parametrize("kind", ["empty page", "password", "damaged", "wrong file type"])
def test_invalid_pdf_upload_has_safe_error(client, kind):
    with pymupdf.open() as source:
        page = source.new_page()
        page.insert_text((40, 60), "PRIVATE_UPLOAD_419")
        if kind == "empty page":
            source.new_page()
        options = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": "required"} if kind == "password" else {}
        data = source.tobytes(**options)
        if kind == "wrong file type":
            data = source[0].get_pixmap().tobytes("png")
        if kind == "damaged":
            data = data[:data.rindex(b"xref\n")]
    response = upload(client, data)
    assert response.status_code == 422
    expected = {"empty page": "extractable text", "password": "unlocked PDF", "damaged": "damaged",
                "wrong file type": "invalid or damaged"}
    assert expected[kind] in response.json()["detail"]
    assert "PRIVATE_UPLOAD_419" not in response.text


def test_excessive_candidates_reject_upload_without_creating_review(client):
    text = "\n".join(f"Contact: check{i}@example.org" for i in range(detection.MAX_CANDIDATES + 1))
    response = upload(client, text.encode(), "text/plain")
    assert response.status_code == 422
    assert "Too many candidates" in response.json()["detail"]
    assert "check0" not in response.text
    assert not api.reviews


@pytest.mark.parametrize(("size", "status"), [(documents.MAX_TEXT + 1, 422), (api.MAX_BYTES + 1, 413)])
def test_real_upload_and_extracted_text_limits(client, size, status):
    response = upload(client, b"x" * size, "text/plain")
    assert response.status_code == status
    assert not api.reviews


def test_expired_review_rejects_previously_verified_release(client):
    review = upload(client, b"Project confidential reference ZX99.", "text/plain").json()
    headers = {"X-Review-Token": review["token"]}
    payload = {"decisions": {}, "manual": ["ZX99"]}
    preview = client.post("/api/preview", headers=headers, json=payload)
    assert preview.status_code == 200
    api.reviews[review["token"]].expires = 0
    response = client.post("/api/release", headers={**headers, "X-Preview-Receipt": preview.json()["receipt"]}, json=payload)
    assert response.status_code == 404


def test_preview_failure_invalidates_older_receipt(client):
    review = upload(client, b"Project confidential reference ZX99.", "text/plain").json()
    headers = {"X-Review-Token": review["token"]}
    payload = {"decisions": {}, "manual": ["ZX99"]}
    for broken in ('{"manual":', '{"manual":["missing"]}'):
        preview = client.post("/api/preview", headers=headers, json=payload)
        assert preview.status_code == 200
        rejected = client.post("/api/preview", headers=headers, content=broken)
        assert rejected.status_code == 422
        response = client.post("/api/release", headers={**headers, "X-Preview-Receipt": preview.json()["receipt"]}, json=payload)
        assert response.status_code == 409


def test_manual_match_budget_and_review_retry(client, monkeypatch):
    monkeypatch.setattr(api, "MAX_MANUAL_MATCHES", 10)
    review = upload(client, b"abababababababababababab", "text/plain").json()
    headers = {"X-Review-Token": review["token"]}
    response = client.post("/api/preview", headers=headers, json={"decisions": {}, "manual": ["ab", "aba"]})
    assert response.status_code == 422
    assert "too many matches" in response.json()["detail"]
    result = release(client, review, {"decisions": {}, "manual": ["abababab"]})
    assert result["text"] == "[REDACTED]"


@pytest.mark.parametrize("text", ["Café — résumé: €30.", "[<6162>]"])
def test_winansi_and_pdf_like_literals_round_trip(text):
    with pymupdf.open(stream=documents.export_pdf(text, set()), filetype="pdf") as output:
        assert output[0].get_text().strip() == text


def test_pdf_reflow_preserves_all_lines_across_page_boundary():
    text = "\n".join(f"Record {index}." for index in range(47))
    with pymupdf.open(stream=documents.export_pdf(text, set()), filetype="pdf") as output:
        assert output.page_count == 2
        assert output[0].get_text().splitlines()[-1] == "Record 45."
        assert output[1].get_text().strip() == "Record 46."
