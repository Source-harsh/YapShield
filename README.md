# PrivyDoc

PrivyDoc is a local document review and redaction application for UTF-8 TXT and text-based PDF. It groups candidate mentions for human review, generates an extractive summary from sanitized text, and checks the release artifacts for approved values.

Built for Thilli AI Hackathon, Problem Statement 3. For the submission walkthrough, see the [demo guide](docs/DEMO_GUIDE.md). For technical evidence, see the [architecture](docs/ARCHITECTURE.md), [data flow](docs/DATA_FLOW.md), [security boundaries](docs/SECURITY.md), and [evaluation summary](docs/EVALUATION.md).

## Architecture

The React and TypeScript interface is built with Vite, styled with CSS and Tailwind, and uses Framer Motion for transitions. A Python FastAPI service runs on loopback under Uvicorn and serves the production frontend. PyMuPDF extracts PDF text and writes and verifies reflowed PDFs. The detector combines local rules with optional spaCy named-entity recognition.

The browser sends the file to `/api/review`. The backend extracts text, detects candidate spans, and proposes document-local identity links. The browser submits mention decisions, link decisions, and manual redactions to `/api/preview`. The backend validates the spans and returns the preview and an opaque receipt. `/api/release` requires that receipt and the matching decisions. The browser downloads verified TXT and, when available, PDF bytes and a checked local extractive summary.

## Run on Windows

Use Python 3.11 or newer and Node.js 20 or newer. Use Node.js 24 for the test commands below. From the repository root in PowerShell, create the environment, install dependencies, build the frontend, and start the loopback server:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
.venv\Scripts\python.exe -m uvicorn privydoc.api:app --host 127.0.0.1 --port 8000 --no-access-log
```

Open `http://127.0.0.1:8000`. For frontend development, keep the backend running and start Vite in a second PowerShell terminal from the repository root:

```powershell
npm.cmd --prefix frontend run dev
```

Open the loopback URL printed by Vite. The development server proxies `/api` to port 8000.

## Review and release

1. Choose a TXT or text-based PDF up to 8 MiB and 100,000 extracted characters. PDFs must be unlocked and have extractable text on every page, with at most 100 pages.
2. Select a highlighted span or a mention in the list. Inspect its context and evidence, then approve it for redaction or keep it. Confirm, reject, or detach each proposed identity link separately from the redaction decision.
3. Add manual redactions for missed values. Manual redaction matches case-insensitive literal occurrences. An approved span or manual match that overlaps an explicitly kept mention blocks the preview and export until the decisions agree.
4. Select **Preview redactions** to see the exact sanitized text. Select **Apply redactions**, then download the sanitized TXT, the summary when available, and the PDF when its saved text passes verification. A review change clears the preview.

For a demo, save the following as UTF-8 TXT:

```text
Name: Jane Doe
J. Doe sent the quarterly report to jane@example.org.
Jane asked the review team to approve the final version before publication.
Internal reference: ZX99.
```

Confirm the proposed name links, approve each name and email mention, and add `ZX99` manually. Inspect the resulting TXT, PDF, and summary. Change a mention decision after previewing and confirm that **Apply redactions** is unavailable until you preview again. To test Keep, make a second review, keep the email explicitly, and check that it remains in the downloaded document and is absent from the summary.

## Engineering notes

- **Detection** combines deterministic patterns, an optional local English spaCy NER model, and span-level evidence. The review response includes a candidate ID, entity type, evidence list, and offsets for each mention. Existing group IDs, approve/keep decisions, and manual redactions use the same workflow.
- **Review lifecycle** uses opaque tokens and an in-process store capped at eight active reviews. Reviews expire after 15 minutes and are removed on discard or successful release. The backend streams uploads with a size limit and keeps the bytes in memory during extraction. The frontend holds review data in React state and requests no-store responses. There is no external inference, upload persistence, or application logging of source text. Use a single loopback worker. Process memory, browser memory, and OS paging have their usual retention characteristics.
- **Release** replaces approved spans in the extracted text. The API requires the receipt from the latest successful preview and binds it to the review decisions and manual redactions. It reopens the UTF-8 bytes served for download and checks for approved values and explicitly kept occurrences. Residual checks account for Unicode compatibility forms, case, whitespace, common punctuation, email obfuscation, and numeric separators.
- **PDF export** typesets sanitized text into a fresh PDF. It drops the input's layout, images, annotations, attachments, and metadata. The exporter reopens the serialized bytes and checks extracted text, metadata, annotations, attachments, and the object and stream structure against the generated document. A verification failure blocks release. Unsupported characters and known layout limits leave verified TXT available. PDF uploads require extractable text on every page. PDF extraction sorts page text, so tables and columns can lose their intended reading order. Repeated headers and footers remain in the review.
- **Summary** masks explicitly kept mentions in addition to approved redactions, verifies the resulting input, and uses the local extractive summarizer. The final summary is checked against its sanitized source, known values, local PII rules, and quasi-identifier signals such as age, location, job, and dates. A failed check withholds the summary. The checks can miss unfamiliar spellings, aliases, or identifying details that depend on context, and can suppress harmless summaries. Review downloaded content before sharing it.
- **Privacy assumptions** include a trusted local machine and browser session. Keep the service bound to loopback. The review intentionally shows the extracted source in the browser. Explicit Keeps and values missed during review can appear in the downloaded document. Release verification checks the reviewer’s decisions and known residual forms. Summary checks are a separate filter and can withhold a summary even when the document is available for download.

## Detection coverage

| Entity | Supported forms |
| --- | --- |
| Email | Conventional dotted domains and lightly obfuscated values such as `jane [at] example [dot] com` and `jane(at)example(dot)com`. |
| Phone | Formatted North American numbers, including parenthesized area codes and optional leading `1`. Compact domestic numbers require a phone label. International rules cover `+1`, `+44` geographic/mobile-style prefixes beginning with `1`, `2`, or `7`, and `+91` mobile-style numbers beginning with `6` through `9`. Spaces, dots, and hyphens are supported in those international formats. |
| Address | Numbered streets with common English suffixes, optional apartment/unit, and PO boxes. A US city/state/ZIP can follow after a comma or on the next line. |
| Employee, account, and student IDs | Supported field labels followed by `:`, `=`, or `#`, then a 2–32 character identifier containing at least one digit. The rules exclude common dashed date shapes and dotted versions. |
| Government ID | Hyphenated US SSNs after an `SSN` or `Social Security Number` label. Component checks catch invalid shapes. Unlabeled SSNs require manual review. |
| Person | Capitalized multiword names after person labels or titles, plus multiword `PERSON` spans from the optional model. The identity resolver proposes links for eligible short names and initials. Repeated full names and shared short forms can require separate decisions. |
| Location | Optional model predictions labeled `GPE`, `LOC`, or `FAC` that start the value of an explicit personal field such as `Home city:`, `Birthplace:`, or `Current residence:`. These appear as Location review candidates. |

Offsets are zero-based Unicode code points with an exclusive end, relative to the normalized `text` in the review response. `line` is the one-based start line in that text, including for PDFs. Exact matches with the same entity type share one candidate and retain all distinct detector evidence. Conflicting types and partially overlapping spans remain reviewable. Person aliases inside structured values are suppressed to avoid spurious duplicate candidates.

## Identity review

The identity resolver uses the raw Phase 2 detector spans and keeps their evidence. It compares document-local name tokens after case folding, whitespace tokenization, and normalization of common apostrophes and hyphens. The text and offsets in the review stay tied to the extracted document. The resolver can also surface an unlabeled two-word name if a separate dotted initial and surname corroborate it nearby.

- A first-name proposal requires a preceding compatible full name within 500 characters and no other compatible anchor. Initials and honorific plus surname may refer to a unique compatible name in either direction within that distance. The interface previews the original wording with surrounding text and explains each proposal.
- A bare surname, a collision between possible owners, or a repeated exact full name stays separately reviewable. Repeated spellings can describe different people. Common-word name forms are handled conservatively. The resolver does not assign pronouns, fuzzy variants, or nicknames to people. The rules assume given-name-first ordering and a final surname token.
- The `identity` field on a review group gives the cluster ID, primary and alternate mention IDs, relationship types, evidence, and uncertainty. Proposed links start pending. Decisions stay in the open review UI and are submitted with release. Separate uncertain mentions show up to eight possible matches and the total count.
- The UI sends `mention_decisions` and `links` objects keyed by mention ID to `/api/preview` and `/api/release`, with the returned receipt in the `X-Preview-Receipt` release header. Every mention has its own `keep` or `redact` decision, including confirmed aliases. The group decision format remains available. In that mode, confirmed links inherit the group decision and rejected or detached links carry an independent decision. Both formats require a resolution for every proposed link.
- All request formats protect explicitly kept spans from overlapping redactions. A spelling can remain in TXT and PDF at its explicitly kept occurrence when that same spelling was approved for removal elsewhere. Release checks for unreviewed repeats and masks the kept occurrence before generating a summary.

### Review navigation and checks

Use **Previous candidate** and **Next candidate** or press `K` and `J`. `R` approves the selected mention, `S` keeps it, and `?` opens keyboard help. The help panel includes a shortcut toggle. Shortcuts are inactive while editing a field. Activate an overlapping highlight repeatedly to cycle through its candidates.

Expand **Review exact occurrences** to preview detected values with identical spelling and case. Bulk actions apply only to pending eligible mentions and preserve existing choices. Ambiguous identities and unconfirmed links require individual decisions. Confirmed identity groups have a separate preview and bulk controls.

To run the review tests on Windows, use the existing Python environment, Node.js 24, and Chrome or Chromium. The browser test starts a temporary loopback API and uses the production frontend. Set `PRIVYDOC_CHROME` or `PRIVYDOC_PYTHON` if your executables use different paths.

```powershell
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\frontend\node_modules\.bin\tsc.cmd --noEmit --project frontend\tsconfig.json
npm.cmd --prefix frontend run build
node --test frontend/tests/review.test.mjs
node --test frontend/tests/review.browser.mjs
git diff --check
```

## Optional local named-entity recognition

Named-entity recognition (NER) identifies likely names and places in text. PrivyDoc uses the small English spaCy model `en_core_web_sm` with its `tok2vec` and `ner` components. NER adds review candidates alongside deterministic rules. Matching entity types and spans retain evidence from both sources. A place mentioned in ordinary text does not become a Location candidate. Postal addresses and phone numbers continue to use deterministic rules. The English model can miss names and locations or produce incorrect labels and boundaries.

Inference runs locally on the CPU. The core requirements omit spaCy. To reproduce the tested Windows x64 configuration with CPython 3.13, install the optional wheels in the project virtual environment and restart PrivyDoc:

```powershell
.venv\Scripts\python.exe -m pip install --only-binary=:all: spacy==3.8.18 https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl
.venv\Scripts\python.exe -m pip check
```

The model loads on demand once per process. Missing packages or a model loading error leave rules available. Set `$env:PRIVYDOC_RULES_ONLY = "1"` before starting the backend to force rules-only mode. Clear the variable and restart to enable the installed model. The browser regression uses rules-only mode for stable fixture expectations. The backend suite runs real-model integration tests when both packages are installed.

On the unchanged 11-document corpus, the model recovered one otherwise missed person name and produced no additional false positives. This corpus has no labeled personal-location fields. Location behavior is covered by separate integration tests. The address and phone scores stayed the same, including the Indian-format misses.

| Mode | TP / FP / FN | Precision | Recall | F1 | Median corpus pass | Model check and load | Peak process RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Rules only | 44 / 2 / 6 | 95.65% | 88.00% | 91.67% | 4.09 ms | <0.01 ms | 67.13 MiB |
| Rules plus spaCy | 45 / 2 / 5 | 95.74% | 90.00% | 92.78% | 95.85 ms | 1,787.05 ms | 158.48 MiB |

Measurements use 30 passes per mode in separate processes on Windows 11 with a Ryzen 7 7735HS. The model load time includes importing spaCy. Process RSS includes the interpreter and evaluator imports. The timing includes detection and identity resolution.

```powershell
.venv\Scripts\python.exe -B -m evaluation.measure_detection --rules-only --peak-memory
.venv\Scripts\python.exe -B -m evaluation.measure_detection --peak-memory
```

## Detection evaluation

```powershell
.venv\Scripts\python.exe -B -m evaluation.measure_detection --rules-only
.venv\Scripts\python.exe -B -m evaluation.measure_detection
```

The evaluator uses the same detector and identity resolver as upload and scores unique `(document, entity type, start, end)` tuples against the labeled synthetic corpus. Partial matches and incorrect types count as both a false positive and a false negative. The report includes micro-averaged and per-entity metrics, mismatches, and pipeline timing. Model startup is timed separately. The second command reports which model, if any, actually ran.

On the expanded 11-document corpus (1,896 characters), rules-only review produced **44 true positives, 2 false positives, and 6 false negatives**. Precision was **95.65%**, recall was **88.00%**, and F1 was **91.67%**. The median over 30 corpus passes was **3.89 ms**. Misses included two Indian addresses, a locally formatted Indian mobile number, a German number, an unlabeled name, and an email written with a fullwidth at sign. These results describe the synthetic corpus and do not estimate real-world accuracy.

## Release performance benchmark

```powershell
.venv\Scripts\python.exe -B -m evaluation.benchmark_release
```

The benchmark runs each size in a fresh CPU-only process and reports the median and maximum over five timed iterations after one warm-up. It uses synthetic text PDFs and the application code for extraction, detection, identity resolution, preview preparation, verified artifact generation, and checked summary generation. It excludes fixture generation, HTTP transfer, and browser rendering from the timings. Process peak RSS includes startup and native PDF memory.

Windows 11 build 26300, Ryzen 7 7735HS with 16 logical CPUs and approximately 15.31 GiB of reported physical RAM, Python 3.13.2, PyMuPDF 1.28.2, and FastAPI 0.165.0.

| Input pages | Extract | Detect | Resolve | Preview | TXT | PDF | Summary | Peak process RSS |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 8.66 ms | 0.54 ms | 4.18 ms | 0.30 ms | 0.26 ms | 5.08 ms | 0.64 ms | 70.86 MiB |
| 10 | 81.85 ms | 5.24 ms | 42.47 ms | 2.73 ms | 2.53 ms | 18.17 ms | 4.06 ms | 71.95 MiB |
| 50 | 395.57 ms | 25.50 ms | 212.55 ms | 14.65 ms | 13.97 ms | 74.29 ms | 20.73 ms | 75.57 MiB |

These measurements cover one active document at a time. Dense or compressed PDFs can consume native memory during parsing before the extracted-text limit is checked. Manual matching stops after 200,000 matches per request, including overlaps. For a manual fidelity check, preview a synthetic document with mixed orientations, two columns, and repeated headers. Check each page's reading order in the review, verify the downloaded text against the preview, and check that an unsupported character leaves verified TXT available.

## Phase 8 evidence

Run the full synthetic evaluation with the command below after installing the development requirements. Use a new output filename for each run. The [Phase 8 report](evaluation/PHASE8_REPORT.md) describes the datasets, integrity probes, summary tests, ablation, and Phase 6 comparison. The [recorded JSON](evaluation/phase8_results.json) contains the measured results.

```powershell
.venv/Scripts/python.exe -B -m evaluation.run_phase8 --output evaluation/phase8_reproduction.json
```

## Manual acceptance tests

1. Open the built app and upload the TXT example above. Select a highlighted mention, read its evidence, and use the next and previous controls or `J` and `K`. Confirm one identity link and reject or detach another. Set each mention's release decision separately.
2. Add a repeated email to the sample. Keep the first occurrence and use the exact-occurrence controls to approve the pending copy. Try adding the kept email as a manual redaction and confirm preview reports the overlap. Remove the conflicting manual entry, preview, change one decision, and confirm that release requires another preview.
3. Repeat with a text-based PDF. Download TXT, PDF, and the summary when offered. Compare the visible text with the preview, search for the approved values, and check that explicitly kept values remain only where intended. If your PDF editor can add a synthetic metadata or annotation marker, check that it is absent from the downloaded PDF.
4. Try opening a damaged PDF, then upload a valid file at a narrow viewport. Check that the error is readable, the workspace fits the viewport, and the source and candidate controls remain usable.
