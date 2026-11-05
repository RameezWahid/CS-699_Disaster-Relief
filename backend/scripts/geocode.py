"""Mumbai gazetteer geocoder (spec 4.3): n-gram spans, pg_trgm + RapidFuzz, kind weights."""

import math

from psycopg import Connection
from rapidfuzz import fuzz

from scripts.text import normalize_place, tokens

# Reports outside this box (lon_min, lat_min, lon_max, lat_max) are not in Mumbai.
MUMBAI_BBOX = (72.77, 18.89, 72.99, 19.28)
KIND_WEIGHT = {"chronic_spot": 1.0, "landmark": 1.0, "road": 0.8, "locality": 0.6, "ward": 0.4}
MAX_SPAN_WORDS = 4
MIN_SPAN_CHARS = 4
# pg_trgm prefilter; RapidFuzz rescoring decides. 0.45 keeps "andheri" -> "andheri subway".
TRGM_THRESHOLD = 0.45
# An exact ward-name match scores 0.4, so anything lower is noise.
MIN_SCORE = 0.4
AMBIGUITY_SCORE_GAP = 0.1
AMBIGUITY_DISTANCE_M = 1000
GPS_CONFIDENCE = 0.95
GPS_DEFAULT_ACCURACY_M = 30
# "near X" keeps X's point but the report is somewhere around it (spec: widen to 150-300 m).
RELATIONAL_WORDS = {
    "near",
    "nearby",
    "paas",
    "javal",
    "samor",
    "opposite",
    "behind",
    "पास",
    "जवळ",
    "समोर",
    "सामने",
}
RELATIONAL_UNCERTAINTY_M = 250
# Words that are never a place on their own: function words and generic place nouns.
STOPWORDS = {
    "the",
    "and",
    "near",
    "help",
    "please",
    "water",
    "people",
    "road",
    "station",
    "nagar",
    "marg",
    "lane",
    "subway",
    "bridge",
    "area",
    "east",
    "west",
    "hai",
    "hain",
    "mein",
    "paani",
    "pani",
    "gaya",
    "log",
    "jaldi",
    "madad",
    "bhejo",
    "ahe",
    "ahet",
    "amhi",
    "madat",
    "lavkar",
    "पानी",
    "पाणी",
    "आहे",
    "है",
    "में",
    "लोग",
    "मदत",
    "मदद",
}


def candidate_spans(text: str) -> list[str]:
    words = tokens(normalize_place(text))
    spans = set()
    for size in range(1, MAX_SPAN_WORDS + 1):
        for start in range(len(words) - size + 1):
            window = words[start : start + size]
            if window[0] in STOPWORDS or window[-1] in STOPWORDS:
                continue
            span = " ".join(window)
            if len(span) >= MIN_SPAN_CHARS:
                spans.add(span)
    return sorted(spans)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance; accurate enough at city scale."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def in_mumbai(lat: float, lon: float) -> bool:
    lon_min, lat_min, lon_max, lat_max = MUMBAI_BBOX
    return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max


def rank_places(conn: Connection, text: str) -> list[dict]:
    spans = candidate_spans(text)
    if not spans:
        return []
    with conn.transaction():
        conn.execute(
            "SELECT set_config('pg_trgm.similarity_threshold', %s, true)", (str(TRGM_THRESHOLD),)
        )
        rows = conn.execute(
            """
            SELECT s.span, pn.name_norm, similarity(pn.name_norm, s.span), p.id, p.name, p.kind,
                   ST_Y(p.geom), ST_X(p.geom), p.uncertainty_m
              FROM unnest(%s::text[]) AS s(span)
              JOIN place_names pn ON pn.name_norm %% s.span
              JOIN places p ON p.id = pn.place_id
            """,
            (spans,),
        ).fetchall()
    best = {}
    for span, name_norm, trgm, place_id, name, kind, lat, lon, uncertainty in rows:
        fuzzy = (trgm + fuzz.ratio(span, name_norm) / 100) / 2
        score = fuzzy * KIND_WEIGHT[kind]
        if score >= MIN_SCORE and score > best.get(place_id, {}).get("score", 0):
            best[place_id] = {
                "place_id": place_id,
                "name": name,
                "kind": kind,
                "lat": lat,
                "lon": lon,
                "uncertainty_m": uncertainty,
                "score": round(score, 3),
                "span": span,
            }
    # On equal scores the match on more words wins: "Kranti Nagar Kurla" over its "Kurla".
    return sorted(best.values(), key=lambda c: (c["score"], len(c["span"].split())), reverse=True)


def geocode(conn: Connection, text: str, lat: float | None, lon: float | None) -> dict:
    """Locate a report. A device GPS pin wins; text matching is the fallback."""
    if lat is not None and lon is not None and in_mumbai(lat, lon):
        return {
            "name": None,
            "lat": lat,
            "lon": lon,
            "uncertainty_m": GPS_DEFAULT_ACCURACY_M,
            "confidence": GPS_CONFIDENCE,
            "source": "gps",
            "ambiguous": False,
        }
    ranked = rank_places(conn, text)
    if not ranked:
        return {
            "name": None,
            "lat": None,
            "lon": None,
            "uncertainty_m": None,
            "confidence": 0.0,
            "source": None,
            "ambiguous": False,
        }
    top = ranked[0]
    uncertainty = top["uncertainty_m"]
    if set(tokens(text)) & RELATIONAL_WORDS:
        uncertainty = max(uncertainty, RELATIONAL_UNCERTAINTY_M)
    rivals = [
        c
        for c in ranked[1:]
        if top["score"] - c["score"] <= AMBIGUITY_SCORE_GAP
        and distance_m(top["lat"], top["lon"], c["lat"], c["lon"]) > AMBIGUITY_DISTANCE_M
    ]
    location = {
        "name": top["name"],
        "lat": top["lat"],
        "lon": top["lon"],
        "uncertainty_m": uncertainty,
        "confidence": top["score"],
        "source": f"gazetteer:{top['kind']}",
        "ambiguous": bool(rivals),
    }
    if rivals:
        location["alternatives"] = [
            {k: c[k] for k in ("name", "lat", "lon", "score")} for c in rivals[:2]
        ]
    return location
