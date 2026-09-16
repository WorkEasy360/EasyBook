-- pgvector is NOT a "trusted" extension (its control file has no
-- `trusted = true`), so the non-superuser `easybook_app` role cannot run
-- CREATE EXTENSION itself. The superuser installs it once here:
--   * in `easybook`, the application database;
--   * in `template1`, so every database created afterwards - including the
--     `test_easybook` database Django's test runner creates as easybook_app -
--     inherits it.
-- backend/ai/migrations/0001_initial.py only VERIFIES the extension exists;
-- it never needs superuser. Like 01-app-role.sql this only runs against an
-- empty data volume — for an existing volume run the two statements by hand
-- as the `easybook` superuser (see infrastructure/CLAUDE.md).
CREATE EXTENSION IF NOT EXISTS vector;
\connect template1
CREATE EXTENSION IF NOT EXISTS vector;
