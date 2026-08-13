-- Local development only: extensions, created while the owner still has the privileges for it.
--
-- The migrations declare these too (`CREATE EXTENSION IF NOT EXISTS`), but by the time they run
-- the owner role is no longer a superuser - see 40_drop_superuser.sql - and `vector` is not a
-- trusted extension, so it cannot be created by a plain owner. Creating them here makes the
-- migrations' declarations no-ops that still document the dependency.
--
-- Mounted into /docker-entrypoint-initdb.d by docker-compose, so this runs once, on the first
-- start of a fresh volume.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
