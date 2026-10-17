import json
import logging
import psycopg
from psycopg import Connection

from scripts.config import DATABASE_URL

# Stop retrying after three failed attempts.
MAX_ATTEMPTS = 3

# Poll periodically in case a notification is missed.
IDLE_POLL_S = 5

log = logging.getLogger("worker")

def analyse(payload: dict) -> dict:
    # Placeholder for the NLP pipeline: normalize, extract, classify, geocode, and triage.
    return {"text": payload.get("text", ""), "lang": "en", "category": "unknown", "urgency": 1}

def process(conn: Connection, inbox_id, source, sender, received_at, payload) -> None:
    # Extract the report fields. Replace this stub when the NLP pipeline is ready.
    r = analyse(payload)
    lat, lon = payload.get("lat"), payload.get("lon")

    # Create the report only if this inbox row has not produced one already.
    # Resolve the ward from the report's coordinates.
    row = conn.execute(
        """
        INSERT INTO reports (inbox_id, received_at, source, sender_hash, text, lang, simhash,
                             has_gps, has_photo, category, category_p, needs, vulnerability,
                             geom, ward, urgency_level, needs_review, extraction)
        SELECT %(id)s, %(at)s, %(source)s, %(sender)s, %(text)s, %(lang)s, 0,
               %(gps)s, %(photo)s, %(category)s, 0, '{}', '{}', pt,
               (SELECT code FROM wards WHERE ST_Contains(geom, pt) LIMIT 1),
               %(urgency)s, true, %(extraction)s::jsonb
          FROM (SELECT ST_SetSRID(ST_MakePoint(%(lon)s::float8, %(lat)s::float8), 4326) AS pt) p
        ON CONFLICT (inbox_id) DO NOTHING
        RETURNING id, ward
        """,
        {"id": inbox_id, "at": received_at, "source": source, "sender": sender,
         "text": r["text"], "lang": r["lang"], "gps": lat is not None,
         "photo": bool(payload.get("photo")), "category": r["category"],
         "urgency": r["urgency"], "lat": lat, "lon": lon, "extraction": json.dumps(r)},
    ).fetchone()

    # Do not log the same report twice if an earlier attempt already inserted it.
    if row:
        conn.execute(
            "INSERT INTO event_log (kind, ward, payload) VALUES ('report.created', %s, %s)",
            (row[1], json.dumps({"report_id": row[0]})),
        )

def claim_and_process(conn: Connection) -> bool:
    # Claim one row. Return False when there is no work to do.
    with conn.transaction():
        # SKIP LOCKED lets another worker take a different row instead of waiting.
        row = conn.execute(
            "SELECT id, source, sender_hash, received_at, payload, attempts FROM inbox "
            "WHERE status = 'pending' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"
        ).fetchone()

        if row is None:
            return False

        try:
            # Keep report creation separate from the outer transaction.
            # A failed processing attempt must not leave a partial report behind.
            with conn.transaction():
                process(conn, *row[:5])

        except psycopg.OperationalError:
            # A database failure is not a normal message error. Let the supervisor restart us.
            raise

        except Exception as error:
            # Record the failure and stop retrying after the attempt limit.
            log.exception("inbox %s failed", row[0])
            status = "dead" if row[5] + 1 >= MAX_ATTEMPTS else "pending"
            conn.execute(
                "UPDATE inbox SET attempts = attempts + 1, last_error = %s, status = %s "
                "WHERE id = %s", (repr(error), status, row[0]))
            return True

        # The report succeeded. Mark the inbox row as done in the same outer transaction.
        conn.execute("UPDATE inbox SET status = 'done', attempts = attempts + 1 WHERE id = %s", (row[0],))

    return True


def run() -> None:
    log.info("worker starting")

    # Use one connection for database work and another for notifications.
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn, \
            psycopg.connect(DATABASE_URL, autocommit=True) as listener:
        # PostgreSQL sends notifications when new inbox rows arrive.
        listener.execute("LISTEN inbox")

        while True:
            # Drain available work before waiting for another notification.
            if not claim_and_process(conn):
                # Notifications wake the worker early. The timeout also handles missed notifications.
                for _ in listener.notifies(timeout=IDLE_POLL_S, stop_after=1):
                    pass


if __name__ == "__main__":
    # Show progress and errors without adding debug-level noise.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()