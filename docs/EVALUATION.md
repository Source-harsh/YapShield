# Evaluation and submission evidence

## Synthetic detection results

Both configurations used the unchanged 11-document corpus with exact typed-span matching and identity resolution. A partial match is counted as both a false positive and a false negative. These measurements describe the synthetic corpus. Real-world accuracy is not established.

| Mode | TP / FP / FN | Precision | Recall | F1 | Median corpus pass | Peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Rules only | 44 / 2 / 6 | 95.65% | 88.00% | 91.67% | 4.09 ms | 67.13 MiB |
| Rules plus spaCy | 45 / 2 / 5 | 95.74% | 90.00% | 92.78% | 95.85 ms | 158.48 MiB |

The real spaCy 3.8.18 and `en_core_web_sm` 3.8.0 recovered one otherwise missed person span on this corpus. The measured model load including import was 1,787.05 ms. The Phase 8 supplement and ablation are separate evaluations. See the [README NLP procedure](../README.md#optional-local-named-entity-recognition) and [Phase 8 report](../evaluation/PHASE8_REPORT.md).

Indian postal addresses and domestic phone forms were missed. Exact address scoring penalized a partial street match. A phone-shaped product code was an extra detection. A fullwidth-at email and non-US government ID shape were missed in the expanded corpus. Personal-location candidates were tested separately from the scored corpus.

## Integrity, summary, and performance

Phase 8 passed the integrity checks for both the TXT and PDF inputs. All five negative cases blocked downloads. The known, direct, obfuscated, and quasi-identifier summary probes were withheld in all 20 trials. The safe summary was returned in all five control trials. The benchmark below reuses Phase 8’s CPU-only run and includes artifact and summary checks. Full per-stage timings and Phase 6 comparisons remain in the [report](../evaluation/PHASE8_REPORT.md) and [JSON](../evaluation/phase8_results.json).

| Input | Extraction | Detection | Identity | Preview | Verified TXT | Verified PDF | Checked summary | Peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 page | 9.44 ms | 0.53 ms | 4.09 ms | 0.30 ms | 0.27 ms | 5.40 ms | 0.65 ms | 70.90 MiB |
| 10 pages | 80.39 ms | 5.09 ms | 43.12 ms | 2.60 ms | 2.43 ms | 17.95 ms | 3.99 ms | 71.92 MiB |
| 50 pages | 401.46 ms | 25.71 ms | 214.13 ms | 14.79 ms | 14.34 ms | 75.62 ms | 21.04 ms | 75.43 MiB |

PDF export retypesets sanitized text and discards the input layout. Scanned and image-only PDFs are unsupported. Heuristic detection and summary checks require human review.

## Final quality gate

The gate below records Phase 9. See [local Qwen validation](#local-qwen-validation) for the later branch results.

| Check | Result |
| --- | --- |
| Backend with real spaCy enabled | 111 passed, 0 skipped |
| Backend forced to rules-only | 109 passed, 2 real-model tests intentionally skipped |
| Frontend unit tests | 2 passed |
| Headless Chromium | 1 rules-only workflow passed, covering TXT, PDF, conflict recovery, downloads, and summary withholding |
| TypeScript and production build | Passed |
| Python and frontend dependency consistency | Passed |
| Git whitespace check | Passed |

Both backend modes exercised the checked-in demo documents through the review, receipt, export, and summary APIs. Headless Chromium was also used for desktop and 390 px review inspection. The runs reported a Starlette TestClient deprecation warning, Framer Motion bundler notices, and Git line-ending conversion notices.

To repeat the backend gate in both modes:

```powershell
Remove-Item Env:PRIVYDOC_RULES_ONLY -ErrorAction SilentlyContinue
.venv/Scripts/python.exe -B -m pytest -q -rs -p no:cacheprovider
$env:PRIVYDOC_RULES_ONLY = '1'
.venv/Scripts/python.exe -B -m pytest -q -rs -p no:cacheprovider
Remove-Item Env:PRIVYDOC_RULES_ONLY
```

## Local Qwen validation

The comparison reused the unchanged 11-document `pii_synthetic.json` corpus. Both modes used the real spaCy model and production FastAPI review path. Exact matches require the same document, entity type, and Unicode start and exclusive end. Each mode uploaded each fixture once using FastAPI TestClient, with spaCy preloaded. The upload timings include in-process HTTP handling. Adapter timings include the loopback Ollama call, parsing, and grounding. These small synthetic samples do not establish real-world accuracy.

| Mode | TP / FP / FN | Precision | Recall | F1 | Median upload | Median adapter | Added candidates |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Rules plus spaCy | 45 / 2 / 5 | 95.74% | 90.00% | 92.78% | 14.350 ms | — | 0 |
| Rules plus spaCy plus Qwen | 45 / 2 / 5 | 95.74% | 90.00% | 92.78% | 566.270 ms | 549.904 ms | 0 |

Ollama 0.40.17 reported the local `qwen2.5:0.5b` model already resident, so cold startup was not isolated. Reported resident model size was 483,645,193 bytes, approximately 461 MiB. Overall evaluation-process peak RSS across both modes was 169.04 MiB, excluding the separate Ollama process. On the corpus, five opted-in requests completed without new candidates and six fell back. The original two-name fixture had previously fallen back because Qwen’s valid span exactly duplicated a spaCy candidate. Exact baseline duplicates are now ignored. Partial overlaps and other validation failures still discard the optional batch.

| Fictional API smoke test | HTTP | Ollama status | Baseline | Added | Source and offsets | Decision required |
| --- | ---: | --- | ---: | ---: | --- | --- |
| Full name | 200 | completed | 1 | 0 | — | — |
| Initial-based name | 200 | completed | 1 | 0 | — | — |
| Multiple people | 200 | completed | 2 | 0 | — | — |
| Repeated name | 200 | fallback | 2 | 0 | — | — |
| No names | 200 | fallback | 0 | 0 | — | — |
| Prompt-injection-like text | 200 | fallback | 1 | 0 | — | — |
| Unicode novel name | 200 | completed | 0 | 1 | Exact `Luma Fenwick`, [2, 14) | Yes |

Chromium confirmed that the real suggestion appeared with Ollama evidence, required a decision, could be kept, and could then be approved for redaction and released after a fresh preview. The test saw no browser HTTP requests outside the local app. A separate live connection-refusal check against an isolated loopback port returned HTTP 200 with fallback and retained the baseline candidate. The existing release and sanitized-summary tests passed. The daemon's background traffic and logs were outside the application checks.

The full backend gate passed 141 tests with zero skips. Forcing rules-only with the Ollama flag on passed 139 tests and intentionally skipped two real-spaCy tests. The two frontend unit tests and both Chromium workflows passed, as did TypeScript, the production build, dependency checks, and Git's whitespace check.

The [new evidence JSON](../evaluation/ollama_results.json) contains per-upload timings and grounding checks. Reproduce it with a new output filename. Run the browser test once in each mode against the built frontend:

```powershell
.venv/Scripts/python.exe -B -m evaluation.measure_ollama --output evaluation/ollama_reproduction.json
node --test frontend/tests/review.browser.mjs
$env:PRIVYDOC_BROWSER_LIVE_OLLAMA = '1'
node --test frontend/tests/review.browser.mjs
Remove-Item Env:PRIVYDOC_BROWSER_LIVE_OLLAMA
```
