"""Extraction, destructive text replacement, and validation of release artifacts."""

import re
import unicodedata
from collections.abc import Iterable

import pymupdf

MAX_TEXT = 100_000
MAX_PAGES = 100


def extract(data: bytes, pdf: bool) -> str:
    if pdf:
        try:
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                if doc.needs_pass or doc.page_count > MAX_PAGES:
                    raise ValueError("Use an unlocked PDF of at most 100 pages.")
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
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def count_value(text: str, value: str) -> int:
    if not value:
        return 0
    return sum(1 for _ in re.finditer(r"(?=(?<!\w)" + re.escape(value) + r"(?!\w))", text))


def validate(text: str, removed: set[str], *, allowed: dict[str, int] | None = None) -> None:
    reviewed = canonical(text.replace("[REDACTED]", "\x00"))
    for value in removed:
        needle = canonical(value)
        if count_value(reviewed, needle) > (allowed or {}).get(needle, 0):
            raise ValueError("An approved value still appears in the release. Review repeated mentions.")


def summarize(sanitized: str) -> str:
    """Select up to three substantive excerpts from sanitized text, in source order."""
    passages = re.split(r"(?<=[.!?])\s+|\n+", sanitized.strip())
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


def export_pdf(sanitized: str, removed: set[str], *, allowed: dict[str, int] | None = None) -> bytes:
    """Generate a new PDF, then validate the bytes through a fresh parser."""
    font = pymupdf.Font("helv")
    size = 11
    width = 500
    lines: list[str] = []
    for paragraph in sanitized.expandtabs(4).splitlines():
        line = ""
        for word in paragraph.split():
            if font.text_length(word, fontsize=size) > width:
                raise ValueError("A word is too wide for PDF export. The verified TXT is available.")
            candidate = f"{line} {word}" if line else word
            if font.text_length(candidate, fontsize=size) > width:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line or (lines and lines[-1]):
            lines.append(line)
        if len(lines) > MAX_PAGES * 46:
            raise ValueError("The reflowed PDF exceeds 100 pages. The verified TXT is available.")
    with pymupdf.open() as document:
        for offset in range(0, max(1, len(lines)), 46):
            page = document.new_page(width=612, height=792)
            for index, line in enumerate(lines[offset : offset + 46]):
                page.insert_text((56, 60 + index * 15), line, fontsize=size, fontname="helv")
        payload = document.tobytes(garbage=4, deflate=True)
    with pymupdf.open(stream=payload, filetype="pdf") as reopened:
        extracted = "\n".join(page.get_text() for page in reopened)
        if " ".join(extracted.split()) != " ".join(sanitized.split()):
            raise ValueError("PDF text did not round-trip exactly. The verified TXT is available.")
        validate(extracted, removed, allowed=allowed)
    return payload
