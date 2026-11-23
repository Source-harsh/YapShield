import base64

import pymupdf
import pytest
from fastapi.testclient import TestClient

from privydoc import api, detection


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(detection, "local_nlp", lambda: None)
    with TestClient(api.app) as session:
        yield session


def upload(client, text):
    response = client.post("/api/review", content=text.encode(), headers={"Content-Type": "text/plain"})
    assert response.status_code == 200, response.text
    review = response.json()
    mentions = [mention for group in review["groups"] for mention in group["mentions"]]
    return {"X-Review-Token": review["token"]}, mentions


def test_preview_and_release_share_exact_mention_and_manual_redactions(client, monkeypatch):
    text = "Name: Jane Doe\nJ. Doe filed it.\nEmail: jane@example.com\nCopy: jane@example.com\nProject ZX99."
    headers, mentions = upload(client, text)
    alias = next(item for item in mentions if item["text"] == "J. Doe")
    kept_email = next(item for item in mentions if item["line"] == 4)
    payload = {
        "mention_decisions": {item["id"]: "keep" if item in (alias, kept_email) else "redact" for item in mentions},
        "links": {alias["id"]: {"status": "confirm"}},
        "manual": ["ZX99"],
    }
    expected = "Name: [REDACTED]\nJ. Doe filed it.\nEmail: [REDACTED]\nCopy: jane@example.com\nProject [REDACTED]."
    summarize = api.summarize
    seen = []

    def record_sanitized(value):
        seen.append(value)
        return summarize(value)

    monkeypatch.setattr(api, "summarize", record_sanitized)
    for _ in range(2):
        response = client.post("/api/preview", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["text"] == expected
        assert response.headers["Cache-Control"] == "no-store"
    assert seen == []

    response = client.post("/api/release", headers={**headers, "X-Preview-Receipt": response.json()["receipt"]}, json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["text"] == expected
    assert seen == [expected.replace("J. Doe", "[REDACTED]").replace("jane@example.com", "[REDACTED]")]
    with pymupdf.open(stream=base64.b64decode(result["pdf"]), filetype="pdf") as pdf:
        assert pdf[0].get_text().strip() == expected
    assert client.post("/api/preview", headers=headers, json=payload).status_code == 404


def test_incomplete_or_conflicting_mention_requests_cannot_preview_or_release(client):
    headers, mentions = upload(client, "Name: Jane Doe\nJ. Doe filed it.")
    primary, alias = [item["id"] for item in mentions]
    base = {"mention_decisions": {primary: "keep", alias: "redact"}, "links": {alias: {"status": "detach"}}, "manual": []}
    variants = [
        {"mention_decisions": {primary: "redact"}},
        {"mention_decisions": {primary: "redact", alias: "redact", "unknown": "keep"}},
        {"decisions": {0: "redact"}},
        {"links": {}},
        {"links": {alias: {"status": "detach", "decision": "redact"}}},
        {"manual": ["Jane"]},
    ]
    preview = client.post("/api/preview", headers=headers, json=base).json()
    assert preview["text"] == "Name: Jane Doe\n[REDACTED] filed it."
    for changes in variants:
        assert client.post("/api/release", headers={**headers, "X-Preview-Receipt": preview["receipt"]},
                           json={**base, **changes}).status_code == 409
        assert client.post("/api/preview", headers=headers, json={**base, **changes}).status_code == 422


def test_overlapping_and_adjacent_unicode_mentions(client, monkeypatch):
    monkeypatch.setattr(api, "detect_mentions", lambda _: [
        detection.candidate(1, 3, "Email", "Pattern", "Test span"),
        detection.candidate(2, 4, "Phone", "Pattern", "Test overlap"),
        detection.candidate(4, 5, "Address", "Pattern", "Test adjacent span"),
    ])
    headers, mentions = upload(client, "🙂abcd!")
    assert [item["text"] for item in mentions] == ["ab", "bc", "d"]
    first, second, adjacent = [item["id"] for item in mentions]
    payload = {"mention_decisions": {first: "redact", second: "keep", adjacent: "redact"}, "links": {}, "manual": []}
    assert client.post("/api/preview", headers=headers, json=payload).status_code == 422
    payload["mention_decisions"][second] = "redact"
    assert client.post("/api/preview", headers=headers, json=payload).json()["text"] == "🙂[REDACTED]!"
