# Privacy and release boundaries

PrivyDoc assumes a trusted machine, browser session, and user. Run one Uvicorn worker bound to `127.0.0.1`. The review intentionally exposes original extracted text to the local reviewer. Application inference, extraction, verification, and summarization run on the machine. The package and model installation commands require downloads.

The service holds extracted review data in process memory. The frontend handles it in React state. Reviews expire after 15 minutes and can be discarded or consumed by release. The service caps active reviews at eight. API responses use `Cache-Control: no-store`, host validation, and a cross-site browser-request check. Process memory, browser memory, operating-system paging, and files the user downloads remain part of the local trust boundary.

## Release controls

- Redaction replaces approved offsets in extracted text. Overlap with a Keep blocks release. An approved spelling may survive at an explicitly kept occurrence, with residual-value checks applied to the rest of the release.
- A new valid preview supersedes the old receipt. Release validates the receipt against the same review token and request choices. Identity proposals require explicit resolution and every mention requires a decision.
- TXT bytes are decoded and verified. PDF is retypeset from sanitized text into a fresh document. Output verification checks extracted text and the generated structure including metadata, annotations, attachments, and streams. Source page objects are outside the writer input.
- The summarizer receives verified redacted text with kept spans masked separately. The final output must match source excerpts and pass direct and quasi-identifier checks. Withholding the summary leaves successfully verified documents available.

## Known limits

- Detection and residual checks cover known patterns and normalization forms. Missed PII, unfamiliar aliases, contextual clues, and intentionally kept information can appear in the released document. The summary checks are heuristic and can miss disclosures or suppress harmless excerpts.
- PDF extraction requires text on every page. Scanned and image-only PDFs need OCR outside PrivyDoc. Output reflows the text, so tables, columns, layout and visuals can change or be lost. Unsupported characters and layout constraints may leave only verified TXT.
- Byte and text limits bound supported inputs. PDF parsing may expand compressed content before the text limit applies. Evaluation used bounded synthetic documents and selected failure cases.
