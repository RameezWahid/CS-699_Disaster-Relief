"""Offline responder app backend, demo scope (spec 4.7): task list and a batched POST /sync.

A responder's tasks are the accepted or dispatched allocations in their wards. The device queues
actions while offline and sends them in one batch. Each action is applied at most once (keyed by
its client_action_id), and a task changed on the server meanwhile is rejected, not overwritten.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from scripts.intake import enqueue, sender_hash
from scripts.matching import deliver, dispatch

router = APIRouter(prefix="/api")


def _api():
    # Looked up at request time: scripts.api includes this router, so importing it at the top
    # would be circular (and would break `python -m scripts.api adduser`).
    from scripts import api

    return api


def _current_user(request: Request) -> dict:
    return _api().current_user(request)


User = Annotated[dict, Depends(_current_user)]
# step: (status it must be in, function, status after)
STEPS = {
    "dispatch": ("accepted", dispatch, "dispatched"),
    "deliver": ("dispatched", deliver, "delivered"),
}

TASK_COLUMNS = """
    a.id, a.status, a.qty, n.kind, o.provider, i.id AS incident_id, i.category, i.ward,
    i.urgency_level, i.headcount, ST_X(i.geom) AS lon, ST_Y(i.geom) AS lat
"""
TASK_JOINS = """
    FROM allocations a JOIN needs n ON n.id = a.need_id
    JOIN incidents i ON i.id = n.incident_id JOIN offers o ON o.id = a.offer_id
"""


@router.get("/tasks")
def my_tasks(user: User) -> list[dict]:
    api = _api()
    with api.db() as conn:
        return (
            conn.cursor(row_factory=dict_row)
            .execute(
                f"SELECT {TASK_COLUMNS} {TASK_JOINS} "
                f"WHERE a.status IN ('accepted', 'dispatched') AND {api.IN_SCOPE} "
                "ORDER BY i.urgency_level DESC, a.id",
                {"wards": user["wards"]},
            )
            .fetchall()
        )


class Action(BaseModel):
    client_action_id: uuid.UUID
    type: Literal["task_status", "report"]
    payload: dict
    base_status: str | None = None  # the task status the device saw when the action was made
    client_ts: datetime | None = None


class SyncBatch(BaseModel):
    device_id: str = Field(min_length=8, max_length=64)
    actions: list[Action] = Field(max_length=200)


def apply_task_status(conn, user: dict, action: Action) -> tuple[str, str | None]:
    allocation_id = int(action.payload["allocation_id"])
    step = action.payload["action"]
    if step not in STEPS:
        return "rejected", f"unknown task action {step}"
    api = _api()
    row = conn.execute(
        f"SELECT a.status, i.ward {TASK_JOINS} WHERE a.id = %(id)s AND {api.IN_SCOPE} "
        "FOR UPDATE OF a",
        {"id": allocation_id, "wards": user["wards"]},
    ).fetchone()
    if row is None:
        return "rejected", "this task is outside your wards"
    current, ward = row
    if action.base_status and current != action.base_status:
        if current == "cancelled":
            return "rejected", "this task was cancelled or reassigned by a coordinator"
        return "rejected", f"this task changed on the server (now {current})"
    allowed_from, apply, new_status = STEPS[step]
    if current != allowed_from or not apply(conn, allocation_id):
        return "rejected", f"cannot {step} a task that is {current}"
    api.log_event(
        conn,
        f"allocation.{new_status}",
        ward,
        {"allocation_id": allocation_id, "by": user["username"], "via": "sync"},
    )
    return "applied", None


def apply_report(conn, user: dict, device_id: str, action: Action) -> tuple[str, str | None]:
    p = action.payload
    if not str(p.get("text", "")).strip() and p.get("lat") is None:
        return "rejected", "a report needs text or a location"
    payload = {
        "text": str(p.get("text", ""))[:2000],
        "lat": p.get("lat"),
        "lon": p.get("lon"),
        "accuracy_m": p.get("accuracy_m"),
        "category_hint": p.get("category_hint"),
        # Sent by a logged-in responder: the worker counts it as responder evidence.
        "responder": user["username"],
    }
    sender = sender_hash("web", device_id)
    enqueue(conn, "web", str(action.client_action_id), sender, payload)
    return "applied", None


@router.post("/sync")
def sync(batch: SyncBatch, user: User) -> dict:
    """Answer every action with applied, duplicate (already seen), or rejected (with a reason)."""
    results = []
    with _api().db() as conn:
        for action in batch.actions:
            with conn.transaction():
                # Holding this row lock makes a concurrent resend of the same id wait, then see it.
                fresh = conn.execute(
                    "INSERT INTO sync_actions (client_action_id, device_id, username, type, "
                    "client_ts, result) VALUES (%s, %s, %s, %s, %s, 'rejected') "
                    "ON CONFLICT DO NOTHING RETURNING 1",
                    (
                        action.client_action_id,
                        batch.device_id,
                        user["username"],
                        action.type,
                        action.client_ts,
                    ),
                ).fetchone()
                if fresh is None:
                    original, reason = conn.execute(
                        "SELECT result, reason FROM sync_actions WHERE client_action_id = %s",
                        (action.client_action_id,),
                    ).fetchone()
                    result = "duplicate"
                else:
                    try:
                        if action.type == "task_status":
                            original, reason = apply_task_status(conn, user, action)
                        else:
                            original, reason = apply_report(conn, user, batch.device_id, action)
                    except (KeyError, ValueError) as error:
                        original, reason = "rejected", f"bad action: {error}"
                    conn.execute(
                        "UPDATE sync_actions SET result = %s, reason = %s "
                        "WHERE client_action_id = %s",
                        (original, reason, action.client_action_id),
                    )
                    result = original
            results.append(
                {
                    "client_action_id": str(action.client_action_id),
                    "result": result,
                    "original": original,
                    "reason": reason,
                }
            )
    return {"results": results}
