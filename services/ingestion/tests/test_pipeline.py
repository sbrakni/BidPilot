"""Pipeline and queue tests against a real Postgres.

These need a migrated database (`pnpm db:migrate`) and `DATABASE_URL` set. They are skipped
rather than failed when it is absent, so the pure-logic suites still run anywhere - but CI
always has the database, so the skip never hides a regression there.

The point of testing against real Postgres rather than a fake: `FOR UPDATE SKIP LOCKED`,
`ON CONFLICT DO NOTHING`, jsonb casts and enum casts either work in Postgres or they do not.
A mock would only confirm we wrote the SQL we intended to write.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; pipeline tests need a migrated Postgres",
)


@pytest.fixture(scope="module")
def db():
    from bidpilot_ingestion import db as db_module

    engine = db_module.get_engine()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return db_module


@pytest.fixture
def connection(db):
    """One transaction per test, rolled back at the end.

    Rollback rather than cleanup queries: the pipeline writes to eight tables and any
    hand-written teardown would drift from what the code actually inserts.
    """
    with db.get_engine().connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


@pytest.fixture
def test_source(connection):
    """A disposable source row, rolled back with the test's transaction."""
    from bidpilot_ingestion.ids import new_id

    code = f"test-src-{new_id('x')[-8:]}"
    source_id = new_id("src")
    connection.execute(
        text(
            """
            INSERT INTO sources (id, code, country, tier, kind, adapter, config, schedule,
                                 legal, health, enabled, cursor, created_at, updated_at)
            VALUES (:id, :code, 'FR', 'official_api', 'api', 'ted', '{}'::jsonb, '',
                    CAST(:legal AS jsonb), 'green', true, '{}'::jsonb, now(), now())
            """
        ),
        {
            "id": source_id,
            "code": code,
            "legal": json.dumps({"basis": "open-license", "notes": "test"}),
        },
    )
    return {"id": source_id, "code": code}


# ---------------------------------------------------------------- queue


def test_enqueue_is_idempotent(connection):
    """§17.3: idempotency keys are `kind + entity + version`, so a replay is a no-op."""
    from bidpilot_ingestion.queue import enqueue

    first = enqueue(connection, "notice.match", {"notice_id": "ntc_x"}, entity="ntc_x")
    second = enqueue(connection, "notice.match", {"notice_id": "ntc_x"}, entity="ntc_x")
    assert first is not None
    assert second is None, "re-enqueueing identical work must not create a second job"

    # A different version *is* different work: an amended notice must be re-processed.
    third = enqueue(connection, "notice.match", {"notice_id": "ntc_x"}, entity="ntc_x", version=2)
    assert third is not None


def test_unknown_job_kind_is_rejected(connection):
    from bidpilot_ingestion.queue import enqueue

    with pytest.raises(ValueError, match="unknown job kind"):
        enqueue(connection, "notice.teleport", {}, entity="x")


def test_claim_marks_running_and_increments_attempts(connection):
    from bidpilot_ingestion.queue import claim, complete, enqueue

    enqueue(connection, "notice.dedupe", {"notice_id": "ntc_claim"}, entity="ntc_claim")
    jobs = claim(connection, kinds=["notice.dedupe"], limit=5)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.attempts == 0, "attempts counts prior attempts, not the current one"

    state = connection.execute(
        text("SELECT state::text, locked_by FROM jobs WHERE id = :id"), {"id": job.id}
    ).first()
    assert state[0] == "running"
    assert state[1], "a claimed job records which worker holds it"

    complete(connection, job.id)
    assert (
        connection.execute(text("SELECT state::text FROM jobs WHERE id = :id"), {"id": job.id}).scalar()
        == "done"
    )


def test_claim_does_not_return_the_same_job_twice(connection):
    """The property `FOR UPDATE SKIP LOCKED` buys: a claimed job is invisible to others."""
    from bidpilot_ingestion.queue import claim, enqueue

    enqueue(connection, "notice.dedupe", {"notice_id": "ntc_once"}, entity="ntc_once")
    first = claim(connection, kinds=["notice.dedupe"], limit=5)
    second = claim(connection, kinds=["notice.dedupe"], limit=5)
    assert len(first) == 1
    assert second == []


def test_claim_ignores_future_jobs(connection):
    from bidpilot_ingestion.queue import claim, enqueue

    enqueue(
        connection,
        "notice.match",
        {"notice_id": "ntc_later"},
        entity="ntc_later",
        run_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert claim(connection, kinds=["notice.match"], limit=5) == []


def test_failure_retries_with_backoff_then_dead_letters(connection):
    """Retries are capped and the final state is `dead`, not deleted: §17.3 wants an ops UI
    that can replay, and a job that vanished cannot be diagnosed."""
    from bidpilot_ingestion.queue import claim, enqueue, fail

    enqueue(connection, "notice.match", {"notice_id": "ntc_fail"}, entity="ntc_fail", max_attempts=2)

    job = claim(connection, kinds=["notice.match"], limit=1)[0]
    assert fail(connection, job, "boom") == "queued"
    run_at = connection.execute(text("SELECT run_at FROM jobs WHERE id = :id"), {"id": job.id}).scalar()
    assert run_at > datetime.now(UTC), "a retry must be delayed, not immediate"

    connection.execute(text("UPDATE jobs SET run_at = now() WHERE id = :id"), {"id": job.id})
    job2 = claim(connection, kinds=["notice.match"], limit=1)[0]
    assert fail(connection, job2, "boom again") == "dead"

    row = connection.execute(
        text("SELECT state::text, last_error FROM jobs WHERE id = :id"), {"id": job.id}
    ).first()
    assert row[0] == "dead"
    assert "boom again" in row[1]


def test_stale_running_jobs_are_reclaimed(connection):
    """A worker that dies mid-job must not strand it - for a deadline alert that is the worst
    failure the product has (§16)."""
    from bidpilot_ingestion.queue import claim, enqueue, reclaim_stale

    enqueue(connection, "notify.send", {"kind": "test"}, entity="stale")
    job = claim(connection, kinds=["notify.send"], limit=1)[0]
    connection.execute(
        text("UPDATE jobs SET locked_at = now() - interval '2 hours' WHERE id = :id"), {"id": job.id}
    )

    assert reclaim_stale(connection) >= 1
    assert (
        connection.execute(text("SELECT state::text FROM jobs WHERE id = :id"), {"id": job.id}).scalar()
        == "queued"
    )


# ---------------------------------------------------------------- fetch & normalize


def _ted_payload(publication_number: str, title: str, deadline_date: str = "2026-11-20+01:00") -> dict:
    return {
        "publication-number": publication_number,
        "notice-title": {"fra": f"France – Services informatiques – {title}"},
        "title-lot": {"fra": [title]},
        "buyer-name": {"fra": ["Département de test"]},
        "buyer-country": ["FRA"],
        "publication-date": "2026-08-12+02:00",
        "deadline-receipt-tender-date-lot": [deadline_date],
        "deadline-receipt-tender-time-lot": ["12:00:00+01:00"],
        "classification-cpv": ["72000000"],
        "place-of-performance": ["FR101", "FRA"],
        "notice-type": "cn-standard",
        "form-type": "competition",
        "procedure-type": "open",
        "official-language": ["FRA"],
        "notice-version": 1,
    }


def test_fetch_stores_raw_and_enqueues_normalisation(connection, test_source, monkeypatch):
    """Stage 1 with a stubbed transport, so the test exercises our pipeline rather than TED."""
    from bidpilot_ingestion import pipeline

    payload = {"notices": [_ted_payload("111111-2026", "Infogérance du SI")], "totalNoticeCount": 1}
    original_build = pipeline.build_adapter

    def build_with_stub(source):
        adapter = original_build(source)
        adapter._client = httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        )
        adapter.config.max_rps = 0
        return adapter

    monkeypatch.setattr(pipeline, "build_adapter", build_with_stub)

    result = pipeline.run_source_fetch(connection, test_source["code"])
    assert result == {"fetched": 1, "stored": 1}

    stored = (
        connection.execute(
            text("SELECT external_id, content_hash FROM raw_notices WHERE source_id = :id"),
            {"id": test_source["id"]},
        )
        .mappings()
        .all()
    )
    assert [row["external_id"] for row in stored] == ["111111-2026"]
    assert stored[0]["content_hash"], "raw payloads are hashed so amendments are detectable"

    # Scoped to this test's source: the jobs table is shared, and a real pipeline run may have
    # left rows behind. Counting globally would make this assertion depend on test ordering.
    queued = (
        connection.execute(
            text(
                """
            SELECT kind, payload FROM jobs
            WHERE kind = 'notice.normalize' AND payload->>'source_code' = :code
            """
            ),
            {"code": test_source["code"]},
        )
        .mappings()
        .all()
    )
    assert len(queued) == 1
    # Payloads are ids, never blobs (§17.3).
    assert set(queued[0]["payload"]) == {"raw_notice_id", "source_code"}


def test_refetching_identical_payload_stores_nothing_new(connection, test_source, monkeypatch):
    from bidpilot_ingestion import pipeline

    payload = {"notices": [_ted_payload("222222-2026", "Hébergement souverain")], "totalNoticeCount": 1}
    original_build = pipeline.build_adapter

    def build_with_stub(source):
        adapter = original_build(source)
        adapter._client = httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        )
        adapter.config.max_rps = 0
        return adapter

    monkeypatch.setattr(pipeline, "build_adapter", build_with_stub)

    assert pipeline.run_source_fetch(connection, test_source["code"])["stored"] == 1
    # An incremental window overlapping the previous one is the normal case, not an error.
    assert pipeline.run_source_fetch(connection, test_source["code"])["stored"] == 0


def test_disabled_source_is_skipped_not_failed(connection, test_source):
    """Disabling a source is the kill switch §6.2 requires; it must not raise."""
    from bidpilot_ingestion import pipeline

    connection.execute(text("UPDATE sources SET enabled = false WHERE id = :id"), {"id": test_source["id"]})
    result = pipeline.run_source_fetch(connection, test_source["code"])
    assert result["skipped_disabled"] == 1


def test_source_without_legal_basis_refuses_to_run(connection, test_source):
    """§24.7, enforced at the registry level as well as inside the adapter."""
    from bidpilot_ingestion import pipeline

    connection.execute(
        text("UPDATE sources SET legal = '{}'::jsonb WHERE id = :id"), {"id": test_source["id"]}
    )
    with pytest.raises(ValueError, match="legal basis"):
        pipeline.run_source_fetch(connection, test_source["code"])


def test_normalize_upserts_a_notice_and_enqueues_dedupe(connection, test_source):
    from bidpilot_ingestion import pipeline
    from bidpilot_ingestion.adapters.base import content_hash
    from bidpilot_ingestion.canonical import RawNotice

    payload = _ted_payload("333333-2026", "Maintenance applicative")
    raw = RawNotice(
        source=test_source["code"],
        external_id="333333-2026",
        payload=payload,
        content_hash=content_hash(payload),
        fetched_at=datetime.now(UTC),
    )
    raw_id = pipeline.store_raw(connection, pipeline.load_source(connection, test_source["code"]), raw)
    assert raw_id

    result = pipeline.run_normalize(connection, raw_id)
    assert result["amended"] is False

    notice = (
        connection.execute(
            text(
                "SELECT title, cpv, nuts, deadline_at, notice_type::text AS kind FROM notices WHERE id = :id"
            ),
            {"id": result["notice_id"]},
        )
        .mappings()
        .first()
    )
    assert notice["title"] == "Maintenance applicative"
    assert notice["cpv"] == ["72000000"]
    assert notice["nuts"] == ["FR101"], "the ISO-3166 alpha-3 token must not land in NUTS"
    assert notice["kind"] == "competition"
    # The deadline is the buyer's clock converted to UTC: 12:00+01:00 is 11:00 UTC.
    assert notice["deadline_at"].hour == 11

    assert (
        connection.execute(
            text("SELECT count(*) FROM jobs WHERE kind = 'notice.dedupe' AND payload->>'notice_id' = :id"),
            {"id": result["notice_id"]},
        ).scalar()
        == 1
    )


def test_changed_payload_is_an_amendment_not_an_overwrite(connection, test_source):
    """§6.7: a changed content hash creates a version with a diff and flags `amended`, which is
    what triggers re-analysis (§9.4) and alerts the team (§12.4)."""
    from bidpilot_ingestion import pipeline
    from bidpilot_ingestion.adapters.base import content_hash
    from bidpilot_ingestion.canonical import RawNotice

    source = pipeline.load_source(connection, test_source["code"])

    first = _ted_payload("444444-2026", "Fourniture de licences")
    raw_id = pipeline.store_raw(
        connection,
        source,
        RawNotice(
            source=source.code,
            external_id="444444-2026",
            payload=first,
            content_hash=content_hash(first),
            fetched_at=datetime.now(UTC),
        ),
    )
    initial = pipeline.run_normalize(connection, raw_id)
    assert initial["amended"] is False

    # The buyer moves the deadline - the change that matters most (P3).
    amended_payload = _ted_payload("444444-2026", "Fourniture de licences", deadline_date="2026-12-05+01:00")
    amended_raw_id = pipeline.store_raw(
        connection,
        source,
        RawNotice(
            source=source.code,
            external_id="444444-2026",
            payload=amended_payload,
            content_hash=content_hash(amended_payload),
            fetched_at=datetime.now(UTC),
        ),
    )
    assert amended_raw_id, "a different payload is a new raw row, not a duplicate"

    second = pipeline.run_normalize(connection, amended_raw_id)
    assert second["notice_id"] == initial["notice_id"], "the same publication stays one notice"
    assert second["amended"] is True

    row = (
        connection.execute(
            text("SELECT status::text AS status, version, deadline_at FROM notices WHERE id = :id"),
            {"id": initial["notice_id"]},
        )
        .mappings()
        .first()
    )
    assert row["status"] == "amended"
    assert row["version"] == 2
    assert row["deadline_at"].month == 12, "the new deadline must be visible"

    versions = (
        connection.execute(
            text("SELECT version, diff FROM notice_versions WHERE notice_id = :id ORDER BY version"),
            {"id": initial["notice_id"]},
        )
        .mappings()
        .all()
    )
    assert [v["version"] for v in versions] == [2]
    assert versions[0]["diff"]["previous_hash"] != versions[0]["diff"]["new_hash"]

    # And the team is told, rather than the change sitting silently in the database.
    assert (
        connection.execute(
            text(
                """
                SELECT count(*) FROM jobs
                WHERE kind = 'notify.send' AND payload->>'kind' = 'tender.amended'
                  AND payload->>'notice_id' = :id
                """
            ),
            {"id": initial["notice_id"]},
        ).scalar()
        == 1
    )


# ---------------------------------------------------------------- dedupe & match


def _insert_notice(
    connection, external_id: str, title: str, source_code: str, deadline: datetime, siren: str | None = None
):
    from bidpilot_ingestion import pipeline
    from bidpilot_ingestion.canonical import (
        Amounts,
        Buyer,
        CanonicalNotice,
        Dates,
        NoticeType,
        Procedure,
        Provenance,
        SourceRef,
    )

    canonical = CanonicalNotice(
        source_refs=[SourceRef(source=source_code, external_id=external_id)],
        notice_type=NoticeType.COMPETITION,
        country="FR",
        buyer=Buyer(name="Département de test", siren=siren),
        title=title,
        cpv=["72000000"],
        nuts=["FR101"],
        procedure=Procedure(),
        amounts=Amounts(estimated_total=250000.0, currency="EUR"),
        dates=Dates(deadline_at=deadline),
        provenance=Provenance(adapter="ted@1.1.0", normalized_at=datetime.now(UTC)),
    )
    return pipeline.upsert_notice(connection, canonical, content_hash=f"hash-{external_id}")[0]


def test_dedupe_creates_a_cluster_then_merges_a_duplicate(connection, test_source):
    """§6.10: the same tender from two sources becomes one card carrying both badges."""
    from bidpilot_ingestion import pipeline

    deadline = datetime(2026, 11, 20, 11, 0, tzinfo=UTC)
    first_id = _insert_notice(
        connection, "555555-2026", "Maintenance multitechnique des lycées", "eu-ted", deadline
    )
    first = pipeline.run_dedupe(connection, first_id)
    assert first["merged"] is False

    # Same buyer, same deadline, buyer's own wording.
    second_id = _insert_notice(
        connection, "26-98765", "maintenance multitechnique des lycees", "fr-boamp", deadline
    )
    second = pipeline.run_dedupe(connection, second_id)
    assert second["merged"] is True
    assert second["cluster_id"] == first["cluster_id"], "one cluster, not two cards"

    merged_from = connection.execute(
        text("SELECT merged_from FROM tender_clusters WHERE id = :id"), {"id": first["cluster_id"]}
    ).scalar()
    assert len(merged_from) == 1
    assert merged_from[0]["notice_id"] == second_id


def test_dedupe_keeps_genuinely_different_tenders_apart(connection, test_source):
    """The failure that would be worse than a duplicate: hiding a real tender."""
    from bidpilot_ingestion import pipeline

    deadline = datetime(2026, 11, 20, 11, 0, tzinfo=UTC)
    first_id = _insert_notice(
        connection, "666666-2026", "Fourniture de repas en liaison froide", "eu-ted", deadline
    )
    second_id = _insert_notice(
        connection,
        "777777-2026",
        "Travaux de réfection de la toiture du gymnase",
        "eu-ted",
        deadline + timedelta(hours=6),
    )
    first = pipeline.run_dedupe(connection, first_id)
    second = pipeline.run_dedupe(connection, second_id)
    assert first["cluster_id"] != second["cluster_id"]


def test_match_creates_scored_matches_for_watch_profiles(connection, test_source):
    """Stage 4 fan-out: notice → orgs, with the breakdown persisted for the UI (§8.4)."""
    from bidpilot_ingestion import pipeline
    from bidpilot_ingestion.ids import new_id

    org_id = new_id("org")
    connection.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": org_id})
    connection.execute(
        text(
            """
            INSERT INTO orgs (id, name, country, locale, tz, settings, plan, ai_credits_balance,
                              created_at, updated_at)
            VALUES (:id, 'Test IT', 'FR', 'fr', 'Europe/Paris', '{}'::jsonb, 'trial', 0, now(), now())
            """
        ),
        {"id": org_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO company_profiles (org_id, identity, revenues, headcount, zones,
                                          cpv_families, keywords, negative_keywords,
                                          capability_text, created_at, updated_at)
            VALUES (:org, '{}'::jsonb, CAST(:revenues AS jsonb), 30, CAST(:zones AS jsonb),
                    ARRAY['72'], ARRAY['infogérance'], ARRAY[]::text[], 'ESN', now(), now())
            """
        ),
        {
            "org": org_id,
            "revenues": json.dumps([{"year": 2025, "amount": 4000000}]),
            "zones": json.dumps({"nuts": ["FR10"], "national": False}),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO watch_profiles (id, org_id, name, filters, alert_policy, enabled,
                                        created_at, updated_at)
            VALUES (:id, :org, 'IT IDF', CAST(:filters AS jsonb), CAST(:policy AS jsonb), true,
                    now(), now())
            """
        ),
        {
            "id": new_id("wp"),
            "org": org_id,
            "filters": json.dumps({"cpv_families": ["72"], "countries": ["FR"], "nuts": ["FR10"]}),
            "policy": json.dumps({"instant_min_score": 80}),
        },
    )

    notice_id = _insert_notice(
        connection,
        "888888-2026",
        "Infogérance du système d'information",
        "eu-ted",
        datetime.now(UTC) + timedelta(days=40),
    )
    result = pipeline.run_match(connection, notice_id)
    assert result["matches"] == 1

    match = (
        connection.execute(
            text("SELECT score, breakdown, state::text AS state FROM matches WHERE notice_id = :id"),
            {"id": notice_id},
        )
        .mappings()
        .first()
    )
    assert match["state"] == "new"
    assert match["score"] > 0
    # §8.4: the factors must sum to the score, and the UI reads this exact JSON.
    total = sum(factor["points"] for factor in match["breakdown"]["factors"])
    assert round(total) == match["breakdown"]["score"] == match["score"]


def test_match_skips_notices_a_profile_filters_out(connection, test_source):
    """A notice failing stage 1 must produce no match row at all - the cost guard of §8.1."""
    from bidpilot_ingestion import pipeline
    from bidpilot_ingestion.ids import new_id

    org_id = new_id("org")
    connection.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": org_id})
    connection.execute(
        text(
            """
            INSERT INTO orgs (id, name, country, locale, tz, settings, plan, ai_credits_balance,
                              created_at, updated_at)
            VALUES (:id, 'Formation', 'FR', 'fr', 'Europe/Paris', '{}'::jsonb, 'trial', 0, now(), now())
            """
        ),
        {"id": org_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO watch_profiles (id, org_id, name, filters, alert_policy, enabled,
                                        created_at, updated_at)
            VALUES (:id, :org, 'Formation only', CAST(:filters AS jsonb), '{}'::jsonb, true, now(), now())
            """
        ),
        {"id": new_id("wp"), "org": org_id, "filters": json.dumps({"cpv_families": ["805"]})},
    )

    notice_id = _insert_notice(
        connection, "999999-2026", "Infogérance du SI", "eu-ted", datetime.now(UTC) + timedelta(days=30)
    )
    result = pipeline.run_match(connection, notice_id)
    assert result["matches"] == 0
    assert (
        connection.execute(
            text("SELECT count(*) FROM matches WHERE notice_id = :id"), {"id": notice_id}
        ).scalar()
        == 0
    )


# ---------------------------------------------------------------- health


def test_health_persists_verdicts_and_records_transitions(connection, test_source):
    from bidpilot_ingestion.health import run_source_health

    connection.execute(
        text("UPDATE sources SET last_success_at = now() - interval '9 hours' WHERE id = :id"),
        {"id": test_source["id"]},
    )
    summary = run_source_health(connection)
    assert summary["checked"] >= 1

    health = connection.execute(
        text("SELECT health::text FROM sources WHERE id = :id"), {"id": test_source["id"]}
    ).scalar()
    assert health == "silent", "a Tier-1 source silent for 9h exceeds its 6h budget"
    assert test_source["code"] in summary["paging"]

    events = (
        connection.execute(
            text("SELECT kind, payload FROM events WHERE entity = :code ORDER BY created_at DESC"),
            {"code": test_source["code"]},
        )
        .mappings()
        .all()
    )
    assert events and events[0]["kind"] == "source.silent"
    assert events[0]["payload"]["page_operator"] is True


# ---------------------------------------------------------------- platform jobs


def test_worker_role_is_subject_to_row_level_security(connection):
    """Every isolation assertion in this file rests on this one, so it is asserted directly.

    SUPERUSER and BYPASSRLS both defeat row-level security outright, and `FORCE ROW LEVEL
    SECURITY` does not reach them. Under such a role `set_config('app.org_id', ...)` still
    succeeds and every query still returns rows - there just is no filtering any more, so the
    tests below would pass whatever the policies said.

    That is not a hypothetical misconfiguration: the postgres Docker image makes `POSTGRES_USER`
    the cluster's bootstrap superuser, so it is what a stock compose file and a stock CI service
    container both hand to `DATABASE_URL`.
    """
    role = (
        connection.execute(
            text(
                """
            SELECT current_user AS name, rolsuper, rolbypassrls
              FROM pg_roles WHERE rolname = current_user
            """
            )
        )
        .mappings()
        .first()
    )
    assert role["rolsuper"] is False, (
        f"role {role['name']} is a superuser, so row-level security does not apply to it and the "
        "cross-tenant assertions in this suite prove nothing"
    )
    assert role["rolbypassrls"] is False, f"role {role['name']} has BYPASSRLS"


def test_platform_helper_returns_ids_only(connection):
    """The one privileged surface in the schema, so its reach is asserted explicitly.

    `app_all_org_ids()` is SECURITY DEFINER and owned by a BYPASSRLS role, which is the only way
    a platform job can enumerate tenants past FORCE ROW LEVEL SECURITY. It must expose exactly
    one column - ids - and nothing about any org.
    """
    columns = connection.execute(
        text(
            """
            SELECT count(*) FROM information_schema.routines r
            JOIN information_schema.parameters p ON p.specific_name = r.specific_name
            WHERE r.routine_name = 'app_all_org_ids' AND p.parameter_mode = 'OUT'
            """
        )
    ).scalar()
    assert columns == 1, "the platform helper must expose org ids and nothing else"

    definer = (
        connection.execute(
            text(
                """
            SELECT r.rolname, r.rolbypassrls, r.rolcanlogin
            FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner
            WHERE p.proname = 'app_all_org_ids'
            """
            )
        )
        .mappings()
        .first()
    )
    assert definer["rolbypassrls"] is True, "the definer must be able to see past the policies"
    assert definer["rolcanlogin"] is False, "nobody may connect as the privileged role"


def test_deadline_alerts_stay_scoped_to_their_own_org(connection):
    """A job that sweeps every tenant is exactly where a cross-tenant leak would hide, so the
    notification it writes must land in the right org and nowhere else."""
    from bidpilot_ingestion.alerts import run_deadline_alerts
    from bidpilot_ingestion.ids import new_id

    orgs = []
    for name in ("alpha", "beta"):
        org_id = new_id("org")
        connection.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": org_id})
        connection.execute(
            text(
                """
                INSERT INTO orgs (id, name, country, locale, tz, settings, plan,
                                  ai_credits_balance, created_at, updated_at)
                VALUES (:id, :name, 'FR', 'fr', 'Europe/Paris', '{}'::jsonb, 'trial', 0, now(), now())
                """
            ),
            {"id": org_id, "name": name},
        )
        connection.execute(
            text(
                """
                INSERT INTO tenders (id, org_id, title, stage, origin, deadline_at, meta,
                                     created_at, updated_at)
                VALUES (:id, :org, :title, 'analysis', 'match', now() + interval '9 days',
                        '{}'::jsonb, now(), now())
                """
            ),
            {"id": f"tnd_{name}", "org": org_id, "title": f"Consultation {name}"},
        )
        orgs.append(org_id)

    result = run_deadline_alerts(connection)
    assert result["raised"] >= 2

    for org_id, name in zip(orgs, ("alpha", "beta"), strict=True):
        connection.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": org_id})
        rows = (
            connection.execute(text("SELECT org_id, payload->>'tender_id' AS tender_id FROM notifications"))
            .mappings()
            .all()
        )
        assert rows, f"org {name} should see its own alert"
        assert {row["org_id"] for row in rows} == {org_id}, "an org must never see another's alerts"
        assert all(row["tender_id"] == f"tnd_{name}" for row in rows)
