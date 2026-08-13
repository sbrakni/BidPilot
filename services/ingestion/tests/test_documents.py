"""Document pipeline tests (SPEC F2, §9.1).

Run against a real multi-page PDF whose prose is real captured procurement text
(`scripts/build_dce_fixture.py`). The container is generated; the French inside is not, which
matters because the things that break PDF text extraction - accents, long administrative
sentences, layout-driven line breaks - are exactly what lorem ipsum does not have.

The property under test throughout is that a page number means the page the text came from.
Everything §9.1 promises about citations rests on it.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from bidpilot_ingestion.documents import (
    MIN_CHARS_FOR_TEXT_PAGE,
    UnreadableDocument,
    clean_page_text,
    extract_pdf,
    page_window,
    parse_page_markers,
    summarise,
    to_prompt_text,
    verify_citation,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dce" / "sample_rc.pdf"

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(), reason="run scripts/build_dce_fixture.py to create the DCE fixture"
)


@pytest.fixture(scope="module")
def document():
    return extract_pdf(FIXTURE)


class TestExtraction:
    def test_reads_every_page(self, document):
        assert document.page_count >= 4
        assert [page.number for page in document.pages] == list(range(1, document.page_count + 1))

    def test_pages_carry_their_own_text(self, document):
        # The deadline is on the page that states it, and on no other. If extraction pooled the
        # document, this would pass for every page - which is the bug worth catching.
        pages_with_deadline = [p.number for p in document.pages if "14/03/2026" in p.text]
        assert len(pages_with_deadline) == 1

    def test_preserves_accented_french(self, document):
        joined = "\n".join(page.text for page in document.pages)
        assert "dématérialisée" in joined
        assert "être" in joined

    def test_reports_coverage_rather_than_assuming_it(self, document):
        summary = summarise(document)
        assert summary["pages"] == document.page_count
        assert 0.0 <= summary["text_coverage"] <= 1.0
        assert summary["needs_ocr"] is (len(document.ocr_pages) > 0)

    def test_a_non_pdf_is_refused_clearly(self):
        with pytest.raises(UnreadableDocument):
            extract_pdf(io.BytesIO(b"this is not a pdf"))


class TestCleaning:
    def test_collapses_layout_whitespace(self):
        assert clean_page_text("Les    offres\t\tseront   jugées") == "Les offres seront jugées"

    def test_does_not_rejoin_hyphenated_line_breaks(self):
        # `presta-\ntion` may be a broken word or a real hyphen. Joining it would silently alter
        # text that a citation later claims is verbatim.
        assert "presta-" in clean_page_text("presta-\ntion")

    def test_keeps_paragraph_structure(self):
        assert clean_page_text("Article 1\n\n\n\nArticle 2") == "Article 1\n\nArticle 2"


class TestPromptRendering:
    def test_every_page_is_marked(self, document):
        rendered = to_prompt_text(document)
        assert parse_page_markers(rendered) == [p.number for p in document.pages]

    def test_a_marker_precedes_the_text_it_labels(self, document):
        rendered = to_prompt_text(document)
        target = next(p for p in document.pages if "14/03/2026" in p.text)
        marker_at = rendered.index(f"[[page:{target.number}]]")
        quote_at = rendered.index("14/03/2026")
        assert marker_at < quote_at
        # And no *other* page marker sits between them, which is what would mislabel the quote.
        assert "[[page:" not in rendered[marker_at + 12 : quote_at]

    def test_a_subset_can_be_rendered_for_one_window(self, document):
        rendered = to_prompt_text(document, pages=[2])
        assert parse_page_markers(rendered) == [2]


class TestWindowing:
    def test_windows_split_on_page_boundaries(self, document):
        windows = page_window(document, max_chars=1500)
        flattened = [number for window in windows for number in window]
        # Every page appears exactly once and in order: a lost page is a lost requirement.
        assert flattened == [p.number for p in document.pages]
        assert len(windows) > 1, "the fixture is larger than one 1500-character window"

    def test_a_generous_budget_is_a_single_window(self, document):
        assert len(page_window(document, max_chars=10_000_000)) == 1

    def test_a_page_larger_than_the_budget_still_gets_its_own_window(self, document):
        # Never silently dropped: a 4000-character page with a 10-character budget is still a
        # page whose requirements have to be read.
        windows = page_window(document, max_chars=10)
        assert len(windows) == document.page_count

    def test_rejects_a_nonsense_budget(self, document):
        with pytest.raises(ValueError):
            page_window(document, max_chars=0)


class TestCitationVerification:
    """The check behind Annex E.4's citation-validity gate (≥ 0.98)."""

    def test_accepts_a_quote_that_is_on_the_cited_page(self, document):
        target = next(p for p in document.pages if "14/03/2026" in p.text)
        assert verify_citation(document, page=target.number, quote="14/03/2026 à 12h00")

    def test_tolerates_line_breaks_inside_the_quote(self, document):
        """A PDF breaks lines where the layout did, so a real quote spans them."""
        target = next(p for p in document.pages if "14/03/2026" in p.text)
        words = target.text.split()
        start = next(i for i, w in enumerate(words) if "14/03/2026" in w)
        quote = "\n  ".join(words[start : start + 6])
        assert verify_citation(document, page=target.number, quote=quote)

    def test_rejects_a_quote_from_a_different_page(self, document):
        target = next(p for p in document.pages if "14/03/2026" in p.text)
        other = next(p.number for p in document.pages if p.number != target.number)
        assert not verify_citation(document, page=other, quote="14/03/2026 à 12h00")

    def test_rejects_an_invented_quote(self, document):
        # The case the gate exists for: plausible, procurement-shaped, and not in the document.
        assert not verify_citation(
            document, page=1, quote="Les offres seront remises avant le 1er janvier 2030"
        )

    def test_rejects_a_citation_to_a_page_that_does_not_exist(self, document):
        assert not verify_citation(document, page=999, quote="Article 1")

    def test_rejects_an_empty_quote(self, document):
        # Otherwise "" is on every page, and the gate measures nothing.
        assert not verify_citation(document, page=1, quote="   ")


class TestOcrDetection:
    def test_an_empty_page_is_flagged_rather_than_ignored(self):
        from bidpilot_ingestion.documents import ExtractedDocument, Page

        scanned = Page(number=2, text=" ")
        document = ExtractedDocument(pages=[Page(1, "x" * 500), scanned], ocr_pages=[2])
        assert scanned.needs_ocr
        assert document.needs_ocr
        # Coverage is what tells a user the register may be incomplete (§6.9 applied to a file).
        assert document.text_coverage == 0.5

    def test_the_threshold_is_about_scans_not_short_pages(self):
        from bidpilot_ingestion.documents import Page

        assert Page(1, "x" * (MIN_CHARS_FOR_TEXT_PAGE + 1)).needs_ocr is False
