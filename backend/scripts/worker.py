import json
import logging
import time
import psycopg
from psycopg import Connection

from scripts.classifier import load_classifier
from scripts.config import DATABASE_URL
from scripts.extract import extract
from scripts.geocode import geocode
from scripts.incidents import attach_report, detect_silent_zones
from scripts.matching import upsert_needs
from scripts.text import normalize, simhash, tag_language, tokens
from scripts.triage import CATEGORY_SEVERITY, urgency

# Stop retrying after three failed attempts.
MAX_ATTEMPTS = 3

# Poll periodically in case a notification is missed.
IDLE_POLL_S = 5

# Look for cells that went quiet this often, even while messages keep arriving.
SILENT_CHECK_S = 60

# A report within this many SimHash bits of a recent report is a near copy of it.
# Reports are short, so one changed word ("mein" -> "me") moves about 5 bits, while
# unrelated reports sit around 25 bits apart. Tune on the gold set.
REPEAT_MAX_BITS = 6
REPEAT_WINDOW_MIN = 60

# A category picked by the citizen counts for less than a rule match.
HINT_CATEGORY_P = 0.5

log = logging.getLogger("worker")


def analyse(payload: dict, conn: Connection | None = None) -> dict:
    """Run the text pipeline on one inbox payload. Without conn it uses GPS only (unit tests)."""
    text = payload.get("text") or ""
    norm = normalize(text)
    fields = extract(norm)
    category, category_p = fields["category"]["label"], fields["category"]["p"]

    # Rules found nothing specific. Ask MuRIL, if a trained model is installed.
    classifier = load_classifier() if category == "info_noise" and norm else None
    if classifier:
        guess = classifier.classify(norm)
        if guess["label"] != "info_noise":
            category, category_p = guess["label"], guess["p"]

    # Still nothing. Use the category the citizen picked, if any.
    # A hard rule (trapped, drowning, ...) already forced rescue, so it is never overridden here.
    hint = payload.get("category_hint")
    if category == "info_noise" and hint in CATEGORY_SEVERITY:
        category, category_p = hint, HINT_CATEGORY_P

    headcount = fields["headcount"]["value"] if fields["headcount"] else None
    # Waiting time is zero at intake. Incidents account for it as they age.
    triage = urgency(category, headcount, bool(fields["vulnerability"]), 0, fields["override"])

    lat, lon = payload.get("lat"), payload.get("lon")
    has_gps = lat is not None and lon is not None
    if conn is not None:
        # A GPS pin inside Mumbai wins. Otherwise match a place named in the text.
        location = geocode(conn, norm, lat, lon)
        if location["source"] == "gps" and payload.get("accuracy_m"):
            location["uncertainty_m"] = payload["accuracy_m"]
        lat, lon = location["lat"], location["lon"]
        has_gps = location["source"] == "gps"
    else:
        lat, lon = (lat, lon) if has_gps else (None, None)
        location = {
            "name": None,
            "source": "gps" if has_gps else None,
            "confidence": 1.0 if has_gps else 0.0,
            "uncertainty_m": payload.get("accuracy_m") if has_gps else None,
            "ambiguous": False,
        }
    # TODO(gazetteer): only the seeded places are matched. Add OSM roads, stations and localities.

    return {
        "text": text,
        "lang": tag_language(norm),
        # Empty or punctuation-only text has no features, so it must not match other reports.
        "simhash": simhash(norm) if tokens(norm) else None,
        "category": category,
        "category_p": category_p,
        "headcount": headcount,
        "needs": fields["needs"],
        "vulnerability": fields["vulnerability"],
        "override": fields["override"],
        "urgency": triage,
        "location": location,
        "has_gps": has_gps,
        "lat": lat,
        "lon": lon,
    }


def find_repeat(conn: Connection, received_at, sh: int | None) -> int | None:
    # Point at the closest recent near copy. Repeats still join incidents as extra evidence.
    if sh is None:
        return None
    row = conn.execute(
        """
        SELECT id FROM reports
         WHERE received_at BETWEEN %(at)s - make_interval(mins => %(window)s) AND %(at)s
           AND bit_count((simhash # %(h)s)::bit(64)) <= %(bits)s
         ORDER BY bit_count((simhash # %(h)s)::bit(64)), received_at
         LIMIT 1
        """,
        {"at": received_at, "window": REPEAT_WINDOW_MIN, "h": sh, "bits": REPEAT_MAX_BITS},
    ).fetchone()
    return row[0] if row else None


def process(conn: Connection, inbox_id, source, sender, received_at, payload) -> None:
    r = analyse(payload, conn)
    loc = r["location"]

    # Create the report only if this inbox row has not produced one already.
    # Resolve the ward from the report's coordinates. A NULL point gives a NULL ward.
    row = conn.execute(
        """
        INSERT INTO reports (inbox_id, received_at, source, sender_hash, text, lang, simhash,
                             repeat_of, has_gps, has_photo, category, category_p, headcount,
                             needs, vulnerability, geom, ward, loc_uncertainty_m, loc_confidence,
                             loc_source, urgency_level, needs_review, extraction)
        SELECT %(id)s, %(at)s, %(source)s, %(sender)s, %(text)s, %(lang)s, %(simhash)s,
               %(repeat_of)s, %(gps)s, %(photo)s, %(category)s, %(category_p)s, %(headcount)s,
               %(needs)s, %(vulnerability)s, pt,
               (SELECT code FROM wards WHERE ST_Contains(geom, pt) LIMIT 1),
               %(uncertainty)s, %(confidence)s, %(loc_source)s,
               %(urgency)s, %(review)s, %(extraction)s::jsonb
          FROM (SELECT ST_SetSRID(ST_MakePoint(%(lon)s::float8, %(lat)s::float8), 4326) AS pt) p
        ON CONFLICT (inbox_id) DO NOTHING
        RETURNING id, ward
        """,
        {"id": inbox_id, "at": received_at, "sender": sender,
         # Reports sent by a logged-in responder count as responder evidence.
         "source": "responder" if payload.get("responder") else source,
         "text": r["text"], "lang": r["lang"], "simhash": r["simhash"] or 0,
         "repeat_of": find_repeat(conn, received_at, r["simhash"]),
         "gps": r["has_gps"], "photo": bool(payload.get("photo")),
         "category": r["category"], "category_p": r["category_p"], "headcount": r["headcount"],
         "needs": r["needs"], "vulnerability": r["vulnerability"],
         "lat": r["lat"], "lon": r["lon"], "uncertainty": loc["uncertainty_m"],
         "confidence": loc["confidence"], "loc_source": loc["source"],
         "urgency": r["urgency"]["level"],
         # Coordinators check reports with no location, an ambiguous place match,
         # or no recognisable category.
         "review": r["lat"] is None or loc["ambiguous"] or r["category"] == "info_noise",
         "extraction": json.dumps(r, ensure_ascii=False)},
    ).fetchone()

    # Do not log or attach the same report twice if an earlier attempt already inserted it.
    if row:
        report_id, ward = row
        conn.execute(
            "INSERT INTO event_log (kind, ward, payload) VALUES ('report.created', %s, %s)",
            (ward, json.dumps({"report_id": report_id})),
        )
        incident_id = attach_report(conn, report_id)
        upsert_needs(conn, incident_id)

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

        # TODO(periodic): every ~30 s between messages, run the work that does not depend on a
        # new report: incidents.refresh_active() (urgency grows with waiting time), then
        # matching.expire_proposals() and matching.propose(). Until then nothing creates
        # proposals or returns stock from expired ones.
        # TODO(later): DBSCAN merging of incidents that drift together, rainfall from
        # Open-Meteo and official alerts as credibility signals.
        next_silent_check = 0.0
        while True:
            if time.monotonic() >= next_silent_check:
                for zone in detect_silent_zones(conn):
                    log.warning("silent zone: %s", zone)
                next_silent_check = time.monotonic() + SILENT_CHECK_S

            # Drain available work before waiting for another notification.
            if not claim_and_process(conn):
                # Notifications wake the worker early. The timeout also handles missed notifications.
                for _ in listener.notifies(timeout=IDLE_POLL_S, stop_after=1):
                    pass


if __name__ == "__main__":
    # Show progress and errors without adding debug-level noise.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()