"""Group reports into incidents. One active incident per area/category group."""

import json

from psycopg import Connection

JOIN_RADIUS_M = 150
ACTIVE_WINDOW_MIN = 60
INCIDENT_LOCK = 4202  # Serialize assignment. Without this, workers can create duplicate incidents.

# Temporary mapping. Unknown classifier labels fall back to "info".
CATEGORY_GROUP = {"rescue": "rescue", "medical": "medical", "supply": "supply", "road": "road"}


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
    # Derive counts and urgency from reports. Do not maintain a second, drifting copy.
    ward, level, count = conn.execute(
        """
        UPDATE incidents SET
            geom = best.geom, uncertainty_m = best.loc_uncertainty_m, ward = best.ward,
            headcount = s.headcount, headcount_conflict = s.conflict,
            urgency_level = s.level, urgency = jsonb_build_object('level', s.level),
            report_count = s.n, sender_count = s.senders,
            first_report_at = s.first_at, last_report_at = s.last_at
          FROM (SELECT max(headcount) AS headcount, count(DISTINCT headcount) > 1 AS conflict,
                       max(urgency_level) AS level, count(*) AS n,
                       count(DISTINCT sender_hash) AS senders,
                       min(received_at) AS first_at, max(received_at) AS last_at
                  FROM reports WHERE incident_id = %(id)s) s
          LEFT JOIN (SELECT geom, loc_uncertainty_m, ward FROM reports
                      WHERE incident_id = %(id)s AND geom IS NOT NULL
                      ORDER BY loc_confidence DESC NULLS LAST, received_at DESC LIMIT 1) best
                 ON true
         WHERE incidents.id = %(id)s
        RETURNING incidents.ward, incidents.urgency_level, incidents.report_count
        """,
        {"id": incident_id},
    ).fetchone()

    # Return the summary needed by the caller and the event log.
    return {"incident_id": incident_id, "ward": ward, "urgency_level": level, "report_count": count}


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