"""Group reports into incidents. One active incident per area/category group."""

import json
import logging
from datetime import datetime

from psycopg import Connection

from scripts.matching import merge_needs

# Unknown classifier labels fall back to "info".
from scripts.triage import (
    CATEGORY_GROUP,
    CATEGORY_SEVERITY,
    VERIFY_BELOW_CREDIBILITY,
    credibility,
    urgency,
)

JOIN_RADIUS_M = 150
ACTIVE_WINDOW_MIN = 60
INCIDENT_LOCK = 4202  # Serialize assignment. Without this, workers can create duplicate incidents.
# An incident this close to a chronic flood spot gets the chronic-spot credibility signal.
CHRONIC_SPOT_RADIUS_M = 300

# Silent zones: an H3 cell that was reporting and went quiet while the rest of the city did not.
# Silence there often means dead phones or no power, not that the flood is over.
SILENT_QUIET_MIN = 60  # no report from the cell for this long ...
SILENT_BEFORE_MIN = 120  # ... after reports in the window just before it
SILENT_MIN_SENDERS = 3  # from this many people, so one sender stopping is not silence
SILENT_MIN_CITY_REPORTS = 10  # and the city still sent this many reports while the cell was quiet

log = logging.getLogger("incidents")


def assign_incident(conn: Connection, report_id: int) -> tuple[int, bool]:
    """Reuse a nearby active incident or create one."""
    # Keep incident assignment serialized. Two workers must not create the same incident.
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (INCIDENT_LOCK,))

    category, received_at, located, repeat_incident = conn.execute(
        """
        SELECT r.category, r.received_at, r.geom IS NOT NULL,
               (SELECT o.incident_id FROM reports o JOIN incidents i ON i.id = o.incident_id
                 WHERE o.id = r.repeat_of AND i.status IN ('open', 'verified'))
          FROM reports r WHERE r.id = %s
        """, (report_id,)
    ).fetchone()
    group = CATEGORY_GROUP.get(category, "info")
    row = None

    if repeat_incident is not None:
        # A forwarded copy belongs with the report it copies, wherever it says it is.
        row = (repeat_incident,)
    # No coordinates means no spatial match. Do not guess where the report belongs.
    elif located:
        row = conn.execute(
            """
            SELECT i.id FROM incidents i, reports r
             WHERE r.id = %(id)s AND i.status IN ('open', 'verified')
               AND i.category_group = %(group)s AND i.category <> 'silent_zone'
               AND i.last_report_at > r.received_at - make_interval(mins => %(window)s)
               AND ST_DWithin(i.geom::geography, r.geom::geography,
                              %(radius)s + coalesce(r.loc_uncertainty_m, 0))
             ORDER BY ST_Distance(i.geom::geography, r.geom::geography) LIMIT 1
            """,
            {"id": report_id, "group": group, "window": ACTIVE_WINDOW_MIN, "radius": JOIN_RADIUS_M},
        ).fetchone()

    created = row is None
    if created:
        # Insert a valid row first. Refresh it after attaching the report.
        row = conn.execute(
            """
            INSERT INTO incidents (category_group, category, urgency_level, urgency, credibility,
                                   credibility_signals, report_count, sender_count,
                                   first_report_at, last_report_at)
            VALUES (%s, %s, 1, '{}', 0, '{}', 0, 0, %s, %s) RETURNING id
            """,
            (group, category, received_at, received_at),
        ).fetchone()

    # The report-to-incident link is the source used by the refresh query below.
    conn.execute("UPDATE reports SET incident_id = %s WHERE id = %s", (row[0], report_id))
    return row[0], created


def refresh_incident(conn: Connection, incident_id: int) -> dict:
    """Recompute incident fields from its attached reports."""
    # Derive counts and location from reports. Do not maintain a second, drifting copy.
    ward, count = conn.execute(
        """
        UPDATE incidents SET
            geom = best.geom, uncertainty_m = best.loc_uncertainty_m, ward = best.ward,
            headcount = s.headcount, headcount_conflict = s.conflict,
            report_count = s.n, sender_count = s.senders,
            first_report_at = s.first_at, last_report_at = s.last_at
          FROM (SELECT max(headcount) AS headcount, count(DISTINCT headcount) > 1 AS conflict,
                       count(*) AS n,
                       count(DISTINCT sender_hash) AS senders,
                       min(received_at) AS first_at, max(received_at) AS last_at
                  FROM reports WHERE incident_id = %(id)s) s
          LEFT JOIN (SELECT geom, loc_uncertainty_m, ward FROM reports
                      WHERE incident_id = %(id)s AND geom IS NOT NULL
                      ORDER BY loc_confidence DESC NULLS LAST, received_at DESC LIMIT 1) best
                 ON true
         WHERE incidents.id = %(id)s
        RETURNING incidents.ward, incidents.report_count
        """,
        {"id": incident_id},
    ).fetchone()

    # Urgency and credibility depend on the totals above, so compute them afterwards.
    triage = refresh_triage(conn, incident_id)
    return {"incident_id": incident_id, "ward": ward, "report_count": count,
            "urgency_level": triage["urgency_level"], "credibility": triage["credibility"],
            "needs_verification": triage["needs_verification"], "fast_track": triage["fast_track"]}


def refresh_triage(conn: Connection, incident_id: int) -> dict | None:
    """Recompute urgency (it grows with waiting time) and credibility from attached reports."""
    row = conn.execute(
        """
        SELECT i.headcount, i.status, i.urgency_level, i.needs_verification,
               array_agg(DISTINCT r.category),
               -- Forwarded copies (repeat_of set) are not independent evidence.
               greatest(count(DISTINCT r.sender_hash) FILTER (WHERE r.repeat_of IS NULL), 1),
               bool_or(r.has_gps), bool_or(r.has_photo), bool_or(r.source = 'responder'),
               bool_or(cardinality(r.vulnerability) > 0),
               min(r.extraction ->> 'override'),
               EXISTS (SELECT 1 FROM places p
                        WHERE p.kind = 'chronic_spot' AND i.geom IS NOT NULL
                          AND ST_DWithin(p.geom::geography, i.geom::geography, %(spot_m)s)),
               extract(epoch FROM app_now() - i.first_report_at) / 60,
               -- An official alert for this ward (or the city) while reports were coming in.
               EXISTS (SELECT 1 FROM alerts a
                        WHERE (a.ward IS NULL OR a.ward = i.ward)
                          AND i.last_report_at BETWEEN a.starts_at AND a.ends_at)
          FROM incidents i JOIN reports r ON r.incident_id = i.id
         WHERE i.id = %(id)s
         GROUP BY i.id
        """,
        {"id": incident_id, "spot_m": CHRONIC_SPOT_RADIUS_M},
    ).fetchone()
    if row is None:
        return None
    (headcount, status, old_level, old_verify, categories, senders, gps, photo, responder,
     vulnerable, override, chronic_spot, waited_min, official_alert) = row

    # The most severe category among the reports speaks for the incident.
    category = max(categories, key=lambda c: CATEGORY_SEVERITY.get(c, 0))
    u = urgency(category, headcount, vulnerable, float(waited_min), override)
    # TODO(rain): heavy rain nearby (Open-Meteo) as a credibility signal. Until then it stays off.
    c = credibility(senders, gps, photo, False, chronic_spot, official_alert, responder)
    # Coordinators check low-credibility incidents. Urgency 5 is acted on while being checked.
    needs_verification = status == "open" and c["R"] < VERIFY_BELOW_CREDIBILITY
    fast_track = u["level"] == 5

    conn.execute(
        """
        UPDATE incidents SET category = %s, urgency_level = %s, urgency = %s, credibility = %s,
                             credibility_signals = %s, needs_verification = %s, fast_track = %s
         WHERE id = %s
        """,
        (category, u["level"], json.dumps(u), c["R"], json.dumps(c["signals"]),
         needs_verification, fast_track, incident_id),
    )
    return {"urgency_level": u["level"], "credibility": c["R"],
            "needs_verification": needs_verification, "fast_track": fast_track,
            "changed": (u["level"], needs_verification) != (old_level, old_verify)}


def refresh_active(conn: Connection) -> int:
    """Refresh triage on every active incident. Returns how many changed level or queue."""
    changed = 0
    ids = conn.execute("SELECT id FROM incidents WHERE status IN ('open', 'verified')").fetchall()
    for (incident_id,) in ids:
        with conn.transaction():
            triage = refresh_triage(conn, incident_id)
            # Log only real changes. Waiting time alone moves S every minute.
            if triage and triage["changed"]:
                changed += 1
                conn.execute(
                    "INSERT INTO event_log (kind, ward, payload) "
                    "SELECT 'incident.updated', ward, %s FROM incidents WHERE id = %s",
                    (json.dumps({"incident_id": incident_id, **triage}), incident_id),
                )
    return changed


def detect_silent_zones(conn: Connection, at: datetime | None = None) -> list[dict]:
    """Open a 'silent_zone' incident for each cell that stopped reporting. Returns the new ones.

    A silent zone goes to the verification queue as a "go and check" task, at the highest
    urgency its earlier reports had. It has no reports, so it never gets needs or proposals.
    """
    # Times follow the app clock, so a replay finds silence in scenario time.
    at = at or conn.execute("SELECT app_now()").fetchone()[0]
    with conn.transaction():
        # Same lock as assign_incident, so two workers cannot open the same silent zone.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (INCIDENT_LOCK,))
        rows = conn.execute(
            """
            WITH city AS (
                SELECT count(*) AS n FROM reports
                 WHERE received_at > %(at)s - make_interval(mins => %(quiet)s)
                   AND received_at <= %(at)s
            ), quiet AS (
                SELECT c.h3, c.ward, ST_Centroid(c.geom) AS pt, max(r.urgency_level) AS level,
                       count(DISTINCT r.sender_hash) AS senders,
                       min(r.received_at) AS first_at, max(r.received_at) AS last_at
                  FROM reports r JOIN cells c ON ST_Contains(c.geom, r.geom)
                 WHERE r.received_at > %(at)s - make_interval(mins => %(quiet)s + %(before)s)
                   AND r.received_at <= %(at)s
                 GROUP BY c.h3
                HAVING count(DISTINCT r.sender_hash) >= %(senders)s
                   AND max(r.received_at) <= %(at)s - make_interval(mins => %(quiet)s)
            )
            INSERT INTO incidents (category_group, category, geom, ward, urgency_level, urgency,
                                   credibility, credibility_signals, needs_verification,
                                   report_count, sender_count, first_report_at, last_report_at)
            SELECT 'info', 'silent_zone', q.pt, q.ward, q.level,
                   jsonb_build_object('level', q.level, 'silent', jsonb_build_object(
                       'h3', q.h3, 'senders_before', q.senders, 'city_reports', city.n)),
                   0, '{}', true, 0, 0, q.first_at, q.last_at
              FROM quiet q, city
             WHERE city.n >= %(city_min)s
               -- One alert per silence: a cell that reports again and then goes quiet again
               -- has a later last_at, so it gets a new alert. A dismissed one is not repeated.
               AND NOT EXISTS (SELECT 1 FROM incidents i
                                WHERE i.category = 'silent_zone' AND ST_Equals(i.geom, q.pt)
                                  AND i.last_report_at >= q.last_at)
            RETURNING id, ward, urgency_level, urgency -> 'silent'
            """,
            {"at": at, "quiet": SILENT_QUIET_MIN, "before": SILENT_BEFORE_MIN,
             "senders": SILENT_MIN_SENDERS, "city_min": SILENT_MIN_CITY_REPORTS},
        ).fetchall()

        created = []
        for incident_id, ward, level, silent in rows:
            summary = {"incident_id": incident_id, "ward": ward, "urgency_level": level,
                       "silent_zone": silent}
            conn.execute(
                "INSERT INTO event_log (kind, ward, payload) VALUES ('incident.created', %s, %s)",
                (ward, json.dumps(summary)),
            )
            created.append(summary)
    # TODO(silent): close an open silent zone automatically once its cell reports again. Until
    # then a coordinator resolves it. Later, expect a rate from rainfall and past events instead
    # of requiring earlier reports, so areas that were never able to report are found too.
    return created


def merge_drifted(conn: Connection) -> list[dict]:
    """Merge active incidents that have drifted within JOIN_RADIUS_M of each other.

    DBSCAN runs on points projected to UTM 43N (EPSG:32643) so eps is in metres. The oldest
    incident in each cluster survives; the others are marked merged into it.
    """
    clusters = conn.execute(
        """
        SELECT array_agg(id ORDER BY id) FROM (
            SELECT id, category_group,
                   ST_ClusterDBSCAN(ST_Transform(geom, 32643), eps := %(eps)s, minpoints := 1)
                       OVER (PARTITION BY category_group) AS cluster
              FROM incidents
             WHERE status IN ('open', 'verified') AND geom IS NOT NULL
               AND category <> 'silent_zone'
               AND last_report_at > app_now() - make_interval(mins => %(window)s)
        ) clustered
         GROUP BY category_group, cluster HAVING count(*) > 1
        """,
        {"eps": JOIN_RADIUS_M, "window": ACTIVE_WINDOW_MIN},
    ).fetchall()
    merged = []
    for (ids,) in clusters:
        with conn.transaction():
            # Same lock as assign_incident, so a new report cannot join an incident mid-merge.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (INCIDENT_LOCK,))
            keep, absorbed = ids[0], ids[1:]
            conn.execute("UPDATE reports SET incident_id = %s WHERE incident_id = ANY(%s)",
                         (keep, absorbed))
            # If any of them was verified, the merged incident is verified.
            conn.execute(
                "UPDATE incidents SET status = 'verified' WHERE id = %s AND EXISTS ("
                "SELECT 1 FROM incidents WHERE id = ANY(%s) AND status = 'verified')",
                (keep, absorbed),
            )
            conn.execute("UPDATE incidents SET status = 'merged', merged_into = %s "
                         "WHERE id = ANY(%s)", (keep, absorbed))
            merge_needs(conn, keep, absorbed)
            summary = {**refresh_incident(conn, keep), "absorbed": absorbed}
            conn.execute("INSERT INTO event_log (kind, ward, payload) "
                         "VALUES ('incident.merged', %s, %s)", (summary["ward"], json.dumps(summary)))
            merged.append(summary)
    if merged:
        log.info("merged %d incident clusters", len(merged))
    return merged


def attach_report(conn: Connection, report_id: int) -> int:
    """Attach a report, refresh the incident, and record the change."""
    incident_id, created = assign_incident(conn, report_id)

    # Recompute after attachment so the new report is included in the totals.
    summary = refresh_incident(conn, incident_id)

    # Record the result in the same transaction as the incident update.
    conn.execute(
        "INSERT INTO event_log (kind, ward, payload) VALUES (%s, %s, %s)",
        ("incident.created" if created else "incident.updated", summary["ward"], json.dumps(summary)),
    )
    return incident_id