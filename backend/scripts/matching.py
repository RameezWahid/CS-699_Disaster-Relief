import json
import math
from datetime import datetime

from psycopg import Connection, Rollback

# Map the words used in reports to the resource types stored in the database.
NEED_KIND = {
    "rescue": "boat",
    "boat": "boat",
    "food": "food",
    "water": "water",
    "medicine": "medicine",
    "doctor": "medical_team",
    "blanket": "blanket",
    "shelter": "shelter_bed",
}

DEFAULT_PEOPLE = 4  # Assume four people if the report does not give a headcount.
BOAT_CAPACITY = 6  # One boat can carry six people.
PROPOSAL_TTL_MIN = 10
MAX_DISTANCE_M = 30_000
VERIFIED_DISTANCE_FACTOR = 0.8  # Give verified offers a better ranking.


def need_quantity(kind: str, headcount: int | None) -> int:
    people = headcount or DEFAULT_PEOPLE

    # Calculate how many boats are needed for the given number of people.
    if kind == "boat":
        return math.ceil(people / BOAT_CAPACITY)

    # Treat medicine and medical teams as one unit per need.
    if kind in ("medical_team", "medicine"):
        return 1

    return people


def upsert_needs(conn: Connection, incident_id: int) -> None:
    # Get the incident's headcount and the distinct needs reported for it.
    headcount, report_needs = conn.execute(
        """
        SELECT i.headcount, array(SELECT DISTINCT unnest(r.needs) FROM reports r
                                   WHERE r.incident_id = i.id)
          FROM incidents i WHERE i.id = %s
        """,
        (incident_id,),
    ).fetchone()

    # Convert the reported needs to resource types and process them in order.
    for kind in sorted({NEED_KIND[n] for n in report_needs if n in NEED_KIND}):
        qty = need_quantity(kind, headcount)
        conn.execute(
            """
            INSERT INTO needs (incident_id, kind, qty_total, qty_open) VALUES (%s, %s, %s, %s)
            ON CONFLICT (incident_id, kind) DO UPDATE
               SET qty_open = needs.qty_open + greatest(EXCLUDED.qty_total - needs.qty_total, 0),
                   qty_total = greatest(needs.qty_total, EXCLUDED.qty_total)
            """,
            (incident_id, kind, qty, qty),
        )


def claim(conn: Connection, need_id: int, offer_id: int, qty: int) -> int | None:
    with conn.transaction():
        # Reserve the requested quantity only if the offer is active and has enough stock.
        offer = conn.execute(
            """
            UPDATE offers SET qty_available = qty_available - %(q)s
             WHERE id = %(offer)s AND status = 'active' AND qty_available >= %(q)s
               AND kind = (SELECT kind FROM needs WHERE id = %(need)s)
            RETURNING id
            """,
            {"q": qty, "offer": offer_id, "need": need_id},
        ).fetchone()

        # Reduce the open need only if the offer was reserved successfully.
        need = offer and conn.execute(
            "UPDATE needs SET qty_open = qty_open - %s WHERE id = %s AND qty_open >= %s "
            "RETURNING id",
            (qty, need_id, qty),
        ).fetchone()

        # Roll back the transaction if the need cannot be updated.
        if not need:
            raise Rollback

        # Create a proposal that expires if it is not accepted within ten minutes.
        return conn.execute(
            """
            INSERT INTO allocations (need_id, offer_id, qty, status, expires_at)
            VALUES (%s, %s, %s, 'proposed', now() + make_interval(mins => %s)) RETURNING id
            """,
            (need_id, offer_id, qty, PROPOSAL_TTL_MIN),
        ).fetchone()[0]

    return None


def release(conn: Connection, offer_id: int, need_id: int, qty: int) -> None:
    # Restore the reserved stock and then increase the open need.
    conn.execute(
        "UPDATE offers SET qty_available = qty_available + %s WHERE id = %s",
        (qty, offer_id),
    )
    conn.execute(
        "UPDATE needs SET qty_open = qty_open + %s WHERE id = %s",
        (qty, need_id),
    )


def accept(conn: Connection, allocation_id: int) -> bool:
    # Accept the allocation only if it is still a pending proposal.
    return (
        conn.execute(
            "UPDATE allocations SET status = 'accepted', updated_at = now() "
            "WHERE id = %s AND status = 'proposed'",
            (allocation_id,),
        ).rowcount
        == 1
    )


def cancel(conn: Connection, allocation_id: int) -> bool:
    with conn.transaction():
        # Cancel the allocation and get the quantities that must be restored.
        row = conn.execute(
            "UPDATE allocations SET status = 'cancelled', updated_at = now() "
            "WHERE id = %s AND status IN ('proposed', 'accepted') "
            "RETURNING offer_id, need_id, qty",
            (allocation_id,),
        ).fetchone()

        # Return the reserved quantities if the allocation was cancelled.
        if row:
            release(conn, *row)

    return row is not None


def expire_proposals(conn: Connection) -> int:
    expired = 0

    # Find proposals that have passed their expiry time.
    ids = conn.execute(
        "SELECT id FROM allocations WHERE status = 'proposed' AND expires_at < now()"
    ).fetchall()

    for (allocation_id,) in ids:
        with conn.transaction():
            # Check the status again so an accepted proposal is not expired.
            row = conn.execute(
                "UPDATE allocations SET status = 'expired', updated_at = now() "
                "WHERE id = %s AND status = 'proposed' RETURNING offer_id, need_id, qty",
                (allocation_id,),
            ).fetchone()

            # Release the reserved quantities only if the proposal was expired.
            if row:
                release(conn, *row)
                expired += 1

    return expired


def candidate_offers(conn: Connection, need_id: int) -> list[tuple]:
    # Find active offers of the required type within 30 km of the incident.
    # Verified offers get a better ranking but must still pass the actual distance check.
    return conn.execute(
        """
        SELECT o.id, o.qty_available,
               ST_Distance(o.geom::geography, i.geom::geography) AS distance_m
          FROM needs n JOIN incidents i ON i.id = n.incident_id
          JOIN offers o ON o.kind = n.kind
         WHERE n.id = %(need)s AND o.status = 'active' AND o.qty_available > 0
           AND ST_DWithin(o.geom::geography, i.geom::geography, %(max)s)
         ORDER BY ST_Distance(o.geom::geography, i.geom::geography)
                  * CASE WHEN o.verified THEN %(factor)s ELSE 1 END
         LIMIT 10
        """,
        {"need": need_id, "max": MAX_DISTANCE_M, "factor": VERIFIED_DISTANCE_FACTOR},
    ).fetchall()


def propose(conn: Connection) -> list[dict]:
    # Get open needs for active incidents that do not already have a pending proposal.
    needs = conn.execute(
        """
        SELECT n.id, n.qty_open, i.id, i.ward FROM needs n JOIN incidents i ON i.id = n.incident_id
         WHERE n.qty_open > 0 AND i.status IN ('open', 'verified') AND i.geom IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM allocations a
                            WHERE a.need_id = n.id AND a.status = 'proposed')
         ORDER BY i.urgency_level DESC, i.first_report_at
        """
    ).fetchall()

    proposals = []

    for need_id, qty_open, incident_id, ward in needs:
        # Try the closest suitable offers first.
        for offer_id, qty_available, distance_m in candidate_offers(conn, need_id):
            # Do not reserve more than either side needs or has available.
            qty = min(qty_open, qty_available)
            allocation_id = claim(conn, need_id, offer_id, qty)

            # Try another offer if this one could not be reserved.
            if allocation_id is None:
                continue

            summary = {
                "allocation_id": allocation_id,
                "need_id": need_id,
                "incident_id": incident_id,
                "offer_id": offer_id,
                "qty": qty,
                "distance_m": round(distance_m),
                "ward": ward,
            }

            # Record the proposed allocation in the event log.
            conn.execute(
                "INSERT INTO event_log (kind, ward, payload) VALUES ('allocation.proposed', %s, %s)",
                (ward, json.dumps(summary)),
            )
            proposals.append(summary)

            # Make only one proposal for each need in this pass.
            break

    return proposals