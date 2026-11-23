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
- **PDF export** typesets sanitized text into a fresh PDF. It drops the input's layout, images, annotations, attachments, and metadata. The exporter reopens the serialized bytes and checks extracted text, metadata, annotations, attachments, and the object and stream structure against the generated document. A verification failure blocks release. Known font and layout limits leave verified TXT available. PDF uploads require extractable text on every page.
- **Summary** masks explicitly kept mentions in addition to approved redactions, verifies the resulting input, and uses the local extractive summarizer. The final summary is checked against its sanitized source, known values, local PII rules, and quasi-identifier signals such as age, location, job, and dates. A failed check withholds the summary. The checks can miss unfamiliar spellings, aliases, or identifying details that depend on context, and can suppress harmless summaries. Review downloaded content before sharing it.

## Detection coverage

| Entity | Supported forms |
| --- | --- |
| Email | Conventional dotted domains and lightly obfuscated values such as `jane [at] example [dot] com` and `jane(at)example(dot)com`. |
| Phone | Formatted North American numbers, including parenthesized area codes and optional leading `1`. Compact domestic numbers require a phone label. International rules cover `+1`, `+44` geographic/mobile-style prefixes beginning with `1`, `2`, or `7`, and `+91` mobile-style numbers beginning with `6` through `9`. Spaces, dots, and hyphens are supported in those international formats. |
| Address | Numbered streets with common English suffixes, optional apartment/unit, and PO boxes. A US city/state/ZIP can follow after a comma or on the next line. |
| Employee, account, and student IDs | Supported field labels followed by `:`, `=`, or `#`, then a 2–32 character identifier containing at least one digit. The rules exclude common dashed date shapes and dotted versions. |
| Government ID | Hyphenated US SSNs after an `SSN` or `Social Security Number` label. Component checks catch invalid shapes. Unlabeled SSNs require manual review. |
| Person | Capitalized multiword names after person labels or titles, plus multiword `PERSON` spans from the optional model. The detector links repeated full names, capitalized short forms, and dotted or undotted first initials with surnames. Shared short forms have their own review group. |

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
npm.cmd --prefix frontend run build
node --test frontend/tests/review.test.mjs
node --test frontend/tests/review.browser.mjs
```

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

The evaluator uses the Phase 2 detection interface and scores unique `(document, entity type, start, end)` tuples against the labeled synthetic corpus. Partial matches and incorrect types count as both a false positive and a false negative. The report includes micro-averaged and per-entity metrics, mismatch details, and detector-only timings. Model startup is timed separately. The second command reports which model, if any, actually ran. Identity linking and reviewer decisions are covered separately by the identity regression tests.

The measured Windows rules-only baseline on seven synthetic documents (1,112 characters) was **29 true positives, 0 false positives, and 2 false negatives**. Precision was **100.00%**, recall **93.55%**, and F1 **96.67%**. The first pass took **3.58 ms**, and the median over 30 corpus passes was **1.65 ms**. The misses were an unlabeled person name in prose and a German `+49` number. These results describe this small corpus. Coverage for unseen documents, memory at the upload limit, and optional model performance require separate measurements.
