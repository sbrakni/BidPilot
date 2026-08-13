"""Deadline alert tests (SPEC §12.4, §16).

§16 calls a missed deadline alert "the worst product failure", so the pure logic deciding which
alerts are due is tested directly - including weekend boundaries and the case that motivates
computing alerts instead of pre-scheduling them: a deadline that moves.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from bidpilot_ingestion.alerts import (
    DEADLINE_ALERT_OFFSETS,
    due_deadline_alerts,
    is_working_day,
    working_days_before,
)

# 2026-11-20 is a Friday; 2026-11-23 a Monday.
FRIDAY = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
MONDAY = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def _tender(deadline: datetime | None, tender_id: str = "tnd_1", org_id: str = "org_1") -> dict:
    return {"id": tender_id, "org_id": org_id, "title": "Infogérance", "deadline_at": deadline}


def test_working_day_recognition():
    assert is_working_day(date(2026, 11, 20)) is True  # Friday
    assert is_working_day(date(2026, 11, 21)) is False  # Saturday
    assert is_working_day(date(2026, 11, 22)) is False  # Sunday
    assert is_working_day(date(2026, 11, 23)) is True  # Monday


def test_working_days_before_skips_weekends():
    """A J-3 alert on a Monday deadline must land on the previous Wednesday.

    Counting calendar days would put it on Friday, which gives the team no working time.
    """
    assert working_days_before(MONDAY, 1).date() == date(2026, 11, 20)  # Friday
    assert working_days_before(MONDAY, 3).date() == date(2026, 11, 18)  # Wednesday
    assert working_days_before(FRIDAY, 1).date() == date(2026, 11, 19)  # Thursday
    # Seven working days back from a Friday crosses two weekends.
    assert working_days_before(FRIDAY, 7).date() == date(2026, 11, 11)


def test_working_days_before_keeps_the_time_of_day():
    """The alert fires at the deadline's own hour, so a 09:00 deadline is not alerted at 23:00."""
    assert working_days_before(FRIDAY, 3).hour == 12


def test_no_alert_fires_too_early():
    assert due_deadline_alerts([_tender(FRIDAY)], now=FRIDAY - timedelta(days=40), already_sent=set()) == []


def test_only_the_nearest_unsent_offset_fires():
    """A tender pursued close to its deadline must not emit J-14, J-7 and J-3 at once - that
    reads as noise and buries the one that matters."""
    now = working_days_before(FRIDAY, 3)
    due = due_deadline_alerts([_tender(FRIDAY)], now=now, already_sent=set())
    assert [alert.offset_days for alert in due] == [14]

    due = due_deadline_alerts(
        [_tender(FRIDAY)],
        now=now,
        already_sent={("tnd_1", "deadline.j14"), ("tnd_1", "deadline.j7")},
    )
    assert [alert.offset_days for alert in due] == [3]


def test_each_offset_fires_exactly_once_across_repeated_ticks():
    """Idempotency: ticking every 15 minutes must not re-raise an alert already recorded."""
    already: set[tuple[str, str]] = set()
    raised: list[int] = []
    moment = FRIDAY - timedelta(days=30)

    while moment < FRIDAY:
        for alert in due_deadline_alerts([_tender(FRIDAY)], now=moment, already_sent=already):
            raised.append(alert.offset_days)
            already.add((alert.tender_id, alert.notification_kind))
        moment += timedelta(hours=6)

    assert sorted(raised, reverse=True) == sorted(DEADLINE_ALERT_OFFSETS, reverse=True)
    assert len(raised) == len(set(raised)), "no offset may fire twice"


def test_a_moved_deadline_recomputes_rather_than_misfiring():
    """The reason alerts are computed instead of pre-scheduled.

    An amendment pushing a deadline out must not leave a J-3 alert queued against the old date -
    which is exactly what pre-written schedule rows would do.
    """
    now = working_days_before(FRIDAY, 3)
    assert due_deadline_alerts([_tender(FRIDAY)], now=now, already_sent=set())
    assert due_deadline_alerts([_tender(FRIDAY + timedelta(days=21))], now=now, already_sent=set()) == []


def test_passed_deadlines_raise_nothing():
    """A missed deadline needs a different conversation, not a countdown alert."""
    assert due_deadline_alerts([_tender(FRIDAY)], now=FRIDAY + timedelta(days=1), already_sent=set()) == []


def test_tenders_without_a_deadline_are_skipped():
    assert due_deadline_alerts([_tender(None)], now=FRIDAY, already_sent=set()) == []


def test_naive_deadlines_are_treated_as_utc():
    """Defensive: a naive value must not crash the most critical job in the product."""
    due = due_deadline_alerts(
        [_tender(FRIDAY.replace(tzinfo=None))],
        now=working_days_before(FRIDAY, 1),
        already_sent={
            ("tnd_1", "deadline.j14"),
            ("tnd_1", "deadline.j7"),
            ("tnd_1", "deadline.j3"),
        },
    )
    assert [alert.offset_days for alert in due] == [1]


def test_alerts_carry_the_org_so_delivery_stays_scoped():
    due = due_deadline_alerts(
        [_tender(FRIDAY, org_id="org_x")], now=working_days_before(FRIDAY, 14), already_sent=set()
    )
    assert due[0].org_id == "org_x"
    assert due[0].notification_kind == "deadline.j14"
