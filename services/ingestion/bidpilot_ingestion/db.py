"""Database access for the Python services (SPEC §17.1).

Prisma owns the schema; Python treats it as given and reads/writes through SQLAlchemy Core
against reflected tables. There is deliberately no second set of model definitions here: a
Python ORM layer mirroring the Prisma schema would be a second source of truth for the same
tables, and the two would drift.

Connection choice matters. The ingestion service writes market data (notices, sources, raw
payloads), which the application role is explicitly denied (§17.6, ADR-0007). So it connects
with `DATABASE_URL` - it is admin-side tooling, like the seeder, not a tenant.

That makes one property of `DATABASE_URL`'s role load-bearing, and `assert_policy_bound` below
is what enforces it: the role must still be *subject* to row-level security. Admin-side does
not mean unfiltered.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.engine import Connection, Engine

#: Query parameters Prisma understands and libpq does not. `schema` is translated into a
#: search_path option below; the rest are Prisma-side pooling hints with no libpq equivalent.
_PRISMA_ONLY_PARAMS = ("schema", "connection_limit", "pool_timeout", "pgbouncer", "connect_timeout")


def database_url() -> tuple[str, dict[str, Any]]:
    """Translate the shared `DATABASE_URL` into a SQLAlchemy URL plus connect arguments.

    One environment variable has to serve both Prisma and psycopg, and they disagree on the
    query string: `?schema=public` is meaningful to Prisma but psycopg rejects it as an unknown
    connection option. Rather than maintain a second variable that could drift out of sync, the
    Prisma-specific parameters are translated here - `schema` becomes a `search_path` option,
    which is what it actually means.
    """
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL must be set for the ingestion service")

    parsed = urlsplit(raw)
    params = dict(parse_qsl(parsed.query))
    schema = params.get("schema")
    remaining = {key: value for key, value in params.items() if key not in _PRISMA_ONLY_PARAMS}

    scheme = "postgresql+psycopg" if parsed.scheme in ("postgres", "postgresql") else parsed.scheme
    url = urlunsplit((scheme, parsed.netloc, parsed.path, urlencode(remaining), parsed.fragment))

    connect_args: dict[str, Any] = {}
    if schema:
        connect_args["options"] = f"-csearch_path={schema}"
    return url, connect_args


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url, connect_args = database_url()
    return create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        # Each pipeline stage is a short transaction; a small pool is plenty and keeps the
        # worker's footprint predictable.
        pool_size=5,
        max_overflow=5,
        future=True,
    )


@lru_cache(maxsize=1)
def get_metadata() -> MetaData:
    """Reflect the tables the ingestion service touches, once per process."""
    metadata = MetaData()
    metadata.reflect(
        bind=get_engine(),
        only=[
            "sources",
            "raw_notices",
            "notices",
            "notice_versions",
            "notice_documents",
            "tender_clusters",
            "matches",
            "watch_profiles",
            "company_profiles",
            "jobs",
            "events",
        ],
    )
    return metadata


def table(name: str) -> Table:
    return get_metadata().tables[name]


class RlsBypassError(RuntimeError):
    """The connected role can see past row-level security, so per-org work is not safe.

    This is fatal rather than a warning on purpose. A worker whose role bypasses RLS still
    *appears* to work: `set_config('app.org_id', ...)` succeeds, every query returns rows, every
    job reports success. What silently stops happening is the filtering - so a per-org loop
    reads and writes every tenant's rows under one org's context, and the first symptom is a
    tenant seeing another tenant's data. There is no failure mode worth trading for that.
    """


#: Engines whose role has been confirmed subject to RLS. A login role's attributes do not
#: change under a running worker, so one query per engine per process is enough - and the
#: check sits on a per-org loop, so it has to be close to free.
_POLICY_BOUND_ENGINES: set[Engine] = set()

_ROLE_BYPASSES_RLS_SQL = """
SELECT current_user AS role, (rolsuper OR rolbypassrls) AS bypasses
  FROM pg_roles
 WHERE rolname = current_user
"""


def assert_policy_bound(connection: Connection) -> None:
    """Fail loudly if this connection's role is exempt from row-level security.

    Two role attributes defeat RLS outright, and neither leaves a trace at query time:
    SUPERUSER and BYPASSRLS. Table ownership does *not*, because the schema uses `FORCE ROW
    LEVEL SECURITY` - which is why the owner connection this service uses is otherwise fine.

    The reason this needs enforcing rather than documenting: the forbidden configuration is also
    the default one. `POSTGRES_USER` in the postgres Docker image becomes the cluster's
    bootstrap superuser, so a stock `docker compose up` - and a stock CI service container -
    hands `DATABASE_URL` a role that quietly ignores every policy in the database.
    """
    engine = connection.engine
    if engine in _POLICY_BOUND_ENGINES:
        return

    row = connection.execute(text(_ROLE_BYPASSES_RLS_SQL)).mappings().first()
    if row is None:
        # current_user always has a pg_roles row; if it does not, something is wrong enough
        # that carrying on with per-org work is not defensible.
        raise RlsBypassError("cannot determine whether the connected role is subject to RLS")
    if row["bypasses"]:
        raise RlsBypassError(
            f"role {row['role']!r} is SUPERUSER or BYPASSRLS, so row-level security does not "
            "apply to it and per-org context would be silently ignored. Connect as a role that "
            "owns the schema but has neither attribute (see packages/db/sql/bootstrap_roles.sql)."
        )

    _POLICY_BOUND_ENGINES.add(engine)


@contextmanager
def transaction() -> Iterator[Connection]:
    """A single committed transaction.

    Pipeline stages are individually transactional so a failure in one stage cannot leave a
    half-written notice behind, and a retry re-runs cleanly (SPEC §17.3).
    """
    with get_engine().begin() as connection:
        yield connection


def fetch_one(connection: Connection, sql: str, **params: Any) -> dict[str, Any] | None:
    row = connection.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def fetch_all(connection: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(text(sql), params).mappings()]
