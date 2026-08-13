"""Source health monitoring (SPEC §6.1).

The spec is blunt about why this exists: *"This monitoring is not optional - an aggregator
that silently loses a source is lying to its customers."* If BOAMP stops responding and
nobody notices, every user sees a quiet feed and concludes there are no tenders.

Rules, straight from §6.1:
  * a Tier-1 source silent > 6h, or Tier-2 silent > 24h, pages the operator;
  * a parser whose extraction yield drops > 40% week-over-week is flagged `degraded`
    automatically.

The yield rule is the subtle one. A source can answer with HTTP 200 all day while its HTML
changed and the parser now extracts nothing. Freshness checks pass; the feed is empty. So
volume is compared against the source's own recent history rather than any absolute figure -
BOAMP's normal day and Luxembourg's normal day differ by three orders of magnitude.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .ids import new_id

log = logging.getLogger(__name__)

#: Silence budgets per tier (§6.1). Tier 3/4 are long-tail and signal sources, polled daily
#: at most, so a day of silence is normal for them.
SILENCE_BUDGET: dict[str, timedelta] = {
    "official_api": timedelta(hours=6),
    "platform": timedelta(hours=24),
    "long_tail": timedelta(hours=72),
    "signals": timedelta(hours=72),
}

#: Week-over-week volume drop that flags a parser as degraded (§6.1).
YIELD_DROP_THRESHOLD = 0.4

#: Below this weekly volume the ratio is statistical noise - going from 2 notices to 1 is not
#: a parser regression, and treating it as one would train operators to ignore the alert.
MIN_VOLUME_FOR_YIELD_CHECK = 20


@dataclass(frozen=True)
class SourceHealth:
    code: str
    tier: str
    health: str
    reason: str
    notices_7d: int
    notices_prev_7d: int
    silent_for: timedelta | None

    @property
    def should_page(self) -> bool:
        """Tier-1 and Tier-2 silence pages an operator; a yield drop is a ticket, not a page."""
        return self.health == "silent" and self.tier in ("official_api", "platform")


def evaluate(
    *,
    code: str,
    tier: str,
    enabled: bool,
    silent_for: timedelta | None,
    notices_7d: int,
    notices_prev_7d: int,
    polled: bool = True,
) -> SourceHealth:
    """Decide a source's health. Pure, so the thresholds are unit-testable without a database."""
    if not enabled:
        return SourceHealth(
            code, tier, "disabled", "source disabled", notices_7d, notices_prev_7d, silent_for
        )

    if not polled:
        # Push sources - the email connector (§6.5), manual import - are never fetched on a
        # schedule, so "no successful run" says nothing about their health. Applying a silence
        # budget to them would put a permanent false alarm on the coverage page, which is the
        # fastest way to teach operators to ignore it.
        return SourceHealth(
            code, tier, "green", "push source; not polled", notices_7d, notices_prev_7d, silent_for
        )

    budget = SILENCE_BUDGET.get(tier, timedelta(hours=24))
    if silent_for is None:
        return SourceHealth(code, tier, "silent", "never ran", notices_7d, notices_prev_7d, None)
    if silent_for > budget:
        hours = silent_for.total_seconds() / 3600
        return SourceHealth(
            code,
            tier,
            "silent",
            f"no successful run for {hours:.1f}h (budget {budget.total_seconds() / 3600:.0f}h)",
            notices_7d,
            notices_prev_7d,
            silent_for,
        )

    # Yield check: only meaningful with enough history to compare against.
    if notices_prev_7d >= MIN_VOLUME_FOR_YIELD_CHECK:
        drop = (notices_prev_7d - notices_7d) / notices_prev_7d
        if drop > YIELD_DROP_THRESHOLD:
            return SourceHealth(
                code,
                tier,
                "degraded",
                f"extraction yield down {drop:.0%} week-over-week "
                f"({notices_prev_7d} → {notices_7d}); parser may be broken",
                notices_7d,
                notices_prev_7d,
                silent_for,
            )

    return SourceHealth(
        code, tier, "green", "responding, yield stable", notices_7d, notices_prev_7d, silent_for
    )


def run_source_health(connection: Connection) -> dict[str, Any]:
    """Evaluate every source, persist the verdict, and record an event for anything not green.

    The event log is what the ops channel and the public status page (§6.9 "coverage
    transparency") read from - the moat is only worth something if users can see it working.
    """
    rows = (
        connection.execute(
            text(
                """
            SELECT s.code, s.tier::text AS tier, s.enabled, s.health::text AS health,
                   s.schedule,
                   EXTRACT(EPOCH FROM (now() - s.last_success_at)) AS silent_seconds,
                   (SELECT count(*) FROM raw_notices r
                     WHERE r.source_id = s.id AND r.fetched_at > now() - interval '7 days') AS notices_7d,
                   (SELECT count(*) FROM raw_notices r
                     WHERE r.source_id = s.id
                       AND r.fetched_at > now() - interval '14 days'
                       AND r.fetched_at <= now() - interval '7 days') AS notices_prev_7d
            FROM sources s
            """
            )
        )
        .mappings()
        .all()
    )

    verdicts: list[SourceHealth] = []
    for row in rows:
        silent_seconds = row["silent_seconds"]
        verdict = evaluate(
            code=row["code"],
            tier=row["tier"],
            enabled=row["enabled"],
            silent_for=timedelta(seconds=float(silent_seconds)) if silent_seconds is not None else None,
            notices_7d=int(row["notices_7d"] or 0),
            notices_prev_7d=int(row["notices_prev_7d"] or 0),
            # A source with no cron is push-driven, not neglected.
            polled=bool((row["schedule"] or "").strip()),
        )
        verdicts.append(verdict)

        if verdict.health != row["health"]:
            connection.execute(
                text(
                    """
                    UPDATE sources SET health = CAST(:health AS "SourceHealth"), updated_at = now()
                    WHERE code = :code
                    """
                ),
                {"code": verdict.code, "health": verdict.health},
            )
            # Only transitions are recorded. A source that has been silent for a week should
            # not generate an event every run - that is how operators learn to ignore alerts.
            if verdict.health in ("silent", "degraded"):
                connection.execute(
                    text(
                        """
                        INSERT INTO events (id, org_id, actor, kind, entity, payload, created_at)
                        VALUES (:id, NULL, 'ingestion', :kind, :code, CAST(:payload AS jsonb), now())
                        """
                    ),
                    {
                        "id": new_id("evt"),
                        "kind": f"source.{verdict.health}",
                        "code": verdict.code,
                        "payload": json.dumps(
                            {
                                "reason": verdict.reason,
                                "tier": verdict.tier,
                                "notices_7d": verdict.notices_7d,
                                "notices_prev_7d": verdict.notices_prev_7d,
                                "page_operator": verdict.should_page,
                            }
                        ),
                    },
                )
                log.warning("source %s is %s: %s", verdict.code, verdict.health, verdict.reason)

    return {
        "checked": len(verdicts),
        "green": sum(1 for v in verdicts if v.health == "green"),
        "degraded": sum(1 for v in verdicts if v.health == "degraded"),
        "silent": sum(1 for v in verdicts if v.health == "silent"),
        "disabled": sum(1 for v in verdicts if v.health == "disabled"),
        "paging": [v.code for v in verdicts if v.should_page],
    }


def coverage_report(connection: Connection) -> list[dict[str, Any]]:
    """Data for the public status page (§6.9): connected sources and their freshness.

    Public, because "coverage transparency (MUST)" is how the moat becomes visible trust
    rather than a claim.
    """
    rows = (
        connection.execute(
            text(
                """
            SELECT s.code, s.country, s.tier::text AS tier, s.kind::text AS kind,
                   s.health::text AS health, s.enabled, s.last_success_at, s.notices_7d,
                   s.legal->>'basis' AS legal_basis
            FROM sources s
            ORDER BY s.tier, s.code
            """
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]
