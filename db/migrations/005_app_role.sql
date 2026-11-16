-- Runtime services (api, worker, bot) connect as relief_app, not as the database owner.
-- migrate.py sets the role's password from APP_DB_PASSWORD after migrations run.
-- Later migrations that add tables must grant on them explicitly; nothing is granted by default.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relief_app') THEN
        CREATE ROLE relief_app LOGIN;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO relief_app;

-- Read and write application tables. No DELETE: rows change status instead of disappearing.
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO relief_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO relief_app;

-- Reference data and migration history are written only by setup.
REVOKE INSERT, UPDATE ON wards, cells, places, place_names, schema_migrations, spatial_ref_sys
    FROM relief_app;

-- The audit log is append-only for the application.
REVOKE UPDATE ON event_log FROM relief_app;
