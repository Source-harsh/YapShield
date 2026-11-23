"""Collect Phase 8 evidence without replacing the earlier evaluation."""

import argparse
import base64
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from statistics import median
from time import perf_counter
from unittest.mock import patch

import pymupdf
from fastapi.testclient import TestClient

from evaluation.measure_detection import DATASET, expected_spans, scores
from privydoc import api, detection, documents, identity

SUPPLEMENT = Path(__file__).with_name("pii_phase8_supplement.json")


def command(module, *args):
    return json.loads(subprocess.check_output([sys.executable, "-B", "-m", module, *args], encoding="utf-8"))


def detect(records, *, use_nlp, resolve, runs=30):
    gold = set().union(*(expected_spans(record) for record in records))
    texts = {record["id"]: record["text"] for record in records}
    if len(texts) != len(records):
        raise ValueError("Corpus document IDs must be unique.")
    durations = []
    for _ in range(runs):
        start = perf_counter()
        predicted = set()
        for doc, text in texts.items():
            mentions = detection.detect_mentions(text, use_nlp=use_nlp)
            if resolve:
                groups, _ = identity.resolve(text, mentions)
                mentions = [mention for group in groups for mention in group]
            predicted.update((doc, item.kind, item.start, item.end) for item in mentions)
        durations.append((perf_counter() - start) * 1000)

    def describe(spans):
        return [{"document": doc, "kind": kind, "start": start, "end": end, "text": texts[doc][start:end]}
                for doc, kind, start, end in sorted(spans)]

    return {"overall": scores(gold, predicted), "per_entity": {
        kind: scores({span for span in gold if span[1] == kind}, {span for span in predicted if span[1] == kind})
        for kind in sorted({span[1] for span in gold | predicted})
    }, "false_positives": describe(predicted - gold), "false_negatives": describe(gold - predicted),
        "median_corpus_ms": median(durations), "runs": runs}


def submit(client, text, choose, *, pdf=False):
    if pdf:
        with pymupdf.open() as source:
            source.new_page().insert_text((45, 60), text, fontsize=10)
            upload = client.post("/api/review", content=source.tobytes(), headers={"Content-Type": "application/pdf"})
    else:
        upload = client.post("/api/review", content=text.encode(), headers={"Content-Type": "text/plain"})
    upload.raise_for_status()
    review = upload.json()
    headers = {"X-Review-Token": review["token"]}
    payload = choose(review["groups"])
    preview = client.post("/api/preview", headers=headers, json=payload)
    if preview.is_success:
        headers["X-Preview-Receipt"] = preview.json()["receipt"]
    release = client.post("/api/release", headers=headers, json=payload)
    return preview, release


def identity_selection(groups):
    mentions = {item["text"]: item["id"] for group in groups for item in group["mentions"]}
    kept = {"Jane", "Ms. Doe", "public@example.org"}
    return {
        "mention_decisions": {item["id"]: "keep" if item["text"] in kept else "redact"
                              for group in groups for item in group["mentions"]},
        "links": {mentions[value]: {"status": decision}
                  for value, decision in (("J. Doe", "confirm"), ("Jane", "reject"), ("Ms. Doe", "detach"))},
        "manual": ["ZX99"],
    }


def release_cases(client):
    source = ("Name: Jane Doe\nJ. Doe first filed it.\nJane sent it.\nMs. Doe logged it.\n"
              "Email: private@example.org\nCopy: private@example.org\nKeep: public@example.org\nRef: ZX99\n"
              "The review team approved the public report for publication.")
    results = []
    for pdf_input in (False, True):
        preview, response = submit(client, source, identity_selection, pdf=pdf_input)
        response.raise_for_status()
        release = response.json()
        txt = base64.b64decode(release["txt"], validate=True).decode()
        with pymupdf.open(stream=base64.b64decode(release["pdf"], validate=True), filetype="pdf") as pdf:
            extracted = "\n".join(page.get_text() for page in pdf)
        artifacts = (txt, extracted)
        results.append({"fixture": "pdf" if pdf_input else "txt",
                        "approved_removed": all("Jane Doe" not in text and "ZX99" not in text for text in artifacts),
                        "explicit_keep": all("public@example.org" in text for text in artifacts),
                        "confirmed_removed": all("J. Doe" not in text for text in artifacts),
                        "rejected_preserved": all("Jane sent" in text for text in artifacts),
                        "detached_preserved": all("Ms. Doe logged" in text for text in artifacts),
                        "repeats_removed": all("private@example.org" not in text and text.count("[REDACTED]") == 5
                                               for text in artifacts),
                        "txt_verified": txt == release["text"] == preview.json()["text"],
                        "pdf_verified": txt.split() == extracted.split()})

    def keep_all(groups):
        return {"decisions": {group["id"]: "keep" for group in groups}, "manual": ["public@example.org"]}

    failures = []
    cases = [
        ("overlap_conflict", "Email: public@example.org", keep_all, "overlaps", 422, 409),
        ("normalized_residual", "Project Harbor\nProject\nHarbor.", lambda _: {"manual": ["Project Harbor"]},
         "still appears", 422, 409),
        ("unresolved_link", "Name: Jane Doe\nJ. Doe filed it.",
         lambda _: {"decisions": {0: "redact"}, "manual": []}, "Resolve every proposed link", 422, 409),
    ]
    for name, text, choose, message, preview_code, release_code in cases:
        preview, response = submit(client, text, choose)
        failures.append({"fixture": name, "preview_status": preview.status_code, "release_status": response.status_code,
                         "passed": preview.status_code == preview_code and response.status_code == release_code
                         and message in preview.json()["detail"] and "txt" not in response.json()})

    def contaminate(document):
        document.set_metadata({"author": "SYNTHETIC CHECK"})
        return document.tobytes(no_new_id=True)

    with patch.object(documents, "serialize_pdf", contaminate):
        preview, response = submit(client, "Internal ref ZX99.", lambda _: {"manual": ["ZX99"]})
    failures.append({"fixture": "contaminated_pdf", "preview_status": preview.status_code,
                     "release_status": response.status_code, "passed": preview.status_code == 200
                     and response.status_code == 422 and "txt" not in response.json()})

    def contaminate_txt(payload, expected, removed, **checks):
        return documents.verify_txt(payload + b"ZX99", expected, removed, **checks)

    with patch.object(api, "verify_txt", contaminate_txt):
        preview, response = submit(client, "Internal ref ZX99.", lambda _: {"manual": ["ZX99"]})
    failures.append({"fixture": "contaminated_txt", "preview_status": preview.status_code,
                     "release_status": response.status_code, "passed": preview.status_code == 200
                     and response.status_code == 422 and "txt" not in response.json()})
    return {"successful_releases": results, "negative_cases": failures}


def summary_cases(client):
    fixtures = [
        ("safe_control", "The review team completed the audit and approved the public report.", None),
        ("direct_identifier", "Please notify the entire support group through demo＠example.org immediately.", None),
        ("obfuscated_identifier", "The confidential appendix lists the address demo at example dot org.", None),
        ("known_pii_injected", "The review team completed the audit and approved the public report.",
         "The internal reference was ZX99."),
        ("quasi_combination", "The application records age 47 and lists a home postal code of 90210.", None),
    ]
    results = []
    original = api.summarize
    for name, excerpt, injected in fixtures:
        samples = []
        returned = []
        for _ in range(5):
            def record(sanitized):
                if ("ZX99" in sanitized or "Mira Lane" in sanitized
                        or not sanitized.startswith("Internal reference: [REDACTED]")):
                    raise AssertionError("The summarizer input did not pass the synthetic redaction guard.")
                start = perf_counter()
                result = original(sanitized)
                samples.append(((perf_counter() - start) * 1000, result))
                return injected if injected is not None else result

            with patch.object(api, "summarize", record):
                _, release = submit(client, f"Internal reference: ZX99\nName: Mira Lane\n{excerpt}",
                                    lambda groups: {"decisions": {group["id"]: "keep" for group in groups},
                                                    "manual": ["ZX99"]})
            release.raise_for_status()
            result = release.json()
            if "Mira Lane" not in result["text"] or "ZX99" in result["text"]:
                raise AssertionError("The released document did not preserve the test decisions.")
            returned.append(result["summary"])
        results.append({"fixture": name, "expected_withholding": name != "safe_control",
                        "trials": len(returned), "withheld_count": returned.count(""),
                        "generation_ms_median": median(time for time, _ in samples),
                        "attempts_reached_gate": sum(injected is not None or excerpt in text for _, text in samples),
                        "final_summaries": sorted(set(returned))})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New JSON file to create")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output path to preserve existing evidence.")
    inputs = (DATASET, SUPPLEMENT)
    records = [record for path in inputs for record in json.loads(path.read_text(encoding="utf-8"))]
    baseline = command("evaluation.measure_detection", "--rules-only")
    performance = command("evaluation.benchmark_release")
    nlp = detection.local_nlp()
    modes = {"rules_plus_identity": detect(records, use_nlp=False, resolve=True),
             "rules_without_identity": detect(records, use_nlp=False, resolve=False)}
    modes["optional_nlp"] = ({"status": "unavailable", "model": detection.MODEL} if nlp is None else
                             {"status": "measured", "model": detection.MODEL,
                              "plus_identity": detect(records, use_nlp=True, resolve=True),
                              "without_identity": detect(records, use_nlp=True, resolve=False)})
    with patch.object(detection, "local_nlp", return_value=None), TestClient(api.app) as client:
        integrity = release_cases(client)
        summaries = summary_cases(client)
    results = {
        "dataset": {"documents": len(records), "characters": sum(len(record["text"]) for record in records),
                    "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}},
        "environment": {**performance["environment"], "machine": platform.machine(),
                        "processor": os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor()},
        "commands": ["python -B -m evaluation.run_phase8 --output " + str(args.output),
                     "python -B -m evaluation.measure_detection --rules-only",
                     "python -B -m evaluation.benchmark_release"],
        "phase6_corpus_rerun": baseline, "expanded_detection": modes, "redaction_integrity": integrity,
        "summary_privacy": summaries, "performance": performance,
    }
    with args.output.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(results, output, indent=2, ensure_ascii=False)
        output.write("\n")
    print(args.output)
    passed = (
        all(all(value for key, value in row.items() if key != "fixture") for row in integrity["successful_releases"])
        and all(row["passed"] for row in integrity["negative_cases"])
        and all(row["withheld_count"] == (row["trials"] if row["expected_withholding"] else 0)
                and row["attempts_reached_gate"] == row["trials"] for row in summaries)
    )
    if not passed:
        raise SystemExit("See saved evidence for evaluation failures.")


if __name__ == "__main__":
    main()
