"""S1 scenario and its rain fixture are well formed and agree with each other."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from scripts.messages import CLASSES, STYLES

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "scenarios" / "S1_cloudburst_spring_tide.yaml"


@pytest.fixture(scope="module")
def scenario() -> dict:
    return yaml.safe_load(SCENARIO.read_text())


def test_incidents_are_valid_and_inside_the_run(scenario):
    incidents = scenario["incidents"]
    assert len({i["id"] for i in incidents}) == len(incidents)
    for incident in incidents:
        assert incident["category"] in CLASSES
        assert 0 <= incident["onset_min"] < scenario["duration_min"]
        reports = incident["reports"]
        assert 1 <= reports["senders"] <= reports["count"]
        assert 0.0 <= reports["with_gps"] <= 1.0
        assert set(reports["styles"]) <= set(STYLES)


def test_offers_and_noise_are_valid(scenario):
    for offer in scenario["offers"]:
        assert offer["qty"] > 0
        assert 0 <= offer["available_from_min"] < scenario["duration_min"]
    assert set(scenario["noise"]["styles"]) <= set(STYLES)


def test_rain_fixture_covers_the_run(scenario):
    fixture = json.loads((ROOT / scenario["context"]["rain_fixture"]).read_text())
    rows = fixture["rows"]
    assert rows and all(row["mm"] >= 0 for row in rows)
    hours = [datetime.fromisoformat(row["hour"]) for row in rows]
    start = datetime.fromisoformat(scenario["start"])
    assert min(hours) <= start
    assert (
        max(hours).timestamp()
        >= start.timestamp() + 60 * scenario["duration_min"] - 3600
    )
