"""End to end: web form -> inbox -> worker -> report -> incident -> needs.

Needs a migrated database with base data loaded (docker compose up db setup).
Writes real rows, so point it at a development database only:
    RELIEF_E2E=1 DATABASE_URL=postgresql://relief_app:<password>@localhost:5432/relief pytest
"""

import os
import time
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RELIEF_E2E") != "1", reason="set RELIEF_E2E=1 to run against a database"
)

# Hindmata is a chronic flood spot, so the point falls inside a BMC ward.
FORM = {
    "text": "Hindmata ke paas 5 log phase hain, ek baccha bhi hai",
    "lat": "19.00914",
    "lon": "72.84193",
    "accuracy_m": "20",
}
DEADLINE_S = 15


def test_report_becomes_incident_with_needs():
    import psycopg
    from fastapi.testclient import TestClient

    from scripts.api import app
    from scripts.config import DATABASE_URL
    from scripts.worker import claim_and_process

    form = {**FORM, "client_msg_id": str(uuid.uuid4()), "device_id": str(uuid.uuid4())}
    with TestClient(app) as client:
        first = client.post("/api/reports", data=form)
        assert first.status_code == 202, first.text
        inbox_id = first.json()["inbox_id"]
        assert inbox_id is not None

        # The same client message is ignored, not queued twice.
        again = client.post("/api/reports", data=form)
        assert again.json() == {"inbox_id": None, "duplicate": True}

        assert client.get("/api/wards").json()["features"]

    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        # Process it here. A running worker container may take it first; either is fine.
        deadline = time.monotonic() + DEADLINE_S
        while time.monotonic() < deadline:
            status = conn.execute("SELECT status FROM inbox WHERE id = %s", (inbox_id,)).fetchone()[0]
            if status != "pending":
                break
            if not claim_and_process(conn):
                time.sleep(0.2)
        assert status == "done"

        category, headcount, level, ward, incident_id = conn.execute(
            "SELECT category, headcount, urgency_level, ward, incident_id FROM reports "
            "WHERE inbox_id = %s",
            (inbox_id,),
        ).fetchone()
        assert (category, headcount, level) == ("rescue", 5, 5)
        assert ward is not None
        assert incident_id is not None

        needs = conn.execute(
            "SELECT kind, qty_total FROM needs WHERE incident_id = %s", (incident_id,)
        ).fetchall()
        assert ("boat", 1) in needs

        # Hindmata is a chronic flood spot; urgency 5 fast-tracks past verification.
        signals, fast_track, urgency = conn.execute(
            "SELECT credibility_signals, fast_track, urgency FROM incidents WHERE id = %s",
            (incident_id,),
        ).fetchone()
        assert signals["chronic_spot"] is True
        assert fast_track is True
        assert set(urgency) >= {"S", "factors", "level", "override"}


def test_coordinator_flow_login_verify_offer_accept():
    import psycopg
    from fastapi.testclient import TestClient

    from scripts.api import app, hasher
    from scripts.config import DATABASE_URL
    from scripts.worker import claim_and_process

    run = uuid.uuid4().hex[:8]
    coordinator, responder, outsider = f"coord-{run}", f"resp-{run}", f"other-{run}"
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        for name, role, wards in [(coordinator, "coordinator", ["K/E"]),
                                  (responder, "responder", ["K/E"]),
                                  (outsider, "coordinator", ["A"])]:
            conn.execute("INSERT INTO users (username, password_hash, role, wards) "
                         "VALUES (%s, %s, %s, %s)", (name, hasher.hash("correct-horse"), role, wards))

    def login(client, name, password="correct-horse"):
        return client.post("/api/auth/login", json={"username": name, "password": password})

    with TestClient(app) as client:
        # Dashboard data needs a session; citizen reporting does not.
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/incidents").status_code == 401
        assert login(client, coordinator, "wrong").status_code == 401
        assert login(client, "nobody", "wrong").status_code == 401

        # Andheri subway (ward K/E): one sender with GPS is below the credibility bar.
        form = {"text": "Andheri subway mein 4 log phase hain, boat bhejo", "lat": "19.12037",
                "lon": "72.84908", "client_msg_id": run, "device_id": run}
        inbox_id = client.post("/api/reports", data=form).json()["inbox_id"]
        with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
            deadline = time.monotonic() + DEADLINE_S
            while time.monotonic() < deadline:
                row = conn.execute("SELECT incident_id FROM reports WHERE inbox_id = %s",
                                   (inbox_id,)).fetchone()
                if row:
                    break
                if not claim_and_process(conn):
                    time.sleep(0.2)
        incident_id = row[0]

        me = login(client, coordinator).json()
        assert me == {"username": coordinator, "role": "coordinator", "wards": ["K/E"]}
        assert client.get("/api/auth/me").json() == me

        assert incident_id in [i["id"] for i in client.get("/api/incidents").json()]
        assert incident_id in [i["id"] for i in client.get("/api/incidents?queue=verify").json()]
        detail = client.get(f"/api/incidents/{incident_id}").json()
        assert detail["reports"][0]["extraction"]["location"]["source"] == "gps"
        assert [n["kind"] for n in detail["needs"]] == ["boat"]

        # Verifying takes it out of the queue.
        assert client.post(f"/api/incidents/{incident_id}/status",
                           json={"status": "verified"}).status_code == 200
        assert incident_id not in [i["id"] for i in client.get("/api/incidents?queue=verify").json()]

        # A boat offer next door gets proposed, and the coordinator accepts it once.
        offer = client.post("/api/offers", json={"kind": "boat", "provider": f"Fire brigade {run}",
                                                 "qty": 3, "lat": 19.1206, "lon": 72.8492})
        assert offer.status_code == 201
        assert offer.json()["id"] in [o["id"] for o in client.get("/api/offers").json()]
        assert client.post("/api/offers", json={"kind": "boat", "provider": "x", "qty": 0,
                                                "lat": 19.1, "lon": 72.8}).status_code == 422
        mine = [a for a in client.get("/api/allocations?status=proposed").json()
                if a["incident_id"] == incident_id]
        assert mine and mine[0]["expires_in_min"] > 0
        allocation_id = mine[0]["id"]
        assert client.post(f"/api/allocations/{allocation_id}/accept").status_code == 200
        assert client.post(f"/api/allocations/{allocation_id}/accept").status_code == 409

        # Another ward's coordinator cannot see it; a responder can look but not decide.
        login(client, outsider)
        assert client.get(f"/api/incidents/{incident_id}").status_code == 404
        assert incident_id not in [i["id"] for i in client.get("/api/incidents").json()]
        login(client, responder)
        assert client.get(f"/api/incidents/{incident_id}").status_code == 200
        assert client.post(f"/api/incidents/{incident_id}/status",
                           json={"status": "resolved"}).status_code == 403

        # Resolve it so later runs start a fresh incident here.
        login(client, coordinator)
        assert client.post(f"/api/incidents/{incident_id}/status",
                           json={"status": "resolved"}).status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/auth/me").status_code == 401
