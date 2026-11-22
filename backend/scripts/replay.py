"""Scenario replay (spec 4.9): drive the real pipeline with planted ground truth at 1x-60x.

usage:
  python -m scripts.replay run <scenario.yaml> [--speed N] [--reset]
  python -m scripts.replay stop          # return the app clock to wall time

Message text comes from the replay-only templates (scripts.messages), never the ones the
classifier trained on. Ground truth rides in each inbox payload under "truth" (the pipeline
ignores it) and is written to results/<scenario id>/truth.json. --reset needs the table owner.

Rain and the simulated clock are written only when their tables exist, so a replay also runs on
a database that has just the inbox, incidents and matching tables.
"""

import json
import logging
import random
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import psycopg

from scripts.config import DATABASE_URL, REPO_DIR
from scripts.intake import sender_hash
from scripts.messages import VULNERABLE, add_noise, headcount_phrase, templates_for
from scripts.text import normalize_place

SEED = 7
# Reports about one incident arrive over this many minutes after its onset (most early).
REPORT_SPREAD_MIN = 40
# A GPS pin lands within about 60 m of the true spot (phone accuracy in a dense city).
GPS_JITTER_DEG = 0.0005
RESET_TABLES = ["allocations", "needs", "offers", "reports", "incidents", "inbox", "rain_hourly"]

log = logging.getLogger("replay")


def gazetteer_lookup(conn: psycopg.Connection):
    """A find(name) function over the gazetteer, preferring chronic spots."""

    def find(name: str) -> dict:
        row = conn.execute(
            """
            SELECT p.name, ST_Y(p.geom), ST_X(p.geom),
                   (SELECT pn2.name FROM place_names pn2 WHERE pn2.place_id = p.id
                     AND pn2.name ~ '[ऀ-ॿ]' LIMIT 1)
              FROM place_names pn JOIN places p ON p.id = pn.place_id
             WHERE pn.name_norm = %s
             ORDER BY p.kind = 'chronic_spot' DESC, p.kind = 'locality' DESC LIMIT 1
            """,
            (normalize_place(name),),
        ).fetchone()
        if row is None:
            raise SystemExit(f"scenario place {name!r} is not in the gazetteer")
        return {"name": row[0], "lat": row[1], "lon": row[2], "deva": row[3]}

    return find


def message_text(style: str, category: str, place: dict, headcount: int, rng) -> str:
    template = rng.choice(templates_for(style, category, "replay"))
    place_name = place["deva"] if style in ("hi", "mr") and place["deva"] else place["name"]
    count, _ = headcount_phrase(style, rng)
    count = count.replace(count.split()[0], str(headcount), 1)  # keep the planted number
    text = template.format(p=place_name, n=count, v=rng.choice(VULNERABLE[style]))
    return add_noise(text, style, rng)


def build_events(find, scenario: dict, run_id: str) -> list[tuple]:
    """(scenario minute, kind, data) for every message and offer, sorted by time."""
    rng = random.Random(SEED)
    events = []
    for incident in scenario["incidents"]:
        place = find(incident["place"])
        spec = incident["reports"]
        senders = [f"{run_id}:{incident['id']}:s{i}" for i in range(spec["senders"])]
        for n in range(spec["count"]):
            sender = senders[n] if n < len(senders) else rng.choice(senders)
            style = spec["styles"][n % len(spec["styles"])]
            minute = incident["onset_min"] + rng.expovariate(3 / REPORT_SPREAD_MIN)
            payload = {
                "text": message_text(
                    style, incident["category"], place, incident["headcount"], rng
                ),
                "truth": {
                    "incident": incident["id"],
                    "category": incident["category"],
                    "headcount": incident["headcount"],
                    "place": place["name"],
                    "lat": place["lat"],
                    "lon": place["lon"],
                    "style": style,
                },
            }
            if rng.random() < spec.get("with_gps", 0):
                payload["lat"] = place["lat"] + rng.uniform(-GPS_JITTER_DEG, GPS_JITTER_DEG)
                payload["lon"] = place["lon"] + rng.uniform(-GPS_JITTER_DEG, GPS_JITTER_DEG)
            events.append((minute, "message", {"sender": sender, "payload": payload}))
    noise = scenario.get("noise", {})
    for n in range(noise.get("count", 0)):
        place = find(rng.choice(noise["places"]))
        style = noise["styles"][n % len(noise["styles"])]
        payload = {
            "text": message_text(style, "info_noise", place, 1, rng),
            "truth": {"incident": None, "category": "info_noise", "style": style},
        }
        minute = rng.uniform(0, scenario["duration_min"])
        events.append((minute, "message", {"sender": f"{run_id}:noise{n}", "payload": payload}))
    for offer in scenario.get("offers", []):
        data = {**offer, "place": find(offer["place"])}
        events.append((offer["available_from_min"], "offer", data))
    return sorted(events, key=lambda e: e[0])


def has_table(conn: psycopg.Connection, name: str) -> bool:
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (name,)).fetchone()[0]


def store_rain(conn: psycopg.Connection, rows: list[dict]) -> None:
    conn.cursor().executemany(
        "INSERT INTO rain_hourly (point_id, hour, mm) VALUES (%(point_id)s, %(hour)s, %(mm)s) "
        "ON CONFLICT (point_id, hour) DO UPDATE SET mm = EXCLUDED.mm",
        rows,
    )


def insert_offer(conn: psycopg.Connection, data: dict) -> None:
    place = data["place"]
    conn.execute(
        """
        INSERT INTO offers (kind, provider, qty_total, qty_available, geom, ward, verified)
        SELECT %(kind)s, %(provider)s, %(qty)s, %(qty)s, pt,
               (SELECT code FROM wards WHERE ST_Contains(geom, pt) LIMIT 1), %(verified)s
          FROM ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326) AS pt
        """,
        {
            "kind": data["kind"],
            "provider": f"{data['kind']} at {place['name']} (synthetic)",
            "qty": data["qty"],
            "lon": place["lon"],
            "lat": place["lat"],
            "verified": data.get("verified", False),
        },
    )


def truth_file(scenario: dict, run_id: str, speed: float, events: list[tuple]) -> dict:
    messages = [
        {"minute": round(m, 2), **d["payload"]["truth"]} for m, k, d in events if k == "message"
    ]
    return {
        "scenario": scenario["id"],
        "run_id": run_id,
        "speed": speed,
        "incidents": scenario["incidents"],
        "messages": messages,
    }


def run(path: Path, speed: float, reset: bool) -> None:
    import yaml

    scenario = yaml.safe_load(path.read_text())
    start = datetime.fromisoformat(scenario["start"])
    run_id = uuid.uuid4().hex[:8]
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        if reset:
            tables = ", ".join(t for t in RESET_TABLES if has_table(conn, t))
            conn.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
        events = build_events(gazetteer_lookup(conn), scenario, run_id)
        rain_fixture = scenario.get("context", {}).get("rain_fixture")
        if rain_fixture and has_table(conn, "rain_hourly"):
            store_rain(conn, json.loads((REPO_DIR / rain_fixture).read_text())["rows"])
        if has_table(conn, "sim_clock"):
            conn.execute("DELETE FROM sim_clock")
            conn.execute(
                "INSERT INTO sim_clock (wall_start, scenario_start, speed) "
                "VALUES (clock_timestamp(), %s, %s)",
                (start, speed),
            )
        log.info("replaying %s (%d events) at %sx", scenario["id"], len(events), speed)
        wall_start = time.monotonic()
        for n, (minute, kind, data) in enumerate(events):
            delay = wall_start + minute * 60 / speed - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            if kind == "message":
                conn.execute(
                    "INSERT INTO inbox (source, source_msg_id, sender_hash, payload) "
                    "VALUES ('replay', %s, %s, %s)",
                    (
                        f"{run_id}:{n}",
                        sender_hash("replay", data["sender"]),
                        json.dumps(data["payload"], ensure_ascii=False),
                    ),
                )
            else:
                insert_offer(conn, data)
        out_dir = REPO_DIR / "results" / scenario["id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        truth = truth_file(scenario, run_id, speed, events)
        (out_dir / "truth.json").write_text(json.dumps(truth, indent=1, ensure_ascii=False))
    log.info("replay done; truth written to %s", out_dir / "truth.json")


def stop() -> None:
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        if has_table(conn, "sim_clock"):
            conn.execute("DELETE FROM sim_clock")
    log.info("app clock back to wall time")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = sys.argv[1:]
    if args[:1] == ["stop"]:
        stop()
    elif args[:1] == ["run"] and len(args) >= 2:
        speed = float(args[args.index("--speed") + 1]) if "--speed" in args else 10.0
        run(Path(args[1]), speed, "--reset" in args)
    else:
        raise SystemExit(__doc__)
