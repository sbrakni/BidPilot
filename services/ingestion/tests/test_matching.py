"""Matching engine tests, including the F3 acceptance criteria (SPEC §8.4).

The volume tests run against fixtures/notices/replay_48h.json - a real 48h FR/BE/LU
stream of ~1,400 notices from TED and BOAMP. Using the real stream is the point: a
hand-built corpus would silently encode our assumptions about what tenders look like,
which is exactly what the matching engine is supposed to be tested against.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from bidpilot_ingestion.canonical import (
    Amounts,
    Buyer,
    CanonicalNotice,
    Dates,
    Lot,
    NoticeType,
    Procedure,
    Provenance,
    SourceRef,
)
from bidpilot_ingestion.matching import (
    DEFAULT_WEIGHTS,
    INSTANT_ALERT_THRESHOLD,
    LLM_QUALIFICATION_THRESHOLD,
    CompanyProfile,
    FilterOutcome,
    WatchFilters,
    apply_hard_filters,
    cpv_matches,
    nuts_matches,
    run_funnel,
    score_notice,
)

REPLAY = Path(__file__).resolve().parents[3] / "fixtures" / "notices" / "replay_48h.json"

# A fixed "now" keeps deadline-sensitive assertions stable as the corpus ages.
NOW = datetime(2026, 8, 13, tzinfo=UTC)

IT_SECTOR_CPV = ["72", "48", "302", "79511"]


@pytest.fixture(scope="module")
def replay_corpus() -> list[CanonicalNotice]:
    payload = json.loads(REPLAY.read_text())
    return [CanonicalNotice.model_validate(entry) for entry in payload["notices"]]


@pytest.fixture(scope="module")
def competition_notices(replay_corpus: list[CanonicalNotice]) -> list[CanonicalNotice]:
    return [n for n in replay_corpus if n.notice_type is NoticeType.COMPETITION]


@pytest.fixture
def it_profile() -> CompanyProfile:
    """Léa's 35-person ESN in Île-de-France (SPEC §3.1)."""
    return CompanyProfile(
        org_id="org_demo_it",
        country="FR",
        cpv_families=IT_SECTOR_CPV,
        zones_nuts=["FR10"],
        keywords=["infogérance", "développement", "informatique", "logiciel", "numérique"],
        annual_revenue=4_000_000,
    )


def make_notice(**overrides) -> CanonicalNotice:
    defaults = dict(
        source_refs=[SourceRef(source="eu-ted", external_id="1-2026")],
        notice_type=NoticeType.COMPETITION,
        country="FR",
        title="Infogérance du système d'information",
        description="Prestations d'infogérance et de support informatique.",
        cpv=["72000000"],
        nuts=["FR101"],
        procedure=Procedure(type="open"),
        amounts=Amounts(estimated_total=200_000.0, currency="EUR"),
        dates=Dates(deadline_at=NOW + timedelta(days=30)),
        provenance=Provenance(adapter="ted@1.0.0", normalized_at=NOW),
    )
    defaults.update(overrides)
    return CanonicalNotice(**defaults)


# ---------------------------------------------------------------- §8.4 acceptance criteria


def test_seeded_it_org_gets_at_least_ten_matches_from_the_48h_replay(competition_notices, it_profile):
    """§8.4: "A seeded org (IT services, IDF) receives >= 10 relevant matches from a 48h
    TED+BOAMP replay fixture, zero matches violating hard filters."
    """
    filters = WatchFilters(
        cpv_families=IT_SECTOR_CPV,
        countries=["FR"],
        nuts=["FR10"],
        notice_types=[NoticeType.COMPETITION],
    )
    results = run_funnel(competition_notices, it_profile, filters, now=NOW)
    passed = [r for r in results if r.passed]

    assert len(passed) >= 10, f"expected >= 10 matches from the replay, got {len(passed)}"

    # "zero matches violating hard filters" - re-check each survivor independently.
    by_id = {n.source_refs[0].external_id: n for n in competition_notices}
    for result in passed:
        notice = by_id[result.notice_external_id]
        assert apply_hard_filters(notice, filters, now=NOW) is FilterOutcome.PASS
        assert notice.country == "FR"
        assert notice.notice_type is NoticeType.COMPETITION
        codes = [*notice.cpv, *(c for lot in notice.lots for c in lot.cpv)]
        assert any(cpv_matches(code, IT_SECTOR_CPV) for code in codes)


def test_displayed_factors_sum_exactly_to_the_displayed_score(competition_notices, it_profile):
    """§8.4: "sum of displayed factors = displayed score" - the UI renders both, and a
    breakdown that does not add up destroys the explainability promise (P4)."""
    for notice in competition_notices[:200]:
        score = score_notice(notice, it_profile, now=NOW)
        rendered = score.as_dict()
        # Compare what the UI actually shows: the rounded points and the integer score.
        total = sum(factor["points"] for factor in rendered["factors"])
        assert round(total) == rendered["score"], f"{total} != {rendered['score']}"
        assert 0 <= score.score <= 100


def test_stage_three_llm_never_runs_on_stage_one_failures(competition_notices, it_profile):
    """§8.4 cost guard. The failure mode is a silent invoice, so it is structural: a
    notice that fails stage 1 is never scored, and `llm_eligible` cannot become True."""
    filters = WatchFilters(
        cpv_families=IT_SECTOR_CPV, countries=["FR"], notice_types=[NoticeType.COMPETITION]
    )
    results = run_funnel(competition_notices, it_profile, filters, now=NOW)

    rejected = [r for r in results if not r.passed]
    assert rejected, "the replay must contain notices this profile rejects"
    for result in rejected:
        assert result.llm_eligible is False
        assert result.score is None, "a rejected notice must not even be scored"

    for result in (r for r in results if r.passed):
        assert result.llm_eligible == (result.score.score >= LLM_QUALIFICATION_THRESHOLD)


def test_every_scored_match_carries_a_breakdown(competition_notices, it_profile):
    filters = WatchFilters(
        cpv_families=IT_SECTOR_CPV, countries=["FR"], notice_types=[NoticeType.COMPETITION]
    )
    for result in run_funnel(competition_notices, it_profile, filters, now=NOW):
        if not result.passed:
            continue
        factors = result.score.as_dict()["factors"]
        assert {f["key"] for f in factors} == set(DEFAULT_WEIGHTS)
        assert all(f["label_fr"] for f in factors), "the UI needs a French label per factor"
        assert all(f["detail"] for f in factors), "every factor must explain itself"


# ---------------------------------------------------------------- hard filters (stage 1)


def test_cpv_family_matching_is_hierarchical():
    assert cpv_matches("72212000", ["72"])
    assert cpv_matches("72212000", ["7221"])
    assert cpv_matches("72000000", ["72*"]), "a trailing star is accepted for readability"
    assert not cpv_matches("50700000", ["72", "48"])


def test_nuts_matching_works_in_both_directions():
    """A FR10-scoped org matches a FR101 tender (inside their zone) and a FR1 tender
    (their zone is inside the tender's area). Either way the work is reachable."""
    assert nuts_matches("FR101", ["FR10"])
    assert nuts_matches("FR1", ["FR10"])
    assert not nuts_matches("FRK21", ["FR10"])


def test_notice_without_a_place_is_not_excluded_on_geography():
    """Absent is not a mismatch (P1): a poorly tagged notice must stay visible."""
    notice = make_notice(nuts=[], lots=[])
    filters = WatchFilters(nuts=["FR10"])
    assert apply_hard_filters(notice, filters, now=NOW) is FilterOutcome.PASS


def test_lot_level_cpv_and_place_satisfy_the_filters():
    """Multi-lot tenders often carry classification only at lot level (SPEC §5)."""
    notice = make_notice(
        cpv=[],
        nuts=[],
        lots=[Lot(lot_id="1", title="Lot 1", cpv=["72212000"], place_nuts=["FR101"])],
    )
    filters = WatchFilters(cpv_families=["72"], nuts=["FR10"])
    assert apply_hard_filters(notice, filters, now=NOW) is FilterOutcome.PASS


@pytest.mark.parametrize(
    ("overrides", "filters", "expected"),
    [
        ({"cpv": ["50700000"]}, WatchFilters(cpv_families=["72"]), FilterOutcome.CPV),
        ({"country": "BE"}, WatchFilters(countries=["FR"]), FilterOutcome.COUNTRY),
        ({"nuts": ["FRK21"]}, WatchFilters(nuts=["FR10"]), FilterOutcome.GEOGRAPHY),
        (
            {"dates": Dates(deadline_at=NOW + timedelta(days=3))},
            WatchFilters(min_days_to_deadline=10),
            FilterOutcome.DEADLINE,
        ),
        (
            {"amounts": Amounts(estimated_total=5_000.0)},
            WatchFilters(amount_min=50_000),
            FilterOutcome.AMOUNT,
        ),
        (
            {"amounts": Amounts(estimated_total=9_000_000.0)},
            WatchFilters(amount_max=1_000_000),
            FilterOutcome.AMOUNT,
        ),
        (
            {"procedure": Procedure(type="restricted")},
            WatchFilters(procedure_types=["open"]),
            FilterOutcome.PROCEDURE,
        ),
        (
            {"notice_type": NoticeType.RESULT},
            WatchFilters(notice_types=[NoticeType.COMPETITION]),
            FilterOutcome.NOTICE_TYPE,
        ),
        (
            # Both title and description must be clear of the term: the filter searches
            # the whole text, as a user would expect.
            {"title": "Travaux de peinture", "description": "Réfection des murs."},
            WatchFilters(keywords_include=["infogérance"]),
            FilterOutcome.KEYWORD_MISSING,
        ),
        (
            {"title": "Infogérance avec sous-traitance obligatoire"},
            WatchFilters(keywords_exclude=["sous-traitance obligatoire"]),
            FilterOutcome.KEYWORD_EXCLUDED,
        ),
    ],
)
def test_each_hard_filter_vetoes_with_its_own_reason(overrides, filters, expected):
    """The reason is user-visible ("pourquoi je ne vois pas cet avis ?") and feeds the
    dismissal/weight-tuning loop (§8.3), so each veto must be individually identifiable."""
    assert apply_hard_filters(make_notice(**overrides), filters, now=NOW) is expected


def test_keyword_matching_ignores_accents_and_case():
    notice = make_notice(title="INFOGERANCE du systeme d'information")
    assert (
        apply_hard_filters(notice, WatchFilters(keywords_include=["infogérance"]), now=NOW)
        is FilterOutcome.PASS
    )


def test_amount_filters_do_not_exclude_notices_without_an_amount():
    """Most below-threshold notices publish no estimate; excluding them would hide exactly
    the SME-accessible tenders BidPilot exists to surface (SPEC §1.2)."""
    notice = make_notice(amounts=Amounts())
    filters = WatchFilters(amount_min=100_000, amount_max=500_000)
    assert apply_hard_filters(notice, filters, now=NOW) is FilterOutcome.PASS


# ---------------------------------------------------------------- scoring (stage 2)


def test_perfect_fit_scores_high_and_poor_fit_scores_low(it_profile):
    strong = make_notice(
        cpv=["72212000"],
        nuts=["FR101"],
        amounts=Amounts(estimated_total=300_000.0),
        dates=Dates(deadline_at=NOW + timedelta(days=45)),
        buyer=Buyer(name="Région Île-de-France", siren="237500019"),
    )
    it_profile.known_buyer_sirens = ["237500019"]
    weak = make_notice(
        title="Travaux de couverture",
        description="Réfection de toiture.",
        cpv=["45261000"],
        nuts=["FRK21"],
        amounts=Amounts(estimated_total=20_000_000.0),
        dates=Dates(deadline_at=NOW + timedelta(days=2)),
    )
    assert score_notice(strong, it_profile, now=NOW).score >= INSTANT_ALERT_THRESHOLD
    assert score_notice(weak, it_profile, now=NOW).score < 40


def test_oversized_tender_is_flagged_not_hidden(it_profile):
    """A contract above half of annual revenue is a size risk worth saying out loud, but
    not a veto - it can be the right stretch (SPEC §8.2)."""
    notice = make_notice(amounts=Amounts(estimated_total=3_000_000.0))  # 75% of 4M revenue
    score = score_notice(notice, it_profile, now=NOW)
    assert any("CA annuel" in warning for warning in score.warnings)
    assert score.breakdown["size_fit"] < DEFAULT_WEIGHTS["size_fit"] * 100 * 0.5


def test_missing_certification_is_detected_from_the_notice_text(it_profile):
    """§20.2: the inbox card pre-flags "Qualiopi requise" before anyone opens the DCE."""
    notice = make_notice(description="Le titulaire devra être certifié Qualiopi et ISO 27001.")
    score = score_notice(notice, it_profile, now=NOW)
    assert score.breakdown["certification_fit"] == 0.0
    assert any("Qualiopi" in warning for warning in score.warnings)

    it_profile.certifications = ["Qualiopi", "ISO 27001"]
    held = score_notice(notice, it_profile, now=NOW)
    assert held.breakdown["certification_fit"] == pytest.approx(DEFAULT_WEIGHTS["certification_fit"] * 100)


def test_unknown_values_score_neutral_never_negative(it_profile):
    """Unknowns are surfaced, not punished (P1). A notice missing amount, place and
    deadline must still be scored on what is known."""
    notice = make_notice(nuts=[], lots=[], amounts=Amounts(), dates=Dates())
    score = score_notice(notice, it_profile, now=NOW)
    assert all(factor.points >= 0 for factor in score.factors)
    assert score.breakdown["geographic_fit"] == pytest.approx(DEFAULT_WEIGHTS["geographic_fit"] * 100 * 0.5)
    assert "non publié" in dict((f.key, f.detail) for f in score.factors)["size_fit"]


def test_semantic_similarity_is_used_when_an_embedding_exists(it_profile):
    """Cold start must work without embeddings, and improve with them (SPEC §23)."""
    notice = make_notice(cpv=["98000000"], description="Prestations diverses.")
    it_profile.capability_embedding = [1.0, 0.0, 0.0]
    object.__setattr__(notice, "_embedding", [0.99, 0.14, 0.0])

    with_embedding = score_notice(notice, it_profile, now=NOW)
    it_profile.capability_embedding = None
    without_embedding = score_notice(notice, it_profile, now=NOW)
    assert with_embedding.breakdown["activity_fit"] > without_embedding.breakdown["activity_fit"]


def test_expired_deadline_scores_zero_comfort(it_profile):
    notice = make_notice(dates=Dates(deadline_at=NOW - timedelta(days=1)))
    score = score_notice(notice, it_profile, now=NOW)
    assert score.breakdown["deadline_comfort"] == 0.0


def test_weights_are_overridable_per_org(it_profile):
    """§8.2 allows per-org weight overrides; the sum must still be 100 points maximum."""
    notice = make_notice()
    weights = {**DEFAULT_WEIGHTS, "activity_fit": 0.60, "deadline_comfort": 0.0}
    score = score_notice(notice, it_profile, weights=weights, now=NOW)
    assert score.breakdown["deadline_comfort"] == 0.0
    assert score.score <= 100


def test_three_sector_packs_all_produce_matches(competition_notices):
    """The launch sector packs are data, not code (SPEC §4.2) - each must work unchanged."""
    packs = {
        "it": (IT_SECTOR_CPV, ["informatique", "logiciel"]),
        "training": (["805", "79632", "80533"], ["formation"]),
        "maintenance": (["50", "45259", "90910", "71314"], ["maintenance", "entretien"]),
    }
    for name, (families, keywords) in packs.items():
        profile = CompanyProfile(
            org_id=f"org_{name}",
            cpv_families=families,
            national=True,
            keywords=keywords,
            annual_revenue=5_000_000,
        )
        filters = WatchFilters(cpv_families=families, countries=["FR"], notice_types=[NoticeType.COMPETITION])
        passed = [r for r in run_funnel(competition_notices, profile, filters, now=NOW) if r.passed]
        assert passed, f"sector pack {name!r} matched nothing in a 48h stream"
