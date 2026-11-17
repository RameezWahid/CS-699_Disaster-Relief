"""Group reports into incidents. One active incident per area/category group."""

import json

from psycopg import Connection

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


def assign_incident(conn: Connection, report_id: int) -> tuple[int, bool]:
    """Reuse a nearby active incident or create one."""
    # Keep incident assignment serialized. Two workers must not create the same incident.
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (INCIDENT_LOCK,))

    category, received_at, located = conn.execute(
        "SELECT category, received_at, geom IS NOT NULL FROM reports WHERE id = %s", (report_id,)
    ).fetchone()
    group = CATEGORY_GROUP.get(category, "info")
    row = None

    # No coordinates means no spatial match. Do not guess where the report belongs.
    if located:
        row = conn.execute(
            """
            SELECT i.id FROM incidents i, reports r
             WHERE r.id = %(id)s AND i.status IN ('open', 'verified')
               AND i.category_group = %(group)s
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
               extract(epoch FROM now() - i.first_report_at) / 60
          FROM incidents i JOIN reports r ON r.incident_id = i.id
         WHERE i.id = %(id)s
         GROUP BY i.id
        """,
        {"id": incident_id, "spot_m": CHRONIC_SPOT_RADIUS_M},
    ).fetchone()
    if row is None:
        return None
    (headcount, status, old_level, old_verify, categories, senders, gps, photo, responder,
     vulnerable, override, chronic_spot, waited_min) = row

    # The most severe category among the reports speaks for the incident.
    category = max(categories, key=lambda c: CATEGORY_SEVERITY.get(c, 0))
    u = urgency(category, headcount, vulnerable, float(waited_min), override)
    # Rainfall and official alerts have no data source yet, so those signals stay off.
    c = credibility(senders, gps, photo, False, chronic_spot, False, responder)
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