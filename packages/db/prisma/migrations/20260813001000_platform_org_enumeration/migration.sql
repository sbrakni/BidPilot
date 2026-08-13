-- Let platform jobs enumerate tenants, without weakening tenant isolation.
--
-- The problem: cross-org background work (deadline alerts, vault freshness, digests) has to
-- know which orgs exist. But `orgs` is RLS-protected on `id = app_current_org_id()`, so a job
-- with no tenant context sees nothing - correctly, because the fail-closed default is what
-- makes a forgotten `withOrgContext` harmless.
--
-- Rejected alternatives, and why:
--
--   * A `BYPASSRLS` role for workers. Ambient and coarse: every query that role makes is
--     unfiltered, so one mistake in a worker leaks across tenants with nothing to catch it.
--   * Relaxing the policies to "no context sees everything". That inverts the fail-closed
--     default: an API path that forgot to establish context would suddenly see every tenant.
--     This is the most dangerous option and the most tempting one, because it is the smallest
--     diff.
--
-- What this does instead: exactly one SECURITY DEFINER function, returning ids and nothing
-- else. A job calls it to get the list, then processes each org *inside that org's context*,
-- so every row it reads or writes is still policy-checked. The privileged surface is one
-- function returning one column, which is small enough to audit by reading it.
--
-- The function is owned by `bidpilot_platform` (created in sql/bootstrap_roles.sql), a NOLOGIN
-- BYPASSRLS role that owns nothing else. That indirection is required, not decorative: because
-- the tables use FORCE ROW LEVEL SECURITY, a SECURITY DEFINER function owned by the schema owner
-- would still be filtered by the very policies it needs to look past.

CREATE OR REPLACE FUNCTION app_all_org_ids()
  RETURNS TABLE (org_id text)
  LANGUAGE sql
  SECURITY DEFINER
  -- Pinned search_path: a SECURITY DEFINER function without one can be hijacked by a caller
  -- who puts a malicious `orgs` earlier in their own search_path.
  SET search_path = public
  STABLE
  AS $$ SELECT id FROM orgs $$;

-- Two preconditions this migration cannot create for itself, both satisfied by
-- sql/bootstrap_roles.sql. Checked here so a missing bootstrap step fails with instructions
-- rather than with Postgres' own error, and so the reason is recorded where the statement that
-- needs it lives:
--
--   1. The role must exist, and the migration role must be able to SET ROLE to it - `ALTER
--      FUNCTION ... OWNER TO` requires that of the caller.
--   2. The role must be able to create in this schema - `ALTER ... OWNER TO` checks the
--      *incoming* owner's privileges too, and fails with `permission denied for schema public`
--      without it.
--
-- Neither shows up when the migration runs as a superuser, because a superuser skips both
-- checks. They are the price of a correctly unprivileged migration role, which is the one worth
-- paying for.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidpilot_platform') THEN
    RAISE EXCEPTION
      'role bidpilot_platform is missing. Run packages/db/sql/bootstrap_roles.sql as a '
      'superuser before migrating - platform jobs cannot enumerate tenants without it.';
  END IF;
  IF NOT pg_has_role(current_user, 'bidpilot_platform', 'USAGE') THEN
    RAISE EXCEPTION
      'role % cannot SET ROLE to bidpilot_platform, so it cannot hand the platform function '
      'over to it. Re-run packages/db/sql/bootstrap_roles.sql (it grants the membership), '
      'passing -v migration_role=% if it is not the role bootstrapping the database.',
      current_user, current_user;
  END IF;
  IF NOT has_schema_privilege('bidpilot_platform', current_schema(), 'CREATE') THEN
    RAISE EXCEPTION
      'bidpilot_platform cannot create in schema %, so it cannot own the platform function. '
      'Re-run packages/db/sql/bootstrap_roles.sql, which grants it.', current_schema();
  END IF;
END
$$;

ALTER FUNCTION app_all_org_ids() OWNER TO bidpilot_platform;

-- BYPASSRLS skips *policies*, not table privileges, so the owning role still needs the grant -
-- and it gets SELECT on `orgs` alone. Its reach is one column of one table.
GRANT SELECT ON orgs TO bidpilot_platform;

COMMENT ON FUNCTION app_all_org_ids() IS
  'Platform-job helper: every org id, for background work that must iterate tenants. '
  'Returns ids only. Callers MUST then set app.org_id per org so RLS still applies to the '
  'rows they touch (SPEC §17.6).';

-- Executable by the application role too, because the API's own scheduled work runs as it.
-- It exposes only the existence of an org id, never any org data.
GRANT EXECUTE ON FUNCTION app_all_org_ids() TO bidpilot_app;
