-- One-time privileged bootstrap. Run as a superuser BEFORE the first `prisma migrate deploy`.
--
-- Why this is not part of a migration: creating a login role needs CREATEROLE, which the
-- migration user does not have on managed Postgres (and should not have). Keeping it
-- separate means the migration can *require* the role and fail loudly if it is missing,
-- rather than skipping row-level security and quietly shipping a cross-tenant leak.
--
--   psql "$SUPERUSER_URL" -v app_password="$DATABASE_APP_PASSWORD" \
--        -f packages/db/sql/bootstrap_roles.sql
--
-- In local development docker-compose mounts this into the Postgres init directory, so a
-- fresh `docker compose up` needs no manual step.

\set app_password_default 'bidpilot_app'
\if :{?app_password}
\else
  \set app_password :app_password_default
\endif

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidpilot_app') THEN
    -- NOSUPERUSER / NOBYPASSRLS are the entire point: this role must be subject to the
    -- policies. It also owns nothing, since a table owner is exempt from its own RLS
    -- unless FORCE is set (the migration sets FORCE too, as belt and braces).
    CREATE ROLE bidpilot_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    RAISE NOTICE 'created role bidpilot_app';
  ELSE
    RAISE NOTICE 'role bidpilot_app already exists';
  END IF;
END
$$;

ALTER ROLE bidpilot_app WITH PASSWORD :'app_password';

-- A second role whose only purpose is to OWN the platform helper function.
--
-- `FORCE ROW LEVEL SECURITY` subjects even the table owner to its policies, which is what makes
-- the isolation tests meaningful - and it also means a SECURITY DEFINER function owned by the
-- schema owner still cannot enumerate tenants. Only a BYPASSRLS role can.
--
-- So the bypass is scoped as narrowly as it can be: this role is NOLOGIN (nobody can connect
-- as it) and owns exactly one function, which returns org ids and nothing else. Compare that
-- with granting BYPASSRLS to the worker's login role, where every query it makes would be
-- unfiltered and a single mistake would leak across tenants silently.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidpilot_platform') THEN
    CREATE ROLE bidpilot_platform NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
    RAISE NOTICE 'created role bidpilot_platform';
  ELSE
    RAISE NOTICE 'role bidpilot_platform already exists';
  END IF;
END
$$;
