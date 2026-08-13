"""Source-health threshold tests (SPEC §6.1).

`evaluate` is pure, so the thresholds that decide whether an operator gets paged at 3am are
testable without a database - and worth testing precisely, because both failure directions are
costly: a missed silent source means users see an empty feed and trust it, while a noisy
threshold trains operators to ignore the alert.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from bidpilot_ingestion.health import (
    MIN_VOLUME_FOR_YIELD_CHECK,
    YIELD_DROP_THRESHOLD,
    evaluate,
)


def _evaluate(**overrides):
    defaults = dict(
        code="fr-boamp",
        tier="official_api",
        enabled=True,
        silent_for=timedelta(minutes=20),
        notices_7d=400,
        notices_prev_7d=420,
    )
    defaults.update(overrides)
    return evaluate(**defaults)


def test_a_responsive_source_with_stable_yield_is_green():
    assert _evaluate().health == "green"


@pytest.mark.parametrize(
    ("tier", "silent_hours", "expected"),
    [
        # Tier 1: 6h budget.
        ("official_api", 5, "green"),
        ("official_api", 7, "silent"),
        # Tier 2: 24h budget.
        ("platform", 20, "green"),
        ("platform", 30, "silent"),
        # Tier 3/4 are polled daily at most, so a day of quiet is normal.
        ("long_tail", 40, "green"),
        ("long_tail", 80, "silent"),
    ],
)
def test_silence_budget_per_tier(tier, silent_hours, expected):
    verdict = _evaluate(tier=tier, silent_for=timedelta(hours=silent_hours))
    assert verdict.health == expected


def test_a_source_that_never_ran_is_silent():
    verdict = _evaluate(silent_for=None)
    assert verdict.health == "silent"
    assert verdict.reason == "never ran"


def test_only_tier_one_and_two_silence_pages_an_operator():
    """§6.1 pages on Tier-1/Tier-2 silence. Long-tail sources file a ticket instead: waking
    someone for a quiet regional portal is how alerting gets muted."""
    assert _evaluate(tier="official_api", silent_for=timedelta(hours=9)).should_page is True
    assert _evaluate(tier="platform", silent_for=timedelta(hours=30)).should_page is True
    assert _evaluate(tier="long_tail", silent_for=timedelta(hours=90)).should_page is False
    # A degraded parser is not a page - it needs a fix, not a wake-up.
    assert _evaluate(notices_7d=10, notices_prev_7d=100).should_page is False


def test_yield_collapse_flags_a_broken_parser():
    """The failure mode a freshness check cannot see: HTTP 200 all day, parser extracting
    nothing because the page changed."""
    verdict = _evaluate(notices_7d=10, notices_prev_7d=100)
    assert verdict.health == "degraded"
    assert "yield down 90%" in verdict.reason


def test_yield_drop_just_under_the_threshold_stays_green():
    previous = 100
    current = int(previous * (1 - YIELD_DROP_THRESHOLD)) + 1
    assert _evaluate(notices_7d=current, notices_prev_7d=previous).health == "green"


def test_low_volume_sources_are_not_judged_on_yield():
    """Luxembourg publishes a handful of notices a week; 2 → 1 is noise, not a regression."""
    assert _evaluate(notices_7d=1, notices_prev_7d=MIN_VOLUME_FOR_YIELD_CHECK - 1).health == "green"


def test_growth_is_never_a_problem():
    assert _evaluate(notices_7d=900, notices_prev_7d=100).health == "green"


def test_a_disabled_source_reports_disabled_not_silent():
    """Disabled sources are the expected steady state for Tier-2 pending legal review (§24.7);
    reporting them as silent would bury the real alerts."""
    verdict = _evaluate(enabled=False, silent_for=timedelta(days=30))
    assert verdict.health == "disabled"
    assert verdict.should_page is False
