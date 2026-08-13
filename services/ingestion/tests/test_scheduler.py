"""Scheduler tests (SPEC §17.1).

The cron parser is small and hand-written, so it is worth pinning precisely: a source whose
schedule silently never matches is a source that silently stops being ingested - the failure
§6.1 exists to prevent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from bidpilot_ingestion.scheduler import cron_matches, parse_cron, time_bucket


@pytest.mark.parametrize(
    ("expression", "moment", "expected"),
    [
        # Every 30 minutes - the seeded TED and BOAMP schedule.
        ("*/30 * * * *", datetime(2026, 8, 13, 10, 0, tzinfo=UTC), True),
        ("*/30 * * * *", datetime(2026, 8, 13, 10, 30, tzinfo=UTC), True),
        ("*/30 * * * *", datetime(2026, 8, 13, 10, 15, tzinfo=UTC), False),
        # Twice a day, as configured for Luxembourg's low-volume portal.
        ("0 6,18 * * *", datetime(2026, 8, 13, 6, 0, tzinfo=UTC), True),
        ("0 6,18 * * *", datetime(2026, 8, 13, 18, 0, tzinfo=UTC), True),
        ("0 6,18 * * *", datetime(2026, 8, 13, 7, 0, tzinfo=UTC), False),
        # Daily, as configured for DECP.
        ("0 4 * * *", datetime(2026, 8, 13, 4, 0, tzinfo=UTC), True),
        ("0 4 * * *", datetime(2026, 8, 13, 5, 0, tzinfo=UTC), False),
        # Every minute, for the email connector.
        ("* * * * *", datetime(2026, 8, 13, 3, 47, tzinfo=UTC), True),
        # Weekday ranges. 2026-08-13 is a Thursday, which is cron weekday 4.
        ("0 8 * * 1-5", datetime(2026, 8, 13, 8, 0, tzinfo=UTC), True),
        ("0 8 * * 6,0", datetime(2026, 8, 13, 8, 0, tzinfo=UTC), False),
        # 2026-08-16 is a Sunday, which is cron weekday 0 - the off-by-one worth pinning.
        ("0 8 * * 0", datetime(2026, 8, 16, 8, 0, tzinfo=UTC), True),
    ],
)
def test_cron_matching(expression, moment, expected):
    assert cron_matches(expression, moment) is expected


def test_malformed_cron_is_rejected_loudly():
    with pytest.raises(ValueError, match="expected 5 cron fields"):
        parse_cron("*/30 * *")


def test_star_fields_parse_as_every_value():
    field = parse_cron("* * * * *")
    assert field.minutes is None
    assert field.hours is None


def test_step_and_range_fields():
    field = parse_cron("0,15,30,45 9-17 * * *")
    assert field.minutes == {0, 15, 30, 45}
    assert field.hours == set(range(9, 18))


def test_time_bucket_is_stable_within_an_interval():
    """The bucket is the idempotency version, which is what lets redundant schedulers
    (§16: "alert scheduler MUST be redundant") collapse to a single job."""
    every = timedelta(minutes=15)
    base = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
    assert time_bucket(base, every) == time_bucket(base + timedelta(minutes=14), every)
    assert time_bucket(base, every) != time_bucket(base + timedelta(minutes=16), every)
