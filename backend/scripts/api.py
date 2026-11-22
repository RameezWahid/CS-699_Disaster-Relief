"""HTTP API: citizen reports (public) and the coordinator dashboard (login required).

Create a user: python -m scripts.api adduser <username> <responder|coordinator|admin> [ward ...]
"""

import getpass
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

import jwt
import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from scripts.config import DATABASE_URL, JWT_SECRET, TOKEN_TTL_MIN
from scripts.intake import enqueue, rate_limited, sender_hash
from scripts.matching import accept, cancel, expire_proposals, propose

hasher = PasswordHasher()  # Argon2id


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Each request borrows a connection; it commits on success and rolls back on an error.
    with ConnectionPool(DATABASE_URL, min_size=1, max_size=10) as pool:
        app.state.pool = pool
        yield


app = FastAPI(title="Mumbai flood relief", lifespan=lifespan)


def db():
    return app.state.pool.connection()


def log_event(conn, kind: str, ward: str | None, payload: dict) -> None:
    conn.execute("INSERT INTO event_log (kind, ward, payload) VALUES (%s, %s, %s)",
                 (kind, ward, json.dumps(payload)))


# ---------------------------------------------------------------- public

@app.post("/api/reports", status_code=202)
def create_report(
    request: Request,
    client_msg_id: Annotated[str, Form(max_length=64)],
    device_id: Annotated[str, Form(max_length=64)],
    text: Annotated[str, Form(max_length=2000)] = "",
    category_hint: Annotated[str | None, Form()] = None,
    lat: Annotated[float | None, Form(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Form(ge=-180, le=180)] = None,
    accuracy_m: Annotated[float | None, Form(ge=0)] = None,
) -> dict:
    # TODO(photos): accept the photo upload and set payload["photo"] (a credibility signal).
    if not text.strip() and lat is None:
        raise HTTPException(422, "describe the situation or share a location")
    # Store only a keyed hash of the device ID, never the ID itself.
    sender = sender_hash("web", device_id)
    payload = {"text": text, "category_hint": category_hint, "lat": lat, "lon": lon,
               "accuracy_m": accuracy_m}
    # A logged-in staff member in the field is a responder: a credibility signal. Their phone
    # may sync a whole offline outbox at once, so the citizen rate limit does not apply.
    try:
        payload["responder"] = current_user(request)["username"]
    except HTTPException:
        pass
    with db() as conn:
        if "responder" not in payload and rate_limited(conn, sender):
            raise HTTPException(429, "too many reports from this device, please wait")
        # A retried submission (same client_msg_id) is stored once.
        inbox_id = enqueue(conn, "web", f"{sender[:16]}:{client_msg_id}", sender, payload)
    return {"inbox_id": inbox_id, "duplicate": inbox_id is None}


@app.get("/api/wards")
def wards() -> dict:
    # Ward outlines as GeoJSON, simplified to about 10 m for the map.
    with db() as conn:
        return conn.execute(
            """
            SELECT json_build_object('type', 'FeatureCollection', 'features', json_agg(
                json_build_object('type', 'Feature', 'properties', json_build_object('code', code),
                                  'geometry', ST_AsGeoJSON(ST_Simplify(geom, 0.0001), 6)::json)))
              FROM wards
            """
        ).fetchone()[0]


# ---------------------------------------------------------------- login

class Credentials(BaseModel):
    username: str
    password: str


def current_user(request: Request) -> dict:
    # The session is a signed JWT in an httpOnly cookie.
    try:
        claims = jwt.decode(request.cookies.get("session", ""), JWT_SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise HTTPException(401, "not logged in") from None
    return {"username": claims["sub"], "role": claims["role"], "wards": claims["wards"]}


User = Annotated[dict, Depends(current_user)]


def require_coordinator(user: dict) -> None:
    # Responders can look but not decide.
    if user["role"] == "responder":
        raise HTTPException(403, "only coordinators can do this")


# Users see incidents in their wards (an empty list means all wards). Incidents without a ward
# are shown to everyone so that no report is lost.
IN_SCOPE = "(cardinality(%(wards)s::text[]) = 0 OR i.ward = ANY(%(wards)s) OR i.ward IS NULL)"


@app.post("/api/auth/login")
def login(body: Credentials, response: Response) -> dict:
    # TODO(auth): rate-limit failed logins; set secure=True on the cookie once served over HTTPS.
    with db() as conn:
        row = conn.execute("SELECT password_hash, role, wards FROM users WHERE username = %s",
                           (body.username,)).fetchone()
    try:
        if row:
            hasher.verify(row[0], body.password)
    except VerificationError:
        row = None
    if row is None:
        raise HTTPException(401, "wrong username or password")
    me = {"username": body.username, "role": row[1], "wards": row[2]}
    exp = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MIN)
    token = jwt.encode({"sub": body.username, "role": row[1], "wards": row[2], "exp": exp},
                       JWT_SECRET, algorithm="HS256")
    response.set_cookie("session", token, max_age=TOKEN_TTL_MIN * 60, httponly=True,
                        samesite="strict")
    return me


@app.get("/api/auth/me")
def me(user: User) -> dict:
    return user


@app.post("/api/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie("session")
    return {"ok": True}


# ---------------------------------------------------------------- incidents

INCIDENT_COLUMNS = """
    i.id, ST_X(i.geom) AS lon, ST_Y(i.geom) AS lat, i.category, i.status, i.ward, i.headcount,
    i.headcount_conflict, i.urgency_level, i.urgency, i.credibility, i.credibility_signals,
    i.report_count, i.sender_count, i.first_report_at, i.last_report_at,
    floor(extract(epoch FROM now() - i.first_report_at) / 60)::int AS waiting_min
"""


def rematch(conn) -> None:
    # TODO(periodic): the worker should do this every ~30 s. Until then, adding an offer or
    # verifying an incident triggers it.
    expire_proposals(conn)
    with conn.transaction():
        propose(conn)


@app.get("/api/incidents")
def incidents(user: User, queue: Literal["verify"] | None = None) -> list[dict]:
    # ?queue=verify: open incidents too low in credibility to act on without a check.
    verify = "AND i.status = 'open' AND i.needs_verification" if queue else ""
    with db() as conn:
        return conn.cursor(row_factory=dict_row).execute(
            f"SELECT {INCIDENT_COLUMNS} FROM incidents i "
            f"WHERE i.status IN ('open', 'verified') AND {IN_SCOPE} {verify} "
            "ORDER BY i.urgency_level DESC, i.first_report_at",
            {"wards": user["wards"]},
        ).fetchall()


@app.get("/api/incidents/{incident_id}")
def incident_detail(incident_id: int, user: User) -> dict:
    with db() as conn:
        cur = conn.cursor(row_factory=dict_row)
        incident = cur.execute(
            f"SELECT {INCIDENT_COLUMNS} FROM incidents i WHERE i.id = %(id)s AND {IN_SCOPE}",
            {"id": incident_id, "wards": user["wards"]},
        ).fetchone()
        if incident is None:
            raise HTTPException(404, "incident not found")
        incident["reports"] = cur.execute(
            "SELECT id, received_at, source, text, lang, category, headcount, needs, "
            "vulnerability, has_gps, has_photo, repeat_of, extraction "
            "FROM reports WHERE incident_id = %s ORDER BY received_at", (incident_id,),
        ).fetchall()
        incident["needs"] = cur.execute(
            "SELECT id, kind, qty_total, qty_open FROM needs WHERE incident_id = %s",
            (incident_id,),
        ).fetchall()
        incident["allocations"] = cur.execute(
            "SELECT a.id, a.need_id, a.offer_id, o.provider, o.kind, a.qty, a.status, a.expires_at "
            "FROM allocations a JOIN needs n ON n.id = a.need_id JOIN offers o ON o.id = a.offer_id "
            "WHERE n.incident_id = %s ORDER BY a.created_at", (incident_id,),
        ).fetchall()
    return incident


class StatusChange(BaseModel):
    status: Literal["verified", "resolved", "dismissed"]


@app.post("/api/incidents/{incident_id}/status")
def set_incident_status(incident_id: int, body: StatusChange, user: User) -> dict:
    # TODO(incidents): on resolve/dismiss, cancel the incident's pending proposals.
    require_coordinator(user)
    with db() as conn:
        row = conn.execute(
            f"UPDATE incidents i SET status = %(status)s, needs_verification = false "
            f"WHERE i.id = %(id)s AND i.status IN ('open', 'verified') AND {IN_SCOPE} "
            "RETURNING i.ward",
            {"id": incident_id, "status": body.status, "wards": user["wards"]},
        ).fetchone()
        if row is None:
            raise HTTPException(409, "incident not found or already closed")
        log_event(conn, f"incident.{body.status}", row[0],
                  {"incident_id": incident_id, "by": user["username"]})
        if body.status == "verified":
            rematch(conn)
    return {"status": body.status}


# ---------------------------------------------------------------- offers and proposals

class NewOffer(BaseModel):
    # TODO(offers): availability windows (the form sends available_from/until; ignored for now).
    kind: str
    provider: str
    qty: int = Field(gt=0)
    lat: float
    lon: float
    verified: bool = False


@app.get("/api/offers")
def offers(user: User) -> list[dict]:
    with db() as conn:
        return conn.cursor(row_factory=dict_row).execute(
            "SELECT id, kind, provider, qty_total, qty_available, ST_X(geom) AS lon, "
            "ST_Y(geom) AS lat, ward, verified FROM offers WHERE status = 'active' ORDER BY id"
        ).fetchall()


@app.post("/api/offers", status_code=201)
def create_offer(body: NewOffer, user: User) -> dict:
    require_coordinator(user)
    with db() as conn:
        offer_id, ward = conn.execute(
            """
            INSERT INTO offers (kind, provider, qty_total, qty_available, geom, ward, verified)
            SELECT %(kind)s, %(provider)s, %(qty)s, %(qty)s, pt,
                   (SELECT code FROM wards WHERE ST_Contains(geom, pt) LIMIT 1), %(verified)s
              FROM ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326) AS pt
            RETURNING id, ward
            """,
            body.model_dump(),
        ).fetchone()
        log_event(conn, "offer.created", ward, {"offer_id": offer_id, "by": user["username"]})
        rematch(conn)
    return {"id": offer_id}


@app.get("/api/allocations")
def proposals(user: User) -> list[dict]:
    # Open proposals only (the dashboard asks for ?status=proposed).
    with db() as conn:
        return conn.cursor(row_factory=dict_row).execute(
            f"""
            SELECT a.id, a.status, a.qty, a.expires_at,
                   ceil(extract(epoch FROM a.expires_at - now()) / 60)::int AS expires_in_min,
                   o.kind, a.need_id, n.incident_id, i.ward, i.urgency_level, a.offer_id,
                   o.provider, o.verified,
                   ST_Distance(o.geom::geography, i.geom::geography)::int AS distance_m
              FROM allocations a JOIN needs n ON n.id = a.need_id
              JOIN incidents i ON i.id = n.incident_id JOIN offers o ON o.id = a.offer_id
             WHERE a.status = 'proposed' AND a.expires_at > now() AND {IN_SCOPE}
             ORDER BY i.urgency_level DESC, a.created_at
            """,
            {"wards": user["wards"]},
        ).fetchall()


@app.post("/api/allocations/{allocation_id}/{action}")
def decide(allocation_id: int, action: Literal["accept", "cancel"], user: User) -> dict:
    require_coordinator(user)
    with db() as conn:
        row = conn.execute(
            f"SELECT i.ward FROM allocations a JOIN needs n ON n.id = a.need_id "
            f"JOIN incidents i ON i.id = n.incident_id WHERE a.id = %(id)s AND {IN_SCOPE}",
            {"id": allocation_id, "wards": user["wards"]},
        ).fetchone()
        if row is None:
            raise HTTPException(404, "proposal not found")
        # Guarded updates: an expired or already-decided proposal fails.
        if not (accept if action == "accept" else cancel)(conn, allocation_id):
            raise HTTPException(409, "this proposal is no longer open")
        log_event(conn, "allocation.accepted" if action == "accept" else "allocation.cancelled",
                  row[0], {"allocation_id": allocation_id, "by": user["username"]})
    return {"ok": True}


# TODO(offers): POST /api/offers/{id}/withdraw (cancel its open proposals first).
# TODO(allocations): POST /api/allocations, a coordinator's manual pick (matching.claim).
# TODO(alerts): GET/POST /api/alerts and POST /api/alerts/{id}/end (needs an alerts table).
# TODO(events): GET /api/events, live updates over SSE. The dashboard refreshes every 60 s.


# ---------------------------------------------------------------- command line

def add_user(argv: list[str]) -> None:
    if len(argv) < 3 or argv[0] != "adduser" or argv[2] not in ("responder", "coordinator", "admin"):
        raise SystemExit(__doc__)
    username, role, ward_codes = argv[1], argv[2], argv[3:]
    password = getpass.getpass(f"password for {username}: ")
    with psycopg.connect(DATABASE_URL) as conn:
        # Running it again for the same user resets the password, role, and wards.
        conn.execute(
            "INSERT INTO users (username, password_hash, role, wards) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash, "
            "role = EXCLUDED.role, wards = EXCLUDED.wards",
            (username, hasher.hash(password), role, ward_codes),
        )
    print(f"saved {role} {username}")


if __name__ == "__main__":
    add_user(sys.argv[1:])
