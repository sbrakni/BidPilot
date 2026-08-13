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
-- In local development docker-compose mounts this into the Postgres init directory as
-- `20_bootstrap_roles.sql`, between the extensions and the step that makes the owner
-- unprivileged, so a fresh `docker compose up` needs no manual step. It is idempotent, so
-- re-running it against an existing database is safe - and is how an already-migrated database
-- picks up the two grants ADR-0014 added.

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

-- The migration role has to be able to hand that function over: `ALTER FUNCTION ... OWNER TO`
-- requires the ability to SET ROLE to the new owner, so without this grant the platform
-- migration fails with `must be able to SET ROLE "bidpilot_platform"` on any deployment whose
-- migration role is not a superuser - which is every deployment that follows this file's own
-- advice. Superusers pass it without the grant, so the gap only shows up in the *correct*
-- configuration; that is exactly why it is granted here rather than left to chance.
--
-- This does not widen what the migration role can reach. Role attributes are not inherited
-- through membership - BYPASSRLS applies only after an explicit `SET ROLE` - and the migration
-- role owns every table anyway, so it could drop the policies outright if it wanted to. What it
-- must NOT be is BYPASSRLS itself, because that applies to every query it makes with nothing to
-- signal it; the ingestion service asserts that on connect (`db.assert_policy_bound`).
--
-- Defaults to whoever runs this script, which is right when the superuser bootstrapping the
-- database is also the schema owner (docker-compose, CI). Pass -v migration_role=<name> when
-- they differ.
\if :{?migration_role}
\else
  \set migration_role CURRENT_USER
\endif

-- Handed to the block below through a setting rather than interpolated into it: psql does not
-- substitute its variables inside dollar-quoted strings, so `:'migration_role'` in a DO body is
-- a syntax error rather than the role name.
SELECT set_config('bidpilot.migration_role', :'migration_role', false);

DO $$
DECLARE
  target text := current_setting('bidpilot.migration_role');
BEGIN
  IF upper(target) = 'CURRENT_USER' THEN
    target := current_user;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
    RAISE EXCEPTION 'migration_role % does not exist', target;
  END IF;
  EXECUTE format('GRANT bidpilot_platform TO %I', target);
  RAISE NOTICE 'granted bidpilot_platform to % (for the ALTER FUNCTION ... OWNER TO handoff)', target;
END
$$;

-- The other half of that handoff: `ALTER ... OWNER TO` also checks that the *incoming* owner can
-- create in the object's schema, so without this the platform migration fails with `permission
-- denied for schema public`. It is a privilege to hold one function, not to reach any data - the
-- role is NOLOGIN, and what it may read is one column of `orgs`, granted by the migration.
GRANT CREATE ON SCHEMA public TO bidpilot_platform;
