"""Postgres-backed job queue (SPEC §17.1, §17.3).

Chosen over Redis/BullMQ for two reasons the spec names: it is language-agnostic, so the
TypeScript notifier and the Python workers consume the same queue, and enqueueing is
transactional with the business write that caused it. That second property is what stops the
classic "row committed, job lost" and "job enqueued, row rolled back" bugs.

Claiming uses `FOR UPDATE SKIP LOCKED`, so many workers can run concurrently without
coordinating and without ever handing the same job to two of them.

Two invariants that matter operationally:
  * **Idempotency keys.** `kind + entity + version` (SPEC §17.3), unique in the schema. So
    re-enqueueing the same work is a no-op rather than duplicate processing.
  * **Payloads are ids, never blobs.** A queue row must stay small; the worker re-reads what
    it needs. This also means a payload cannot go stale relative to the row it refers to.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .db import transaction
from .ids import new_id

#: Job kinds (SPEC §17.3). Declared so a typo becomes an error rather than a job nothing
#: will ever claim.
JOB_KINDS = frozenset(
    {
        "source.fetch",
        "notice.normalize",
        "notice.dedupe",
        "notice.enrich",
        "notice.match",
        "docs.fetch",
        "docs.extract",
        "analysis.admin",
        "analysis.requirements",
        "analysis.redflags",
        "gonogo.compute",
        "gen.outline",
        "gen.section",
        "gen.deck",
        "gen.forms",
        "notify.send",
        "digest.daily",
        "vault.freshness",
        "awards.ingest",
        "renewals.compute",
        "source.health",
        "notify.deadlines",
    }
)

#: Retry backoff per attempt, capped. Deliberately coarse: a source that is down stays down
#: for minutes, not milliseconds, and hammering it is both rude and pointless (§16).
RETRY_BACKOFF_SECONDS = (30, 120, 600, 1800, 3600)

DEFAULT_MAX_ATTEMPTS = 5

#: A job whose worker died mid-run stays `running` forever unless something reclaims it.
STALE_RUNNING_AFTER = timedelta(minutes=30)


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    idempotency_key: str

    @property
    def is_last_attempt(self) -> bool:
        return self.attempts + 1 >= self.max_attempts


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def idempotency_key(kind: str, entity: str, version: str | int = 1) -> str:
    """`kind + entity_id + version` (SPEC §17.3)."""
    return f"{kind}:{entity}:{version}"


def enqueue(
    connection: Connection,
    kind: str,
    payload: dict[str, Any],
    *,
    entity: str,
    version: str | int = 1,
    run_at: datetime | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> str | None:
    """Enqueue a job inside the caller's transaction.

    Takes a `Connection` rather than opening its own, so the job and the rows that justify it
    commit together or not at all.

    Returns the job id, or None when an identical job already exists - a no-op, not an error,
    because "this work is already scheduled" is the expected outcome of a replay.
    """
    if kind not in JOB_KINDS:
        raise ValueError(f"unknown job kind {kind!r}; declare it in JOB_KINDS")

    key = idempotency_key(kind, entity, version)
    job_id = new_id("job")
    result = connection.execute(
        text(
            """
            INSERT INTO jobs (id, kind, payload, state, run_at, attempts, max_attempts,
                              idempotency_key, created_at, updated_at)
            VALUES (:id, :kind, CAST(:payload AS jsonb), 'queued', :run_at, 0, :max_attempts,
                    :key, now(), now())
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """
        ),
        {
            "id": job_id,
            "kind": kind,
            "payload": json.dumps(payload),
            "run_at": run_at or datetime.now(UTC),
            "max_attempts": max_attempts,
            "key": key,
        },
    ).first()
    return result[0] if result else None


def claim(connection: Connection, kinds: list[str] | None = None, limit: int = 1) -> list[Job]:
    """Claim runnable jobs with `FOR UPDATE SKIP LOCKED`.

    SKIP LOCKED is what lets N workers share one queue with no coordination: a row another
    worker is already claiming is skipped rather than waited on.
    """
    filter_sql = "AND kind = ANY(:kinds)" if kinds else ""
    rows = connection.execute(
        text(
            f"""
            WITH claimed AS (
                SELECT id FROM jobs
                WHERE state = 'queued' AND run_at <= now() {filter_sql}
                ORDER BY run_at
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            UPDATE jobs SET state = 'running', locked_by = :worker, locked_at = now(),
                            attempts = attempts + 1, updated_at = now()
            WHERE id IN (SELECT id FROM claimed)
            RETURNING id, kind, payload, attempts, max_attempts, idempotency_key
            """
        ),
        {"kinds": kinds, "limit": limit, "worker": worker_name()}
        if kinds
        else {"limit": limit, "worker": worker_name()},
    ).mappings()

    return [
        Job(
            id=row["id"],
            kind=row["kind"],
            payload=row["payload"] or {},
            # `attempts` was incremented by the claim, so the count already includes this run.
            attempts=row["attempts"] - 1,
            max_attempts=row["max_attempts"],
            idempotency_key=row["idempotency_key"],
        )
        for row in rows
    ]


def complete(connection: Connection, job_id: str) -> None:
    connection.execute(
        text("UPDATE jobs SET state = 'done', locked_by = NULL, updated_at = now() WHERE id = :id"),
        {"id": job_id},
    )


def fail(connection: Connection, job: Job, error: str) -> str:
    """Record a failure: retry with backoff, or move to the dead-letter state.

    `dead` rather than deletion, because §17.3 requires an ops UI that can replay - and a
    job that vanished is a job nobody can diagnose.
    """
    attempts = job.attempts + 1
    if attempts >= job.max_attempts:
        state, run_at = "dead", None
    else:
        state = "queued"
        delay = RETRY_BACKOFF_SECONDS[min(attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
        run_at = datetime.now(UTC) + timedelta(seconds=delay)

    connection.execute(
        text(
            """
            UPDATE jobs
            SET state = :state,
                run_at = COALESCE(:run_at, run_at),
                locked_by = NULL,
                -- Truncated: a multi-megabyte traceback in a queue row is a liability, and
                -- the full detail belongs in the logs.
                last_error = left(:error, 2000),
                updated_at = now()
            WHERE id = :id
            """
        ),
        {"id": job.id, "state": state, "run_at": run_at, "error": error},
    )
    return state


def reclaim_stale(connection: Connection, older_than: timedelta = STALE_RUNNING_AFTER) -> int:
    """Return jobs whose worker died back to the queue.

    Without this a crashed worker silently strands its in-flight jobs, which for a deadline
    alert is the worst failure the product has (§16).
    """
    result = connection.execute(
        text(
            """
            UPDATE jobs
            SET state = 'queued', locked_by = NULL, updated_at = now(),
                last_error = COALESCE(last_error, 'reclaimed after worker timeout')
            WHERE state = 'running' AND locked_at < now() - CAST(:interval AS interval)
            """
        ),
        {"interval": f"{int(older_than.total_seconds())} seconds"},
    )
    return result.rowcount or 0


def queue_depth(connection: Connection) -> dict[str, int]:
    """Depth by state - the metric §16 wants alerting on ("queue stalled > 15 min")."""
    rows = connection.execute(text("SELECT state, count(*) AS n FROM jobs GROUP BY state")).mappings()
    return {row["state"]: row["n"] for row in rows}


def enqueue_standalone(
    kind: str, payload: dict[str, Any], *, entity: str, version: str | int = 1
) -> str | None:
    """Convenience wrapper opening its own transaction, for CLI use and scheduling."""
    with transaction() as connection:
        return enqueue(connection, kind, payload, entity=entity, version=version)
