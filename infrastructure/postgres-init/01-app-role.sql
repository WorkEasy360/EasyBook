-- The role created via POSTGRES_USER is a superuser and therefore always
-- bypasses Row Level Security, no matter what FORCE ROW LEVEL SECURITY says.
-- Django must connect as a separate, non-superuser role so RLS policies
-- (see backend/core/rls.py) are actually enforced. This role owns the
-- schema objects it creates via `manage.py migrate`, same as production
-- (where the RDS master user should likewise not be the app's runtime user).
DO
$$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'easybook_app') THEN
      CREATE ROLE easybook_app WITH LOGIN PASSWORD 'easybook_app' CREATEDB;
   END IF;
END
$$;

GRANT ALL PRIVILEGES ON DATABASE easybook TO easybook_app;
GRANT CREATE, USAGE ON SCHEMA public TO easybook_app;
