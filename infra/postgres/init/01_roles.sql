-- Runs once, on first container start.
--
-- Two database roles:
--   procurax      : owner / migration role (superuser in local docker only)
--   procurax_app  : runtime role used by the API. NOT a superuser, NOT the table owner,
--                   so PostgreSQL Row-Level Security policies always apply to it.
-- Table-level grants for procurax_app are issued by the Alembic migrations.

CREATE ROLE procurax_app LOGIN PASSWORD 'procurax_app_dev' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

CREATE DATABASE procurax_test OWNER procurax;

GRANT CONNECT ON DATABASE procurax TO procurax_app;
GRANT CONNECT ON DATABASE procurax_test TO procurax_app;
