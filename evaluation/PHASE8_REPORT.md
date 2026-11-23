# PrivyDoc Phase 8 evaluation

This evaluation covers synthetic inputs on the Phase 7 application revision, `1b6a117`. No application code, detector thresholds, existing fixtures, scoring rules, or Phase 6 benchmark code changed. The [recorded JSON](phase8_results.json) contains exact results, mismatch spans, corpus hashes, timing maxima, and the measured environment.

## Reproduce on Windows

From the repository root in PowerShell, with the environment set up using the [README](../README.md):

```powershell
.venv/Scripts/python.exe -B -m evaluation.run_phase8 --output evaluation/phase8_reproduction.json
```

The evidence recorded here used the same command with `evaluation/phase8_results.json` as the output path. The runner refuses to overwrite existing evidence. It invokes the unchanged Phase 6 commands below in separate Python processes before running the expanded scoring and in-process API probes.

```powershell
.venv/Scripts/python.exe -B -m evaluation.measure_detection --rules-only
.venv/Scripts/python.exe -B -m evaluation.benchmark_release
```

The environment was Windows 11 build 26300 on an AMD Ryzen 7 7735HS with 16 logical CPUs and approximately 15.31 GiB of reported physical RAM. Python was 3.13.2, PyMuPDF 1.28.2, and FastAPI 0.165.0. The performance run used the CPU and local rules. The optional real spaCy English model was unavailable in the evaluation environment.

## Detection and ablation

The [original corpus](pii_synthetic.json) has 11 synthetic documents and 1,896 characters. The [supplement](pii_phase8_supplement.json) adds three documents covering repeated email and Indian phone occurrences, an initials collision, Indian identifiers, and a placeholder national-ID shape distinct from the supported US SSN type. The combined corpus has 14 documents, 2,282 characters, and 64 gold spans. Other examples cover Unicode names, obfuscated emails, Indian addresses, dates, product codes, and ordinary numbers. All examples are synthetic. Repeated values are annotated separately using unique surrounding anchors.

Each gold or predicted item is a tuple of document ID, entity type, start offset, and exclusive end offset in Unicode code points. An exact tuple match is a true positive. A partial or mistyped span adds one false positive and one false negative. Precision is TP divided by TP plus FP, recall is TP divided by TP plus FN, and F1 is their harmonic mean. Overall scores are micro-averaged. A dash means precision was undefined because that type had no predictions.

| Entity | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| Person | 22 | 0 | 1 | 100.00% | 95.65% | 97.78% |
| Ambiguous name | 2 | 0 | 0 | 100.00% | 100.00% | 100.00% |
| Email | 11 | 0 | 1 | 100.00% | 91.67% | 95.65% |
| Phone | 7 | 1 | 2 | 87.50% | 77.78% | 82.35% |
| Address | 3 | 1 | 2 | 75.00% | 60.00% | 66.67% |
| Employee ID | 4 | 0 | 0 | 100.00% | 100.00% | 100.00% |
| Student ID | 4 | 0 | 0 | 100.00% | 100.00% | 100.00% |
| Account ID | 2 | 0 | 0 | 100.00% | 100.00% | 100.00% |
| Government ID, non-US placeholder | 0 | 0 | 1 | — | 0.00% | 0.00% |
| Government ID, US SSN | 2 | 0 | 0 | 100.00% | 100.00% | 100.00% |
| **Overall** | **57** | **2** | **7** | **96.61%** | **89.06%** | **92.68%** |

| Expanded corpus mode | TP / FP / FN | Precision | Recall | F1 | Median of 30 passes |
|---|---:|---:|---:|---:|---:|
| Rules with identity resolution | 57 / 2 / 7 | 96.61% | 89.06% | 92.68% | 4.830 ms |
| Rules without identity resolution | 46 / 2 / 18 | 95.83% | 71.88% | 82.14% | 1.063 ms |
| Real spaCy with or without identity resolution | — | — | — | — | Unavailable |

On this corpus, identity resolution added 11 exact matches, including two ambiguous name mentions that can be reviewed separately. This scores surfaced spans and types. Confirmation accuracy and reviewer effort are outside the metric. The unchanged Phase 6 corpus rerun reproduced **44 TP, 2 FP, 6 FN**, with **95.65% precision, 88.00% recall, and 91.67% F1**. Median time was 3.905 ms, compared with the published 3.89 ms. The expanded corpus scores reflect a different set of documents.

The two false positives were a partial street span within a full Indian address and a phone-shaped catalog code. Misses included that full address, a second Indian address, a domestic Indian mobile format, a German number, an unlabeled name, a fullwidth-at email, and the placeholder national ID. Labeled US SSNs and the repeated contact examples did well. Dates and ordinary product-code examples mostly stayed unflagged.

## Redaction integrity

The runner submits the same approved and kept selections to the production API through `TestClient` for one TXT upload and one synthetic text PDF upload. It checks both verified artifacts per release with independent expectations, including reparsing the PDF bytes and comparing their text with downloaded TXT. The numerator counts passing fixture releases, each of which must satisfy the check for both artifacts.

| Check | Passed / attempted |
|---|---:|
| Approved name and manual reference removed | 2 / 2 |
| Explicit Keep preserved | 2 / 2 |
| Confirmed alias redacted | 2 / 2 |
| Rejected link with explicit Keep preserved | 2 / 2 |
| Detached mention with explicit Keep preserved | 2 / 2 |
| Both occurrences of repeated approved email removed | 2 / 2 |
| TXT bytes matched release text and preview | 2 / 2 |
| Reopened PDF text matched released TXT after whitespace folding | 2 / 2 |

| Negative case | Preview → release | Outcome |
|---|---|---|
| Manual redaction overlaps explicit Keep | 422 → 409 | Blocked |
| Normalized repeat survives proposed redaction | 422 → 409 | Blocked |
| Identity link remains unresolved | 422 → 409 | Blocked |
| Hidden metadata injected after PDF generation | 200 → 422 | Blocked |
| Extra approved data injected into TXT before verification | 200 → 422 | Blocked |

All five negative cases returned no download. An unresolved proposal blocks release. After explicit rejection or detachment, the mention’s independent Keep controls whether it remains in the document.

## Summary privacy

Each trial manually removes a synthetic reference and explicitly keeps a synthetic name in the released document. A guard checks that both values are absent from the summarizer input before calling the production extractive function. The direct and quasi-identifier fixtures deliberately contain signals missed by document review. The real summarizer selected those signals from the redacted input in every trial, exercising the final privacy gate. The known-value case injects a reference into the proposed output after generation. That case exercises the complete output gate, including provenance, without isolating which check rejects it.

| Scenario | Withheld / trials | Median generation only |
|---|---:|---:|
| Safe control | 0 / 5 | 0.207 ms |
| Direct email with fullwidth at sign | 5 / 5 | 0.183 ms |
| Email written with “at” and “dot” | 5 / 5 | 0.206 ms |
| Injected known approved reference | 5 / 5 | 0.216 ms |
| Age and postal-code combination | 5 / 5 | 0.241 ms |

The tested identifying summaries returned **0 outputs in 20 leakage attempts**. All 25 document releases succeeded. The synthetic guard passed on all trials. Generation-only times exclude input validation, output checks, and HTTP. The performance benchmark below includes the summary checks.

## Performance comparison

The benchmark is the unchanged Phase 6 workload. Each size runs in a fresh process with one warm-up and five timed iterations. Entries show **Phase 6 → Phase 8** medians in ms, except peak process RSS in MiB. Preview includes redaction preparation. TXT and PDF include reopened-artifact verification. Summary includes input validation and output checks. Stage timings exclude fixture generation, HTTP transfer, and browser rendering.

| Input | Extract | Detect | Identity | Preview | TXT | PDF | Summary | Peak RSS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 page | 8.66 → 9.44 | 0.54 → 0.53 | 4.18 → 4.09 | 0.30 → 0.30 | 0.26 → 0.27 | 5.08 → 5.40 | 0.64 → 0.65 | 70.86 → 70.90 |
| 10 pages | 81.85 → 80.39 | 5.24 → 5.09 | 42.47 → 43.12 | 2.73 → 2.60 | 2.53 → 2.43 | 18.17 → 17.95 | 4.06 → 3.99 | 71.95 → 71.92 |
| 50 pages | 395.57 → 401.46 | 25.50 → 25.71 | 212.55 → 214.13 | 14.65 → 14.79 | 13.97 → 14.34 | 74.29 → 75.62 | 20.73 → 21.04 | 75.57 → 75.43 |

Extraction and identity resolution remain the largest measured costs on the 50-page fixture. With five iterations per size and no application changes, the small differences are reported as run-to-run variation. Peak RSS includes startup and native PDF memory.

## Interpretation

PrivyDoc carried the tested review decisions into both artifacts. Identity resolution surfaced additional initials and ambiguous references that rules alone missed. Human review remains necessary because formats vary, people can share initials, and some context-dependent identifiers are only evident to a reviewer. The corpus is small and synthetic, with very few examples of some categories. The results do not establish accuracy on unseen documents or privacy against arbitrary re-identification.

PDF release reflows sanitized text into a fresh file so source objects need never be carried over. Original column order, table layout, images, annotations, and typography can be lost. Some Unicode and layout cases use verified TXT fallback. These measurements cover single documents, controlled source shapes, and selected failure cases. They do not measure concurrent load, arbitrary PDF decompression costs, OCR, or real spaCy accuracy.
