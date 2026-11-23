# PrivyDoc

PrivyDoc is a local document review and redaction application for UTF-8 TXT and text-based PDF. It groups candidate mentions for human review, generates an extractive summary from sanitized text, and checks the release artifacts for approved values.

## Run on Windows

Use Python 3.11 or newer and Node.js 20 or newer. From the repository root in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
.venv\Scripts\python.exe -m uvicorn privydoc.api:app --host 127.0.0.1 --port 8000 --no-access-log
```

Open `http://127.0.0.1:8000`. For frontend development, run `npm.cmd --prefix frontend run dev` in a second terminal and use its printed URL. Vite proxies the API to port 8000.

Run the backend tests with `.venv\Scripts\python.exe -m pytest -q`. The frontend build also runs the TypeScript checker.

## Review and release

1. Choose a TXT or text-based PDF up to 8 MiB and 100,000 extracted characters. PDFs must be unlocked and have extractable text on every page, with at most 100 pages.
2. Inspect the extracted source. For every group, choose **Redact group** or **Keep group**. Expand a group to see its mentions, line numbers, and detection method.
3. Add manual redactions for missed values. Manual redaction replaces every case-insensitive literal match, including overlaps, and approved spans take precedence wherever they overlap other candidates.
4. Apply redactions. Download the sanitized TXT, the summary when enough text remains, and the PDF when its saved text passes verification.

For a demo, save the following as UTF-8 TXT:

```text
Name: Jane Doe
J. Doe sent the quarterly report to jane@example.org.
Jane asked the review team to approve the final version before publication.
Internal reference: ZX99.
```

Approve the name and email groups, and add `ZX99` manually. Inspect the resulting TXT, PDF, and summary.

## Engineering notes

- **Detection** combines deterministic patterns, an optional local English spaCy NER model, and span-level evidence. The review response includes a candidate ID, entity type, evidence list, and offsets for each mention. Existing group IDs, approve/keep decisions, and manual redactions use the same workflow.
- **Review lifecycle** uses opaque tokens and an in-process store capped at eight active reviews. Reviews expire after 15 minutes and are removed on discard or successful release. The backend streams uploads with a size limit and keeps the bytes in memory during extraction. The frontend holds review data in React state and requests no-store responses. There is no external inference, upload persistence, or application logging of source text. Use a single loopback worker. Process memory, browser memory, and OS paging have their usual retention characteristics.
- **Release** replaces approved spans before summarization. Validation checks known approved values with Unicode normalization, case folding and whitespace folding. It can catch an approved value left elsewhere, but cannot establish that the document is free of personal information. Spelling variants, unlinked references, and context can require manual review.
- **PDF export** typesets sanitized text into a fresh PDF. It drops the input's layout, images, annotations, attachments, and metadata. The exporter reopens the actual serialized bytes and checks the extracted text against the expected text and approved values. If the built-in font cannot represent the output or the PDF exceeds its layout limits, verified TXT remains available. OCR, source layout preservation, and PDF.js viewing are outside this implementation.

## Detection coverage

| Entity | Supported forms |
| --- | --- |
| Email | Conventional dotted domains and lightly obfuscated values such as `jane [at] example [dot] com` and `jane(at)example(dot)com`. |
| Phone | Formatted North American numbers, including parenthesized area codes and optional leading `1`. Compact domestic numbers require a phone label. International rules cover `+1`, `+44` geographic/mobile-style prefixes beginning with `1`, `2`, or `7`, and `+91` mobile-style numbers beginning with `6` through `9`. Spaces, dots, and hyphens are supported in those international formats. |
| Address | Numbered streets with common English suffixes, optional apartment/unit, and PO boxes. A US city/state/ZIP can follow after a comma or on the next line. |
| Employee, account, and student IDs | Supported field labels followed by `:`, `=`, or `#`, then a 2–32 character identifier containing at least one digit. The rules exclude common dashed date shapes and dotted versions. |
| Government ID | Hyphenated US SSNs after an `SSN` or `Social Security Number` label. Component checks catch invalid shapes. Unlabeled SSNs require manual review. |
| Person | Capitalized multiword names after person labels or titles, plus multiword `PERSON` spans from the optional model. The detector links repeated full names, capitalized short forms, and dotted or undotted first initials with surnames. Shared short forms have their own review group. |

Offsets are zero-based Unicode code points with an exclusive end, relative to the normalized `text` in the review response. `line` is the one-based start line in that text, including for PDFs. Exact matches with the same entity type share one candidate and retain all distinct detector evidence. Conflicting types and partially overlapping spans remain reviewable. The release API continues to give approved spans precedence over overlapping kept spans. Person aliases inside structured values are suppressed to avoid spurious duplicate candidates.

Rules run locally on the CPU without model packages. To enable the optional model in an environment with compatible Python packages, install spaCy and its English model into the same virtual environment, then restart PrivyDoc:

```powershell
.venv\Scripts\python.exe -m pip install spacy
.venv\Scripts\python.exe -m spacy download en_core_web_sm
```

The model is loaded once per process on demand. Missing packages or a model loading error leave rules available. Inference runs locally. The optional model integration test runs automatically when both packages are available.

## Detection evaluation

```powershell
.venv\Scripts\python.exe -B -m evaluation.measure_detection --rules-only
.venv\Scripts\python.exe -B -m evaluation.measure_detection
```

The evaluator scores unique `(document, entity type, start, end)` tuples against the labeled synthetic corpus. Partial matches and incorrect types count as both a false positive and a false negative. The report includes micro-averaged and per-entity metrics, mismatch details, and detector-only timings. Model startup is timed separately. The second command reports which model, if any, actually ran.

The measured Windows rules-only baseline on seven synthetic documents (1,112 characters) was **29 true positives, 0 false positives, and 2 false negatives**. Precision was **100.00%**, recall **93.55%**, and F1 **96.67%**. The first pass took **3.58 ms**, and the median over 30 corpus passes was **1.65 ms**. The misses were an unlabeled person name in prose and a German `+49` number. These results describe this small corpus. Coverage for unseen documents, memory at the upload limit, and optional model performance require separate measurements.
