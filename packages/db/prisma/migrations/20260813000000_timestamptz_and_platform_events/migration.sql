-- Two corrections found by wiring the ingestion pipeline to the database.
--
-- 1. Every instant becomes `timestamptz`.
--
--    Prisma's default `DateTime` maps to `timestamp without time zone`, which is the wrong
--    type for this product. A submission deadline is defined by the buyer's clock (SPEC §5)
--    and P3 makes a missed deadline the worst failure there is; a column that discards the
--    offset means any writer using a non-UTC session silently shifts the value, and Postgres
--    cannot compare it to `now()` without guessing a zone. Values already stored are UTC, so
--    the conversion states that explicitly rather than reinterpreting anything.
--
-- 2. Platform-level audit events become writable by admin tooling.
--
--    The original `events` policy required `org_id = app_current_org_id()` for both reading
--    and writing, which made a row with `org_id IS NULL` impossible to insert - by anyone,
--    since the table also has FORCE ROW LEVEL SECURITY. That blocked exactly the events §6.1
--    and §15.4 depend on: source health, fetch failures, ops actions. Tenants still cannot
--    see or write them.

-- ---------------------------------------------------------------------------
-- 1. timestamp -> timestamptz
-- ---------------------------------------------------------------------------
-- `AT TIME ZONE 'UTC'` on a naive timestamp attaches UTC without shifting the clock reading,
-- which is correct because everything written so far was UTC by convention.

DO $$
DECLARE
  target record;
BEGIN
  FOR target IN
    SELECT c.table_name, c.column_name
    FROM information_schema.columns c
    JOIN information_schema.tables t
      ON t.table_schema = c.table_schema AND t.table_name = c.table_name
    WHERE c.table_schema = 'public'
      AND t.table_type = 'BASE TABLE'
      AND c.data_type = 'timestamp without time zone'
    ORDER BY c.table_name, c.column_name
  LOOP
    EXECUTE format(
      'ALTER TABLE %I ALTER COLUMN %I TYPE timestamptz USING %I AT TIME ZONE ''UTC''',
      target.table_name, target.column_name, target.column_name
    );
  END LOOP;
END
$$;

-- ---------------------------------------------------------------------------
-- 2. events: per-command policies
-- ---------------------------------------------------------------------------
-- Both commands use the same rule, which reads simply: *you see the scope you are acting in*.
--   With a tenant context  -> that tenant's events, and nothing else.
--   With no tenant context -> platform events (org_id IS NULL), and nothing else.
--
-- So a tenant can never see or write platform events, and admin tooling can never see tenant
-- events without deliberately establishing that tenant's context. `NULL = NULL` is NULL rather
-- than true in SQL, which is why the platform case needs its own explicit branch.

DROP POLICY IF EXISTS org_isolation ON events;
DROP POLICY IF EXISTS events_select ON events;
DROP POLICY IF EXISTS events_insert ON events;

CREATE POLICY events_select ON events FOR SELECT
  USING (
    org_id = app_current_org_id()
    OR (org_id IS NULL AND app_current_org_id() IS NULL)
  );

CREATE POLICY events_insert ON events FOR INSERT
  WITH CHECK (
    org_id = app_current_org_id()
    OR (org_id IS NULL AND app_current_org_id() IS NULL)
  );

-- No UPDATE or DELETE policy: the audit log is append-only (§18.4), enforced by the
-- forbid_mutation trigger as well as by the absence of a policy here.
