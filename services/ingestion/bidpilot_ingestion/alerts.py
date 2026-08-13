"""Deadline and freshness alerts (SPEC §12.4, §7.2).

This module carries the product's highest-stakes logic. §16 says it plainly: *"deadline alerts
are the most critical path - alert scheduler MUST be redundant (missed-alert = worst product
failure)"*, and P3 makes deadlines sacred.

Three design consequences follow:

  * **Alerts are computed, never scheduled ahead.** Rather than writing "send at J-7" rows when
    a tender is pursued, each tick asks "which alerts are due and not yet sent?". A deadline
    that moves (which happens - that is what an amendment often is) therefore corrects itself,
    where pre-scheduled rows would fire on the old date.
  * **A `notifications` row is the record that an alert was raised**, and it is inserted in the
    same transaction as the send job. So a duplicate tick cannot double-send, and a crash
    cannot lose an alert: `sent_at` distinguishes raised from delivered.
  * **Working days, not calendar days.** A J-3 alert on a Monday deadline must fire on the
    preceding Wednesday, not Friday - a Friday alert gives the team no working time at all.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .db import assert_policy_bound
from .ids import new_id
from .queue import enqueue

log = logging.getLogger(__name__)

#: Alert offsets in working days before the deadline (SPEC §12.4).
DEADLINE_ALERT_OFFSETS = (14, 7, 3, 1)

#: Evidence expiring within this window raises a vault alert (SPEC §7.2).
EVIDENCE_EXPIRY_WINDOW_DAYS = 30

#: An unacknowledged J-3 alert escalates to the Owner after this long (SPEC §12.4).
ESCALATION_AFTER = timedelta(hours=24)


def is_working_day(day: date) -> bool:
    """Monday-Friday. Public holidays are deliberately not modelled here.

    Treating a holiday as a working day makes an alert fire *earlier* than needed, which is
    safe. Treating it as a non-working day would push the alert later, which is not. Given the
    asymmetry, the naive rule is the correct default until a holiday calendar exists per
    country (FR/BE/LU differ).
    """
    return day.weekday() < 5


def working_days_before(deadline: datetime, working_days: int) -> datetime:
    """The instant `working_days` working days before `deadline`, keeping the time of day.

    Counting backwards day by day rather than arithmetically, because the number of weekends
    crossed depends on where you start.
    """
    remaining = working_days
    moment = deadline
    while remaining > 0:
        moment -= timedelta(days=1)
        if is_working_day(moment.date()):
            remaining -= 1
    return moment


@dataclass(frozen=True)
class DueAlert:
    org_id: str
    tender_id: str
    kind: str
    offset_days: int
    deadline_at: datetime
    title: str

    @property
    def notification_kind(self) -> str:
        return f"deadline.j{self.offset_days}"


def due_deadline_alerts(
    tenders: list[dict[str, Any]], *, now: datetime, already_sent: set[tuple[str, str]]
) -> list[DueAlert]:
    """Which deadline alerts are due now.

    Pure, because this is the logic that must not be wrong: it is unit-tested against weekend
    boundaries and against deadlines that have moved.

    Only the *nearest* unsent offset fires per tender. Otherwise a tender pursued five days
    before its deadline would immediately emit J-14, J-7 and J-3 together, which reads as noise
    and buries the one that matters.
    """
    due: list[DueAlert] = []
    for tender in tenders:
        deadline = tender.get("deadline_at")
        if deadline is None:
            continue
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        if deadline <= now:
            continue  # passed deadlines are a different conversation, not an alert

        for offset in sorted(DEADLINE_ALERT_OFFSETS, reverse=True):
            key = (tender["id"], f"deadline.j{offset}")
            if key in already_sent:
                continue
            if now >= working_days_before(deadline, offset):
                due.append(
                    DueAlert(
                        org_id=tender["org_id"],
                        tender_id=tender["id"],
                        kind="deadline",
                        offset_days=offset,
                        deadline_at=deadline,
                        title=tender.get("title") or "",
                    )
                )
                break
    return due


def set_org_context(connection: Connection, org_id: str | None) -> None:
    """Set (or clear) the tenant context for this transaction.

    Platform jobs iterate orgs and process each *inside* that org's context, so every row they
    read or write is still policy-checked. See the 20260813001000 migration for why this is done
    with per-org context rather than a BYPASSRLS role.

    This is Python's `withOrgContext`: the one place tenant context is established, which makes
    it the one place worth checking that setting it means anything at all. A role exempt from RLS
    turns every call below into a no-op that still looks like it worked.
    """
    assert_policy_bound(connection)
    connection.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": org_id or ""})


def all_org_ids(connection: Connection) -> list[str]:
    """Every org id, via the one SECURITY DEFINER helper that may see across tenants."""
    return [row[0] for row in connection.execute(text("SELECT org_id FROM app_all_org_ids()"))]


def run_deadline_alerts(connection: Connection, now: datetime | None = None) -> dict[str, Any]:
    """Raise every due deadline alert exactly once, across every org.

    Each org is processed inside its own tenant context, so a bug here cannot read or write
    another tenant's rows - the isolation still holds even for a job whose whole purpose is to
    sweep every tenant.
    """
    now = now or datetime.now(UTC)
    tenders: list[dict[str, Any]] = []
    already_sent: set[tuple[str, str]] = set()

    for org_id in all_org_ids(connection):
        set_org_context(connection, org_id)
        tenders.extend(
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT t.id, t.org_id, t.title, t.deadline_at
                    FROM tenders t
                    WHERE t.deadline_at IS NOT NULL
                      AND t.deadline_at > now()
                      AND t.stage NOT IN ('submitted', 'closed')
                    """
                )
            ).mappings()
        )
        already_sent.update(
            (row["tender_id"], row["kind"])
            for row in connection.execute(
                text(
                    """
                    SELECT payload->>'tender_id' AS tender_id, kind
                    FROM notifications
                    WHERE kind LIKE 'deadline.j%'
                    """
                )
            ).mappings()
            if row["tender_id"]
        )

    due = due_deadline_alerts(tenders, now=now, already_sent=already_sent)

    for alert in due:
        # Writing the notification needs that org's context, since `notifications` is org-scoped.
        set_org_context(connection, alert.org_id)
        # The notification row and its send job commit together: a crash between them would
        # otherwise either lose the alert or duplicate it.
        connection.execute(
            text(
                """
                INSERT INTO notifications (id, org_id, user_id, kind, payload, channels, created_at)
                VALUES (:id, :org_id, NULL, :kind, CAST(:payload AS jsonb), ARRAY['in_app','email'], now())
                """
            ),
            {
                "id": new_id("ntf"),
                "org_id": alert.org_id,
                "kind": alert.notification_kind,
                "payload": json.dumps(
                    {
                        "tender_id": alert.tender_id,
                        "title": alert.title,
                        "deadline_at": alert.deadline_at.isoformat(),
                        "offset_working_days": alert.offset_days,
                    }
                ),
            },
        )
        enqueue(
            connection,
            "notify.send",
            {"kind": alert.notification_kind, "tender_id": alert.tender_id, "org_id": alert.org_id},
            entity=f"{alert.tender_id}:{alert.notification_kind}",
        )
        log.info("deadline alert J-%d raised for tender %s", alert.offset_days, alert.tender_id)

    set_org_context(connection, None)
    return {"considered": len(tenders), "raised": len(due)}


def run_vault_freshness(connection: Connection, now: datetime | None = None) -> dict[str, Any]:
    """Flag evidence that has expired or expires soon (SPEC §7.2).

    Status is recomputed from `expires_at` rather than trusted, because an evidence row marked
    `valid` two months ago may not be valid today - and eligibility checks read this status.
    """
    now = now or datetime.now(UTC)
    horizon = (now + timedelta(days=EVIDENCE_EXPIRY_WINDOW_DAYS)).date()

    reclassified = 0
    counts: dict[str, int] = {}
    for org_id in all_org_ids(connection):
        set_org_context(connection, org_id)
        reclassified += _reclassify_evidence(connection, now, horizon)
        for status, count in _evidence_counts(connection).items():
            counts[status] = counts.get(status, 0) + count
    set_org_context(connection, None)
    return {"reclassified": reclassified, "by_status": counts}


def _reclassify_evidence(connection: Connection, now: datetime, horizon: date) -> int:
    return (
        connection.execute(
            text(
                """
            UPDATE evidences
            SET status = CASE
                    WHEN expires_at IS NULL THEN 'valid'
                    WHEN expires_at < CAST(:today AS date) THEN 'expired'
                    WHEN expires_at <= CAST(:horizon AS date) THEN 'expiring'
                    ELSE 'valid'
                END::"EvidenceStatus",
                updated_at = now()
            WHERE status IS DISTINCT FROM (
                CASE
                    WHEN expires_at IS NULL THEN 'valid'
                    WHEN expires_at < CAST(:today AS date) THEN 'expired'
                    WHEN expires_at <= CAST(:horizon AS date) THEN 'expiring'
                    ELSE 'valid'
                END::"EvidenceStatus"
            )
            """
            ),
            {"today": now.date(), "horizon": horizon},
        ).rowcount
        or 0
    )


def _evidence_counts(connection: Connection) -> dict[str, int]:
    rows = connection.execute(
        text("SELECT status::text AS status, count(*) AS n FROM evidences GROUP BY status")
    ).mappings()
    return {row["status"]: row["n"] for row in rows}


def escalate_unacknowledged(connection: Connection, now: datetime | None = None) -> dict[str, Any]:
    """Escalate an unacknowledged J-3 alert to the Owner (SPEC §12.4).

    The escalation exists because delivery is not the same as being read: an alert nobody
    acknowledged is, for a deadline, indistinguishable from one never sent.
    """
    now = now or datetime.now(UTC)
    escalated = 0
    for org_id in all_org_ids(connection):
        set_org_context(connection, org_id)
        escalated += _escalate_for_org(connection)
    set_org_context(connection, None)
    return {"escalated": escalated}


def _escalate_for_org(connection: Connection) -> int:
    stale = (
        connection.execute(
            text(
                """
            SELECT n.id, n.org_id, n.payload
            FROM notifications n
            WHERE n.kind = 'deadline.j3'
              AND n.ack_at IS NULL
              AND n.sent_at IS NOT NULL
              AND n.sent_at < now() - CAST(:interval AS interval)
              AND NOT EXISTS (
                    SELECT 1 FROM notifications e
                    WHERE e.kind = 'deadline.escalated'
                      AND e.payload->>'tender_id' = n.payload->>'tender_id'
              )
            """
            ),
            {"interval": f"{int(ESCALATION_AFTER.total_seconds())} seconds"},
        )
        .mappings()
        .all()
    )

    for row in stale:
        connection.execute(
            text(
                """
                INSERT INTO notifications (id, org_id, user_id, kind, payload, channels, created_at)
                VALUES (:id, :org_id, NULL, 'deadline.escalated', :payload, ARRAY['in_app','email'], now())
                """
            ),
            {"id": new_id("ntf"), "org_id": row["org_id"], "payload": json.dumps(row["payload"])},
        )
        enqueue(
            connection,
            "notify.send",
            {
                "kind": "deadline.escalated",
                "org_id": row["org_id"],
                "tender_id": row["payload"].get("tender_id"),
            },
            entity=f"{row['payload'].get('tender_id')}:escalated",
        )
    return len(stale)
