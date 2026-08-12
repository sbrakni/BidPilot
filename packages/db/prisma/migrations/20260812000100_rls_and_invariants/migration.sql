-- Multi-tenancy enforcement, INSERT-only invariants, and search infrastructure.
-- Hand-written companion to the generated base migration (SPEC §17.5, §17.6, §18).
--
-- The load-bearing idea: isolation is a property of the *database*, not of the ORM. Any
-- query - from the API, a worker, a psql session, or a future service nobody has written
-- yet - is filtered by Postgres itself. An ORM-level `where org_id` is one forgotten
-- clause away from a cross-tenant leak; RLS is not.

-- ---------------------------------------------------------------------------
-- 1. Application role
-- ---------------------------------------------------------------------------
-- RLS does not apply to a table's owner, so the app MUST NOT connect as the owner.
-- The role is created by packages/db/sql/bootstrap_roles.sql, which needs privileges a
-- migration user does not have on managed Postgres. We require it here and fail loudly:
-- a migration that silently skipped these policies would ship a cross-tenant leak.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidpilot_app') THEN
    RAISE EXCEPTION
      'role bidpilot_app is missing. Run packages/db/sql/bootstrap_roles.sql as a '
      'superuser before migrating - row-level security depends on the application '
      'connecting as a non-owner role (SPEC §17.6).';
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO bidpilot_app;

-- Tenant-writable and tenant-readable tables get DML; market data is read-only below.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO bidpilot_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO bidpilot_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bidpilot_app;

-- ---------------------------------------------------------------------------
-- 2. Shared market data: global read, never writable by tenants
-- ---------------------------------------------------------------------------
-- Notices, sources and awards are the commons. A tenant that could write them could
-- poison every other tenant's feed, so the grant is SELECT only (SPEC §17.6).

REVOKE INSERT, UPDATE, DELETE ON
  sources, raw_notices, notices, notice_versions, notice_documents, tender_clusters,
  award_records, cpv_labels, nuts_labels, naf_cpv_map, procurement_thresholds,
  document_lead_times, renewal_signals
FROM bidpilot_app;

-- ---------------------------------------------------------------------------
-- 3. Row-level security on every org-scoped table
-- ---------------------------------------------------------------------------
-- Policies key on `current_setting('app.org_id')`, which the API sets per request after
-- authorization. `true` as the second argument makes a missing setting return NULL
-- instead of raising - and a NULL org_id matches nothing, so the safe default is
-- "see nothing" rather than "see everything".

CREATE OR REPLACE FUNCTION app_current_org_id() RETURNS text
  LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.org_id', true), '') $$;

-- The acting user, where it is known. Identity is platform-level, not org-scoped: a
-- consultant belongs to several orgs (SPEC §15.1), so some policies need "who" as well
-- as "which org".
CREATE OR REPLACE FUNCTION app_current_user_id() RETURNS text
  LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.user_id', true), '') $$;

DO $$
DECLARE
  target text;
  org_scoped text[] := ARRAY[
    'orgs', 'org_members', 'company_profiles', 'watch_profiles', 'evidences',
    'reference_projects', 'content_chunks', 'matches', 'tenders', 'analyses',
    'requirements', 'compliance_items', 'decisions', 'tasks', 'qa_items',
    'generations', 'memo_documents', 'org_renewal_watches', 'subscriptions',
    'ai_usage', 'notifications'
  ];
  id_column text;
BEGIN
  FOREACH target IN ARRAY org_scoped LOOP
    -- `orgs` is keyed by its own id; every other table carries org_id.
    id_column := CASE WHEN target = 'orgs' THEN 'id' ELSE 'org_id' END;

    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', target);
    -- FORCE so the owner is subject to the same policies: it closes the "migrations and
    -- admin scripts silently see everything" hole, and makes tests meaningful.
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', target);
    EXECUTE format('DROP POLICY IF EXISTS org_isolation ON %I', target);
    EXECUTE format(
      'CREATE POLICY org_isolation ON %I USING (%I = app_current_org_id()) '
      'WITH CHECK (%I = app_current_org_id())',
      target, id_column, id_column
    );
  END LOOP;
END
$$;

-- `users` needs per-command policies, because identity does not belong to an org.
--
-- The threat to close is *enumeration*: org A must not be able to list org B's people.
-- Creating a user row is not that threat - and it cannot be membership-gated, because at
-- signup (and at invitation acceptance) the user exists before any membership does. A
-- single policy would have made signup impossible, so the commands are split:
--
--   SELECT        co-members of the current org, plus yourself
--   INSERT        permitted (signup / invitation); the row carries no tenant data
--   UPDATE/DELETE yourself only - nobody edits another person's identity
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS user_visibility ON users;
DROP POLICY IF EXISTS user_select ON users;
DROP POLICY IF EXISTS user_insert ON users;
DROP POLICY IF EXISTS user_update ON users;
DROP POLICY IF EXISTS user_delete ON users;

CREATE POLICY user_select ON users FOR SELECT
  USING (
    id = app_current_user_id()
    OR EXISTS (
      SELECT 1 FROM org_members m
      WHERE m.user_id = users.id AND m.org_id = app_current_org_id()
    )
  );

CREATE POLICY user_insert ON users FOR INSERT WITH CHECK (true);

CREATE POLICY user_update ON users FOR UPDATE
  USING (id = app_current_user_id())
  WITH CHECK (id = app_current_user_id());

CREATE POLICY user_delete ON users FOR DELETE USING (id = app_current_user_id());

-- `events` is the audit log: org-scoped rows are isolated, and platform-level rows
-- (org_id IS NULL) are invisible to tenants.
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON events;
CREATE POLICY org_isolation ON events
  USING (org_id = app_current_org_id())
  WITH CHECK (org_id = app_current_org_id());

-- ---------------------------------------------------------------------------
-- 4. INSERT-only invariants
-- ---------------------------------------------------------------------------
-- SPEC §10.6: "Decision log immutable (update forbidden at API level; superseding
-- decisions create new records)". Enforced in the database, because "at API level" is
-- exactly the guarantee a future endpoint forgets.

CREATE OR REPLACE FUNCTION forbid_mutation() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % is not permitted (SPEC §10.6/§18.4)',
    TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'restrict_violation';
END
$$;

DROP TRIGGER IF EXISTS decisions_append_only ON decisions;
CREATE TRIGGER decisions_append_only
  BEFORE UPDATE OR DELETE ON decisions
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

DROP TRIGGER IF EXISTS events_append_only ON events;
CREATE TRIGGER events_append_only
  BEFORE UPDATE OR DELETE ON events
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

-- ---------------------------------------------------------------------------
-- 5. Search & matching infrastructure (SPEC §17.5)
-- ---------------------------------------------------------------------------
-- Generated tsvector over title + description + buyer, unaccented, with both French and
-- English configurations: notices arrive in any EU language and users search in theirs.
--
-- `unaccent(text)` is only STABLE - it resolves the default dictionary at run time - so
-- Postgres refuses it in a generated column. Pinning the dictionary explicitly removes
-- that dependency, which is what makes the IMMUTABLE marking on this wrapper sound. The
-- standing caveat: changing the `unaccent` dictionary requires reindexing what uses it.

CREATE OR REPLACE FUNCTION bidpilot_unaccent(input text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
  AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, input) $$;

ALTER TABLE notices
  ADD COLUMN IF NOT EXISTS tsv tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('french', bidpilot_unaccent(coalesce(title, ''))), 'A') ||
    setweight(to_tsvector('english', bidpilot_unaccent(coalesce(title, ''))), 'A') ||
    setweight(to_tsvector('french', bidpilot_unaccent(coalesce(buyer_name, ''))), 'B') ||
    setweight(to_tsvector('french', bidpilot_unaccent(coalesce(description, ''))), 'C') ||
    setweight(to_tsvector('english', bidpilot_unaccent(coalesce(description, ''))), 'C')
  ) STORED;

CREATE INDEX IF NOT EXISTS notices_tsv_idx ON notices USING gin (tsv);

-- Trigram index on the title: this is what narrows dedupe candidates in SQL before the
-- Python decision logic runs, using the same notion of similarity (§6.7).
CREATE INDEX IF NOT EXISTS notices_title_trgm_idx ON notices USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS notices_buyer_trgm_idx ON notices USING gin (buyer_name gin_trgm_ops);

-- Embeddings. See docs/DECISIONS.md ADR-0004: `vector` rather than the spec's `halfvec`
-- because halfvec needs pgvector >= 0.7 and 0.6 is what several distributions still ship.
-- Dimension 1024 matches the configured embedding model (EMBEDDING_DIM).
ALTER TABLE notices ADD COLUMN IF NOT EXISTS embedding vector(1024);
ALTER TABLE company_profiles ADD COLUMN IF NOT EXISTS capability_embedding vector(1024);
ALTER TABLE evidences ADD COLUMN IF NOT EXISTS embedding vector(1024);
ALTER TABLE reference_projects ADD COLUMN IF NOT EXISTS embedding vector(1024);
ALTER TABLE content_chunks ADD COLUMN IF NOT EXISTS embedding vector(1024);

CREATE INDEX IF NOT EXISTS notices_embedding_idx
  ON notices USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS content_chunks_embedding_idx
  ON content_chunks USING hnsw (embedding vector_cosine_ops);

-- Deadline index restricted to live notices: the inbox and every countdown read this
-- path, and P95 < 300 ms is the requirement (SPEC §16).
CREATE INDEX IF NOT EXISTS notices_deadline_active_idx
  ON notices (deadline_at)
  WHERE status = 'active';

-- ---------------------------------------------------------------------------
-- 6. Job queue claim path
-- ---------------------------------------------------------------------------
-- Partial index for `FOR UPDATE SKIP LOCKED` claims (SPEC §17.1): workers only ever look
-- at runnable rows, so the index stays small no matter how much history accumulates.

CREATE INDEX IF NOT EXISTS jobs_runnable_idx
  ON jobs (run_at)
  WHERE state = 'queued';
