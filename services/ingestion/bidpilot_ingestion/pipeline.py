"""The ingestion pipeline (SPEC §6.2, §17.3).

`fetch → store raw → normalize → dedupe → enrich → match`, each stage a separate queue job so
any of them can be restarted independently and none of them can half-complete.

The stages are written as plain functions taking a connection, so they are testable against a
real database without a worker loop, and the worker is only a dispatch table.

Three behaviours are load-bearing:

  * **Raw payloads are stored before anything interprets them** (§6.2). When a parser improves
    we re-normalise history instead of losing it, and any notice can be traced back to exactly
    what the source sent (§6.10: "Raw payload of any notice is retrievable by ID").
  * **A changed content hash is an amendment, not an update** (§6.7). It creates a
    `notice_version` with a diff and flags the notice `amended`, which is what triggers
    re-analysis (§9.4) and alerts the team (§12.4). Silently overwriting would lose the fact
    that the buyer moved a deadline.
  * **Clustering runs against existing rows, not just the current batch** (§6.7 re-entrancy).
    A duplicate arriving days later joins its cluster rather than creating a second card.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .adapters import Cursor, LegalBasis, SourceConfig, get_adapter
from .adapters.base import BaseAdapter
from .canonical import CanonicalNotice, NoticeStatus, RawNotice
from .dedupe import same_tender
from .ids import new_id
from .matching import CompanyProfile, FilterOutcome, WatchFilters, apply_hard_filters, score_notice
from .queue import enqueue

log = logging.getLogger(__name__)

#: Candidate window for clustering. A duplicate publication of the same tender lands within
#: days, not months, so this bounds the comparison set without missing real duplicates.
CLUSTER_CANDIDATE_WINDOW = timedelta(days=45)

#: Cap on candidates compared per notice. Above this the SQL pre-filter is too loose to be
#: useful and the right fix is a better pre-filter, not more comparisons.
MAX_CLUSTER_CANDIDATES = 50


# ---------------------------------------------------------------- source registry


@dataclass(frozen=True)
class SourceRow:
    id: str
    code: str
    country: str
    adapter: str
    kind: str
    tier: str
    config: dict[str, Any]
    legal: dict[str, Any]
    cursor: dict[str, Any]
    enabled: bool


def load_source(connection: Connection, code: str) -> SourceRow:
    row = (
        connection.execute(
            text(
                """
            SELECT id, code, country, adapter, kind, tier::text AS tier, config, legal, cursor, enabled
            FROM sources WHERE code = :code
            """
            ),
            {"code": code},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise LookupError(f"unknown source {code!r}")
    return SourceRow(
        id=row["id"],
        code=row["code"],
        country=row["country"],
        adapter=row["adapter"],
        kind=row["kind"],
        tier=row["tier"],
        config=row["config"] or {},
        legal=row["legal"] or {},
        cursor=row["cursor"] or {},
        enabled=row["enabled"],
    )


def build_adapter(source: SourceRow) -> BaseAdapter:
    """Instantiate the adapter family named by the row, configured for this portal.

    This is the mechanism behind §6.4: one atexo adapter serves 30+ portals because the
    portal-specific part is a config row, not code.
    """
    legal = source.legal or {}
    basis = legal.get("basis")
    if not basis:
        # §24.7: the registry field is mandatory and the adapter refuses to run without it.
        # Raised here with the source code, because the operator needs to know *which* row.
        raise ValueError(f"source {source.code!r} has no legal basis recorded (SPEC §24.7)")

    adapter_cls = get_adapter(source.adapter)
    config = SourceConfig(
        code=source.code,
        country=source.country,
        tier={"official_api": 1, "platform": 2, "long_tail": 3, "signals": 4}.get(source.tier, 3),
        kind=source.kind,
        legal=LegalBasis(basis=basis, notes=legal.get("notes", ""), reviewed_at=legal.get("reviewed_at")),
        options=source.config,
    )
    return adapter_cls(config)


# ---------------------------------------------------------------- stage: fetch


def store_raw(connection: Connection, source: SourceRow, raw: RawNotice) -> str | None:
    """Persist a raw payload untouched.

    Returns the row id, or None when this exact payload was already stored - which is the
    normal outcome of an incremental fetch overlapping its previous window, and the reason
    the unique key is (source, external_id, content_hash).
    """
    row_id = new_id("raw")
    result = connection.execute(
        text(
            """
            INSERT INTO raw_notices (id, source_id, external_id, fetched_at, payload, content_hash)
            VALUES (:id, :source_id, :external_id, :fetched_at, CAST(:payload AS jsonb), :hash)
            ON CONFLICT (source_id, external_id, content_hash) DO NOTHING
            RETURNING id
            """
        ),
        {
            "id": row_id,
            "source_id": source.id,
            "external_id": raw.external_id,
            "fetched_at": raw.fetched_at or datetime.now(UTC),
            "payload": json.dumps(raw.payload, default=str),
            "hash": raw.content_hash or "",
        },
    ).first()
    return result[0] if result else None


def run_source_fetch(connection: Connection, source_code: str, *, limit: int | None = None) -> dict[str, int]:
    """Stage 1: fetch from a source, store raw payloads, enqueue normalisation.

    Enqueueing happens in the same transaction as the raw insert, so a stored payload always
    has a normalisation job and a normalisation job always has its payload.
    """
    source = load_source(connection, source_code)
    if not source.enabled:
        # Not an error: disabling a source is the kill switch §6.2 requires, and a disabled
        # Tier-2 source pending legal review is the expected steady state.
        log.info("source %s is disabled; skipping fetch", source_code)
        return {"fetched": 0, "stored": 0, "skipped_disabled": 1}

    adapter = build_adapter(source)
    since = source.cursor.get("since")
    cursor = Cursor(since=datetime.fromisoformat(since) if since else None)

    fetched = stored = 0
    latest_seen: datetime | None = None
    for raw in adapter.fetch_since(cursor):
        fetched += 1
        raw_id = store_raw(connection, source, raw)
        if raw_id is not None:
            stored += 1
            enqueue(
                connection,
                "notice.normalize",
                {"raw_notice_id": raw_id, "source_code": source.code},
                entity=raw_id,
            )
        if raw.fetched_at and (latest_seen is None or raw.fetched_at > latest_seen):
            latest_seen = raw.fetched_at
        if limit is not None and fetched >= limit:
            break

    # The watermark only advances on a successful pass, so a crash mid-window re-reads rather
    # than skipping notices. Re-reading is cheap (the raw unique key deduplicates); skipping
    # loses a tender.
    connection.execute(
        text(
            """
            UPDATE sources
            SET cursor = CAST(:cursor AS jsonb), last_run_at = now(), last_success_at = now(),
                notices_7d = (
                    SELECT count(*) FROM raw_notices
                    WHERE source_id = :source_id AND fetched_at > now() - interval '7 days'
                ),
                health = 'green', updated_at = now()
            WHERE id = :source_id
            """
        ),
        {
            "source_id": source.id,
            "cursor": json.dumps({"since": (latest_seen or datetime.now(UTC)).isoformat()}),
        },
    )
    return {"fetched": fetched, "stored": stored}


def record_fetch_failure(connection: Connection, source_code: str, error: str) -> None:
    """Mark a source degraded. §6.1 is explicit that silent source loss is unacceptable."""
    connection.execute(
        text(
            """
            UPDATE sources
            SET last_run_at = now(), health = 'degraded', updated_at = now()
            WHERE code = :code
            """
        ),
        {"code": source_code},
    )
    connection.execute(
        text(
            """
            INSERT INTO events (id, org_id, actor, kind, entity, payload, created_at)
            VALUES (:id, NULL, 'ingestion', 'source.fetch_failed', :code,
                    CAST(:payload AS jsonb), now())
            """
        ),
        {"id": new_id("evt"), "code": source_code, "payload": json.dumps({"error": error[:1000]})},
    )


# ---------------------------------------------------------------- stage: normalize


def run_normalize(connection: Connection, raw_notice_id: str) -> dict[str, Any]:
    """Stage 2: map a stored raw payload to the canonical schema and upsert the notice."""
    row = (
        connection.execute(
            text(
                """
            SELECT r.id, r.external_id, r.payload, r.content_hash, r.fetched_at, s.code AS source_code
            FROM raw_notices r JOIN sources s ON s.id = r.source_id
            WHERE r.id = :id
            """
            ),
            {"id": raw_notice_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise LookupError(f"raw notice {raw_notice_id!r} not found")

    source = load_source(connection, row["source_code"])
    adapter = build_adapter(source)
    canonical = adapter.normalize(
        RawNotice(
            source=source.code,
            external_id=row["external_id"],
            payload=row["payload"],
            content_hash=row["content_hash"],
            fetched_at=row["fetched_at"],
        )
    )

    notice_id, amended = upsert_notice(connection, canonical, row["content_hash"])
    enqueue(connection, "notice.dedupe", {"notice_id": notice_id}, entity=notice_id)
    if amended:
        # §9.4: an amendment re-runs analysis and notifies assignees. The alert is what the
        # user actually needs; the re-analysis is what makes it accurate.
        enqueue(
            connection,
            "notify.send",
            {"kind": "tender.amended", "notice_id": notice_id},
            entity=notice_id,
            version=canonical.version,
        )
    return {"notice_id": notice_id, "amended": amended}


def notice_row_id(source_code: str, external_id: str) -> str:
    """Deterministic id from the source reference.

    Deterministic rather than random so re-normalising a payload updates the same row instead
    of creating a duplicate - the property that makes the whole pipeline safely re-runnable.
    """
    slug = f"{source_code}_{external_id}".replace("/", "_").replace(" ", "_")
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in slug)
    return f"ntc_{cleaned}"[:60]


def upsert_notice(
    connection: Connection, canonical: CanonicalNotice, content_hash: str | None
) -> tuple[str, bool]:
    """Insert or update a canonical notice; returns (id, was_amended).

    A changed content hash means the *source* changed the notice, which is an amendment: a new
    `notice_version` row with a diff, status `amended`, and the version counter incremented.
    """
    ref = canonical.source_refs[0]
    notice_id = notice_row_id(ref.source, ref.external_id)

    existing = (
        connection.execute(
            text("SELECT id, version, provenance, status::text AS status FROM notices WHERE id = :id"),
            {"id": notice_id},
        )
        .mappings()
        .first()
    )

    previous_hash = (existing["provenance"] or {}).get("raw_content_hash") if existing else None
    amended = bool(existing and previous_hash and content_hash and previous_hash != content_hash)
    version = (existing["version"] + 1) if amended else (existing["version"] if existing else 1)

    payload = {
        "id": notice_id,
        "source_refs": json.dumps([ref.model_dump(mode="json") for ref in canonical.source_refs]),
        "status": NoticeStatus.AMENDED.value if amended else canonical.status.value,
        "notice_type": canonical.notice_type.value,
        "country": canonical.country,
        "language": canonical.language,
        "buyer_name": canonical.buyer.name,
        "buyer_siren": canonical.buyer.siren_effective,
        "title": canonical.title,
        "description": canonical.description,
        "cpv": canonical.cpv,
        "nuts": canonical.nuts,
        "procedure_type": canonical.procedure.type.value if canonical.procedure.type else None,
        "amount_est": canonical.amounts.estimated_total,
        "currency": canonical.amounts.currency,
        "published_at": canonical.dates.published_at,
        "deadline_at": canonical.dates.deadline_at,
        "questions_deadline_at": canonical.dates.questions_deadline_at,
        "lots": json.dumps([lot.model_dump(mode="json") for lot in canonical.lots]),
        "urls": json.dumps(canonical.urls.model_dump(mode="json")),
        "award": json.dumps(canonical.award.model_dump(mode="json")) if canonical.award else None,
        "provenance": json.dumps(canonical.provenance.model_dump(mode="json")),
        "requires_account": canonical.requires_account_for_docs,
        "version": version,
    }

    connection.execute(
        text(
            """
            INSERT INTO notices (id, source_refs, status, notice_type, country, language,
                buyer_name, buyer_siren, title, description, cpv, nuts, procedure_type,
                amount_est, currency, published_at, deadline_at, questions_deadline_at,
                lots, urls, award, provenance, requires_account_for_docs, version,
                docs_available, created_at, updated_at)
            VALUES (:id, CAST(:source_refs AS jsonb), CAST(:status AS "NoticeStatus"),
                CAST(:notice_type AS "NoticeType"), :country, :language, :buyer_name,
                :buyer_siren, :title, :description, :cpv, :nuts, :procedure_type, :amount_est,
                :currency, :published_at, :deadline_at, :questions_deadline_at,
                CAST(:lots AS jsonb), CAST(:urls AS jsonb), CAST(:award AS jsonb),
                CAST(:provenance AS jsonb), :requires_account, :version, false, now(), now())
            ON CONFLICT (id) DO UPDATE SET
                source_refs = EXCLUDED.source_refs, status = EXCLUDED.status,
                notice_type = EXCLUDED.notice_type, language = EXCLUDED.language,
                buyer_name = EXCLUDED.buyer_name, buyer_siren = EXCLUDED.buyer_siren,
                title = EXCLUDED.title, description = EXCLUDED.description,
                cpv = EXCLUDED.cpv, nuts = EXCLUDED.nuts,
                procedure_type = EXCLUDED.procedure_type, amount_est = EXCLUDED.amount_est,
                currency = EXCLUDED.currency, published_at = EXCLUDED.published_at,
                deadline_at = EXCLUDED.deadline_at,
                questions_deadline_at = EXCLUDED.questions_deadline_at,
                lots = EXCLUDED.lots, urls = EXCLUDED.urls, award = EXCLUDED.award,
                provenance = EXCLUDED.provenance,
                requires_account_for_docs = EXCLUDED.requires_account_for_docs,
                version = EXCLUDED.version, updated_at = now()
            """
        ),
        payload,
    )

    if amended:
        connection.execute(
            text(
                """
                INSERT INTO notice_versions (id, notice_id, version, payload, diff, created_at)
                VALUES (:id, :notice_id, :version, CAST(:payload AS jsonb), CAST(:diff AS jsonb), now())
                ON CONFLICT (notice_id, version) DO NOTHING
                """
            ),
            {
                "id": new_id("nvr"),
                "notice_id": notice_id,
                "version": version,
                "payload": json.dumps(canonical.model_dump(mode="json")),
                "diff": json.dumps({"previous_hash": previous_hash, "new_hash": content_hash}),
            },
        )

    return notice_id, amended


# ---------------------------------------------------------------- stage: dedupe


def run_dedupe(connection: Connection, notice_id: str) -> dict[str, Any]:
    """Stage 3: place the notice in a cluster, merging with an existing one when it matches.

    Candidates are narrowed in SQL (same country, same notice type, nearby deadline or same
    buyer) and the decision is made by `dedupe.same_tender` - the same logic the unit tests
    pin down, so a clustering decision made here is reproducible offline.
    """
    notice = _load_canonical(connection, notice_id)
    if notice is None:
        raise LookupError(f"notice {notice_id!r} not found")

    candidates = (
        connection.execute(
            text(
                """
            SELECT id FROM notices
            WHERE id <> :id
              AND country = :country
              AND notice_type = CAST(:notice_type AS "NoticeType")
              AND cluster_id IS NOT NULL
              AND (
                    (CAST(:deadline AS timestamptz) IS NOT NULL
                      AND deadline_at BETWEEN CAST(:deadline AS timestamptz) - interval '24 hours'
                                          AND CAST(:deadline AS timestamptz) + interval '24 hours')
                 OR (CAST(:siren AS text) IS NOT NULL AND buyer_siren = CAST(:siren AS text))
              )
              AND created_at > now() - CAST(:window AS interval)
            ORDER BY created_at DESC
            LIMIT :limit
            """
            ),
            {
                "id": notice_id,
                "country": notice.country,
                "notice_type": notice.notice_type.value,
                "deadline": notice.dates.deadline_at,
                "siren": notice.buyer.siren_effective,
                "window": f"{int(CLUSTER_CANDIDATE_WINDOW.total_seconds())} seconds",
                "limit": MAX_CLUSTER_CANDIDATES,
            },
        )
        .mappings()
        .all()
    )

    for candidate in candidates:
        other = _load_canonical(connection, candidate["id"])
        if other is None:
            continue
        verdict = same_tender(other, notice)
        if not verdict.matched:
            continue
        cluster_id = connection.execute(
            text("SELECT cluster_id FROM notices WHERE id = :id"), {"id": candidate["id"]}
        ).scalar()
        if not cluster_id:
            continue
        connection.execute(
            text("UPDATE notices SET cluster_id = :cluster, updated_at = now() WHERE id = :id"),
            {"cluster": cluster_id, "id": notice_id},
        )
        connection.execute(
            text(
                """
                UPDATE tender_clusters
                SET merged_from = merged_from || CAST(:entry AS jsonb),
                    confidence = LEAST(confidence, :confidence), updated_at = now()
                WHERE id = :cluster
                """
            ),
            {
                "cluster": cluster_id,
                "confidence": verdict.confidence or 1.0,
                "entry": json.dumps(
                    [
                        {
                            "notice_id": notice_id,
                            "reason": verdict.reason.value if verdict.reason else "unknown",
                        }
                    ]
                ),
            },
        )
        enqueue(connection, "notice.match", {"notice_id": notice_id}, entity=notice_id)
        return {"notice_id": notice_id, "cluster_id": cluster_id, "merged": True}

    # No match: this notice is the canonical member of a new cluster.
    cluster_id = new_id("cls")
    connection.execute(
        text(
            """
            INSERT INTO tender_clusters
                (id, canonical_notice_id, confidence, merged_from, created_at, updated_at)
            VALUES (:id, :notice_id, 1.0, '[]'::jsonb, now(), now())
            """
        ),
        {"id": cluster_id, "notice_id": notice_id},
    )
    connection.execute(
        text("UPDATE notices SET cluster_id = :cluster, updated_at = now() WHERE id = :id"),
        {"cluster": cluster_id, "id": notice_id},
    )
    enqueue(connection, "notice.match", {"notice_id": notice_id}, entity=notice_id)
    return {"notice_id": notice_id, "cluster_id": cluster_id, "merged": False}


def _load_canonical(connection: Connection, notice_id: str) -> CanonicalNotice | None:
    """Rebuild a CanonicalNotice from its stored row, so stored and in-flight notices go
    through exactly the same clustering and scoring code."""
    row = (
        connection.execute(
            text(
                """
            SELECT id, source_refs, status::text AS status, notice_type::text AS notice_type,
                   country, language, buyer_name, buyer_siren, title, description, cpv, nuts,
                   procedure_type, amount_est, currency, published_at, deadline_at,
                   questions_deadline_at, lots, urls, award, provenance, version
            FROM notices WHERE id = :id
            """
            ),
            {"id": notice_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None

    return CanonicalNotice.model_validate(
        {
            "schema_version": 1,
            "uid": row["id"],
            "source_refs": row["source_refs"] or [{"source": "unknown", "external_id": row["id"]}],
            "status": row["status"],
            "notice_type": row["notice_type"],
            "country": row["country"],
            "language": row["language"],
            "buyer": {"name": row["buyer_name"], "siren": row["buyer_siren"]},
            "title": row["title"],
            "description": row["description"],
            "cpv": row["cpv"] or [],
            "nuts": row["nuts"] or [],
            "procedure": {"type": row["procedure_type"]},
            "lots": row["lots"] or [],
            "amounts": {
                "estimated_total": float(row["amount_est"]) if row["amount_est"] is not None else None,
                "currency": row["currency"],
            },
            "dates": {
                "published_at": row["published_at"],
                "deadline_at": row["deadline_at"],
                "questions_deadline_at": row["questions_deadline_at"],
            },
            "urls": row["urls"] or {},
            "award": row["award"],
            "provenance": row["provenance"] or {"adapter": "unknown@0", "normalized_at": datetime.now(UTC)},
            "version": row["version"],
        }
    )


# ---------------------------------------------------------------- stage: match


def run_match(connection: Connection, notice_id: str) -> dict[str, Any]:
    """Stage 4: score the notice against every enabled watch profile (notice → orgs fan-out).

    Stage 1 filters run before scoring, so a notice an org has excluded costs nothing beyond
    the filter evaluation - the cost discipline §8.1 requires.
    """
    notice = _load_canonical(connection, notice_id)
    if notice is None:
        raise LookupError(f"notice {notice_id!r} not found")

    profiles = (
        connection.execute(
            text(
                """
            SELECT w.id, w.org_id, w.filters, w.alert_policy,
                   p.cpv_families, p.keywords, p.negative_keywords, p.zones, p.revenues
            FROM watch_profiles w
            LEFT JOIN company_profiles p ON p.org_id = w.org_id
            WHERE w.enabled = true
            """
            )
        )
        .mappings()
        .all()
    )

    created = 0
    instant_alerts = 0
    for profile_row in profiles:
        filters_json = profile_row["filters"] or {}
        filters = WatchFilters(
            cpv_families=filters_json.get("cpv_families", []),
            countries=filters_json.get("countries", []),
            nuts=filters_json.get("nuts", []),
            min_days_to_deadline=filters_json.get("min_days_to_deadline"),
            amount_min=filters_json.get("amount_min"),
            amount_max=filters_json.get("amount_max"),
            procedure_types=filters_json.get("procedure_types", []),
            keywords_include=filters_json.get("keywords_include", []),
            keywords_exclude=filters_json.get("keywords_exclude", []),
        )
        if apply_hard_filters(notice, filters) is not FilterOutcome.PASS:
            continue

        zones = profile_row["zones"] or {}
        revenues = profile_row["revenues"] or []
        latest_revenue = None
        if isinstance(revenues, list) and revenues:
            latest = max(revenues, key=lambda entry: entry.get("year", 0) if isinstance(entry, dict) else 0)
            latest_revenue = latest.get("amount") if isinstance(latest, dict) else None

        company = CompanyProfile(
            org_id=profile_row["org_id"],
            cpv_families=profile_row["cpv_families"] or [],
            zones_nuts=zones.get("nuts", []) if isinstance(zones, dict) else [],
            national=bool(zones.get("national")) if isinstance(zones, dict) else False,
            keywords=profile_row["keywords"] or [],
            negative_keywords=profile_row["negative_keywords"] or [],
            annual_revenue=latest_revenue,
        )
        score = score_notice(notice, company)

        connection.execute(
            text(
                """
                INSERT INTO matches (id, org_id, notice_id, watch_profile_id, score, breakdown,
                                     state, created_at, updated_at)
                VALUES (:id, :org_id, :notice_id, :watch_id, :score, CAST(:breakdown AS jsonb),
                        'new', now(), now())
                ON CONFLICT (org_id, notice_id) DO UPDATE SET
                    score = EXCLUDED.score, breakdown = EXCLUDED.breakdown, updated_at = now()
                """
            ),
            {
                "id": new_id("mch"),
                "org_id": profile_row["org_id"],
                "notice_id": notice_id,
                "watch_id": profile_row["id"],
                "score": score.score,
                "breakdown": json.dumps(score.as_dict()),
            },
        )
        created += 1

        # Instant push above the configured threshold; everything else waits for the digest
        # (§8.3), which is how the inbox stays useful instead of becoming noise.
        policy = profile_row["alert_policy"] or {}
        threshold = policy.get("instant_min_score", 80)
        if score.score >= threshold:
            enqueue(
                connection,
                "notify.send",
                {"kind": "match.high_score", "notice_id": notice_id, "org_id": profile_row["org_id"]},
                entity=f"{profile_row['org_id']}:{notice_id}",
            )
            instant_alerts += 1

    return {"notice_id": notice_id, "matches": created, "instant_alerts": instant_alerts}
