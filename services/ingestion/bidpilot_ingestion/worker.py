"""Queue worker (SPEC §17.2, §17.3).

A dispatch table over the pipeline stages plus a claim loop. Deliberately thin: all the
behaviour worth testing lives in `pipeline.py` as plain functions, and this file is the part
that would be hard to test and so should contain as little as possible.

Workers have no HTTP endpoints except health (§17.2) - they talk to the database, the queue
and object storage only.

    python -m bidpilot_ingestion.worker              # run until stopped
    python -m bidpilot_ingestion.worker --once       # drain what is runnable, then exit
    python -m bidpilot_ingestion.worker --kinds source.fetch
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from collections.abc import Callable
from typing import Any

from .alerts import escalate_unacknowledged, run_deadline_alerts, run_vault_freshness
from .db import transaction
from .health import run_source_health
from .notify import run_daily_digest, run_notify_send
from .pipeline import record_fetch_failure, run_dedupe, run_match, run_normalize, run_source_fetch
from .queue import Job, claim, complete, fail, queue_depth, reclaim_stale
from .scheduler import tick

log = logging.getLogger("bidpilot.worker")

#: Seconds to wait when the queue is empty. Long enough not to hammer Postgres, short enough
#: that a Tier-1 source stays within the 60-minute freshness SLO (§6.9).
IDLE_SLEEP_SECONDS = 5.0

#: How often the worker runs the scheduler tick. Cron has minute resolution, so 60s is exact.
SCHEDULER_TICK_SECONDS = 60.0

Handler = Callable[[Any, dict[str, Any]], dict[str, Any]]


def _handle_source_fetch(connection, payload: dict[str, Any]) -> dict[str, Any]:
    return run_source_fetch(connection, payload["source_code"], limit=payload.get("limit"))


def _handle_normalize(connection, payload: dict[str, Any]) -> dict[str, Any]:
    return run_normalize(connection, payload["raw_notice_id"])


def _handle_dedupe(connection, payload: dict[str, Any]) -> dict[str, Any]:
    return run_dedupe(connection, payload["notice_id"])


def _handle_match(connection, payload: dict[str, Any]) -> dict[str, Any]:
    return run_match(connection, payload["notice_id"])


def _handle_source_health(connection, payload: dict[str, Any]) -> dict[str, Any]:
    return run_source_health(connection)


def _handle_deadline_alerts(connection, payload: dict[str, Any]) -> dict[str, Any]:
    """Deadline alerts plus the escalation sweep, in one job.

    Together because they are two halves of the same guarantee: raising an alert nobody
    acknowledges is not, for a deadline, meaningfully different from not raising it (§12.4).
    """
    raised = run_deadline_alerts(connection)
    escalated = escalate_unacknowledged(connection)
    return {**raised, **escalated}


def _handle_vault_freshness(connection, payload: dict[str, Any]) -> dict[str, Any]:
    return run_vault_freshness(connection)


def _handle_scheduler_tick(connection, payload: dict[str, Any]) -> dict[str, Any]:
    return tick(connection)


def _handle_notify_send(connection, payload: dict[str, Any]) -> dict[str, Any]:
    """Deliver pending notifications.

    Driven by the `notifications` table rather than this job's payload, so a lost or duplicated
    job cannot cause a double send or a silent miss: the table is the record.
    """
    return run_notify_send(connection)


def _handle_daily_digest(connection, payload: dict[str, Any]) -> dict[str, Any]:
    return run_daily_digest(connection)


def _handle_unimplemented(connection, payload: dict[str, Any]) -> dict[str, Any]:
    """Declared-but-unbuilt stages fail loudly rather than silently succeeding.

    A job that reports success without doing anything is worse than one that dead-letters:
    the queue looks healthy while the work never happens.
    """
    raise NotImplementedError("this stage is not implemented yet; see docs/STATUS.md")


HANDLERS: dict[str, Handler] = {
    "source.fetch": _handle_source_fetch,
    "notice.normalize": _handle_normalize,
    "notice.dedupe": _handle_dedupe,
    "notice.match": _handle_match,
    "source.health": _handle_source_health,
    "notify.deadlines": _handle_deadline_alerts,
    "vault.freshness": _handle_vault_freshness,
    "notify.send": _handle_notify_send,
    "digest.daily": _handle_daily_digest,
}


def process_one(kinds: list[str] | None = None) -> Job | None:
    """Claim and run a single job. Returns the job, or None when nothing was runnable.

    The claim commits before the handler runs, so a crash mid-handler leaves the job
    `running` for `reclaim_stale` to recover rather than losing the claim entirely.
    """
    with transaction() as connection:
        jobs = claim(connection, kinds=kinds, limit=1)
    if not jobs:
        return None
    job = jobs[0]

    handler = HANDLERS.get(job.kind, _handle_unimplemented)
    try:
        with transaction() as connection:
            result = handler(connection, job.payload)
        with transaction() as connection:
            complete(connection, job.id)
        log.info("job %s (%s) done: %s", job.id, job.kind, result)
    except Exception as exc:
        log.warning("job %s (%s) failed: %s", job.id, job.kind, exc, exc_info=True)
        with transaction() as connection:
            state = fail(connection, job, f"{type(exc).__name__}: {exc}")
            # A source that cannot be fetched must be visibly degraded, not quietly retried
            # forever (§6.1): an aggregator that silently loses a source is lying to its users.
            if job.kind == "source.fetch" and state == "dead":
                record_fetch_failure(connection, job.payload.get("source_code", "?"), str(exc))
    return job


def run(kinds: list[str] | None = None, once: bool = False) -> int:
    stopping = False

    def _stop(signum, frame) -> None:
        nonlocal stopping
        log.info("signal %s received; finishing current job then exiting", signum)
        stopping = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    with transaction() as connection:
        reclaimed = reclaim_stale(connection)
        if reclaimed:
            log.warning("reclaimed %d stale running job(s)", reclaimed)

    processed = 0
    last_tick = 0.0
    while not stopping:
        # The worker doubles as the scheduler. One process is easier to run in development,
        # and in production several workers can run - the tick is idempotent per time bucket,
        # which is what makes the redundant scheduler §16 requires safe.
        if not kinds and time.monotonic() - last_tick > SCHEDULER_TICK_SECONDS:
            with transaction() as connection:
                tick(connection)
            last_tick = time.monotonic()

        job = process_one(kinds)
        if job is None:
            if once:
                break
            time.sleep(IDLE_SLEEP_SECONDS)
            continue
        processed += 1

    with transaction() as connection:
        log.info("processed %d job(s); queue depth now %s", processed, queue_depth(connection))
    return processed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kinds", nargs="*", help="restrict to these job kinds")
    parser.add_argument("--once", action="store_true", help="drain runnable jobs then exit")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    run(kinds=args.kinds or None, once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
