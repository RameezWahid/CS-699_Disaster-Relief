"""Load wards, the H3 grid, chronic flood spots, and OSM gazetteer places into an empty database."""

import csv
import json
import logging
import re

import h3
import osmium
import psycopg
from scripts.config import DATA_DIR, DATABASE_URL, H3_RESOLUTION
# Use the geocoder's normalizer, or the same place gets different keys.
from scripts.text import normalize_place

WARDS_FILE = DATA_DIR / "base/BMC_Wards.geojson"
SPOTS_FILE = DATA_DIR / "gazetteer/chronic_spots.csv"
# OpenStreetMap extract: localities, stations, landmarks and main roads that people name.
OSM_FILE = DATA_DIR / "base/mumbai.osm.pbf"
OSM_NAME_TAGS = ["name", "name:en", "name:mr", "name:hi", "alt_name", "old_name", "short_name"]
LOCALITY_PLACES = {"suburb", "neighbourhood", "locality", "quarter", "village"}
LANDMARK_AMENITIES = {"hospital", "clinic", "school", "college", "place_of_worship", "marketplace"}
ROAD_SUFFIXES = {"road", "marg"}
ROAD_HIGHWAYS = {"motorway", "trunk", "primary", "secondary", "tertiary"}
JUNCTION_HIGHWAYS = {"traffic_signals", "junction", "motorway_junction"}
# Typical spatial extent of each kind, used as the location uncertainty when it is matched.
KIND_UNCERTAINTY_M = {"landmark": 100, "road": 400, "locality": 800, "ward": 2500}
# A long road is split into many OSM ways with one name. Keep one point per name per H3
# resolution-7 cell (about 5 km^2) so "SV Road" yields a few points, not hundreds.
ROAD_DEDUPE_RESOLUTION = 7


def osm_kind(tags: dict) -> str | None:
    if tags.get("place") in LOCALITY_PLACES:
        return "locality"
    if (
        tags.get("railway") == "station"
        or tags.get("amenity") in LANDMARK_AMENITIES
        or tags.get("emergency") == "assembly_point"
        or tags.get("highway") in JUNCTION_HIGHWAYS
        or (tags.get("highway") and (tags.get("bridge") or tags.get("tunnel")))
    ):
        return "landmark"
    if tags.get("highway") in ROAD_HIGHWAYS:
        return "road"
    return None


def road_initialisms(name: str) -> list[str]:
    """ "Swami Vivekanand Road" -> "SV Road"; "Jogeshwari-Vikhroli Link Road" -> JVLR."""
    words = re.findall(r"[A-Za-z]+", name)
    if len(words) < 3 or words[-1].lower() not in ROAD_SUFFIXES:
        return []
    prefix = "".join(w[0] for w in words[:-1]).upper()
    if words[-2].lower() == "link":
        return ["".join(w[0] for w in words).upper()]
    return [f"{prefix} Road", f"{prefix} Marg"]


def osm_names(tags: dict, kind: str) -> list[str]:
    names = []
    for key in OSM_NAME_TAGS:
        names.extend(part.strip() for part in tags.get(key, "").split(";") if part.strip())
    if kind == "road":
        names.extend(road_initialisms(tags["name"]))
    return names


def osm_places() -> list[dict]:
    places, seen = [], set()
    processor = osmium.FileProcessor(str(OSM_FILE)).with_locations()
    for obj in processor.with_filter(osmium.filter.KeyFilter("name")):
        tags = dict(obj.tags)
        kind = osm_kind(tags)
        if kind is None:
            continue
        if obj.is_node():
            location = obj.location
        elif obj.is_way():
            locations = [n.location for n in obj.nodes if n.location.valid()]
            if not locations:
                continue
            location = locations[len(locations) // 2]
        else:
            continue
        dedupe_key = (
            kind,
            tags["name"],
            h3.latlng_to_cell(location.lat, location.lon, ROAD_DEDUPE_RESOLUTION),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        places.append(
            {
                "kind": kind,
                "name": tags["name"],
                "names": osm_names(tags, kind),
                "lat": location.lat,
                "lon": location.lon,
                "uncertainty_m": KIND_UNCERTAINTY_M[kind],
                "osm_id": f"{obj.type_str()}{obj.id}",
                "source": "osm",
            }
        )
    return places


def insert_places(conn: psycopg.Connection, places: list[dict]) -> None:
    with conn.cursor() as cur:
        for place in places:
            cur.execute(
                """
                INSERT INTO places (kind, name, geom, uncertainty_m, ward, osm_id, source)
                SELECT %(kind)s, %(name)s, pt, %(uncertainty_m)s,
                       (SELECT code FROM wards WHERE ST_Contains(geom, pt) LIMIT 1),
                       %(osm_id)s, %(source)s
                  FROM ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326) AS pt
                RETURNING id
                """,
                place,
            )
            place_id = cur.fetchone()[0]
            keys = {normalize_place(n): n for n in place["names"]}
            cur.executemany(
                "INSERT INTO place_names (place_id, name, name_norm) VALUES (%s, %s, %s)",
                [(place_id, name, key) for key, name in keys.items() if key],
            )


def load_ward_places(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        INSERT INTO places (kind, name, geom, uncertainty_m, ward, source)
        SELECT 'ward', 'Ward ' || code, ST_PointOnSurface(geom), %s, code, 'BMC_Wards.geojson'
          FROM wards
        """,
        (KIND_UNCERTAINTY_M["ward"],),
    )
    rows = conn.execute("SELECT id, ward FROM places WHERE kind = 'ward'").fetchall()
    with conn.cursor() as cur:
        for place_id, code in rows:
            # "F/N" is written "F North", "F/N ward", or "ward F/N" in messages.
            spoken = (
                code.replace("/N", " North")
                .replace("/S", " South")
                .replace("/E", " East")
                .replace("/W", " West")
                .replace("/C", " Central")
            )
            for name in {f"{code} ward", f"ward {code}", f"{spoken} ward"}:
                cur.execute(
                    "INSERT INTO place_names VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (place_id, name, normalize_place(name)),
                )



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
        if not conn.execute("SELECT count(*) FROM wards").fetchone()[0]:
            wards = load_wards(conn)
            logging.info("loaded %d wards", len(wards))
            logging.info("loaded %d H3 cells", load_cells(conn, wards))
            logging.info("loaded %d chronic spots", load_chronic_spots(conn))
        elif conn.execute("SELECT count(*) FROM places WHERE source = 'osm'").fetchone()[0]:
            logging.info("base data already loaded; skipping")
            return

        # Databases loaded before the OSM places existed get them now.
        osm = osm_places()
        insert_places(conn, osm)
        # The clip box is larger than BMC; drop OSM places outside every ward.
        dropped = conn.execute(
            "DELETE FROM places WHERE ward IS NULL AND kind NOT IN ('chronic_spot', 'ward')"
        ).rowcount
        logging.info("loaded %d OSM places (%d outside BMC dropped)", len(osm) - dropped, dropped)
        load_ward_places(conn)


if __name__ == "__main__":
    # Log progress and failures without adding debug noise.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()