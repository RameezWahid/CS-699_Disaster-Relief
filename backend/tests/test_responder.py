"""Responder app backend: GET /api/tasks and POST /api/sync (exactly-once, conflicts, ward scope).

Writes real rows, so point it at a development database only:
    RELIEF_E2E=1 DATABASE_URL=postgresql://relief_app:<password>@localhost:5432/relief pytest
"""

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RELIEF_E2E") != "1", reason="set RELIEF_E2E=1 to run against a database"
)

PASSWORD = "test-pass-123"
# A point inside each ward, so the incident's ward is the one we ask for.
WARD_POINTS = {"F/S": (72.8419, 19.0091), "K/E": (72.8700, 19.1150)}


@pytest.fixture(scope="module")
def conn():
    import psycopg

    from scripts.config import DATABASE_URL

    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        yield conn


@pytest.fixture(scope="module")
def client(conn):
    from fastapi.testclient import TestClient

    from scripts.api import app, hasher

    conn.execute(
        "INSERT INTO users (username, password_hash, role, wards) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (username) DO UPDATE SET wards = EXCLUDED.wards",
        ("test_resp_fs", hasher.hash(PASSWORD), "responder", ["F/S"]),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login", json={"username": "test_resp_fs", "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        yield client


def accepted_task(conn, ward: str = "F/S") -> int:
    from scripts.matching import accept, claim

    lon, lat = WARD_POINTS[ward]
    incident = conn.execute(
        """
        INSERT INTO incidents (category_group, category, geom, ward, headcount, urgency_level,
                               urgency, credibility, credibility_signals, report_count,
                               sender_count, first_report_at, last_report_at)
        VALUES ('rescue', 'rescue', ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, 4, 5,
                '{}', 0.8, '{}', 1, 1, now(), now()) RETURNING id
        """,
        (lon, lat, ward),
    ).fetchone()[0]
    need = conn.execute(
        "INSERT INTO needs (incident_id, kind, qty_total, qty_open) "
        "VALUES (%s, 'boat', 1, 1) RETURNING id",
        (incident,),
    ).fetchone()[0]
    offer = conn.execute(
        "INSERT INTO offers (kind, provider, qty_total, qty_available, geom, ward, verified) "
        "VALUES ('boat', 'test boat', 1, 1, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, true) "
        "RETURNING id",
        (lon, lat, ward),
    ).fetchone()[0]
    allocation = claim(conn, need, offer, 1)
    assert allocation and accept(conn, allocation)
    return allocation


def status_action(allocation_id: int, step: str, base: str, action_id=None) -> dict:
    return {
        "client_action_id": str(action_id or uuid.uuid4()),
        "type": "task_status",
        "payload": {"allocation_id": allocation_id, "action": step},
        "base_status": base,
    }


def sync(client, *actions) -> list[dict]:
    body = {"device_id": "test-device-0001", "actions": list(actions)}
    response = client.post("/api/sync", json=body)
    assert response.status_code == 200, response.text
    return response.json()["results"]


def status(conn, allocation_id: int) -> str:
    query = "SELECT status FROM allocations WHERE id = %s"
    return conn.execute(query, (allocation_id,)).fetchone()[0]


def test_responder_sees_own_ward_tasks_only(client, conn):
    own, other = accepted_task(conn, "F/S"), accepted_task(conn, "K/E")
    ids = {t["id"] for t in client.get("/api/tasks").json()}
    assert own in ids and other not in ids


def test_resent_action_applies_once(client, conn):
    task = accepted_task(conn)
    action = status_action(task, "dispatch", "accepted")
    assert sync(client, action)[0]["result"] == "applied"
    again = sync(client, action)[0]
    assert (again["result"], again["original"]) == ("duplicate", "applied")
    assert status(conn, task) == "dispatched"
    events = conn.execute(
        "SELECT count(*) FROM event_log WHERE kind = 'allocation.dispatched' "
        "AND payload->>'allocation_id' = %s",
        (str(task),),
    ).fetchone()[0]
    assert events == 1


def test_offline_steps_replay_in_order_and_boat_comes_back(client, conn):
    task = accepted_task(conn)
    results = sync(
        client,
        status_action(task, "dispatch", "accepted"),
        status_action(task, "deliver", "dispatched"),
    )
    assert [r["result"] for r in results] == ["applied", "applied"]
    assert status(conn, task) == "delivered"
    available = conn.execute(
        "SELECT o.qty_available FROM allocations a JOIN offers o ON o.id = a.offer_id "
        "WHERE a.id = %s",
        (task,),
    ).fetchone()[0]
    assert available == 1  # a boat is reusable


def test_task_cancelled_while_offline_is_rejected(client, conn):
    from scripts.matching import cancel

    task = accepted_task(conn)
    assert cancel(conn, task)
    result = sync(client, status_action(task, "dispatch", "accepted"))[0]
    assert result["result"] == "rejected"
    assert "cancelled or reassigned" in result["reason"]
    assert status(conn, task) == "cancelled"


def test_task_in_another_ward_is_rejected(client, conn):
    task = accepted_task(conn, "K/E")
    result = sync(client, status_action(task, "dispatch", "accepted"))[0]
    assert result["result"] == "rejected"
    assert status(conn, task) == "accepted"


def test_offline_report_lands_in_inbox_once_as_responder(client, conn):
    action = {
        "client_action_id": str(uuid.uuid4()),
        "type": "report",
        "payload": {"text": "Road blocked by a fallen tree", "category_hint": "road_blocked"},
    }
    assert sync(client, action)[0]["result"] == "applied"
    assert sync(client, action)[0]["result"] == "duplicate"
    rows = conn.execute(
        "SELECT payload FROM inbox WHERE source_msg_id = %s", (action["client_action_id"],)
    ).fetchall()
    assert len(rows) == 1
    payload = rows[0][0] if isinstance(rows[0][0], dict) else json.loads(rows[0][0])
    assert payload["responder"] == "test_resp_fs"


def test_sync_needs_login():
    from fastapi.testclient import TestClient

    from scripts.api import app

    with TestClient(app) as anonymous:
        response = anonymous.post("/api/sync", json={"device_id": "test-device", "actions": []})
        assert response.status_code == 401
