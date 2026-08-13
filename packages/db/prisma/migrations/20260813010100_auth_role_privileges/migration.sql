-- Confine the web app's database role to authentication, and nothing else (ADR-0015).
--
-- Auth.js needs a database adapter: the magic-link provider stores single-use tokens, and the
-- session strategy stores sessions. That puts a connection string in the web app, which §17.2
-- otherwise forbids - "web ↔ api only", so that tenant isolation has one enforcement point.
--
-- The invariant is kept by making it a *privilege* rather than a convention. `bidpilot_auth` is
-- granted exactly what the adapter calls for and holds no grant on any other table, so a web
-- process that tried to read `matches` or `notices` would be refused by Postgres. The reason
-- §17.2 exists survives intact; what changes is that the database enforces it instead of the
-- absence of a connection string.
--
-- Requires the role, created by sql/bootstrap_roles.sql (ADR-0005).

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bidpilot_auth') THEN
    RAISE EXCEPTION
      'role bidpilot_auth is missing. Run packages/db/sql/bootstrap_roles.sql as a superuser '
      'before migrating - the web app cannot authenticate anyone without it.';
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO bidpilot_auth;

-- The adapter's full surface on its own tables: it creates and deletes sessions, links OAuth
-- accounts, and consumes verification tokens (which it deletes on use).
GRANT SELECT, INSERT, UPDATE, DELETE ON accounts, sessions, verification_tokens TO bidpilot_auth;

-- On `users` it needs less: find by email, create on first sign-in, stamp email_verified. Not
-- DELETE - removing a person is an org administration action, and it cascades into tenant data
-- this role must never influence.
GRANT SELECT, INSERT, UPDATE ON users TO bidpilot_auth;

-- ---------------------------------------------------------------------------
-- Row-level security
-- ---------------------------------------------------------------------------
-- `users` is RLS-protected and FORCEd, with policies keyed to the current tenant context. The
-- auth role has no tenant context - it is establishing identity, which is what happens *before*
-- anyone has an org - so without a policy of its own it would see zero rows and no one could
-- ever sign in.
--
-- Hence three role-scoped policies. `TO bidpilot_auth` is what keeps them narrow: they are
-- invisible to every other role, so the tenant-scoped policies above are untouched, and this
-- role's reach is still bounded by the grants - one table, no org data on it.
DROP POLICY IF EXISTS users_auth_select ON users;
DROP POLICY IF EXISTS users_auth_insert ON users;
DROP POLICY IF EXISTS users_auth_update ON users;

CREATE POLICY users_auth_select ON users FOR SELECT TO bidpilot_auth USING (true);
CREATE POLICY users_auth_insert ON users FOR INSERT TO bidpilot_auth WITH CHECK (true);
CREATE POLICY users_auth_update ON users FOR UPDATE TO bidpilot_auth
  USING (true) WITH CHECK (true);

-- The three Auth.js tables deliberately get no RLS.
--
-- Not an oversight: RLS answers "which rows may this tenant see", and these tables have no
-- tenant. What has to be true of them is narrower and stronger - only the auth role may read a
-- session token or a magic link at all - and that is a GRANT, enforced below. A permissive
-- policy on top would look like protection while adding none.

-- ---------------------------------------------------------------------------
-- What the API may see
-- ---------------------------------------------------------------------------
-- `ALTER DEFAULT PRIVILEGES` in the RLS migration grants the tenant role full DML on every
-- table the owner creates afterwards, which now includes these three. That is more than the API
-- needs and more than it should have: with INSERT on `sessions` it could mint a session for any
-- user, and with SELECT on `verification_tokens` it could read a live magic link.
--
-- It needs exactly one thing: to verify a presented session token.
REVOKE ALL ON accounts, sessions, verification_tokens FROM bidpilot_app;
GRANT SELECT ON sessions TO bidpilot_app;
