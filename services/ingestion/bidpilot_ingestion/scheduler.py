"""Scheduler: turns cron expressions into queued jobs (SPEC §17.1, §17.2).

Each source row carries a `schedule` (§6.1) and the freshness SLO in §6.9 requires a Tier-1
notice to be visible within 60 minutes of publication - which only happens if something
enqueues the fetch. That is this module.

Two deliberate properties:

  * **The scheduler enqueues; it never does work.** It is safe to run several of them, and a
    slow fetch cannot delay the next tick.
  * **Idempotency keys carry the time bucket**, so two schedulers racing on the same minute
    produce one job rather than two. This is what makes the redundant scheduler §16 demands
    ("alert scheduler MUST be redundant") safe to actually run redundantly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .queue import enqueue

log = logging.getLogger(__name__)

#: Periodic platform jobs, independent of any source. `(kind, every)`.
PLATFORM_SCHEDULE: tuple[tuple[str, timedelta], ...] = (
    # Source health has to run more often than the tightest silence budget (6h for Tier 1),
    # or a silent source is discovered hours after it went quiet.
    ("source.health", timedelta(minutes=15)),
    # Deadline alerts are the most critical path in the product (§16), so they are checked
    # every 15 minutes rather than daily: a J-1 alert computed once a day can be 23 hours late.
    ("notify.deadlines", timedelta(minutes=15)),
    ("vault.freshness", timedelta(hours=6)),
    # `digest.daily` is deliberately absent until the notifier exists: scheduling a stage with
    # no handler would dead-letter a job every hour and train operators to ignore the queue.
)


@dataclass(frozen=True)
class CronField:
    minutes: set[int] | None
    hours: set[int] | None
    days: set[int] | None
    months: set[int] | None
    weekdays: set[int] | None


def _parse_field(spec: str, low: int, high: int) -> set[int] | None:
    """Parse one cron field. `None` means "every value" (`*`)."""
    spec = spec.strip()
    if spec == "*":
        return None
    values: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = max(1, int(step_text))
        if part in ("*", ""):
            start, end = low, high
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(part)
        values.update(v for v in range(start, end + 1) if (v - start) % step == 0)
    return {v for v in values if low <= v <= high}


def parse_cron(expression: str) -> CronField:
    """Parse a 5-field cron expression.

    Deliberately a small parser rather than a dependency: the source registry only ever needs
    the standard five fields, and a cron library is a lot of surface area for that.
    """
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError(f"expected 5 cron fields, got {len(parts)} in {expression!r}")
    return CronField(
        minutes=_parse_field(parts[0], 0, 59),
        hours=_parse_field(parts[1], 0, 23),
        days=_parse_field(parts[2], 1, 31),
        months=_parse_field(parts[3], 1, 12),
        weekdays=_parse_field(parts[4], 0, 6),
    )


def cron_matches(expression: str, moment: datetime) -> bool:
    """Whether `moment` (UTC, minute resolution) satisfies the expression."""
    field = parse_cron(expression)
    # cron weekdays run Sunday=0; Python's weekday() runs Monday=0.
    weekday = (moment.weekday() + 1) % 7
    return (
        (field.minutes is None or moment.minute in field.minutes)
        and (field.hours is None or moment.hour in field.hours)
        and (field.days is None or moment.day in field.days)
        and (field.months is None or moment.month in field.months)
        and (field.weekdays is None or weekday in field.weekdays)
    )


def time_bucket(moment: datetime, every: timedelta) -> str:
    """A stable label for the interval `moment` falls in.

    Used as the idempotency version so concurrent schedulers collapse to one job per bucket.
    """
    seconds = int(every.total_seconds())
    epoch_seconds = int(moment.timestamp())
    return str(epoch_seconds // max(1, seconds))


def tick(connection: Connection, now: datetime | None = None) -> dict[str, int]:
    """Enqueue everything due at `now`. Safe to call every minute, and safe to run twice."""
    now = (now or datetime.now(UTC)).replace(second=0, microsecond=0)
    enqueued = 0

    # Per-source fetches, driven by the registry's cron expressions.
    sources = (
        connection.execute(text("SELECT code, schedule FROM sources WHERE enabled = true AND schedule <> ''"))
        .mappings()
        .all()
    )
    for source in sources:
        try:
            due = cron_matches(source["schedule"], now)
        except ValueError as exc:
            # A malformed schedule must not stop every other source from being fetched.
            log.warning("source %s has an invalid schedule %r: %s", source["code"], source["schedule"], exc)
            continue
        if not due:
            continue
        job_id = enqueue(
            connection,
            "source.fetch",
            {"source_code": source["code"]},
            entity=source["code"],
            # Minute-resolution bucket: one fetch per source per minute at most.
            version=now.strftime("%Y%m%d%H%M"),
        )
        if job_id:
            enqueued += 1

    # Platform-wide periodic jobs.
    for kind, every in PLATFORM_SCHEDULE:
        job_id = enqueue(connection, kind, {}, entity="platform", version=f"{kind}:{time_bucket(now, every)}")
        if job_id:
            enqueued += 1

    return {"enqueued": enqueued, "sources_considered": len(sources)}
