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

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidpilot_platform') THEN
    RAISE EXCEPTION
      'role bidpilot_platform is missing. Run packages/db/sql/bootstrap_roles.sql as a '
      'superuser before migrating - platform jobs cannot enumerate tenants without it.';
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
