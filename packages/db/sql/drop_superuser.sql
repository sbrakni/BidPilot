-- Make a development or CI database role look like a production one (ADR-0014).
--
-- The postgres Docker image runs `initdb --username="$POSTGRES_USER"`, so the role that both
-- docker-compose and a CI service container hand to `DATABASE_URL` is the cluster's bootstrap
-- **superuser**. Superusers are exempt from row-level security unconditionally - `FORCE ROW
-- LEVEL SECURITY` does not reach them - so under the default configuration every tenant
-- isolation guarantee in this schema is inert, the suite that exists to prove that isolation
-- would pass no matter what the policies said, and a per-org job would quietly process every
-- tenant's rows under one org's context.
--
-- So: create the extensions and the roles with the privileges those genuinely need, run this,
-- and only then migrate, seed and test - as a plain schema owner, which is what production uses
-- and the only configuration under which any of the isolation tests mean anything. The ingestion
-- service refuses to establish tenant context otherwise (`db.assert_policy_bound`).
--
-- Two ordering constraints:
--
--   * `CREATE EXTENSION vector` is not a trusted extension, so it must happen before this
--     script. Afterwards the migrations' `CREATE EXTENSION IF NOT EXISTS` are no-ops.
--   * If the role named here is the cluster's only superuser, create another one first or the
--     cluster is left with none and no way to restore one. CI does not care - the container is
--     thrown away - but a persistent dev volume does; see dev-init/30_rescue_superuser.sql.

-- Defaults to the connected role, which is the one initdb made a superuser in both the compose
-- and the CI case, whatever POSTGRES_USER is set to. Pass -v role=<name> to target another.
\if :{?role}
\else
  SELECT current_user AS role \gset
\endif

ALTER ROLE :"role" NOSUPERUSER;

-- psql does not substitute its variables inside dollar-quoted strings, so the block below reads
-- the role name from a setting instead.
SELECT set_config('bidpilot.target_role', :'role', false);

-- Verify rather than assume: a silent failure here would restore the very blind spot this
-- script exists to remove, and it would look like a green build.
DO $$
DECLARE
  target text := current_setting('bidpilot.target_role');
  bypasses boolean;
BEGIN
  SELECT rolsuper OR rolbypassrls INTO bypasses FROM pg_roles WHERE rolname = target;
  IF bypasses IS NULL THEN
    RAISE EXCEPTION 'role % does not exist', target;
  END IF;
  IF bypasses THEN
    RAISE EXCEPTION
      'role % still bypasses row-level security; the isolation tests would be meaningless',
      target;
  END IF;
  RAISE NOTICE 'role % is subject to row-level security', target;
END
$$;
