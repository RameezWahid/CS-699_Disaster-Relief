"""Load wards, the H3 grid, and chronic flood spots into an empty database."""

import csv
import json
import logging
import h3
import psycopg
from scripts.config import DATA_DIR, DATABASE_URL, H3_RESOLUTION
# Use the geocoder's normalizer, or the same place gets different keys.
from scripts.text import normalize_place

WARDS_FILE = DATA_DIR / "base/BMC_Wards.geojson"
SPOTS_FILE = DATA_DIR / "gazetteer/chronic_spots.csv"

def load_wards(conn: psycopg.Connection) -> list[dict]:
    # Read the GeoJSON features. Each feature becomes one ward.
    wards = json.loads(WARDS_FILE.read_text())["features"]

    for w in wards:
        conn.execute(
            "INSERT INTO wards (code, geom) "
            "VALUES (%s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)))",
            (w["properties"]["name"], json.dumps(w["geometry"])),
        )

    return wards

def load_cells(conn: psycopg.Connection, wards: list[dict]) -> int:
    # Assign each cell to the ward whose polygon contains its centre.
    # This avoids assigning the same cell to multiple wards.
    cell_ward = {
        cell: w["properties"]["name"]
        for w in wards
        for cell in h3.geo_to_cells(w["geometry"], H3_RESOLUTION)
    }

    for cell, code in cell_ward.items():
        # H3 returns (latitude, longitude). WKT needs (longitude, latitude).
        ring = [(lng, lat) for lat, lng in h3.cell_to_boundary(cell)]
        wkt = ", ".join(f"{x} {y}" for x, y in [*ring, ring[0]])

        conn.execute(
            "INSERT INTO cells (h3, ward, geom) VALUES (%s, %s, ST_GeomFromText(%s, 4326))",
            (cell, code, f"POLYGON(({wkt}))"),
        )
    return len(cell_ward)


def load_chronic_spots(conn: psycopg.Connection) -> int:
    # Read all rows before inserting their place records.
    with SPOTS_FILE.open(newline="", encoding="utf-8") as f:
        spots = list(csv.DictReader(f))

    for s in spots:
        # Let PostGIS find the ward and build the point geometry.
        # RETURNING gives us the ID needed by place_names.
        place_id = conn.execute(
            """
            INSERT INTO places (kind, name, geom, uncertainty_m, ward, source)
            SELECT 'chronic_spot', %s, pt, %s,
                   (SELECT code FROM wards WHERE ST_Contains(geom, pt) LIMIT 1), %s
              FROM ST_SetSRID(ST_MakePoint(%s, %s), 4326) AS pt
            RETURNING id
            """,
            (s["name"], float(s["uncertainty_m"]), f"chronic_spots.csv:{s['sources']}",
             float(s["lon"]), float(s["lat"])),
        ).fetchone()[0]

        # Keep the original names, but remove variants that normalize to the same key.
        variants = filter(None, s["variants"].split("|"))
        for name in {normalize_place(n): n for n in [s["name"], *variants]}.values():
            conn.execute(
                "INSERT INTO place_names (place_id, name, name_norm) VALUES (%s, %s, %s)",
                (place_id, name, normalize_place(name)),
            )

    return len(spots)


def main() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        # Treat existing ward data as the signal that the base load has run.
        if conn.execute("SELECT count(*) FROM wards").fetchone()[0]:
            logging.info("base data already loaded; skipping")
            return

        wards = load_wards(conn)
        logging.info("loaded %d wards", len(wards))
        logging.info("loaded %d H3 cells", load_cells(conn, wards))
        logging.info("loaded %d chronic spots", load_chronic_spots(conn))


if __name__ == "__main__":
    # Log progress and failures without adding debug noise.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()