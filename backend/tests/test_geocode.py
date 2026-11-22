"""Geocoder tests against a throwaway `relief_geocode_test` database in the compose Postgres."""

import os

import psycopg
import pytest

ADMIN_URL = os.environ.get("TEST_ADMIN_URL", "postgresql://relief:relief@localhost:5432/relief")
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + "/relief_geocode_test"
# Must be set before scripts.config is imported by the modules below.
os.environ["DATABASE_URL"] = TEST_URL

from scripts.geocode import geocode  # noqa: E402
from scripts.text import normalize, normalize_place  # noqa: E402


@pytest.fixture(scope="module")
def conn():
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable; run `docker compose up -d db`")
    with admin:
        admin.execute("DROP DATABASE IF EXISTS relief_geocode_test WITH (FORCE)")
        admin.execute("CREATE DATABASE relief_geocode_test")
    from scripts.migrate import migrate

    migrate()
    with psycopg.connect(TEST_URL, autocommit=True) as connection:
        yield connection


def add_place(conn, kind: str, name: str, names: list[str], lat: float, lon: float) -> None:
    place_id = conn.execute(
        "INSERT INTO places (kind, name, geom, uncertainty_m, source) "
        "VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), 300, 'test') RETURNING id",
        (kind, name, lon, lat),
    ).fetchone()[0]
    for variant in names:
        conn.execute(
            "INSERT INTO place_names VALUES (%s, %s, %s)",
            (place_id, variant, normalize_place(variant)),
        )


def test_gps_pin_inside_mumbai_wins(conn):
    location = geocode(conn, normalize("Hindmata pe paani"), 19.0, 72.84)
    assert (location["source"], location["lat"], location["lon"]) == ("gps", 19.0, 72.84)


def test_gps_pin_outside_mumbai_falls_back_to_text(conn):
    conn.execute("TRUNCATE places CASCADE")
    add_place(conn, "chronic_spot", "Hindmata", ["Hindmata", "Hind Mata"], 19.00914, 72.84193)
    location = geocode(conn, normalize("Hind Mata pe paani bhar gaya"), 28.6, 77.2)
    assert (location["name"], location["source"]) == ("Hindmata", "gazetteer:chronic_spot")


def test_near_widens_uncertainty(conn):
    conn.execute("TRUNCATE places CASCADE")
    add_place(conn, "chronic_spot", "Milan Subway", ["Milan Subway"], 19.09042, 72.84306)
    assert geocode(conn, normalize("Milan Subway near paani"), None, None)["uncertainty_m"] >= 250


def test_longer_exact_match_beats_a_contained_one(conn):
    conn.execute("TRUNCATE places CASCADE")
    add_place(conn, "chronic_spot", "Kurla Station", ["Kurla Station", "Kurla"], 19.06528, 72.87938)
    add_place(
        conn, "chronic_spot", "Kranti Nagar Kurla", ["Kranti Nagar Kurla"], 19.06792, 72.88685
    )
    text = normalize("20 log Kranti Nagar Kurla me phas gaye, nikalo plz")
    assert geocode(conn, text, None, None)["name"] == "Kranti Nagar Kurla"
    assert geocode(conn, normalize("Kurla station pe paani"), None, None)["name"] == "Kurla Station"


def test_contained_name_that_sorts_first_does_not_win(conn):
    # Spans are matched in sorted order, so "andheri" is seen before "andheri subway"; a tie on
    # score must still go to the longer, more specific match.
    conn.execute("TRUNCATE places CASCADE")
    add_place(
        conn, "chronic_spot", "Andheri Station", ["Andheri Station", "Andheri"], 19.1197, 72.8464
    )
    add_place(conn, "chronic_spot", "Andheri Subway", ["Andheri Subway"], 19.1204, 72.8491)
    text = normalize("4 log Andheri Subway me phas gaye nikalo plz")
    assert geocode(conn, text, None, None)["name"] == "Andheri Subway"


def test_unknown_place_returns_no_location(conn):
    conn.execute("TRUNCATE places CASCADE")
    location = geocode(conn, normalize("paani bhar gaya madad karo"), None, None)
    assert (location["lat"], location["confidence"]) == (None, 0.0)
