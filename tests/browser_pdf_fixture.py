"""Generate and inspect synthetic PDFs used by the Chromium integration test."""

import sys
from pathlib import Path

import pymupdf


HIDDEN = "SYNTHETIC_HIDDEN_061"


def create(source_text: Path, output: Path) -> None:
    lines = source_text.read_text(encoding="utf-8").splitlines()
    with pymupdf.open() as document:
        page = document.new_page()
        # WinAnsi operands keep curly punctuation intact in the synthetic source.
        operands = [f"[<{line.encode('cp1252').hex()}>]" for line in lines]
        page.insert_text((50, 65), operands, fontsize=10)
        page.add_text_annot((30, 30), HIDDEN)
        document.set_metadata({"author": HIDDEN})
        document.embfile_add("synthetic.txt", HIDDEN.encode())
        document.save(output)


def check(pdf: Path, txt: Path) -> None:
    expected = txt.read_text(encoding="utf-8")
    with pymupdf.open(pdf) as document:
        actual = "\n".join(page.get_text() for page in document)
        assert actual.split() == expected.split()
        assert not document.metadata["author"]
        assert not document.embfile_count()
        assert not any(page.first_annot for page in document)
    assert HIDDEN.encode() not in pdf.read_bytes()


if __name__ == "__main__":
    action, first, second = sys.argv[1:]
    {"create": create, "check": check}[action](Path(first), Path(second))
