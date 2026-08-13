"""Eval harness tests (SPEC Annex E.4).

A scoring harness is measuring equipment, and equipment that has not itself been checked reports
whatever it reports. The cases below are the ones where a plausible implementation is wrong in a
way that flatters the model:

  * a missing corpus scoring as a pass,
  * an eliminatory requirement counted as found when it was filed as something harmless,
  * a citation counted as valid because it matched *gold* rather than the document,
  * small documents outvoting large ones in the corpus average.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bidpilot_ingestion.documents import ExtractedDocument, Page
from bidpilot_ingestion.evals import (
    GATES,
    CaseResult,
    CorpusMissing,
    MetricResult,
    aggregate,
    load_corpus,
    match_requirements,
    run_eval,
    score_case,
    similarity,
)


def _req(ref, text, type_="contractual", page=1, quote=None, confidence=0.9):
    return {
        "ref": ref,
        "text": text,
        "type": type_,
        "needs_evidence": False,
        "page": page,
        "quote": quote if quote is not None else text,
        "confidence": confidence,
    }


class TestGatesMatchTheSpec:
    def test_the_thresholds_are_annex_e4(self):
        # Written down here so that lowering a gate is a visible edit to a test, not a quiet
        # tweak to a constant.
        assert GATES["requirement_recall"] == 0.90
        assert GATES["eliminatory_recall"] == 0.95
        assert GATES["requirement_precision"] == 0.85
        assert GATES["citation_validity"] == 0.98

    def test_eliminatory_is_the_strictest_gate(self):
        assert GATES["eliminatory_recall"] > GATES["requirement_recall"]


class TestMatching:
    def test_paraphrase_of_the_same_obligation_matches(self):
        # Gold is written by a human, the model writes its own sentence; exact comparison would
        # score a correct extraction as a total miss.
        score = similarity(
            "Le candidat doit fournir une attestation d'assurance responsabilité civile.",
            "Le candidat doit fournir une attestation d'assurance responsabilite civile",
        )
        assert score > 0.9

    def test_two_different_obligations_do_not_match(self):
        pairs, _, _ = match_requirements(
            [_req("REQ-001", "Fournir une attestation d'assurance décennale")],
            [_req("G1", "Le mémoire technique est limité à 30 pages")],
        )
        assert pairs == []

    def test_each_gold_item_is_matched_at_most_once(self):
        """Otherwise one good prediction could 'find' the same requirement several times."""
        predicted = [
            _req("REQ-001", "Attestation URSSAF à jour"),
            _req("REQ-002", "Attestation URSSAF à jour"),
        ]
        gold = [_req("G1", "Attestation URSSAF à jour")]
        pairs, unmatched_predicted, unmatched_gold = match_requirements(predicted, gold)
        assert len(pairs) == 1
        assert len(unmatched_predicted) == 1
        assert unmatched_gold == []


class TestScoring:
    def test_recall_and_precision_are_computed_over_the_right_denominators(self):
        predicted = [_req("REQ-001", "Attestation URSSAF"), _req("REQ-002", "Invention totale")]
        gold = [_req("G1", "Attestation URSSAF"), _req("G2", "Attestation fiscale")]

        result = score_case(case="c", predicted_requirements=predicted, gold_requirements=gold)
        recall = next(m for m in result.metrics if m.name == "requirement_recall")
        precision = next(m for m in result.metrics if m.name == "requirement_precision")
        assert recall.value == 0.5, "one of two gold requirements found"
        assert precision.value == 0.5, "one of two predictions is real"

    def test_an_eliminatory_item_typed_as_something_else_is_not_found(self):
        """The heart of the eliminatory gate.

        Finding the sentence and filing it as a formatting note leaves the bidder just as
        disqualified, so it must not count towards a gate that exists to prevent exactly that.
        """
        predicted = [_req("REQ-001", "La visite de site est obligatoire", type_="format")]
        gold = [_req("G1", "La visite de site est obligatoire", type_="eliminatory")]

        result = score_case(case="c", predicted_requirements=predicted, gold_requirements=gold)
        eliminatory = next(m for m in result.metrics if m.name == "eliminatory_recall")
        assert eliminatory.value == 0.0
        assert eliminatory.passed is False
        assert result.missed_eliminatory, "a missed eliminatory item must be named in the report"

    def test_a_correctly_typed_eliminatory_item_counts(self):
        predicted = [_req("REQ-001", "La visite de site est obligatoire", type_="eliminatory")]
        gold = [_req("G1", "La visite de site est obligatoire", type_="eliminatory")]
        result = score_case(case="c", predicted_requirements=predicted, gold_requirements=gold)
        assert next(m for m in result.metrics if m.name == "eliminatory_recall").value == 1.0

    def test_a_corpus_with_no_eliminatory_items_does_not_fail_the_gate(self):
        result = score_case(
            case="c",
            predicted_requirements=[_req("REQ-001", "Un délai de préavis de trois mois")],
            gold_requirements=[_req("G1", "Un délai de préavis de trois mois")],
        )
        assert next(m for m in result.metrics if m.name == "eliminatory_recall").value == 1.0


class TestCitationValidity:
    """Checked against the document, never against gold."""

    @staticmethod
    def _document():
        return ExtractedDocument(
            pages=[
                Page(1, "Article 1. La visite de site est obligatoire le 20/02/2026."),
                Page(2, "Article 2. Le mémoire technique est limité à 30 pages."),
            ],
            ocr_pages=[],
        )

    def test_a_real_quote_on_the_right_page_is_valid(self):
        result = score_case(
            case="c",
            predicted_requirements=[
                _req("REQ-001", "Visite obligatoire", page=1, quote="La visite de site est obligatoire")
            ],
            gold_requirements=[_req("G1", "Visite obligatoire")],
            document=self._document(),
        )
        assert next(m for m in result.metrics if m.name == "citation_validity").value == 1.0

    def test_a_quote_that_matches_gold_but_not_the_document_is_invalid(self):
        """The failure P1 is about: agreeing with the label while citing something unreadable."""
        result = score_case(
            case="c",
            predicted_requirements=[
                _req(
                    "REQ-001",
                    "Visite obligatoire",
                    page=1,
                    quote="Une visite facultative est proposée aux candidats",
                )
            ],
            gold_requirements=[_req("G1", "Visite obligatoire")],
            document=self._document(),
        )
        citation = next(m for m in result.metrics if m.name == "citation_validity")
        assert citation.value == 0.0
        assert result.invalid_citations, "an unverifiable citation must be listed for review"

    def test_a_quote_on_the_neighbouring_page_is_tolerated(self):
        # A requirement can straddle a page break; counting that as fabrication punishes a
        # correct extraction.
        result = score_case(
            case="c",
            predicted_requirements=[_req("REQ-001", "Mémoire limité", page=1, quote="limité à 30 pages")],
            gold_requirements=[_req("G1", "Mémoire limité")],
            document=self._document(),
        )
        assert next(m for m in result.metrics if m.name == "citation_validity").value == 1.0

    def test_a_citation_to_a_distant_page_is_invalid(self):
        result = score_case(
            case="c",
            predicted_requirements=[
                _req("REQ-001", "Visite obligatoire", page=9, quote="La visite de site est obligatoire")
            ],
            gold_requirements=[_req("G1", "Visite obligatoire")],
            document=self._document(),
        )
        assert next(m for m in result.metrics if m.name == "citation_validity").value == 0.0


class TestAggregation:
    def test_metrics_pool_over_items_rather_than_averaging_cases(self):
        """A 5-requirement DCE must not outvote a 200-requirement one."""
        small = CaseResult("small", metrics=[MetricResult("requirement_recall", 1.0, 0.9, 5, 5)])
        large = CaseResult("large", metrics=[MetricResult("requirement_recall", 0.5, 0.9, 100, 200)])

        pooled = next(m for m in aggregate([small, large]) if m.name == "requirement_recall")
        assert pooled.value == pytest.approx(105 / 205)
        # The mean of the two case rates would have been 0.75 - a pass.
        assert pooled.passed is False


class TestCorpusIsRequired:
    def test_a_missing_corpus_directory_raises(self, tmp_path):
        with pytest.raises(CorpusMissing):
            load_corpus(tmp_path / "nope")

    def test_an_empty_corpus_raises_rather_than_passing(self, tmp_path):
        # The failure this whole module guards: a green check that measured nothing.
        with pytest.raises(CorpusMissing, match=r"Annex E\.3"):
            load_corpus(tmp_path)

    def test_a_gold_file_without_its_document_raises(self, tmp_path):
        (tmp_path / "case.gold.json").write_text('{"requirements": []}')
        with pytest.raises(CorpusMissing, match="no matching PDF"):
            load_corpus(tmp_path)

    def test_run_eval_propagates_a_missing_corpus(self, tmp_path):
        with pytest.raises(CorpusMissing):
            run_eval(tmp_path, lambda document, name: {"requirements": []})


class TestEndToEndAgainstTheFixture:
    FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "dce"

    @pytest.mark.skipif(
        not (FIXTURE_DIR / "sample_rc.pdf").exists(),
        reason="run scripts/build_dce_fixture.py",
    )
    def test_a_perfect_extractor_passes_every_gate(self, tmp_path):
        """Proves the harness can be satisfied at all - a gate nothing can pass is not a gate."""
        import json
        import shutil

        from bidpilot_ingestion.documents import extract_pdf

        shutil.copy(self.FIXTURE_DIR / "sample_rc.pdf", tmp_path / "case.pdf")
        document = extract_pdf(tmp_path / "case.pdf")
        page = next(p for p in document.pages if "14/03/2026" in p.text)
        quote = "14/03/2026 à 12h00"

        gold = {
            "requirements": [
                {
                    "ref": "G1",
                    "text": "Les offres doivent être remises avant la date limite",
                    "type": "eliminatory",
                    "page": page.number,
                    "quote": quote,
                }
            ]
        }
        (tmp_path / "case.gold.json").write_text(json.dumps(gold), encoding="utf-8")

        report = run_eval(
            tmp_path,
            lambda document, name: {
                "requirements": [
                    _req(
                        "REQ-001",
                        "Les offres doivent être remises avant la date limite",
                        type_="eliminatory",
                        page=page.number,
                        quote=quote,
                    )
                ]
            },
        )
        assert report.passed, report.summary_lines()

    @pytest.mark.skipif(
        not (FIXTURE_DIR / "sample_rc.pdf").exists(),
        reason="run scripts/build_dce_fixture.py",
    )
    def test_an_extractor_that_invents_a_citation_fails(self, tmp_path):
        import json
        import shutil

        shutil.copy(self.FIXTURE_DIR / "sample_rc.pdf", tmp_path / "case.pdf")
        gold = {
            "requirements": [
                {
                    "ref": "G1",
                    "text": "Les offres doivent être remises avant la date limite",
                    "type": "eliminatory",
                    "page": 2,
                    "quote": "x",
                }
            ]
        }
        (tmp_path / "case.gold.json").write_text(json.dumps(gold), encoding="utf-8")

        report = run_eval(
            tmp_path,
            lambda document, name: {
                "requirements": [
                    _req(
                        "REQ-001",
                        "Les offres doivent être remises avant la date limite",
                        type_="eliminatory",
                        page=2,
                        quote="Les offres seront remises avant le 1er janvier 2030",
                    )
                ]
            },
        )
        assert not report.passed
