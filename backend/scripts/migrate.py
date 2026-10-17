"""Run each database migration once, in filename order."""

import logging

import psycopg
from psycopg import sql

from scripts.config import APP_DB_PASSWORD, DATABASE_URL, REPO_DIR

MIGRATIONS_DIR = REPO_DIR / "db/migrations"

# Every migrator must use the same ID. Otherwise, the lock is useless.
MIGRATION_LOCK_ID = 7_261_001


def migrate() -> None:
    paths = sorted(MIGRATIONS_DIR.glob("*.sql"))

    # Do not report success when there is nothing to run.
    if not paths:
        raise SystemExit(f"no migrations found in {MIGRATIONS_DIR}")

    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        # Let one process migrate at a time. PostgreSQL drops the lock when the connection closes.
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))

        # Keep a record of migrations that have already completed.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY)"
        )
        applied = {
            row[0] for row in conn.execute("SELECT name FROM schema_migrations")
        }

        for path in paths:
            # Do not run the same migration twice.
            if path.name in applied:
                continue

            # Apply the SQL and record it together. If either fails, roll back both.
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations VALUES (%s)", (path.name,)
                )

            logging.info("applied %s", path.name)

        if APP_DB_PASSWORD:
            # Check that the role exists before trying to change its password.
            has_role = conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = 'relief_app'"
            ).fetchone()

            if has_role:
                # Quote the password as a SQL value. Do not build this statement by hand.
                conn.execute(
                    sql.SQL("ALTER ROLE relief_app PASSWORD {}").format(
                        sql.Literal(APP_DB_PASSWORD)
                    )
                )
            else:
                # Keep going, but make the missing role visible.
                logging.warning("relief_app role missing; password not set")


if __name__ == "__main__":
    # Show useful progress without dumping unnecessary detail.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    migrate()