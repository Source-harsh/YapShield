import re
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from evaluation.measure_detection import expected_spans, scores
from privydoc import api, detection


def spans(text):
    return [(item.kind, text[item.start:item.end]) for group in detection.detect(text) for item in group]


@pytest.fixture
def rules_only(monkeypatch):
    monkeypatch.setattr(detection, "local_nlp", lambda: None)


@pytest.mark.parametrize(("text", "kind", "value"), [
    ("Mail: jane.doe+work@example.org.", "Email", "jane.doe+work@example.org"),
    ("Mail: jane [at] example [dot] com.", "Email", "jane [at] example [dot] com"),
    ("Mail: jane(at)example(dot)co(dot)uk", "Email", "jane(at)example(dot)co(dot)uk"),
    ("Call +1 (202) 555-0182.", "Phone", "+1 (202) 555-0182"),
    ("Call +44 20 7946 0958.", "Phone", "+44 20 7946 0958"),
    ("Call +91 98765 43210.", "Phone", "+91 98765 43210"),
    ("Call (202) 555-0182.", "Phone", "(202) 555-0182"),
    ("Call 1-202-555-0182.", "Phone", "1-202-555-0182"),
    ("Phone: 2025550182", "Phone", "2025550182"),
    ("Mail to 18 Cedar Lane", "Address", "18 Cedar Lane"),
    ("Address: 18 Cedar Lane Apt 4\nSanta Clara, CA 95050", "Address", "18 Cedar Lane Apt 4\nSanta Clara, CA 95050"),
    ("Ship to P.O. Box 410, Boston, MA 02108.", "Address", "P.O. Box 410, Boston, MA 02108"),
    ("Employee ID: EMP1023", "Employee ID", "EMP1023"),
    ("Account Number: ACC-123456", "Account ID", "ACC-123456"),
    ("Student ID: STU202601", "Student ID", "STU202601"),
    ("SSN: 321-54-9876", "US SSN", "321-54-9876"),
    ("🙂 Author: Élodie D’Arcy", "Person", "Élodie D’Arcy"),
])
def test_supported_shapes(rules_only, text, kind, value):
    assert spans(text) == [(kind, value)]


@pytest.mark.parametrize("text", [
    "Release dates: 2026-11-23 and 11/23/2026.",
    "Product codes: PRD-202-555-0182, EMP1023 and STU202601.",
    "Version: v202.555.0182 and 202.555.0182.4.",
    "Random values: 2025550182, 123456789 and 321-54-9876.",
    "SSN: 000-54-9876. Social Security Number: 321-00-9876.",
    "Technical IDs: ref_321-54-9876, build_4042026, sha256:abcd1234.",
    "Product Name: Release Notes. File Name: Customer Ledger. Project Name: Blue Sky.",
    "Discuss Jane at example dot com. Invalid address: jane@example..com",
    "Employee ID: pending. Account Number: unknown. Student ID: none.",
    "Employee ID: 2026-11-23. Account Number: v1.2.3. Student ID: 23-11-2026.",
])
def test_hard_negatives(rules_only, text):
    assert spans(text) == []


def test_linked_aliases_and_ambiguous_surname(rules_only):
    text = "Name: Jane Chen\nName: Mei Chen\nJ Chen and M. Chen spoke. Chen filed the report."
    assert [
        [(item.kind, text[item.start:item.end]) for item in group] for group in detection.detect(text)
    ] == [
        [("Person", "Jane Chen"), ("Person", "J Chen")],
        [("Person", "Mei Chen"), ("Person", "M. Chen")],
        [("Ambiguous name", "Chen")],
    ]


def test_aliases_do_not_match_inside_contact_values_or_lowercase_words(rules_only):
    text = "Name: Jane May\nEmail: Jane [at] example [dot] com\nThis may help."
    assert spans(text) == [("Person", "Jane May"), ("Email", "Jane [at] example [dot] com")]


def test_accented_name_supports_an_unaccented_initial(rules_only):
    text = "Name: Élodie D’Arcy\nE D’Arcy signed the form."
    assert spans(text) == [("Person", "Élodie D’Arcy"), ("Person", "E D’Arcy")]


def test_load_failure_and_absence(monkeypatch):
    load = detection.local_nlp
    load.cache_clear()
    try:
        monkeypatch.setattr(detection, "find_spec", lambda name: None if name == detection.MODEL else object())
        assert load() is None
        load.cache_clear()

        def fail(*args, **kwargs):
            raise OSError("invalid model")

        monkeypatch.setattr(detection, "find_spec", lambda name: object())
        monkeypatch.setitem(sys.modules, "spacy", SimpleNamespace(load=fail))
        assert load() is None
        assert spans("Student ID: STU202601") == [("Student ID", "STU202601")]
    finally:
        load.cache_clear()


def test_real_spacy_person_path():
    if detection.find_spec("spacy") is None or detection.find_spec(detection.MODEL) is None:
        pytest.skip("Optional spaCy package or English model is absent")
    assert detection.local_nlp() is not None
    text = "🙂 Barack Obama visited Paris."
    assert any(
        text[item.start:item.end] == "Barack Obama" and item.start == 2 and item.source == "spaCy"
        for group in detection.detect(text) for item in group
    )


def test_api_fusion_and_overlapping_release(monkeypatch):
    text = "🙂\nName: Jane Doe\nAccount Number: 202-555-0182\nProject marker ZX99."
    name = re.search("Jane Doe", text)
    person = SimpleNamespace(label_="PERSON", text=name.group(), start_char=name.start(), end_char=name.end())
    monkeypatch.setattr(detection, "local_nlp", lambda: lambda _: SimpleNamespace(ents=[person, person]))
    with TestClient(api.app) as client:
        response = client.post("/api/review", content=text.encode(), headers={"Content-Type": "text/plain"})
        assert response.status_code == 200
        review = response.json()
        groups = {group["kind"]: group for group in review["groups"]}
        assert set(groups) == {"Person", "Account ID", "Phone"}
        [mention] = groups["Person"]["mentions"]
        assert (mention["text"], mention["source"], mention["line"]) == ("Jane Doe", "Context rule", 2)
        assert [entry["source"] for entry in mention["evidence"]] == ["Context rule", "spaCy"]
        items = [item for group in groups.values() for item in group["mentions"]]
        assert len({item["id"] for item in items}) == len(items)
        assert all(text[item["start"]:item["end"]] == item["text"] for item in items)

        headers = {"X-Review-Token": review["token"]}
        payload = {
            "decisions": {group["id"]: "keep" if kind == "Phone" else "redact" for kind, group in groups.items()},
            "manual": ["ZX99"],
        }
        response = client.post("/api/release", headers=headers, json=payload)
        assert response.status_code == 422
        assert "overlaps a mention marked Keep" in response.json()["detail"]
        payload["decisions"][groups["Phone"]["id"]] = "redact"
        response = client.post("/api/release", headers=headers, json=payload)
        assert response.status_code == 200
        assert response.json()["text"].count("[REDACTED]") == 3
        assert "202-555-0182" not in response.json()["text"]


def test_partial_person_overlap_keeps_both_spans(monkeypatch):
    text = "Name: Jane Marie Doe"
    person = SimpleNamespace(label_="PERSON", text="Marie Doe", start_char=11, end_char=20)
    monkeypatch.setattr(detection, "local_nlp", lambda: lambda _: SimpleNamespace(ents=[person]))
    assert spans(text) == [("Person", "Jane Marie Doe"), ("Person", "Marie Doe")]


def test_evaluation_requires_exact_offset_and_type():
    record = {"id": "sample", "text": "🙂 Name: Jane Doe", "entities": [["Person", "Name: Jane Doe", "Jane Doe"]]}
    expected = expected_spans(record)
    assert expected == {("sample", "Person", 8, 16)}
    predicted = expected | {("sample", "Person", 8, 15), ("sample", "Address", 8, 16)}
    result = scores(expected, predicted)
    assert (result["true_positives"], result["false_positives"], result["false_negatives"]) == (1, 2, 0)


def test_rule_only_mode_bypasses_model(monkeypatch):
    def unexpected_model_call():
        pytest.fail("Rules-only mode called the model loader")

    monkeypatch.setattr(detection, "local_nlp", unexpected_model_call)
    assert len(detection.detect("Student ID: STU202601", use_nlp=False)) == 1
