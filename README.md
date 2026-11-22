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

- **Detection** uses patterns for email, US phone numbers and US SSNs, context rules for multiword person names, and local alias linking for repeated names, first names, surnames, and first-initial-plus-surname forms. Shared aliases are surfaced as separate candidates. English spaCy NER is enabled when both `spacy` and `en_core_web_sm` are installed in the same environment. The default rule-based path uses the CPU and requires no model download. Presidio and local SLM inference are outside this implementation.
- **Review lifecycle** uses opaque tokens and an in-process store capped at eight active reviews. Reviews expire after 15 minutes and are removed on discard or successful release. The backend streams uploads with a size limit and keeps the bytes in memory during extraction. The frontend holds review data in React state and requests no-store responses. There is no external inference, upload persistence, or application logging of source text. Use a single loopback worker. Process memory, browser memory, and OS paging have their usual retention characteristics.
- **Release** replaces approved spans before summarization. Validation checks known approved values with Unicode normalization, case folding and whitespace folding. It can catch an approved value left elsewhere, but cannot establish that the document is free of personal information. Spelling variants, unlinked references, and context can require manual review.
- **PDF export** typesets sanitized text into a fresh PDF. It drops the input's layout, images, annotations, attachments, and metadata. The exporter reopens the actual serialized bytes and checks the extracted text against the expected text and approved values. If the built-in font cannot represent the output or the PDF exceeds its layout limits, verified TXT remains available. OCR, source layout preservation, and PDF.js viewing are outside this implementation.
