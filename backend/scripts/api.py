"""HTTP API for citizen reports and the coordinator dashboard.

Intake only queues messages. The worker turns them into reports and incidents.
Authentication arrives in Phase C; until then the read endpoints are open.
"""

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, UploadFile
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from scripts.config import DATABASE_URL, PHOTOS_DIR
from scripts.intake import enqueue, rate_limited, sender_hash
from scripts.triage import CATEGORY_SEVERITY

# Match the limits in ReportForm.tsx and nginx.conf (client_max_body_size 6m).
MAX_TEXT_CHARS = 2000
MAX_PHOTO_BYTES = 5 * 1024 * 1024
# About 10 m. Enough detail for the map and much smaller responses.
WARD_SIMPLIFY_DEG = 0.0001

log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One pool per process. Each request borrows a connection and commits when it returns it.
    pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=False)
    await pool.open()
    app.state.pool = pool
    try:
        yield
    finally:
        await pool.close()


app = FastAPI(title="Mumbai flood relief", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict:
    async with app.state.pool.connection() as conn:
        await conn.execute("SELECT 1")
    return {"ok": True}


@app.post("/api/reports", status_code=202)
async def create_report(
    client_msg_id: Annotated[str, Form(min_length=1, max_length=64)],
    device_id: Annotated[str, Form(min_length=1, max_length=64)],
    text: Annotated[str, Form(max_length=MAX_TEXT_CHARS)] = "",
    category_hint: Annotated[str | None, Form()] = None,
    lat: Annotated[float | None, Form(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Form(ge=-180, le=180)] = None,
    accuracy_m: Annotated[float | None, Form(ge=0)] = None,
    photo: UploadFile | None = None,
) -> dict:
    # Validate before touching the database or the disk.
    if (lat is None) != (lon is None):
        raise HTTPException(422, "lat and lon must be sent together")
    if not text.strip() and lat is None:
        raise HTTPException(422, "describe the situation or share a location")
    if category_hint is not None and category_hint not in CATEGORY_SEVERITY:
        raise HTTPException(422, "unknown category")

    photo_bytes = None
    if photo is not None and photo.filename:
        if not (photo.content_type or "").startswith("image/"):
            raise HTTPException(415, "photo must be an image")
        # Read one byte past the limit to detect oversized uploads without reading them whole.
        photo_bytes = await photo.read(MAX_PHOTO_BYTES + 1)
        if len(photo_bytes) > MAX_PHOTO_BYTES:
            raise HTTPException(413, "photo too large")

    # The device ID is a random UUID kept in the browser. Store only its keyed hash.
    sender = sender_hash("web", device_id)

    payload = {"text": text, "category_hint": category_hint, "lat": lat, "lon": lon,
               "accuracy_m": accuracy_m, "photo": None}

    async with app.state.pool.connection() as conn:
        if await rate_limited(conn, sender):
            raise HTTPException(429, "too many reports from this device, please wait a few minutes")

        # Name photos on the server. Never use the client's file name as a path.
        photo_path = None
        if photo_bytes:
            photo_path = PHOTOS_DIR / f"{uuid.uuid4()}.jpg"
            PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(photo_path.write_bytes, photo_bytes)
            payload["photo"] = photo_path.name

        # Scope client message IDs to the sender so one device cannot block another's IDs.
        inbox_id = await enqueue(conn, "web", f"{sender[:16]}:{client_msg_id}", sender, payload)

    # A retried submission is not an error, but its new photo copy is not needed.
    if inbox_id is None and photo_path:
        photo_path.unlink(missing_ok=True)

    return {"inbox_id": inbox_id, "duplicate": inbox_id is None}


@app.get("/api/wards")
async def wards() -> dict:
    # Build the GeoJSON in PostGIS. The map labels and filters wards by "code".
    async with app.state.pool.connection() as conn:
        cursor = await conn.execute(
            """
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', coalesce(json_agg(json_build_object(
                    'type', 'Feature',
                    'properties', json_build_object('code', code),
                    'geometry', ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, %s), 6)::json
                ) ORDER BY code), '[]'::json))
              FROM wards
            """,
            (WARD_SIMPLIFY_DEG,),
        )
        (collection,) = await cursor.fetchone()
    return collection


@app.get("/api/incidents")
async def incidents() -> list[dict]:
    # Active incidents only, most urgent and longest waiting first. Fields match api.ts Incident.
    async with app.state.pool.connection() as conn:
        cursor = conn.cursor(row_factory=dict_row)
        await cursor.execute(
            """
            SELECT id, ST_X(geom) AS lon, ST_Y(geom) AS lat, category, status, ward,
                   headcount, headcount_conflict, urgency_level, urgency,
                   credibility, credibility_signals, report_count, sender_count,
                   first_report_at, last_report_at,
                   floor(extract(epoch FROM now() - first_report_at) / 60)::int AS waiting_min
              FROM incidents
             WHERE status IN ('open', 'verified')
             ORDER BY urgency_level DESC, first_report_at
            """
        )
        return await cursor.fetchall()
