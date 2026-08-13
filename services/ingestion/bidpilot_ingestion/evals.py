"""The extraction eval harness (SPEC Annex E.3/E.4, §9.2).

*"Prompt changes require eval run in CI (E.4) — a failing gate blocks merge."*

The gates are not advisory, and the reason is arithmetic rather than principle: at recall 0.90 a
120-requirement register is missing twelve items, and at eliminatory recall below 0.95 the thing
being missed is the sentence that disqualifies the bid. A number that is not enforced is a
number that drifts.

Three properties this harness has to have, each of which is easy to get wrong:

  * **An absent corpus fails.** A harness that finds no gold files and reports success is worse
    than no harness: it produces a green check that means nothing, and green checks are what
    people trust. `run_eval` raises on an empty corpus.
  * **Matching is scored, not asserted.** Two people writing the same requirement will not write
    the same sentence, so comparing strings exactly would score a correct extraction as a total
    miss. Similarity is used, with a threshold, and the threshold is stated.
  * **A citation counts only if it is real.** Citation validity is checked against the document,
    not against the gold file - a quote can match gold and still name the wrong page, and that
    is precisely the failure P1 is about.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .documents import ExtractedDocument, extract_pdf, verify_citation

log = logging.getLogger(__name__)

#: Annex E.4, verbatim. Changing one of these is changing what the product promises, so they
#: live here as named constants rather than inline in a comparison.
GATES: dict[str, float] = {
    "requirement_recall": 0.90,
    "eliminatory_recall": 0.95,
    "requirement_precision": 0.85,
    "admin_exact_match": 0.95,
    "citation_validity": 0.98,
}

#: Two requirements are the same requirement above this similarity. Set by what the texts
#: actually look like: gold and model wording of one obligation typically agree on the nouns and
#: differ in connectives, which lands well above 0.6, while two genuinely different obligations
#: from the same document rarely reach it because the nouns differ.
MATCH_THRESHOLD = 0.6

#: A cited page may be one off when a requirement straddles a page break, and counting that as a
#: fabricated citation would punish a correct extraction.
PAGE_TOLERANCE = 1


class CorpusMissing(RuntimeError):
    """No gold corpus was found. Never a pass - see the module docstring."""


@dataclass
class MetricResult:
    name: str
    value: float
    gate: float
    #: Numerator and denominator, so a failure can be read without re-running.
    matched: int = 0
    total: int = 0

    @property
    def passed(self) -> bool:
        return self.value >= self.gate


@dataclass
class CaseResult:
    case: str
    metrics: list[MetricResult] = field(default_factory=list)
    missed_eliminatory: list[str] = field(default_factory=list)
    invalid_citations: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass
class EvalReport:
    cases: list[CaseResult] = field(default_factory=list)
    metrics: list[MetricResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(case.error for case in self.cases) and all(m.passed for m in self.metrics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "metrics": [asdict(metric) for metric in self.metrics],
            "cases": [asdict(case) for case in self.cases],
        }

    def summary_lines(self) -> list[str]:
        lines = []
        for metric in self.metrics:
            mark = "PASS" if metric.passed else "FAIL"
            lines.append(
                f"  {mark}  {metric.name:<24} {metric.value:.3f}  (gate {metric.gate:.2f}, "
                f"{metric.matched}/{metric.total})"
            )
        return lines


def similarity(left: str, right: str) -> float:
    """How alike two requirement texts are, ignoring case and spacing."""
    return SequenceMatcher(None, _normalise(left), _normalise(right)).ratio()


def _normalise(value: str) -> str:
    return " ".join(value.split()).casefold()


def match_requirements(
    predicted: list[dict[str, Any]], gold: list[dict[str, Any]]
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Greedy best-first pairing of predictions to gold items.

    Greedy rather than optimal: an optimal assignment would move a metric by a fraction of a
    point on registers this size, and a scoring rule nobody can reproduce by hand is a scoring
    rule nobody will trust when it fails a merge.

    Returns `(pairs, unmatched_predicted, unmatched_gold)`.
    """
    scored: list[tuple[float, int, int]] = []
    for p_index, prediction in enumerate(predicted):
        for g_index, expected in enumerate(gold):
            score = similarity(prediction.get("text", ""), expected.get("text", ""))
            if score >= MATCH_THRESHOLD:
                scored.append((score, p_index, g_index))
    scored.sort(reverse=True)

    pairs: list[tuple[int, int]] = []
    used_p: set[int] = set()
    used_g: set[int] = set()
    for _score, p_index, g_index in scored:
        if p_index in used_p or g_index in used_g:
            continue
        pairs.append((p_index, g_index))
        used_p.add(p_index)
        used_g.add(g_index)

    return (
        pairs,
        [i for i in range(len(predicted)) if i not in used_p],
        [i for i in range(len(gold)) if i not in used_g],
    )


def score_case(
    *,
    case: str,
    predicted_requirements: list[dict[str, Any]],
    gold_requirements: list[dict[str, Any]],
    predicted_admin: dict[str, Any] | None = None,
    gold_admin: dict[str, Any] | None = None,
    document: ExtractedDocument | None = None,
) -> CaseResult:
    """Score one DCE against its gold file."""
    result = CaseResult(case=case)

    pairs, _unmatched_predicted, _unmatched_gold = match_requirements(
        predicted_requirements, gold_requirements
    )

    total_gold = len(gold_requirements)
    result.metrics.append(
        MetricResult(
            "requirement_recall",
            len(pairs) / total_gold if total_gold else 1.0,
            GATES["requirement_recall"],
            matched=len(pairs),
            total=total_gold,
        )
    )
    result.metrics.append(
        MetricResult(
            "requirement_precision",
            len(pairs) / len(predicted_requirements) if predicted_requirements else 0.0,
            GATES["requirement_precision"],
            matched=len(pairs),
            total=len(predicted_requirements),
        )
    )

    # Eliminatory recall is measured over gold's eliminatory items only, and an item counts as
    # found only if it was *also typed* eliminatory. Finding the sentence and filing it as a
    # formatting note leaves the bidder just as disqualified.
    gold_eliminatory = [i for i, item in enumerate(gold_requirements) if item.get("type") == "eliminatory"]
    matched_by_gold = {g: p for p, g in pairs}
    found_eliminatory = [
        g
        for g in gold_eliminatory
        if g in matched_by_gold and predicted_requirements[matched_by_gold[g]].get("type") == "eliminatory"
    ]
    result.missed_eliminatory = [
        gold_requirements[g].get("ref") or gold_requirements[g].get("text", "")[:60]
        for g in gold_eliminatory
        if g not in {x for x in found_eliminatory}
    ]
    result.metrics.append(
        MetricResult(
            "eliminatory_recall",
            len(found_eliminatory) / len(gold_eliminatory) if gold_eliminatory else 1.0,
            GATES["eliminatory_recall"],
            matched=len(found_eliminatory),
            total=len(gold_eliminatory),
        )
    )

    if gold_admin is not None:
        matched_fields, total_fields = _score_admin(predicted_admin or {}, gold_admin)
        result.metrics.append(
            MetricResult(
                "admin_exact_match",
                matched_fields / total_fields if total_fields else 1.0,
                GATES["admin_exact_match"],
                matched=matched_fields,
                total=total_fields,
            )
        )

    if document is not None:
        valid, total_citations = _score_citations(predicted_requirements, predicted_admin, document, result)
        result.metrics.append(
            MetricResult(
                "citation_validity",
                valid / total_citations if total_citations else 1.0,
                GATES["citation_validity"],
                matched=valid,
                total=total_citations,
            )
        )

    return result


def _score_admin(predicted: dict[str, Any], gold: dict[str, Any]) -> tuple[int, int]:
    """Exact-match on the admin fields gold states.

    Only fields gold actually asserts are counted. A gold file that leaves `questions_deadline`
    null is saying the document does not state one, and scoring the model for agreeing would
    reward it twice for the same fact.
    """
    matched = 0
    total = 0
    for field_name, expected in gold.items():
        if expected is None:
            continue
        total += 1
        actual = predicted.get(field_name)
        if isinstance(expected, dict) and isinstance(actual, dict):
            if _normalise(str(expected.get("value", ""))) == _normalise(str(actual.get("value", ""))):
                matched += 1
        elif isinstance(expected, list) and isinstance(actual, list):
            if len(expected) == len(actual):
                matched += 1
        elif _normalise(str(expected)) == _normalise(str(actual)):
            matched += 1
    return matched, total


def _score_citations(
    requirements: list[dict[str, Any]],
    admin: dict[str, Any] | None,
    document: ExtractedDocument,
    result: CaseResult,
) -> tuple[int, int]:
    """Is each quote actually on (or adjacent to) the page it cites?

    Checked against the *document*, never against gold: a quote can agree with gold and still
    name a page it does not appear on, and an unverifiable citation is the failure mode P1
    exists to prevent.
    """
    valid = 0
    total = 0

    def check(quote: str, page: Any, where: str) -> None:
        nonlocal valid, total
        if not quote or not isinstance(page, int):
            return
        total += 1
        for candidate in range(page - PAGE_TOLERANCE, page + PAGE_TOLERANCE + 1):
            if candidate >= 1 and verify_citation(document, page=candidate, quote=quote):
                valid += 1
                return
        result.invalid_citations.append({"where": where, "page": page, "quote": quote[:120]})

    for item in requirements:
        check(item.get("quote", ""), item.get("page"), item.get("ref", "?"))

    for name, value in (admin or {}).items():
        if isinstance(value, dict):
            check(value.get("quote", ""), value.get("page"), name)
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict):
                    check(entry.get("quote", ""), entry.get("page"), name)

    return valid, total


def aggregate(cases: list[CaseResult]) -> list[MetricResult]:
    """Corpus-wide metrics, pooled over items rather than averaged over cases.

    Pooling matters: averaging per-case rates lets a 5-requirement DCE outvote a 200-requirement
    one, so a model could pass by doing well on the small documents.
    """
    totals: dict[str, tuple[int, int]] = {}
    for case in cases:
        for metric in case.metrics:
            matched, total = totals.get(metric.name, (0, 0))
            totals[metric.name] = (matched + metric.matched, total + metric.total)

    return [
        MetricResult(
            name=name,
            value=matched / total if total else 1.0,
            gate=GATES.get(name, 0.0),
            matched=matched,
            total=total,
        )
        for name, (matched, total) in sorted(totals.items())
    ]


def load_corpus(root: Path) -> list[dict[str, Any]]:
    """Every case in the gold corpus (Annex E.3): a PDF plus its `*.gold.json`."""
    if not root.is_dir():
        raise CorpusMissing(f"no eval corpus at {root}")

    cases: list[dict[str, Any]] = []
    for gold_path in sorted(root.glob("*.gold.json")):
        document_path = gold_path.with_name(gold_path.name.replace(".gold.json", ".pdf"))
        if not document_path.exists():
            raise CorpusMissing(f"{gold_path.name} has no matching PDF at {document_path.name}")
        cases.append(
            {
                "name": document_path.stem,
                "document": document_path,
                "gold": json.loads(gold_path.read_text(encoding="utf-8")),
            }
        )

    if not cases:
        raise CorpusMissing(
            f"no *.gold.json files in {root}. Annex E.3 requires >= 15 labelled DCEs; an eval "
            "that finds none must fail rather than report success."
        )
    return cases


def run_eval(root: Path, extractor: Any) -> EvalReport:
    """Run `extractor` over the corpus and score it.

    `extractor(document, case_name) -> {"requirements": [...], "admin": {...}}`, so the harness
    is independent of how extraction is performed - live model, replayed cassette, or a stub in
    a test that checks the harness itself.
    """
    cases = load_corpus(root)
    report = EvalReport()

    for case in cases:
        try:
            document = extract_pdf(case["document"])
            predicted = extractor(document, case["name"])
            result = score_case(
                case=case["name"],
                predicted_requirements=predicted.get("requirements", []),
                gold_requirements=case["gold"].get("requirements", []),
                predicted_admin=predicted.get("admin"),
                gold_admin=case["gold"].get("admin"),
                document=document,
            )
        except Exception as exc:  # a case that cannot run is a failure, not a skip
            log.exception("eval case %s failed", case["name"])
            result = CaseResult(case=case["name"], error=f"{type(exc).__name__}: {exc}")
        report.cases.append(result)

    report.metrics = aggregate(report.cases)
    return report
