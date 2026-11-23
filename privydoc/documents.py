"""Extraction, destructive text replacement, and validation of release artifacts."""

import re
import unicodedata
from collections.abc import Iterable

import pymupdf

MAX_TEXT = 100_000
MAX_PAGES = 100
PUNCTUATION = str.maketrans("’‘ʼ‐‑‒–—−", "'''------")
EMAIL_MARKER = re.compile(r"\s*(?:\[at\]|\(at\)|@)\s*")
DOT_MARKER = re.compile(r"\s*(?:\[dot\]|\(dot\))\s*")
NUMBER = re.compile(r"(?<!\w)\+?\(?\d(?:[() .-]*\d){6,14}(?!\w)")


class PdfLayoutError(ValueError):
    """The built-in PDF layout cannot represent the verified TXT."""


def extract(data: bytes, pdf: bool) -> str:
    if pdf:
        try:
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                if not doc.is_pdf or doc.is_repaired:
                    raise ValueError("The PDF is invalid or damaged. Save a clean PDF copy and try again.")
                if doc.needs_pass or doc.page_count > MAX_PAGES:
                    raise ValueError("Use an unlocked PDF of at most 100 pages.")
                if not doc.page_count:
                    raise ValueError("The PDF contains no pages.")
                pages: list[str] = []
                size = 0
                for page in doc:
                    part = page.get_text(sort=True)
                    if not part.strip():
                        raise ValueError("Every PDF page must contain extractable text. OCR is unavailable.")
                    size += len(part) + 2
                    if size > MAX_TEXT:
                        raise ValueError("The extracted text exceeds 100,000 characters.")
                    pages.append(part)
                text = "\n\n".join(pages)
        except (RuntimeError, pymupdf.FileDataError) as error:
            raise ValueError("The PDF could not be read.") from error
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("Save TXT files as UTF-8 before uploading.") from error
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    if not text.strip() or len(text) > MAX_TEXT or "\x00" in text:
        raise ValueError("Use a nonempty text document of at most 100,000 characters.")
    return text


def redact(text: str, ranges: Iterable[tuple[int, int]]) -> tuple[str, set[str]]:
    merged: list[tuple[int, int]] = []
    removed: set[str] = set()
    for start, end in sorted(ranges):
        if not 0 <= start < end <= len(text):
            raise ValueError("A redaction span is outside the extracted text.")
        removed.add(text[start:end])
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    output: list[str] = []
    cursor = 0
    for start, end in merged:
        output.extend((text[cursor:start], "[REDACTED]"))
        cursor = end
    output.append(text[cursor:])
    return "".join(output), removed


def canonical(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().translate(PUNCTUATION)
    text = "".join("" if unicodedata.category(char) == "Cf" else
                   str(unicodedata.decimal(char)) if char.isdecimal() else char for char in text)
    return " ".join(text.split())


def residual_key(text: str) -> str:
    text = DOT_MARKER.sub(".", EMAIL_MARKER.sub("@", canonical(text)))
    return NUMBER.sub(lambda match: re.sub(r"\D", "", match.group()), text)


def count_value(text: str, value: str) -> int:
    if not value:
        return 0
    count = 0
    start = text.find(value)
    while start != -1:
        end = start + len(value)
        if (start == 0 or not (text[start - 1].isalnum() or text[start - 1] == "_")) and (
            end == len(text) or not (text[end].isalnum() or text[end] == "_")
        ):
            count += 1
        start = text.find(value, start + 1)
    return count


def validate(text: str, removed: set[str], *, allowed: dict[str, int] | None = None,
             required: dict[str, int] | None = None) -> None:
    visible = text.replace("[REDACTED]", "\x00")
    reviewed = residual_key(visible)
    for value in removed:
        needle = residual_key(value)
        if count_value(reviewed, needle) > (allowed or {}).get(needle, 0):
            raise ValueError("An approved value still appears in the release. Review repeated mentions.")
    if required:
        retained = canonical(visible)
        if any(count_value(retained, value) < count for value, count in required.items()):
            raise ValueError("A value marked Keep is missing from the release.")


def verify_txt(payload: bytes, expected: str, removed: set[str], **checks: dict[str, int]) -> str:
    reopened = payload.decode("utf-8")
    if reopened != expected:
        raise ValueError("TXT verification failed.")
    validate(reopened, removed, **checks)
    return reopened


def summarize(sanitized: str) -> str:
    """Select up to three substantive excerpts from sanitized text, in source order."""
    passages = re.split(r"(?<=[.!?])(?<!\b[^\W\d_]\.)(?<!\b(?:Mr|Ms|Dr)\.)(?<!\bMrs\.)[^\S\n]+|\n+",
                        sanitized.strip())
    words = re.compile(r"\b[^\W\d_]+\b")
    ranked: list[tuple[int, int, str]] = []
    for index, passage in enumerate(passages):
        count = len(set(words.findall(passage.replace("[REDACTED]", "").casefold())))
        if count >= 4:
            excerpt = passage.strip()
            if len(excerpt) > 350:
                excerpt = excerpt[:350].rsplit(" ", 1)[0] + "…"
            ranked.append((-count, index, excerpt))
    selected = sorted(sorted(ranked)[:3], key=lambda item: item[1])
    return "\n\n".join(item[2] for item in selected)


def pdf_objects(document: pymupdf.Document) -> list[tuple[str, bytes | None]]:
    return [(document.xref_object(index), document.xref_stream(index))
            for index in range(1, document.xref_length())]


def serialize_pdf(document: pymupdf.Document) -> bytes:
    return document.tobytes(no_new_id=True)


def export_pdf(sanitized: str, removed: set[str], **checks: dict[str, int]) -> bytes:
    """Generate a new PDF, then validate the bytes through a fresh parser."""
    font = pymupdf.Font("helv")
    size = 11
    width = 500
    # Check each distinct non-whitespace character once before doing layout.
    if any(not font.has_glyph(ord(char), fallback=False) for char in set(sanitized) if not char.isspace()):
        raise PdfLayoutError("The PDF font cannot represent the text. The verified TXT is available.")
    lines: list[str] = []
    word_widths: dict[str, float] = {}
    space_width = font.text_length(" ", fontsize=size)
    for paragraph in sanitized.expandtabs(4).splitlines():
        line = ""
        line_width = 0.0
        for word in paragraph.split():
            word_width = word_widths.get(word)
            if word_width is None:
                word_width = word_widths[word] = font.text_length(word, fontsize=size)
            if word_width > width:
                raise PdfLayoutError("A word is too wide for PDF export. The verified TXT is available.")
            if line and line_width + space_width + word_width > width:
                lines.append(line)
                line = ""
            if line:
                line += f" {word}"
                line_width += space_width + word_width
            else:
                line = word
                line_width = word_width
        if line or (lines and lines[-1]):
            lines.append(line)
        if len(lines) > MAX_PAGES * 46:
            raise PdfLayoutError("The reflowed PDF exceeds 100 pages. The verified TXT is available.")
    try:
        # Pass only generated hex operands to PyMuPDF. Its simple font path
        # uses byte codes and otherwise interprets bracketed hex as already encoded.
        encoded_lines = [f"[<{line.encode('cp1252').hex()}>]" for line in lines]
    except UnicodeEncodeError as error:
        raise PdfLayoutError("The PDF font cannot represent the text. The verified TXT is available.") from error
    with pymupdf.open() as document:
        for offset in range(0, max(1, len(lines)), 46):
            page = document.new_page(width=612, height=792)
            page_lines = encoded_lines[offset : offset + 46]
            if page_lines:
                page.insert_text((56, 60), page_lines, fontsize=size, fontname="helv", lineheight=15 / size)
        expected_objects = pdf_objects(document)
        payload = serialize_pdf(document)
    with pymupdf.open(stream=payload, filetype="pdf") as reopened:
        metadata = {key: value for key, value in reopened.metadata.items() if key not in {"format", "encryption"}}
        if (not payload.endswith(b"%%EOF\n") or reopened.is_repaired or reopened.version_count != 1 or any(metadata.values())
                or reopened.xref_xml_metadata() or reopened.embfile_count() or reopened.get_toc()
                or set(reopened.xref_get_keys(-1)) != {"Size", "Root"}
                or any(page.first_annot or page.first_widget or page.first_link for page in reopened)
                or pdf_objects(reopened) != expected_objects):
            raise ValueError("PDF structure verification failed.")
        extracted = "\n".join(page.get_text() for page in reopened)
        if " ".join(extracted.split()) != " ".join(sanitized.split()):
            raise ValueError("PDF text verification failed.")
        validate(extracted, removed, **checks)
    return payload
