"""HTTP API: citizen reports (public) and the coordinator dashboard (login required).

Create a user: python -m scripts.api adduser <username> <responder|coordinator|admin> [ward ...]
"""
import getpass, json, sys, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
import jwt, psycopg
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field
from scripts.config import DATABASE_URL, JWT_SECRET, PHOTOS_DIR, TOKEN_TTL_MIN
from scripts.geocode import in_mumbai
from scripts.intake import enqueue, rate_limited, sender_hash
from scripts.matching import OFFER_KINDS, TRANSITIONS, claim, expire_proposals, propose, transition
hasher, MAX_PHOTO_BYTES, SSE_KEEPALIVE_S = PasswordHasher(), 5 * 1024 * 1024, 15  # Argon2id; 5 MB; ping SSE every 15 s
# Users see their wards (an empty list means all wards). Incidents without a ward are shown to everyone.
IN_SCOPE = "(cardinality(%(wards)s::text[]) = 0 OR i.ward = ANY(%(wards)s) OR i.ward IS NULL)"
ALLOC = "allocations a JOIN needs n ON n.id = a.need_id JOIN incidents i ON i.id = n.incident_id"
INCIDENT_COLUMNS = """i.id, ST_X(i.geom) AS lon, ST_Y(i.geom) AS lat, i.category, i.status, i.ward, i.headcount,
    i.headcount_conflict, i.urgency_level, i.urgency, i.credibility, i.credibility_signals, i.report_count,
    i.sender_count, i.first_report_at, i.last_report_at,
    floor(extract(epoch FROM app_now() - i.first_report_at) / 60)::int AS waiting_min"""
@asynccontextmanager
async def lifespan(app: FastAPI):
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    with ConnectionPool(DATABASE_URL, min_size=1, max_size=10) as pool:  # a request commits on success, rolls back on error
        app.state.pool = pool; yield
app = FastAPI(title="Mumbai flood relief", lifespan=lifespan)
def db(): return app.state.pool.connection()
def fail(code: int, detail: str): raise HTTPException(code, detail)
def rows(sql: str, params=None) -> list[dict]:
    with db() as conn: return conn.cursor(row_factory=dict_row).execute(sql, params).fetchall()
def log_event(conn, kind: str, ward: str | None, payload: dict) -> None:
    conn.execute("INSERT INTO event_log (kind, ward, payload) VALUES (%s, %s, %s)", (kind, ward, json.dumps(payload)))
def current_user(request: Request) -> dict:  # the session is a signed JWT in an httpOnly cookie
    try: c = jwt.decode(request.cookies.get("session", ""), JWT_SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError: raise HTTPException(401, "not logged in") from None
    return {"username": c["sub"], "role": c["role"], "wards": c["wards"]}
User = Annotated[dict, Depends(current_user)]
def require_coordinator(user: dict) -> None:  # responders can look but not decide
    if user["role"] == "responder": fail(403, "only coordinators can do this")
def can_see(user: dict, ward: str | None) -> bool: return not user["wards"] or ward is None or ward in user["wards"]
def may_alert(user: dict, ward: str | None) -> bool:  # a city-wide alert needs an admin or a user with no ward list
    return (user["role"] == "admin" or not user["wards"]) if ward is None else can_see(user, ward)
def rematch(conn) -> None:  # the worker does this every minute; offers and verifications trigger it at once
    expire_proposals(conn)
    with conn.transaction(): propose(conn)
# ---------------------------------------------------------------- public
@app.post("/api/reports", status_code=202)
def create_report(request: Request, client_msg_id: Annotated[str, Form(max_length=64)],
                  device_id: Annotated[str, Form(max_length=64)], text: Annotated[str, Form(max_length=2000)] = "",
                  category_hint: Annotated[str | None, Form()] = None,
                  lat: Annotated[float | None, Form(ge=-90, le=90)] = None,
                  lon: Annotated[float | None, Form(ge=-180, le=180)] = None,
                  accuracy_m: Annotated[float | None, Form(ge=0)] = None,
                  photo: Annotated[UploadFile | None, File()] = None) -> dict:
    if not text.strip() and lat is None: fail(422, "describe the situation or share a location")
    sender = sender_hash("web", device_id)  # store only a keyed hash of the device ID
    payload = {"text": text, "category_hint": category_hint, "lat": lat, "lon": lon, "accuracy_m": accuracy_m}
    try: payload["responder"] = current_user(request)["username"]  # staff: credibility signal, no rate limit
    except HTTPException: pass
    if photo is not None:  # a credibility signal; personal data, kept out of git
        if len(content := photo.file.read(MAX_PHOTO_BYTES + 1)) > MAX_PHOTO_BYTES: fail(413, "photo too large")
        (PHOTOS_DIR / (name := f"web_{sender[:16]}_{client_msg_id}.jpg".replace("/", "_"))).write_bytes(content)
        payload["photo"] = name
    with db() as conn:
        if "responder" not in payload and rate_limited(conn, sender): fail(429, "too many reports from this device, please wait")
        inbox_id = enqueue(conn, "web", f"{sender[:16]}:{client_msg_id}", sender, payload)  # a retry is stored once
    return {"inbox_id": inbox_id, "duplicate": inbox_id is None}
@app.get("/api/wards")
def wards() -> dict:  # ward outlines as GeoJSON, simplified to about 10 m
    return rows("""SELECT json_build_object('type', 'FeatureCollection', 'features', json_agg(json_build_object(
        'type', 'Feature', 'properties', json_build_object('code', code),
        'geometry', ST_AsGeoJSON(ST_Simplify(geom, 0.0001), 6)::json))) AS fc FROM wards""")[0]["fc"]
# ---------------------------------------------------------------- login
class Credentials(BaseModel): username: str; password: str
@app.post("/api/auth/login")
def login(body: Credentials, response: Response) -> dict:
    # TODO(auth): rate-limit failed logins; set secure=True on the cookie once served over HTTPS.
    with db() as conn: row = conn.execute("SELECT password_hash, role, wards FROM users WHERE username = %s",
                                          (body.username,)).fetchone()
    try: row and hasher.verify(row[0], body.password)
    except VerificationError: row = None
    if row is None: fail(401, "wrong username or password")
    exp = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MIN)
    token = jwt.encode({"sub": body.username, "role": row[1], "wards": row[2], "exp": exp}, JWT_SECRET, algorithm="HS256")
    response.set_cookie("session", token, max_age=TOKEN_TTL_MIN * 60, httponly=True, samesite="strict")
    return {"username": body.username, "role": row[1], "wards": row[2]}
@app.get("/api/auth/me")
def me(user: User) -> dict: return user
@app.post("/api/auth/logout")
def logout(response: Response) -> dict: return response.delete_cookie("session") or {"ok": True}
# ---------------------------------------------------------------- incidents
@app.get("/api/incidents")
def incidents(user: User, queue: Literal["verify"] | None = None) -> list[dict]:  # ?queue=verify: low credibility
    verify = "AND i.status = 'open' AND i.needs_verification" if queue else ""
    return rows(f"SELECT {INCIDENT_COLUMNS} FROM incidents i WHERE i.status IN ('open', 'verified') AND {IN_SCOPE} "
                f"{verify} ORDER BY i.urgency_level DESC, i.first_report_at", {"wards": user["wards"]})
@app.get("/api/incidents/{incident_id}")
def incident_detail(incident_id: int, user: User) -> dict:
    with db() as conn:
        cur = conn.cursor(row_factory=dict_row)
        incident = cur.execute(f"SELECT {INCIDENT_COLUMNS} FROM incidents i WHERE i.id = %(id)s AND {IN_SCOPE}",
                               {"id": incident_id, "wards": user["wards"]}).fetchone() or fail(404, "incident not found")
        for key, sql in {"reports": "SELECT id, received_at, source, text, lang, category, headcount, needs, vulnerability, "
                         "has_gps, has_photo, repeat_of, extraction FROM reports WHERE incident_id = %s ORDER BY received_at",
                         "needs": "SELECT id, kind, qty_total, qty_open FROM needs WHERE incident_id = %s",
                         "allocations": "SELECT a.id, a.need_id, a.offer_id, o.provider, o.kind, a.qty, a.status, a.expires_at "
                         "FROM allocations a JOIN needs n ON n.id = a.need_id JOIN offers o ON o.id = a.offer_id "
                         "WHERE n.incident_id = %s ORDER BY a.created_at"}.items():
            incident[key] = cur.execute(sql, (incident_id,)).fetchall()
    return incident
class StatusChange(BaseModel): status: Literal["verified", "resolved", "dismissed"]
@app.post("/api/incidents/{incident_id}/status")
def set_incident_status(incident_id: int, body: StatusChange, user: User) -> dict:
    # TODO(incidents): on resolve/dismiss, cancel the incident's pending proposals.
    require_coordinator(user)
    with db() as conn:
        ward = (conn.execute(f"UPDATE incidents i SET status = %(status)s, needs_verification = false WHERE i.id = %(id)s "
                             f"AND i.status IN ('open', 'verified') AND {IN_SCOPE} RETURNING i.ward",
                             {"id": incident_id, "status": body.status, "wards": user["wards"]}).fetchone()
                or fail(409, "incident not found or already closed"))[0]
        log_event(conn, f"incident.{body.status}", ward, {"incident_id": incident_id, "by": user["username"]})
        if body.status == "verified": rematch(conn)
    return {"status": body.status}
# ---------------------------------------------------------------- offers and proposals
class NewOffer(BaseModel):  # without a window an offer is available from now, for a week
    kind: Literal[tuple(OFFER_KINDS)]  # type: ignore[valid-type]
    provider: str; qty: int = Field(gt=0); lat: float; lon: float
    available_from: datetime | None = None; available_until: datetime | None = None; verified: bool = False
@app.get("/api/offers")
def offers(user: User) -> list[dict]:
    return rows("SELECT id, kind, provider, qty_total, qty_available, ST_X(geom) AS lon, ST_Y(geom) AS lat, ward, "
                "available_from, available_until, verified, synthetic FROM offers WHERE status = 'active' ORDER BY id")
@app.post("/api/offers", status_code=201)
def create_offer(body: NewOffer, user: User) -> dict:
    require_coordinator(user)
    if not in_mumbai(body.lat, body.lon): fail(422, "offer location must be inside Mumbai")
    if body.available_from and body.available_until and body.available_until <= body.available_from:
        fail(422, "available_until must be after available_from")
    with db() as conn:
        offer_id, ward = conn.execute("""INSERT INTO offers (kind, provider, qty_total, qty_available, geom, ward,
                                                             available_from, available_until, verified)
            SELECT %(kind)s, %(provider)s, %(qty)s, %(qty)s, pt, (SELECT code FROM wards WHERE ST_Contains(geom, pt) LIMIT 1),
                   coalesce(%(available_from)s, app_now()), coalesce(%(available_until)s, app_now() + interval '7 days'),
                   %(verified)s FROM ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326) AS pt RETURNING id, ward""",
            body.model_dump()).fetchone()
        log_event(conn, "offer.created", ward, {"offer_id": offer_id, "by": user["username"]}); rematch(conn)
    return {"id": offer_id}
@app.post("/api/offers/{offer_id}/withdraw")
def withdraw_offer(offer_id: int, user: User) -> dict:  # no new claims; allocations already made keep their quantity
    require_coordinator(user)
    with db() as conn:
        ward = (conn.execute("UPDATE offers SET status = 'withdrawn' WHERE id = %s AND status = 'active' RETURNING ward",
                             (offer_id,)).fetchone() or fail(404, "no active offer with that id"))[0]
        log_event(conn, "offer.withdrawn", ward, {"offer_id": offer_id, "by": user["username"]})
    return {"offer_id": offer_id, "status": "withdrawn"}
@app.get("/api/allocations")
def proposals(user: User) -> list[dict]:  # open proposals only (the dashboard asks for ?status=proposed)
    return rows(f"""SELECT a.id, a.status, a.qty, a.expires_at,
               ceil(extract(epoch FROM a.expires_at - app_now()) / 60)::int AS expires_in_min, o.kind, a.need_id,
               n.incident_id, i.ward, i.urgency_level, a.offer_id, o.provider, o.verified,
               ST_Distance(o.geom::geography, i.geom::geography)::int AS distance_m
          FROM {ALLOC} JOIN offers o ON o.id = a.offer_id
         WHERE a.status = 'proposed' AND a.expires_at > app_now() AND {IN_SCOPE}
         ORDER BY i.urgency_level DESC, a.created_at""", {"wards": user["wards"]})
class NewClaim(BaseModel): need_id: int; offer_id: int; qty: int = Field(gt=0); client_action_id: uuid.UUID
@app.post("/api/allocations")
def create_allocation(body: NewClaim, user: User, response: Response) -> dict:
    """A coordinator's own pick. 201 applied, 200 duplicate (a retry), 409 stock gone."""
    require_coordinator(user)
    with db() as conn:
        ward = (conn.execute(f"SELECT i.ward FROM needs n JOIN incidents i ON i.id = n.incident_id WHERE n.id = %(id)s "
                             f"AND {IN_SCOPE}", {"id": body.need_id, "wards": user["wards"]}).fetchone()
                or fail(404, "need not found"))[0]
        if seen := conn.execute("SELECT id FROM allocations WHERE client_action_id = %s", (body.client_action_id,)).fetchone():
            response.status_code = 200; return {"allocation_id": seen[0], "result": "duplicate"}
        allocation_id = (claim(conn, body.need_id, body.offer_id, body.qty, body.client_action_id)
                         or fail(409, "not enough quantity left on the offer or the need"))
        log_event(conn, "allocation.proposed", ward, {"allocation_id": allocation_id, "need_id": body.need_id,
                                                      "offer_id": body.offer_id, "qty": body.qty, "by": user["username"]})
    response.status_code = 201
    return {"allocation_id": allocation_id, "result": "applied"}
@app.post("/api/allocations/{allocation_id}/{action}")
def decide(allocation_id: int, action: Literal[tuple(TRANSITIONS)], user: User) -> dict:  # type: ignore[valid-type]
    if action in ("accept", "cancel"): require_coordinator(user)  # anyone in the ward may dispatch and deliver
    with db() as conn:
        ward = (conn.execute(f"SELECT i.ward FROM {ALLOC} WHERE a.id = %(id)s AND {IN_SCOPE}",
                             {"id": allocation_id, "wards": user["wards"]}).fetchone() or fail(404, "proposal not found"))[0]
        new_status = (transition(conn, allocation_id, action)  # guarded: an expired or decided one fails
                      or fail(409, f"cannot {action} this allocation in its current state"))
        log_event(conn, f"allocation.{new_status}", ward, {"allocation_id": allocation_id, "by": user["username"]})
    return {"ok": True}
# ---------------------------------------------------------------- official alerts (they raise credibility)
class NewAlert(BaseModel):
    title: str = Field(min_length=3, max_length=200); ward: str | None = None; starts_at: datetime; ends_at: datetime
@app.get("/api/alerts")
def alerts(user: User) -> list[dict]:
    return rows("SELECT id, source, title, ward, starts_at, ends_at FROM alerts WHERE ends_at > app_now() ORDER BY starts_at")
@app.post("/api/alerts", status_code=201)
def create_alert(body: NewAlert, user: User) -> dict:
    require_coordinator(user)
    if not may_alert(user, body.ward): fail(403, "outside your wards (city-wide alerts are for admins)")
    if body.ends_at <= body.starts_at: fail(422, "ends_at must be after starts_at")
    with db() as conn:
        alert_id = conn.execute("INSERT INTO alerts (source, title, ward, starts_at, ends_at) VALUES ('manual', %s, %s, %s, %s) "
                                "RETURNING id", (body.title, body.ward, body.starts_at, body.ends_at)).fetchone()[0]
        log_event(conn, "alert.raised", body.ward, {"alert_id": alert_id, "title": body.title, "by": user["username"]})
    return {"alert_id": alert_id}
@app.post("/api/alerts/{alert_id}/end")
def end_alert(alert_id: int, user: User) -> dict:
    require_coordinator(user)
    with db() as conn:
        ward = (conn.execute("SELECT ward FROM alerts WHERE id = %s", (alert_id,)).fetchone() or fail(404, "no such alert"))[0]
        if not may_alert(user, ward): fail(403, "outside your wards (city-wide alerts are for admins)")
        conn.execute("UPDATE alerts SET ends_at = app_now() WHERE id = %s AND ends_at > app_now()", (alert_id,))
        log_event(conn, "alert.ended", ward, {"alert_id": alert_id, "by": user["username"]})
    return {"alert_id": alert_id}
# ---------------------------------------------------------------- responder app (offline)
# Tasks are the accepted or dispatched allocations in the responder's wards. The phone queues actions offline and
# sends them in one batch. Each is applied at most once (by client_action_id); a task changed meanwhile is rejected.
@app.get("/api/tasks")
def tasks(user: User) -> list[dict]:
    return rows(f"""SELECT a.id, a.status, a.qty, n.kind, o.provider, i.id AS incident_id, i.category, i.ward,
               i.urgency_level, i.headcount, ST_X(i.geom) AS lon, ST_Y(i.geom) AS lat
          FROM {ALLOC} JOIN offers o ON o.id = a.offer_id WHERE a.status IN ('accepted', 'dispatched') AND {IN_SCOPE}
         ORDER BY i.urgency_level DESC, a.id""", {"wards": user["wards"]})
class SyncAction(BaseModel):  # base_status: the task status the phone saw when the action was made
    client_action_id: uuid.UUID; type: Literal["task_status", "report"]; payload: dict
    base_status: str | None = None; client_ts: datetime | None = None
class SyncBatch(BaseModel):
    device_id: str = Field(min_length=8, max_length=64); actions: list[SyncAction] = Field(max_length=200)
def apply_action(conn, user: dict, device_id: str, action: SyncAction) -> tuple[str, str | None]:
    p = action.payload
    if action.type == "report":
        if not str(p.get("text", "")).strip() and p.get("lat") is None: return "rejected", "a report needs text or a location"
        enqueue(conn, "web", str(action.client_action_id), sender_hash("web", device_id),  # responder: credibility signal
                {"text": str(p.get("text", ""))[:2000], "responder": user["username"],
                 **{k: p.get(k) for k in ("lat", "lon", "accuracy_m", "category_hint")}})
        return "applied", None
    allocation_id, step = int(p["allocation_id"]), p["action"]
    if step not in ("dispatch", "deliver"): return "rejected", f"unknown task action {step}"
    row = conn.execute(f"SELECT a.status, i.ward FROM {ALLOC} WHERE a.id = %s FOR UPDATE OF a", (allocation_id,)).fetchone()
    if row is None or not can_see(user, row[1]): return "rejected", "no such task in your wards"
    current, ward = row
    if action.base_status and current != action.base_status:
        return "rejected", ("this task was cancelled or reassigned by a coordinator" if current == "cancelled"
                            else f"this task changed on the server (now {current})")
    if not (new_status := transition(conn, allocation_id, step)): return "rejected", f"cannot {step} a task that is {current}"
    log_event(conn, f"allocation.{new_status}", ward, {"allocation_id": allocation_id, "by": user["username"], "via": "sync"})
    return "applied", None
@app.post("/api/sync")
def sync(batch: SyncBatch, user: User) -> dict:
    """Answer every action with applied, duplicate (already seen) or rejected (with a reason)."""
    results = []
    with db() as conn:
        for action in batch.actions:
            key = action.client_action_id
            with conn.transaction():  # the row lock makes a concurrent resend of the same id wait, then see it
                fresh = conn.execute("INSERT INTO sync_actions (client_action_id, device_id, user_id, type, client_ts, result) "
                                     "VALUES (%s, %s, (SELECT id FROM users WHERE username = %s), %s, %s, 'rejected') "
                                     "ON CONFLICT DO NOTHING RETURNING 1",
                                     (key, batch.device_id, user["username"], action.type, action.client_ts)).fetchone()
                if fresh:
                    try:
                        with conn.transaction(): original, reason = apply_action(conn, user, batch.device_id, action)
                    except (KeyError, ValueError, TypeError) as error: original, reason = "rejected", f"bad action: {error}"
                    conn.execute("UPDATE sync_actions SET result = %s, reason = %s WHERE client_action_id = %s",
                                 (original, reason, key))
                else: original, reason = conn.execute("SELECT result, reason FROM sync_actions WHERE client_action_id = %s",
                                                      (key,)).fetchone()
            results.append({"client_action_id": str(key), "original": original, "reason": reason,
                            "result": original if fresh else "duplicate"})
    return {"results": results}
# ---------------------------------------------------------------- live updates
@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    """Server-sent events for the user's wards (and city-wide ones). A reconnect sends Last-Event-ID to catch up."""
    user, last = current_user(request), request.headers.get("last-event-id", "")
    async def stream():
        cursor_id = int(last) if last.isdigit() else None
        # One connection per browser: LISTEN is per connection, and the demo has few browsers.
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
            await conn.execute("LISTEN events")
            if cursor_id is None:
                cursor_id = (await (await conn.execute("SELECT coalesce(max(id), 0) FROM event_log")).fetchone())[0]
            while not await request.is_disconnected():  # LISTEN came first, so nothing committed after it is missed
                for cursor_id, kind, payload in await (await conn.execute(
                        "SELECT id, kind, payload FROM event_log WHERE id > %s AND (cardinality(%s::text[]) = 0 "
                        "OR ward IS NULL OR ward = ANY(%s)) ORDER BY id", (cursor_id, user["wards"], user["wards"]))).fetchall():
                    yield f"id: {cursor_id}\nevent: {kind}\ndata: {json.dumps(payload)}\n\n"
                if not [n async for n in conn.notifies(timeout=SSE_KEEPALIVE_S, stop_after=1)]: yield ": keepalive\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
# TODO(admin): GET /api/admin/dead-letters and POST .../{id}/retry for messages the worker gave up on (now: SQL).
# TODO(health): GET /api/health for an uptime check.
# TODO(audit): a script that re-walks event_log and reports the first broken hash link.
# ---------------------------------------------------------------- command line
def add_user(argv: list[str]) -> None:  # running it again for the same user resets the password, role, and wards
    if len(argv) < 3 or argv[0] != "adduser" or argv[2] not in ("responder", "coordinator", "admin"): raise SystemExit(__doc__)
    username, role, ward_codes = argv[1], argv[2], argv[3:]
    password = getpass.getpass(f"password for {username}: ")
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("INSERT INTO users (username, password_hash, role, wards) VALUES (%s, %s, %s, %s) ON CONFLICT (username) "
                     "DO UPDATE SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role, wards = EXCLUDED.wards",
                     (username, hasher.hash(password), role, ward_codes))
    print(f"saved {role} {username}")
if __name__ == "__main__": add_user(sys.argv[1:])
