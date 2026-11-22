"""Replay builds the planted S1 events from held-out templates (no database needed)."""

import random
from pathlib import Path

import pytest

from scripts import replay
from scripts.messages import STYLES, templates_for

yaml = pytest.importorskip("yaml")

SCENARIO = Path(__file__).resolve().parents[2] / "scenarios" / "S1_cloudburst_spring_tide.yaml"


def fake_find(name: str) -> dict:
    """Stand-in gazetteer: every place at a fixed spot, with a Devanagari name."""
    return {"name": name, "lat": 19.0, "lon": 72.85, "deva": "दादर"}


@pytest.fixture(scope="module")
def scenario() -> dict:
    return yaml.safe_load(SCENARIO.read_text())


@pytest.fixture(scope="module")
def events(scenario) -> list[tuple]:
    return replay.build_events(fake_find, scenario, "test")


def test_every_planted_report_and_offer_is_sent(scenario, events):
    messages = [d for _, k, d in events if k == "message"]
    planted = sum(i["reports"]["count"] for i in scenario["incidents"])
    assert len(messages) == planted + scenario["noise"]["count"]
    assert sum(k == "offer" for _, k, _ in events) == len(scenario["offers"])
    assert [m for m, _, _ in events] == sorted(m for m, _, _ in events)


def test_reports_carry_truth_and_start_after_onset(scenario, events):
    onset = {i["id"]: i["onset_min"] for i in scenario["incidents"]}
    for minute, kind, data in events:
        if kind != "message":
            continue
        truth = data["payload"]["truth"]
        assert truth["style"] in STYLES
        if truth["incident"] is not None:
            assert minute >= onset[truth["incident"]]


def test_gps_pins_stay_near_the_place(events):
    pinned = [d["payload"] for _, k, d in events if k == "message" and "lat" in d["payload"]]
    assert pinned
    for payload in pinned:
        assert abs(payload["lat"] - 19.0) <= replay.GPS_JITTER_DEG
        assert abs(payload["lon"] - 72.85) <= replay.GPS_JITTER_DEG


def test_same_seed_gives_the_same_messages(scenario, events):
    again = replay.build_events(fake_find, scenario, "test")
    assert [d.get("payload") for _, _, d in again] == [d.get("payload") for _, _, d in events]


def test_text_uses_replay_templates_not_training_ones(monkeypatch):
    monkeypatch.setattr(replay, "add_noise", lambda text, style, rng: text)
    prefix = {t.split("{")[0] for t in templates_for("en", "rescue", "replay")}
    train_prefix = {t.split("{")[0] for t in templates_for("en", "rescue", "train")}
    for seed in range(20):
        text = replay.message_text("en", "rescue", fake_find("Hindmata"), 6, random.Random(seed))
        assert "Hindmata" in text
        assert any(p and text.startswith(p) for p in prefix - train_prefix)


def test_truth_file_lists_every_message(scenario, events):
    truth = replay.truth_file(scenario, "test", 10.0, events)
    assert truth["scenario"] == scenario["id"]
    assert len(truth["messages"]) == sum(k == "message" for _, k, _ in events)
