-- Local development only: keep one way back in before the owner stops being a superuser.
--
-- `initdb --username=bidpilot` makes `bidpilot` the cluster's *only* superuser, and the next
-- script takes that attribute away so local development matches production and CI (ADR-0014).
-- Without this role, a dev volume would be left with no superuser at all and no way to restore
-- one - you could not install an extension, and you could not undo it either. Unlike CI's
-- throwaway container, `pgdata` is a persistent volume.
--
-- No password on purpose, so there is no credential in this file and no way to reach the role
-- over TCP: the image's pg_hba trusts local socket connections only, which means this role is
-- reachable from inside the container and nowhere else.
--
--   docker compose exec postgres psql -U postgres -d bidpilot
--
-- Mounted into /docker-entrypoint-initdb.d by docker-compose, so this runs once, on the first
-- start of a fresh volume.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres') THEN
    CREATE ROLE postgres LOGIN SUPERUSER;
    RAISE NOTICE 'created rescue superuser postgres (local socket only)';
  END IF;
END
$$;
