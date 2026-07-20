"""Convert a PDF to plain text for prompt input."""

from pathlib import Path

from pypdf import PdfReader


def pdf_to_text(pdf_path: str | Path) -> str:
    """Extract text from every page, joined with page markers.

    Page markers give the model a coarse location signal for the
    `location` field on supporting quotes.
    """
    reader = PdfReader(str(pdf_path))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        pages.append(f"--- Page {i} ---\n{text}")
    return "\n\n".join(pages)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        sys.exit("usage: python pdf_to_text.py <path/to.pdf>")
    print(pdf_to_text(sys.argv[1]))
