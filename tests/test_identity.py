import base64
from types import SimpleNamespace

import pymupdf
import pytest
from fastapi.testclient import TestClient

from privydoc import api, detection
from privydoc.identity import name_parts, resolve


@pytest.fixture(autouse=True)
def rules_only(monkeypatch):
    monkeypatch.setattr(detection, "local_nlp", lambda: None)


def identities(text):
    _, clusters = resolve(text, detection.detect_mentions(text))
    return [(text[item.start:item.end], cluster) for item, cluster in clusters.items()]


def alternates(text, cluster):
    return [(text[link.mention.start:link.mention.end], link.relationship) for link in cluster.alternates]


def test_unlabelled_name_with_nearby_corroborating_references():
    text = ("Richa Sharma joined the company.\nRicha submitted the report.\n"
            "R. Sharma received approval.\nMs. Sharma attended the meeting.")
    [(name, cluster)] = identities(text)
    assert name == "Richa Sharma"
    assert alternates(text, cluster) == [("Richa", "first_name"), ("R. Sharma", "initials"), ("Ms. Sharma", "honorific_surname")]
    assert all(link.evidence for link in cluster.alternates)


def test_unicode_punctuation_and_first_and_middle_initials():
    text = ("🙂 Name: Élodie Jean D’Arcy\nE.J. D'Arcy filed it. EJ D’Arcy reviewed it.\n"
            "E J D’Arcy replied. Élodie J. D’Arcy sent it. Dr D'Arcy closed the review.")
    [(name, cluster)] = identities(text)
    assert name_parts("DR.  ÉLODIE Jean D’Arcy") == ("élodie", "jean", "d'arcy")
    assert name == "Élodie Jean D’Arcy"
    assert alternates(text, cluster) == [
        ("E.J. D'Arcy", "initials"), ("EJ D’Arcy", "initials"), ("E J D’Arcy", "initials"),
        ("Élodie J. D’Arcy", "initials"), ("Dr D'Arcy", "honorific_surname"),
    ]


def test_raw_person_evidence_is_preserved_when_initialed_forms_are_proposed(monkeypatch):
    text = "Name: Élodie Jean D’Arcy\nÉlodie J D’Arcy filed it.\nÉJ D’Arcy called back."
    values = ["Élodie J D’Arcy", "ÉJ D’Arcy"]
    entities = [SimpleNamespace(label_="PERSON", text=value, start_char=text.index(value),
                               end_char=text.index(value) + len(value)) for value in values]
    monkeypatch.setattr(detection, "local_nlp", lambda: lambda _: SimpleNamespace(ents=entities))
    [(_, cluster)] = identities(text)
    assert alternates(text, cluster) == [(value, "initials") for value in values]
    assert all(link.mention.evidence[0].source == "spaCy" for link in cluster.alternates)


@pytest.mark.parametrize(("text", "reference", "owner_count"), [
    ("Name: Jane Chen\nChen signed it. Chen asked for a copy.", "Chen", 1),
    ("Name: Jane Chen\nName: Mei Chen\nMs. Chen signed it.", "Ms. Chen", 2),
    ("Name: Jane Chen\nName: Jane Park\nJane signed it.", "Jane", 2),
    ("Name: Jane Chen\nName: June Chen\nJ. Chen signed it.", "J. Chen", 2),
    ("Name: Jane Chen\nName: June Chen\nJ Chen signed it.", "J Chen", 2),
    ("Name: May Lee\nMay is a busy month. They signed it.", "May", 1),
])
def test_uncertain_references_are_never_added_to_a_cluster(text, reference, owner_count):
    result = identities(text)
    assert all(not cluster.alternates for _, cluster in result)
    references = [cluster for value, cluster in result if value == reference]
    assert len(references) == (2 if reference == "Chen" else 1)
    assert all(len(cluster.possible_primaries) == owner_count for cluster in references)
    assert not any(value == "They" for value, _ in result)


def test_identical_full_names_and_similar_names_stay_independent():
    text = "Name: Jane Chen\nJANE  CHEN signed it.\nName: Jana Chen\nJane asked about it."
    result = dict(identities(text))
    assert set(result) == {"Jane Chen", "JANE  CHEN", "Jana Chen", "Jane"}
    assert result["Jane Chen"].relationship == result["JANE  CHEN"].relationship == "exact_name"
    assert not any(cluster.alternates for cluster in result.values())
    assert len(result["Jane"].possible_primaries) == 2


def test_first_name_requires_preceding_nearby_context():
    text = "Jane wrote it.\nName: Jane Chen\n" + "Quiet afternoon. " * 40 + "Jane wrote again."
    result = identities(text)
    assert sum(value == "Jane" for value, _ in result) == 2
    assert all(not cluster.alternates for _, cluster in result)


def upload(client, text, *, pdf=False):
    content = text.encode()
    media_type = "text/plain"
    if pdf:
        with pymupdf.open() as source:
            source.new_page().insert_text((40, 70), text)
            content = source.tobytes()
        media_type = "application/pdf"
    response = client.post("/api/review", content=content, headers={"Content-Type": media_type})
    assert response.status_code == 200, response.text
    return response.json()


def release(client, review, decisions, links, manual=()):
    headers = {"X-Review-Token": review["token"]}
    payload = {"decisions": decisions, "links": links, "manual": list(manual)}
    preview = client.post("/api/preview", headers=headers, json=payload)
    if preview.status_code != 200:
        return preview
    return client.post("/api/release", headers={**headers, "X-Preview-Receipt": preview.json()["receipt"]}, json=payload)


@pytest.mark.parametrize("pdf", [False, True])
def test_confirm_reject_detach_and_release_exact_spans(pdf):
    text = "Name: Jane Doe\nJ. Doe filed a report.\nJane copied the full notes.\nMs. Doe logged the decision."
    with TestClient(api.app) as client:
        review = upload(client, text, pdf=pdf)
        [group] = review["groups"]
        identity = group["identity"]
        mentions = {mention["text"]: mention for mention in group["mentions"]}
        assert identity["status"] == "pending" and identity["uncertainty"] == "proposed"
        assert identity["primary_mention_id"] == mentions["Jane Doe"]["id"]
        assert set(identity["mention_ids"]) == {mention["id"] for mention in group["mentions"]}
        assert mentions["Jane Doe"]["evidence"][0]["source"] == "Context rule"
        assert all(review["text"][mention["start"]:mention["end"]] == mention["text"] for mention in group["mentions"])
        decisions = {group["id"]: "redact"}
        links = {
            mentions["J. Doe"]["id"]: {"status": "confirm"},
            mentions["Jane"]["id"]: {"status": "reject", "decision": "keep"},
            mentions["Ms. Doe"]["id"]: {"status": "detach", "decision": "keep"},
        }
        assert release(client, review, decisions, {}).status_code == 422
        assert release(client, review, decisions, None).status_code == 422
        assert release(client, review, decisions, {**links, "unknown": {"status": "confirm"}}).status_code == 422
        assert release(client, review, decisions, {**links, mentions["Jane"]["id"]: {"status": "reject"}}).status_code == 422
        assert release(client, review, decisions, links, manual=["Ms. Doe"]).status_code == 422
        response = release(client, review, decisions, links)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["text"].count("[REDACTED]") == 2
        assert "Jane copied" in result["text"] and "Ms. Doe logged" in result["text"]
        with pymupdf.open(stream=base64.b64decode(result["pdf"]), filetype="pdf") as output:
            pdf_text = output[0].get_text()
            assert "Jane Doe" not in pdf_text and "J. Doe" not in pdf_text
            assert "Jane copied" in pdf_text and "Ms. Doe logged" in pdf_text


def test_one_identical_full_name_can_be_kept_without_releasing_a_summary_leak():
    text = "Name: Jane Doe\nJane Doe delivered the final project report and submitted all supporting documents."
    with TestClient(api.app) as client:
        review = upload(client, text)
        first, second = review["groups"]
        assert first["identity"]["uncertainty"] == second["identity"]["uncertainty"] == "uncertain"
        response = release(client, review, {first["id"]: "redact", second["id"]: "keep"}, {})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["text"].count("Jane Doe") == 1
        assert "Jane Doe" not in result["summary"]
        with pymupdf.open(stream=base64.b64decode(result["pdf"]), filetype="pdf") as output:
            assert output[0].get_text().count("Jane Doe") == 1


def test_detached_mention_can_be_redacted_independently():
    with TestClient(api.app) as client:
        review = upload(client, "Name: Jane Doe\nJ. Doe filed the report.")
        [group] = review["groups"]
        [link] = group["identity"]["alternates"]
        response = release(client, review, {group["id"]: "keep"}, {link["mention_id"]: {"status": "detach", "decision": "redact"}})
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "Name: Jane Doe\n[REDACTED] filed the report."


def test_retained_name_does_not_allow_an_unreviewed_repeat():
    text = "Name: May Lee\nMay sent the report. Later the unreviewed alias may appeared."
    with TestClient(api.app) as client:
        review = upload(client, text)
        primary, ambiguous = review["groups"]
        response = release(client, review, {primary["id"]: "keep", ambiguous["id"]: "redact"}, {})
        assert response.status_code == 422
        assert "still appears" in response.json()["detail"]
