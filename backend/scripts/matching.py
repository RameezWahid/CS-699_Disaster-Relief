import json
import math
import uuid

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
OFFER_KINDS = sorted(set(NEED_KIND.values()))
# Boats and medical teams go back into the offer once a delivery is done. The rest is used up.
REUSABLE_KINDS = {"boat", "medical_team"}
PROPOSAL_TTL_MIN = 10
# Search outward until enough candidates are found, up to 30 km.
SEARCH_RADII_M = [1_000, 3_000, 10_000, 30_000]
CANDIDATES_WANTED = 3
VERIFIED_DISTANCE_FACTOR = 0.8  # Give verified offers a better ranking.

# action -> (statuses it may start from, new status).
TRANSITIONS = {
    "accept": (("proposed",), "accepted"),
    "dispatch": (("accepted",), "dispatched"),
    "deliver": (("dispatched",), "delivered"),
    "cancel": (("proposed", "accepted", "dispatched"), "cancelled"),
}


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


def merge_needs(conn: Connection, keep: int, absorbed: list[int]) -> None:
    """Move the needs of merged incidents onto the surviving one, in the caller's transaction.

    Merged incidents describe the same people, so a merged need is the larger of the two, never
    less than what is already allocated. Allocations move with it.
    """
    rows = conn.execute(
        "SELECT id, kind, qty_total, qty_total - qty_open FROM needs "
        "WHERE incident_id = ANY(%s) ORDER BY id FOR UPDATE",
        (absorbed,),
    ).fetchall()
    for need_id, kind, total, held in rows:
        target = conn.execute(
            "SELECT id, qty_total, qty_total - qty_open FROM needs "
            "WHERE incident_id = %s AND kind = %s FOR UPDATE",
            (keep, kind),
        ).fetchone()
        if target is None:
            conn.execute("UPDATE needs SET incident_id = %s WHERE id = %s", (keep, need_id))
            continue
        target_id, target_total, target_held = target
        new_total = max(total, target_total, held + target_held)
        conn.execute("UPDATE allocations SET need_id = %s WHERE need_id = %s", (target_id, need_id))
        conn.execute("UPDATE needs SET qty_total = %s, qty_open = %s WHERE id = %s",
                     (new_total, new_total - held - target_held, target_id))
        conn.execute("DELETE FROM needs WHERE id = %s", (need_id,))


def claim(conn: Connection, need_id: int, offer_id: int, qty: int,
          client_action_id: uuid.UUID | None = None) -> int | None:
    """Reserve qty of an offer for a need as a proposal. None when either side lacks the qty.

    A manual claim passes the client's id; a retry with the same id then fails on the unique
    index (the caller answers it as a duplicate).
    """
    with conn.transaction():
        # Reserve the requested quantity only if the offer is active, in its time window,
        # and has enough stock.
        offer = conn.execute(
            """
            UPDATE offers SET qty_available = qty_available - %(q)s
             WHERE id = %(offer)s AND status = 'active' AND qty_available >= %(q)s
               AND app_now() BETWEEN available_from AND available_until
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
            INSERT INTO allocations (need_id, offer_id, qty, status, expires_at, client_action_id)
            VALUES (%s, %s, %s, 'proposed', app_now() + make_interval(mins => %s), %s)
            RETURNING id
            """,
            (need_id, offer_id, qty, PROPOSAL_TTL_MIN, client_action_id or uuid.uuid4()),
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


def transition(conn: Connection, allocation_id: int, action: str) -> str | None:
    """Move an allocation along its state machine. Returns the new status, or None if not allowed.

    Cancel returns the quantity to the offer and the need. Deliver returns it to the offer only
    for reusable kinds. Both happen in the same transaction as the status change.
    """
    allowed_from, new_status = TRANSITIONS[action]
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE allocations a SET status = %s, updated_at = now()
              FROM offers o
             WHERE a.id = %s AND a.status = ANY(%s) AND o.id = a.offer_id
               -- An expired proposal can no longer be accepted.
               AND (%s <> 'accept' OR a.expires_at > app_now())
            RETURNING a.offer_id, a.need_id, a.qty, o.kind
            """,
            (new_status, allocation_id, list(allowed_from), action),
        ).fetchone()
        if row is None:
            return None
        offer_id, need_id, qty, kind = row
        if new_status == "cancelled":
            release(conn, offer_id, need_id, qty)
        elif new_status == "delivered" and kind in REUSABLE_KINDS:
            conn.execute("UPDATE offers SET qty_available = qty_available + %s WHERE id = %s",
                         (qty, offer_id))
    return new_status


def expire_proposals(conn: Connection) -> int:
    expired = 0

    # Find proposals that have passed their expiry time.
    ids = conn.execute(
        "SELECT id FROM allocations WHERE status = 'proposed' AND expires_at < app_now()"
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

    # Offers past their time window take no new claims.
    conn.execute("UPDATE offers SET status = 'expired' "
                 "WHERE status = 'active' AND available_until < app_now()")
    return expired


def candidate_offers(conn: Connection, need_id: int) -> list[tuple]:
    # Find active offers of the required type near the incident, searching outward until
    # enough are found. Verified offers rank as if 20% closer.
    for radius in SEARCH_RADII_M:
        rows = conn.execute(
            """
            SELECT o.id, o.qty_available,
                   ST_Distance(o.geom::geography, i.geom::geography) AS distance_m
              FROM needs n JOIN incidents i ON i.id = n.incident_id
              JOIN offers o ON o.kind = n.kind
             WHERE n.id = %(need)s AND o.status = 'active' AND o.qty_available > 0
               AND app_now() BETWEEN o.available_from AND o.available_until
               AND ST_DWithin(o.geom::geography, i.geom::geography, %(radius)s)
             ORDER BY ST_Distance(o.geom::geography, i.geom::geography)
                      * CASE WHEN o.verified THEN %(factor)s ELSE 1 END
             LIMIT 20
            """,
            {"need": need_id, "radius": radius, "factor": VERIFIED_DISTANCE_FACTOR},
        ).fetchall()
        if len(rows) >= CANDIDATES_WANTED or radius == SEARCH_RADII_M[-1]:
            return rows
    return []


def propose(conn: Connection) -> list[dict]:
    # Get open needs for active incidents that do not already have a pending proposal.
    needs = conn.execute(
        """
        SELECT n.id, n.qty_open, i.id, i.ward FROM needs n JOIN incidents i ON i.id = n.incident_id
         WHERE n.qty_open > 0 AND i.status IN ('open', 'verified') AND i.geom IS NOT NULL
           -- Unverified low-credibility incidents wait for a coordinator unless fast-tracked.
           AND (NOT i.needs_verification OR i.fast_track)
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