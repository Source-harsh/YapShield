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


def review(client, text):
    response = client.post("/api/review", content=text.encode(), headers={"Content-Type": "text/plain"})
    assert response.status_code == 200, response.text
    data = response.json()
    return {"X-Review-Token": data["token"]}, data["groups"]


def preview(client, headers, payload):
    response = client.post("/api/preview", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return {**headers, "X-Preview-Receipt": response.json()["receipt"]}


def finalize(client, headers, payload):
    return client.post("/api/release", headers=headers, json=payload)


def test_release_verifies_actual_download_bytes(client):
    text = ("Name: Jane Doe\nJ. Doe filed the report.\nJane asked for feedback.\n"
            "Email: jane@example.org\nCopy: jane [at] example [dot] org\nCopy: jane@example.org\n"
            "Phone: (202) 555-0182\nAddress: 12 Oak Street\n"
            "Employee ID: EMP-2026\nStudent ID: STU-2026\nAccount ID: ACC-2026\n"
            "SSN: 123-45-6789\nContact: Sara Fox\nThe team approved the report for publication.")
    headers, groups = review(client, text)
    payload = {
        "mention_decisions": {item["id"]: "keep" if item["text"] == "Sara Fox" else "redact"
                              for group in groups for item in group["mentions"]},
        "links": {link["mention_id"]: {"status": "confirm"} for group in groups
                  for link in (group["identity"] or {}).get("alternates", [])},
        "manual": [],
    }
    assert len(payload["links"]) == 2
    response = finalize(client, preview(client, headers, payload), payload)
    assert response.status_code == 200, response.text
    result = response.json()
    txt = base64.b64decode(result["txt"]).decode()
    assert txt == result["text"]
    with pymupdf.open(stream=base64.b64decode(result["pdf"]), filetype="pdf") as pdf:
        extracted = "\n".join(page.get_text() for page in pdf)
    for artifact in (txt, extracted):
        assert artifact.count("[REDACTED]") == 12
        assert "Sara Fox" in artifact
        for group in groups:
            for item in group["mentions"]:
                if item["text"] != "Sara Fox":
                    assert item["text"] not in artifact
    assert "Sara Fox" not in result["summary"]


@pytest.mark.parametrize(("approved", "residual"), [
    ("Élodie D’Arcy", "E\u0301LODIE D'Arcy"),
    ("jane@example.org", "ｊａｎｅ [at] example [dot] org"),
    ("202-555-0182", "(٢٠٢) 555.0182"),
    ("EMP-2026", "ＥＭＰ‑２０２６"),
    ("123-45-6789", "123 45 6789"),
])
def test_normalized_residuals_block_preview(client, monkeypatch, approved, residual):
    monkeypatch.setattr(api, "detect_mentions", lambda _: [])
    headers, _ = review(client, f"{approved}\nValue: {residual}")
    payload = {"decisions": {}, "manual": [approved]}
    response = client.post("/api/preview", headers=headers, json=payload)
    assert response.status_code == 422
    assert "still appears" in response.json()["detail"]


def test_preview_is_bound_to_current_selection_and_cannot_be_replayed(client):
    headers, groups = review(client, "Name: Jane Doe\nJ. Doe filed it.")
    primary, alias = groups[0]["mentions"]
    old = {"mention_decisions": {primary["id"]: "redact", alias["id"]: "keep"},
           "links": {alias["id"]: {"status": "detach"}}, "manual": []}
    changed = {**old, "links": {alias["id"]: {"status": "reject"}}}
    assert finalize(client, headers, old).status_code == 409
    old_receipt = preview(client, headers, old)
    assert finalize(client, old_receipt, changed).status_code == 409
    assert finalize(client, old_receipt, old).status_code == 409
    old_receipt = preview(client, headers, old)
    current_receipt = preview(client, headers, changed)
    assert finalize(client, old_receipt, old).status_code == 409
    assert finalize(client, current_receipt, changed).status_code == 200
    assert finalize(client, current_receipt, changed).status_code == 404


def test_group_decision_requires_explicit_resolution(client):
    headers, groups = review(client, "Name: Jane Doe\nJ. Doe filed it.")
    response = client.post("/api/preview", headers=headers, json={"decisions": {groups[0]["id"]: "redact"}, "manual": []})
    assert response.status_code == 422
    assert "Resolve every proposed link" in response.json()["detail"]


@pytest.mark.parametrize("injection", ["metadata", "annotation", "attachment", "catalog", "text", "trailing bytes"])
def test_post_serialization_pdf_failure_rejects_entire_release(client, monkeypatch, injection):
    def contaminated(document):
        secret = "PRIVATE_9082"
        if injection == "metadata":
            document.set_metadata({"subject": secret})
        elif injection == "annotation":
            document[0].add_text_annot((30, 30), secret)
        elif injection == "attachment":
            document.embfile_add("note.txt", secret.encode())
        elif injection == "catalog":
            document.xref_set_key(document.pdf_catalog(), "Private", pymupdf.get_pdf_str(secret))
        elif injection == "text":
            document[0].insert_text((50, 180), secret)
        payload = document.tobytes(no_new_id=True)
        return payload + secret.encode() if injection == "trailing bytes" else payload

    headers, _ = review(client, "The review team approved this report. Ref: PRIVATE_9082")
    payload = {"decisions": {}, "manual": ["PRIVATE_9082"]}
    receipt = preview(client, headers, payload)
    with monkeypatch.context() as patch:
        patch.setattr(documents, "serialize_pdf", contaminated)
        response = finalize(client, receipt, payload)
        assert response.status_code == 422
        assert response.json() == {"detail": "The generated files failed release verification."}
    assert finalize(client, receipt, payload).status_code == 200


def test_txt_verifier_rejects_changed_bytes_and_missing_keeps():
    with pytest.raises(ValueError, match="TXT verification failed"):
        documents.verify_txt(b"private", "[REDACTED]", {"private"})
    with pytest.raises(ValueError, match="marked Keep"):
        documents.verify_txt(b"ordinary text", "ordinary text", set(), required={"sara fox": 1})


@pytest.mark.parametrize("excerpt", [
    "The confidential record refers to É. Dubois in the quarterly report.",
    "The account email appears as demo＠example.org in the confidential appendix.",
    "The account email appears as demo at example dot org in the confidential appendix.",
    "The person referenced as 123 45 6789 sent the quarterly report.",
    "The record indicates age 47.\n\nThe record lists the postal code as 90210.",
    "The person named in the file works as an engineer.\n\nThe record notes their hometown.",
    "The person was born on 1980-02-19 according to the review.",
])
def test_summary_withholds_direct_and_combined_quasi_identifiers(client, monkeypatch, excerpt):
    headers, groups = review(client, f"Project secret: ZX99\n{excerpt}")
    payload = {"decisions": {group["id"]: "keep" for group in groups}, "manual": ["ZX99"]}

    def select_excerpt(sanitized):
        assert "ZX99" not in sanitized
        assert excerpt in sanitized
        return excerpt

    monkeypatch.setattr(api, "summarize", select_excerpt)
    response = finalize(client, preview(client, headers, payload), payload)
    assert response.status_code == 200, response.text
    assert excerpt in response.json()["text"]
    assert response.json()["summary"] == ""


def test_summary_rejects_generated_text_outside_verified_input(client, monkeypatch):
    headers, _ = review(client, "The review team approved all changes. Ref: ZX99")
    payload = {"decisions": {}, "manual": ["ZX99"]}
    monkeypatch.setattr(api, "summarize", lambda _: "The project contains the reference ZX99.")
    response = finalize(client, preview(client, headers, payload), payload)
    assert response.status_code == 200, response.text
    assert response.json()["summary"] == ""
