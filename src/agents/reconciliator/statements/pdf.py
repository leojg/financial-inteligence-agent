"""Load a PDF statement as positioned words and lines (pdfplumber), plus page images on demand.

Positions matter: a statement's Débito and Crédito columns are only told apart
by where a number sits on the line, which flattened text loses (v1.6 F2).
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pdfplumber

logger = logging.getLogger(__name__)

_LINE_TOLERANCE = 3.0  # points: words whose tops differ by less are on the same line


class PdfReadError(Exception):
    """The PDF could not be opened (corrupt, or encrypted without a working password)."""


@dataclass(frozen=True)
class PdfWord:
    """A word and its position on the page (points, origin top-left)."""

    text: str
    x0: float
    x1: float
    top: float

    @property
    def center(self) -> float:
        """Return the horizontal center of the word."""
        return (self.x0 + self.x1) / 2


@dataclass
class PdfLine:
    """Words that share a baseline, left to right."""

    page: int  # 1-based
    top: float
    words: list[PdfWord] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Return the line's words joined by single spaces."""
        return " ".join(w.text for w in self.words)


@dataclass
class PdfPage:
    """One page: its lines and its layout-preserving text."""

    number: int  # 1-based
    width: float
    lines: list[PdfLine]
    layout_text: str

    @property
    def text(self) -> str:
        """Return the page's lines as plain text, one per line."""
        return "\n".join(line.text for line in self.lines)


@dataclass
class PdfDocument:
    """A loaded statement PDF."""

    path: str
    sha256: str
    pages: list[PdfPage]
    password: str | None = None  # the password that opened it, if any

    @property
    def text(self) -> str:
        """Return the whole document as plain text, pages separated by blank lines."""
        return "\n\n".join(page.text for page in self.pages)

    @property
    def has_text_layer(self) -> bool:
        """Return True when the PDF carries text (a scan has none and needs the image path)."""
        return sum(len(page.text.strip()) for page in self.pages) >= 30


def file_sha256(path: str) -> str:
    """Return the SHA-256 of the file's bytes — the file's identity (v1.6 principle 13)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _group_lines(words: Iterable[PdfWord], page: int) -> list[PdfLine]:
    lines: list[PdfLine] = []
    for word in sorted(words, key=lambda w: (w.top, w.x0)):
        if lines and abs(word.top - lines[-1].top) <= _LINE_TOLERANCE:
            lines[-1].words.append(word)
        else:
            lines.append(PdfLine(page=page, top=word.top, words=[word]))
    for line in lines:
        line.words.sort(key=lambda w: w.x0)
    return lines


def _open(path: str, passwords: Iterable[str]) -> tuple[pdfplumber.PDF, str | None]:
    last_error: Exception | None = None
    for password in [None, *passwords]:
        try:
            pdf = pdfplumber.open(path, password=password or "")
            _ = pdf.pages  # force parsing so a wrong password fails here
            return pdf, password
        except Exception as e:  # pdfminer raises several types for bad/encrypted files
            last_error = e
    raise PdfReadError(
        f"could not open {Path(path).name}: {last_error}"
    ) from last_error


def load_pdf(path: str, passwords: Iterable[str] = ()) -> PdfDocument:
    """Load a PDF into positioned lines. Raises PdfReadError when it cannot be opened."""
    pdf, password = _open(path, list(passwords))
    pages: list[PdfPage] = []
    with pdf:
        for number, page in enumerate(pdf.pages, start=1):
            raw_words = page.extract_words(x_tolerance=2, y_tolerance=2)
            words = [
                PdfWord(
                    text=w["text"],
                    x0=float(w["x0"]),
                    x1=float(w["x1"]),
                    top=float(w["top"]),
                )
                for w in raw_words
            ]
            try:
                layout_text = page.extract_text(layout=True) or ""
            except Exception:  # layout mode is best-effort
                layout_text = page.extract_text() or ""
            pages.append(
                PdfPage(
                    number=number,
                    width=float(page.width),
                    lines=_group_lines(words, number),
                    layout_text=layout_text,
                )
            )
    return PdfDocument(
        path=path, sha256=file_sha256(path), pages=pages, password=password
    )


def page_image_png(
    path: str, page_number: int, password: str | None = None, resolution: int = 150
) -> bytes:
    """Render one page (1-based) to PNG bytes for the vision side of the LLM rung."""
    with pdfplumber.open(path, password=password or "") as pdf:
        image = pdf.pages[page_number - 1].to_image(resolution=resolution).original
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
