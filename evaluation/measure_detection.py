"""Measure typed, exact-span detection against the checked-in synthetic corpus."""

import argparse
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from privydoc import detection

DATASET = Path(__file__).with_name("pii_synthetic.json")


def expected_spans(record):
    expected = set()
    for kind, anchor, *subspan in record["entities"]:
        text = record["text"]
        if text.count(anchor) != 1:
            raise ValueError(f"{record['id']}: each annotation anchor must be unique")
        value = subspan[0] if subspan else anchor
        if anchor.count(value) != 1:
            raise ValueError(f"{record['id']}: each entity must be unique within its anchor")
        start = text.index(anchor) + anchor.index(value)
        expected.add((record["id"], kind, start, start + len(value)))
    if len(expected) != len(record["entities"]):
        raise ValueError(f"{record['id']}: duplicate gold spans")
    return expected


def scores(expected, predicted):
    tp = len(expected & predicted)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=30, help="Number of timed corpus passes")
    parser.add_argument("--rules-only", action="store_true", help="Skip the optional spaCy model")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")

    records = json.loads(DATASET.read_text(encoding="utf-8"))
    texts = {record["id"]: record["text"] for record in records}
    if len(texts) != len(records):
        raise ValueError("Document IDs must be unique")
    expected = set().union(*(expected_spans(record) for record in records))
    start = perf_counter()
    model = None if args.rules_only else detection.local_nlp()
    load_ms = (perf_counter() - start) * 1000
    durations = []
    for _ in range(args.runs):
        start = perf_counter()
        predicted = {
            (doc, item.kind, item.start, item.end)
            for doc, text in texts.items()
            for group in detection.detect(text, use_nlp=not args.rules_only)
            for item in group
        }
        durations.append((perf_counter() - start) * 1000)

    def describe(spans):
        return [{"document": doc, "kind": kind, "start": start, "end": end, "text": texts[doc][start:end]}
                for doc, kind, start, end in sorted(spans)]

    print(json.dumps({
        "model": detection.MODEL if model is not None else None,
        "documents": len(texts),
        "characters": sum(map(len, texts.values())),
        "timing_ms": {"model_check_and_load": load_ms, "first_corpus_pass": durations[0],
                      "median_corpus_pass": median(durations), "runs": args.runs},
        "overall": scores(expected, predicted),
        "per_entity": {
            kind: scores({span for span in expected if span[1] == kind}, {span for span in predicted if span[1] == kind})
            for kind in sorted({span[1] for span in expected | predicted})
        },
        "false_positives": describe(predicted - expected),
        "false_negatives": describe(expected - predicted),
    }, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
