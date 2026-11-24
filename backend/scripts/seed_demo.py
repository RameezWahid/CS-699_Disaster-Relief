"""Fill a new database with demo data so every feature can be seen at once.

usage: python -m scripts.seed_demo        (run inside the api container; demo.sh does this)

Adds three demo logins, 18 citizen reports and three relief offers. Reports go into the inbox
like real form submissions, so the worker classifies them, groups them into incidents and
proposes offers through the normal pipeline. Four reports from one place 1.5 to 3 hours ago,
followed by silence while the rest of the city keeps reporting, produce a silent-zone alert.

Safe to run again: users and offers that exist are kept, and each report has a fixed id, so it
is stored once. Demo data only; the password is DEMO_PASSWORD (default demo-pass-123).
"""

import json
import os

import psycopg

from scripts.api import hasher
from scripts.config import DATABASE_URL
from scripts.intake import sender_hash

PASSWORD = os.environ.get("DEMO_PASSWORD", "demo-pass-123")
WARDS = ["F/S", "G/N", "H/E", "H/W", "K/E", "L", "M/W", "S"]
USERS = [
    ("demo_admin", "admin", []),  # an empty list means all wards
    ("demo_coord", "coordinator", WARDS),
    ("demo_resp", "responder", WARDS),
]

# (minutes ago, sender, latitude, longitude, text). Coordinates are real Mumbai places.
REPORTS = [
    # Hindmata: one rescue incident confirmed by three people.
    (40, "a1", 19.00915, 72.84185, "Hindmata ke paas 6 log phase hain, paani gale tak hai, boat bhejo"),
    (35, "a2", 19.00925, 72.84200, "Hindmata flyover ke neeche 2 bacche aur 4 log fase hai, help"),
    (30, "a3", 19.00905, 72.84175, "People trapped near Hindmata cinema, water rising fast, need a boat"),
    # Sion: medical.
    (45, "b1", 19.04655, 72.86330, "Sion mein ek budhe aadmi ko chot lagi hai, doctor chahiye"),
    (25, "b2", 19.04660, 72.86320, "Sion station ke paas accident, 2 log ghayal, ambulance bhejo"),
    # Roads blocked.
    (20, "c1", 19.06530, 72.87940, "Kurla station la pani bharla, rasta band aahe"),
    (18, "c2", 19.06520, 72.87930, "Kurla station road blocked, buses stopped"),
    (15, "d1", 19.12040, 72.84910, "Andheri subway band hai, paani bhara hai"),
    (12, "d2", 19.12030, 72.84900, "Andheri subway closed, water above the knee"),
    # Food, shelter and more medical.
    (30, "e1", 19.05480, 72.89800, "Chembur mein 20 log ke liye khana aur peene ka paani chahiye"),
    (22, "f1", 19.01920, 72.84290, "Dadar station ke paas ghar mein paani ghus gaya, 5 log, rehne ki jagah chahiye"),
    (10, "g1", 19.00840, 72.84250, "Parel mein dialysis patient hai, dawa khatam ho gayi, doctor chahiye"),
    (8, "h1", 19.07930, 72.84710, "My mother needs insulin, we are stuck at home near Santacruz station"),
    (5, "i1", 19.01930, 72.84300, "दादर स्टेशन जवळ ५ जण अडकले आहेत, बोट पाठवा"),
    # Milan Subway: four people reported, then nothing for over an hour (silent zone).
    (165, "s1", 19.09050, 72.84300, "Milan subway ke paas paani bhar gaya hai, 8 log phase hain, boat bhejo"),
    (145, "s2", 19.09030, 72.84320, "Milan subway pe gaadi mein 3 log fase hai, paani badh raha hai"),
    (125, "s3", 19.09060, 72.84290, "People trapped near Milan subway, water up to the chest, send a boat"),
    (105, "s4", 19.09040, 72.84330, "Milan subway javal amhi adakle aahot, lahan mul pan aahe"),
]

# (kind, provider, quantity, latitude, longitude, verified)
OFFERS = [
    ("boat", "Mumbai Fire Brigade, Parel (demo)", 3, 19.00840, 72.84250, True),
    ("medical_team", "KEM Hospital team (demo)", 2, 19.00290, 72.84220, True),
    ("food", "Chembur community kitchen (demo)", 100, 19.05480, 72.89800, False),
]


def main() -> None:
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        for username, role, wards in USERS:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, wards) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (username) DO NOTHING",
                (username, hasher.hash(PASSWORD), role, wards),
            )
        added = 0
        for n, (minutes_ago, sender, lat, lon, text) in enumerate(REPORTS, 1):
            payload = {"text": text, "lat": lat, "lon": lon, "accuracy_m": 20}
            row = conn.execute(
                "INSERT INTO inbox (source, source_msg_id, sender_hash, received_at, payload) "
                "VALUES ('web', %s, %s, app_now() - make_interval(mins => %s), %s) "
                "ON CONFLICT (source, source_msg_id) DO NOTHING RETURNING id",
                (
                    f"demo-seed-{n:02d}",
                    sender_hash("web", f"demo-seed-{sender}"),
                    minutes_ago,
                    json.dumps(payload, ensure_ascii=False),
                ),
            ).fetchone()
            added += row is not None
        for kind, provider, qty, lat, lon, verified in OFFERS:
            conn.execute(
                """
                INSERT INTO offers (kind, provider, qty_total, qty_available, geom, ward, verified)
                SELECT %(kind)s, %(provider)s, %(qty)s, %(qty)s, pt,
                       (SELECT code FROM wards WHERE ST_Contains(geom, pt) LIMIT 1), %(verified)s
                  FROM ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326) AS pt
                 WHERE NOT EXISTS (SELECT 1 FROM offers WHERE provider = %(provider)s)
                """,
                {"kind": kind, "provider": provider, "qty": qty, "lat": lat, "lon": lon,
                 "verified": verified},
            )
    print(f"demo data ready: {len(USERS)} users, {added} new reports, {len(OFFERS)} offers")


if __name__ == "__main__":
    main()
