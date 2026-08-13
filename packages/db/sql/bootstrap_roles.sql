-- One-time privileged bootstrap. Run as a superuser BEFORE the first `prisma migrate deploy`.
--
-- Why this is not part of a migration: creating a login role needs CREATEROLE, which the
-- migration user does not have on managed Postgres (and should not have). Keeping it
-- separate means the migration can *require* the role and fail loudly if it is missing,
-- rather than skipping row-level security and quietly shipping a cross-tenant leak.
--
--   psql "$SUPERUSER_URL" -v owner_password="$DATABASE_PASSWORD" \
--                         -v app_password="$DATABASE_APP_PASSWORD" \
--                         -v auth_password="$DATABASE_AUTH_PASSWORD" \
--        -f packages/db/sql/bootstrap_roles.sql
--
-- It creates all three roles the schema needs, and none of them is the superuser you run it as:
--
--   bidpilot           owns the schema; `DATABASE_URL`. Migrations, the seeder and the ingestion
--                      worker connect as this. NOSUPERUSER NOBYPASSRLS, so `FORCE ROW LEVEL
--                      SECURITY` reaches it (ADR-0014).
--   bidpilot_app       the tenant role; `DATABASE_APP_URL`. Owns nothing, denied DML on market
--                      data by the migration.
--   bidpilot_auth      the web app's Auth.js adapter; `DATABASE_AUTH_URL`. Reaches the four
--                      authentication tables and nothing else, which is what keeps §17.2 true
--                      while Auth.js has the database access it requires (ADR-0015).
--   bidpilot_platform  NOLOGIN BYPASSRLS, owns exactly one function - `app_all_org_ids()`.
--
-- In local development docker-compose mounts this into the Postgres init directory as
-- `20_bootstrap_roles.sql`, right after the extensions, so a fresh `docker compose up` needs no
-- manual step. It is idempotent, so re-running it against an existing database is safe - and is
-- how an already-migrated database picks up the grants ADR-0014 added.

\set owner_password_default 'bidpilot'
\if :{?owner_password}
\else
  \set owner_password :owner_password_default
\endif

\set app_password_default 'bidpilot_app'
\if :{?app_password}
\else
  \set app_password :app_password_default
\endif

\set auth_password_default 'bidpilot_auth'
\if :{?auth_password}
\else
  \set auth_password :auth_password_default
\endif

-- The role that owns the schema, and the one `DATABASE_URL` points at.
--
-- It is created here rather than by `initdb` because the role `initdb` creates - `POSTGRES_USER`
-- in the Docker image - is the cluster's bootstrap superuser, and a superuser is exempt from
-- row-level security no matter what the policies say. That attribute cannot be taken away
-- either: Postgres refuses with `the bootstrap user must have the SUPERUSER attribute`. So the
-- owner has to be a *different* role from the start, which is also how managed Postgres works -
-- an admin role provisions, and the application's owner role is never a superuser.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidpilot') THEN
    CREATE ROLE bidpilot LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    RAISE NOTICE 'created role bidpilot';
  ELSE
    RAISE NOTICE 'role bidpilot already exists';
  END IF;
END
$$;

ALTER ROLE bidpilot WITH PASSWORD :'owner_password';

-- Enough to create the schema objects and `_prisma_migrations`, and no more. Since PostgreSQL 15
-- the `public` schema grants CREATE only to the database owner, so without this the very first
-- migration fails with `permission denied for schema public`.
GRANT CREATE, USAGE ON SCHEMA public TO bidpilot;

-- And CREATE on the database itself, because Prisma's migration engine issues
-- `CREATE SCHEMA IF NOT EXISTS "public"` before applying anything - which needs a database-level
-- privilege even when the schema already exists, and fails with `permission denied for database`
-- without it. Granted dynamically since the database name is deployment-specific.
DO $$
BEGIN
  EXECUTE format('GRANT CREATE ON DATABASE %I TO bidpilot', current_database());
END
$$;

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

-- The web app's role. Same attributes as the tenant role and for the same reason: it must be
-- subject to the policies, because the one table it can read that holds anything personal -
-- `users` - is RLS-protected, and its access there is granted by a policy scoped to this role
-- rather than by an exemption (ADR-0015).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidpilot_auth') THEN
    CREATE ROLE bidpilot_auth LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    RAISE NOTICE 'created role bidpilot_auth';
  ELSE
    RAISE NOTICE 'role bidpilot_auth already exists';
  END IF;
END
$$;

ALTER ROLE bidpilot_auth WITH PASSWORD :'auth_password';

-- A third role whose only purpose is to OWN the platform helper function.
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
\if :{?migration_role}
\else
  \set migration_role 'bidpilot'
\endif

-- Handed to the blocks below through a setting rather than interpolated into them: psql does not
-- substitute its variables inside dollar-quoted strings, so `:'migration_role'` in a DO body is
-- a syntax error rather than the role name.
SELECT set_config('bidpilot.migration_role', :'migration_role', false);

DO $$
DECLARE
  target text := current_setting('bidpilot.migration_role');
BEGIN
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

-- Finally, verify the invariant this file exists to establish, rather than trusting that the
-- statements above had the intended effect. A migration role that bypasses RLS would leave every
-- tenant boundary in the schema inert while everything continued to report success (ADR-0014),
-- so it is worth one query to be sure - and worth failing the bootstrap over.
DO $$
DECLARE
  target text := current_setting('bidpilot.migration_role');
  bypasses boolean;
BEGIN
  SELECT rolsuper OR rolbypassrls INTO bypasses FROM pg_roles WHERE rolname = target;
  IF bypasses THEN
    RAISE EXCEPTION
      'migration_role % is SUPERUSER or BYPASSRLS. Row-level security does not apply to such a '
      'role, so tenant isolation would be silently inert and its test suite would pass '
      'regardless. Point DATABASE_URL at a plain owner role instead.', target;
  END IF;
  RAISE NOTICE '% is subject to row-level security', target;
END
$$;
