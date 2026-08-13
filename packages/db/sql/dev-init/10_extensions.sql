-- Local development only: extensions, created by the one role privileged enough to do it.
--
-- The migrations declare these too (`CREATE EXTENSION IF NOT EXISTS`), but they run as the
-- unprivileged `bidpilot` owner, and `vector` is not a trusted extension - so a plain owner
-- cannot create it. Creating them here, as the bootstrap superuser, makes the migrations'
-- declarations no-ops that still document the dependency.
--
-- Mounted into /docker-entrypoint-initdb.d by docker-compose, so this runs once, on the first
-- start of a fresh volume, before 20_bootstrap_roles.sql.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
