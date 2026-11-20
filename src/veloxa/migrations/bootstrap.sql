-- Bootstrap (run by the cluster administrator, idempotent). ARCHITECTURE.md §6.6, §9.4.
-- Creates the role set and the extensions that need administrator rights.
-- Passwords are set separately by `veloxa migrate` from the environment; none are stored here.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'veloxa_owner') THEN
    CREATE ROLE veloxa_owner NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'veloxa_migrator') THEN
    CREATE ROLE veloxa_migrator LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'veloxa_app') THEN
    CREATE ROLE veloxa_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'veloxa_ingest') THEN
    CREATE ROLE veloxa_ingest LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'veloxa_projection') THEN
    CREATE ROLE veloxa_projection LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END
$$;

-- Enforce the attributes even if the roles already existed, but only where one differs: managed PostgreSQL
-- (Cloud SQL) refuses any ALTER ROLE that names SUPERUSER, even to keep it off. A role that somehow IS a superuser
-- or bypasses RLS still gets the full ALTER, which then needs a real superuser: it fails closed, never silently.
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT rolname, rolname <> 'veloxa_owner' AS login FROM pg_roles
           WHERE rolname IN ('veloxa_owner', 'veloxa_migrator', 'veloxa_app', 'veloxa_ingest', 'veloxa_projection')
             AND (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole
                  OR rolcanlogin <> (rolname <> 'veloxa_owner')) LOOP
    EXECUTE format('ALTER ROLE %I %s NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE', r.rolname,
                   CASE WHEN r.login THEN 'LOGIN' ELSE 'NOLOGIN' END);
  END LOOP;
END
$$;

GRANT veloxa_owner TO veloxa_migrator;

-- Prerequisites (§6.6): btree_gist (temporal exclusion constraints) and pgvector (later projections).
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS vector;

-- Application roles never create objects (§9.3: no CREATE on any schema).
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE __DATABASE__ FROM PUBLIC;
GRANT CONNECT ON DATABASE __DATABASE__ TO veloxa_migrator, veloxa_app, veloxa_ingest, veloxa_projection;
GRANT CREATE ON DATABASE __DATABASE__ TO veloxa_owner;
