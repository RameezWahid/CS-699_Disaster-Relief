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
