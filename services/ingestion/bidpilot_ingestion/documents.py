"""The DCE document pipeline (SPEC F2, §21 Phase 2).

Turns a consultation file into text a model can read *and a citation can point at*. That second
half is the whole reason this module is careful: §9.1 requires every extracted requirement to
carry a document and a page, and a page number is only worth something if it refers to the page
the text actually came from.

So the unit here is the page, never the document. Text is extracted per page and re-assembled
with explicit `[[page:N]]` markers, and the marker is what the prompt is told to cite. A pipeline
that concatenated everything and asked the model to guess pages would produce citations that look
exactly as authoritative and are unverifiable - which is the failure P1 exists to prevent.

Nothing here calls a model. Extraction from a PDF is deterministic, and keeping it that way means
the page anchors are facts rather than outputs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

log = logging.getLogger(__name__)

#: Below this many characters, a page is treated as image-only and flagged for OCR. A PDF page of
#: real prose has thousands; a scanned page yields a handful of stray ligatures at most.
MIN_CHARS_FOR_TEXT_PAGE = 40

#: The marker the extraction prompts are told to cite. Chosen to be something no DCE contains.
PAGE_MARKER = "[[page:{page}]]"

_PAGE_MARKER_RE = re.compile(r"\[\[page:(\d+)\]\]")

#: Runs of whitespace inside a PDF text layer are layout, not content.
_WHITESPACE_RE = re.compile(r"[ \t\xa0]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class Page:
    """One page's text, with the number a citation will name."""

    number: int
    text: str

    @property
    def needs_ocr(self) -> bool:
        return len(self.text.strip()) < MIN_CHARS_FOR_TEXT_PAGE


@dataclass(frozen=True)
class ExtractedDocument:
    """A document reduced to pages, plus what could not be read."""

    pages: list[Page]
    #: Pages whose text layer was empty or near-empty. Reported rather than silently dropped:
    #: a requirement that only exists on a scanned page must not look absent (§6.9's honesty
    #: applied to documents rather than sources).
    ocr_pages: list[int]
    title: str | None = None

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def needs_ocr(self) -> bool:
        return bool(self.ocr_pages)

    @property
    def text_coverage(self) -> float:
        """Share of pages that yielded text. The number a user should see before trusting a register."""
        if not self.pages:
            return 0.0
        return (self.page_count - len(self.ocr_pages)) / self.page_count


class UnreadableDocument(RuntimeError):
    """The file could not be parsed at all - encrypted, corrupt, or not a PDF."""


def clean_page_text(raw: str) -> str:
    """Normalise a page's text layer without changing what it says.

    Only whitespace: PDF extraction produces ragged spacing from the layout, which wastes tokens
    and makes verbatim quotes hard to match back. Hyphenation at line ends is deliberately *not*
    repaired - `presta-\ntion` may be a broken word or may be a real hyphen, and joining it would
    silently alter a quote that a citation later claims is verbatim.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def extract_pdf(source: BinaryIO | Path | str) -> ExtractedDocument:
    """Read a PDF into pages.

    `pypdf` rather than a heavier toolchain: this needs the text layer and the page boundaries,
    both of which it gives, and adding a native dependency for layout analysis we do not use
    would be a build problem in exchange for nothing.
    """
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - declared in pyproject
        raise RuntimeError("pypdf is required to read DCE documents") from exc

    try:
        reader = PdfReader(source)
        if reader.is_encrypted:
            # Some DCEs ship encrypted with an empty owner password, which pypdf can open.
            try:
                reader.decrypt("")
            except Exception as exc:
                raise UnreadableDocument("document is encrypted") from exc
        raw_pages = list(reader.pages)
    except UnreadableDocument:
        raise
    except (PdfReadError, OSError, ValueError) as exc:
        raise UnreadableDocument(f"could not read PDF: {exc}") from exc

    pages: list[Page] = []
    ocr_pages: list[int] = []
    for index, pdf_page in enumerate(raw_pages, start=1):
        try:
            text = clean_page_text(pdf_page.extract_text() or "")
        except Exception as exc:
            # One unreadable page must not lose the other 199.
            log.warning("page %d could not be extracted: %s", index, exc)
            text = ""
        page = Page(number=index, text=text)
        pages.append(page)
        if page.needs_ocr:
            ocr_pages.append(index)

    title = None
    try:
        metadata = reader.metadata
        if metadata and metadata.title:
            title = str(metadata.title).strip() or None
    except Exception:  # pragma: no cover - metadata is optional and often malformed
        title = None

    return ExtractedDocument(pages=pages, ocr_pages=ocr_pages, title=title)


def to_prompt_text(document: ExtractedDocument, *, pages: list[int] | None = None) -> str:
    """Render pages with the markers the extraction prompts cite.

    Empty pages are still marked. A model that sees pages 11 and 13 with nothing between them
    would have to decide what happened to 12; being shown an empty page 12 is unambiguous, and
    the OCR report says why it is empty.
    """
    wanted = set(pages) if pages is not None else None
    chunks: list[str] = []
    for page in document.pages:
        if wanted is not None and page.number not in wanted:
            continue
        chunks.append(f"{PAGE_MARKER.format(page=page.number)}\n{page.text}")
    return "\n\n".join(chunks)


def page_window(document: ExtractedDocument, *, max_chars: int) -> list[list[int]]:
    """Split a document into page runs that each fit a context budget.

    Splitting on page boundaries rather than character offsets is what keeps citations valid: a
    chunk that ended mid-page would make the model quote text whose page it cannot name.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    windows: list[list[int]] = []
    current: list[int] = []
    used = 0
    for page in document.pages:
        # The marker is part of what the model reads, so it is part of the budget.
        cost = len(page.text) + len(PAGE_MARKER.format(page=page.number)) + 2
        if current and used + cost > max_chars:
            windows.append(current)
            current, used = [], 0
        current.append(page.number)
        used += cost
    if current:
        windows.append(current)
    return windows


def verify_citation(document: ExtractedDocument, *, page: int, quote: str) -> bool:
    """Is this quote actually on that page?

    The check behind Annex E.4's "citation validity ≥ 0.98", and the reason the whole pipeline
    keeps pages separate. Comparison is whitespace-insensitive because a PDF text layer breaks
    lines wherever the layout did, but it is otherwise exact: a "close enough" match would let a
    plausible-looking invented quote through, which is the one thing this gate exists to catch.
    """
    target = next((p for p in document.pages if p.number == page), None)
    if target is None or not quote.strip():
        return False
    return _collapse(quote) in _collapse(target.text)


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def parse_page_markers(text: str) -> list[int]:
    """Page numbers present in rendered prompt text. Used by tests and by the eval harness."""
    return [int(match) for match in _PAGE_MARKER_RE.findall(text)]


def summarise(document: ExtractedDocument) -> dict[str, Any]:
    """What to store on `notice_documents` and show to a user before they trust a register."""
    return {
        "pages": document.page_count,
        "ocr_pages": document.ocr_pages,
        "text_coverage": round(document.text_coverage, 4),
        "needs_ocr": document.needs_ocr,
        "title": document.title,
    }
