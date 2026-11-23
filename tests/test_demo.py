import base64
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from privydoc import api


FIXTURES = Path(__file__).resolve().parents[1] / "demo" / "fixtures"
PUBLIC = "public-desk@example.org"


@pytest.mark.parametrize(("extension", "media_type"), [("txt", "text/plain"), ("pdf", "application/pdf")])
def test_hackathon_demo_releases_verified_artifacts(extension, media_type):
    fixture = FIXTURES / f"equipment-handoff.{extension}"
    with TestClient(api.app) as client:
        review = client.post("/api/review", content=fixture.read_bytes(), headers={"Content-Type": media_type})
        assert review.status_code == 200, review.text
        review = review.json()
        mentions = {item["id"]: item for group in review["groups"] for item in group["mentions"]}
        links = {
            link["mention_id"]: {"status": "reject" if mentions[link["mention_id"]]["text"] == "Tessa" else "confirm"}
            for group in review["groups"] for link in (group["identity"] or {}).get("alternates", [])
        }
        assert any(mentions[key]["text"] == "T. Vale" and value["status"] == "confirm" for key, value in links.items())
        assert any(mentions[key]["text"] == "Tessa" and value["status"] == "reject" for key, value in links.items())
        assert any(item["text"] == "Vale" and item["kind"] == "Ambiguous name" for item in mentions.values())
        payload = {"mention_decisions": {key: "keep" if item["text"] == PUBLIC else "redact"
                                         for key, item in mentions.items()},
                   "links": links, "manual": ["ZX-491"]}
        headers = {"X-Review-Token": review["token"]}
        preview = client.post("/api/preview", headers=headers, json=payload)
        assert preview.status_code == 200, preview.text
        headers["X-Preview-Receipt"] = preview.json()["receipt"]
        release = client.post("/api/release", headers=headers, json=payload)
        assert release.status_code == 200, release.text
        artifact = release.json()
        txt = base64.b64decode(artifact["txt"]).decode()
        assert txt == preview.json()["text"]
        with pymupdf.open(stream=base64.b64decode(artifact["pdf"]), filetype="pdf") as pdf:
            assert " ".join(" ".join(page.get_text().split()) for page in pdf) == " ".join(txt.split())
            assert not pdf.metadata["author"]
        assert all(value not in txt for value in ("Vale", "Tessa", "Rina", "records@example.org",
                                                  "555-0148", "DEMO-E1042", "Tallowbrook", "ZX-491"))
        assert PUBLIC in txt and PUBLIC not in artifact["summary"]
        assert "approved the equipment handoff" in artifact["summary"]
