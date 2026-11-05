"""Check a gold-set CSV against data/gold/GUIDELINES.md and print its language/label mix."""

import csv
import json
import re
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

from shapely.geometry import Point, shape

from scripts.config import DATA_DIR
from scripts.extract import NEEDS, VULNERABILITY
from scripts.geocode import in_mumbai
from scripts.text import hamming, normalize, simhash
from scripts.triage import CATEGORY_SEVERITY

COLUMNS = [
    "id",
    "author",
    "lang",
    "script",
    "text",
    "category",
    "headcount",
    "needs",
    "vulnerability",
    "place_text",
    "lat",
    "lon",
    "ward",
    "checked_by",
    "notes",
]
LANGS = {"en", "hi", "mr", "hinglish", "mixed"}
SCRIPTS = {"latin", "devanagari", "mixed"}
ID_PATTERN = re.compile(r"G\d{3,}")
MAX_TEXT_CHARS = 500
# Targets from GUIDELINES.md, as (lang, script) -> count and category -> share.
LANGUAGE_TARGETS = {
    ("en", "latin"): 60,
    ("hinglish", "latin"): 90,
    ("hi", "devanagari"): 60,
    ("mr", "devanagari"): 45,
    ("mr", "latin"): 45,
}
CATEGORY_TARGETS = {
    "rescue": 0.25,
    "medical": 0.15,
    "food_water": 0.15,
    "shelter": 0.12,
    "road_blocked": 0.13,
    "info_noise": 0.20,
}


def load_wards() -> dict:
    features = json.loads((DATA_DIR / "base/BMC_Wards.geojson").read_text())["features"]
    return {f["properties"]["name"]: shape(f["geometry"]) for f in features}


def row_errors(row: dict, wards: dict) -> list[str]:
    errors = []
    if not ID_PATTERN.fullmatch(row["id"]):
        errors.append("id must look like G001")
    for column in ("author", "text"):
        if not row[column].strip():
            errors.append(f"{column} is empty")
    if row["lang"] not in LANGS:
        errors.append(f"lang {row['lang']!r} not in {sorted(LANGS)}")
    if row["script"] not in SCRIPTS:
        errors.append(f"script {row['script']!r} not in {sorted(SCRIPTS)}")
    if len(row["text"]) > MAX_TEXT_CHARS:
        errors.append(f"text longer than {MAX_TEXT_CHARS} characters")
    if row["category"] not in CATEGORY_SEVERITY:
        errors.append(f"category {row['category']!r} not in {sorted(CATEGORY_SEVERITY)}")
    if row["headcount"] and not (row["headcount"].isdigit() and int(row["headcount"]) > 0):
        errors.append("headcount must be a positive whole number or blank")
    for column, vocabulary in (("needs", NEEDS), ("vulnerability", VULNERABILITY)):
        unknown = {v for v in row[column].split(";") if v} - set(vocabulary)
        if unknown:
            errors.append(f"{column} has unknown values {sorted(unknown)}")
    if row["checked_by"] and row["checked_by"] == row["author"]:
        errors.append("checked_by must be someone other than the author")

    has_place = any(row[c].strip() for c in ("place_text", "lat", "lon", "ward"))
    if not has_place:
        return errors
    if not row["place_text"] or row["place_text"] not in row["text"]:
        errors.append("place_text must be copied exactly from text")
    try:
        lat, lon = float(row["lat"]), float(row["lon"])
    except ValueError:
        return errors + ["lat and lon are required numbers when a place is named"]
    if not in_mumbai(lat, lon):
        errors.append("lat/lon is outside Mumbai (check you did not swap them)")
    elif row["ward"] not in wards:
        errors.append(f"ward {row['ward']!r} is not a BMC ward code")
    elif not wards[row["ward"]].buffer(0.001).contains(Point(lon, lat)):
        # 0.001 degrees is about 100 m of slack for points on a ward boundary.
        actual = [code for code, geometry in wards.items() if geometry.contains(Point(lon, lat))]
        errors.append(f"lat/lon falls in ward {actual or 'none'}, not {row['ward']}")
    return errors


def mix_report(rows: list[dict]) -> None:
    total = len(rows)
    print(f"\n{total} messages")
    print("\nLanguage / script     have  target")
    languages = Counter((r["lang"], r["script"]) for r in rows)
    for key in sorted(set(languages) | set(LANGUAGE_TARGETS)):
        print(
            f"  {key[0]:>8} / {key[1]:<10} {languages[key]:>5}  {LANGUAGE_TARGETS.get(key, '-'):>6}"
        )
    print("\nCategory              have  target")
    categories = Counter(r["category"] for r in rows)
    for category, share in CATEGORY_TARGETS.items():
        print(f"  {category:<18} {categories[category] / total:>6.0%}  {share:>6.0%}")
    print("\nCoverage              have  target")
    for label, count, target in (
        ("headcount stated", sum(bool(r["headcount"]) for r in rows), "50%+"),
        ("place named", sum(bool(r["place_text"]) for r in rows), "70%+"),
        ("vulnerable person", sum(bool(r["vulnerability"]) for r in rows), "20%+"),
        ("second check done", sum(bool(r["checked_by"]) for r in rows), "100%"),
    ):
        print(f"  {label:<18} {count / total:>6.0%}  {target:>6}")


def main(path: Path) -> int:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            print(f"Header must be exactly:\n{','.join(COLUMNS)}")
            return 1
        rows = list(reader)
    if not rows:
        print("No rows yet.")
        return 1
    wards = load_wards()
    problems = 0
    for line, row in enumerate(rows, start=2):
        for error in row_errors(row, wards):
            print(f"line {line} ({row['id']}): {error}")
            problems += 1
    for id_, count in Counter(r["id"] for r in rows).items():
        if count > 1:
            print(f"id {id_} is used {count} times")
            problems += 1
    # Near-copies make the test easier than it looks; same SimHash threshold as the pipeline.
    hashes = [(r["id"], simhash(normalize(r["text"]))) for r in rows]
    for (id_a, hash_a), (id_b, hash_b) in combinations(hashes, 2):
        if hamming(hash_a, hash_b) <= 3:
            print(f"warning: {id_a} and {id_b} are near-copies; rewrite one")
    mix_report(rows)
    print(f"\n{problems} problem(s)" if problems else "\nNo problems found.")
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m scripts.validate_gold <gold.csv>")
    raise SystemExit(main(Path(sys.argv[1])))
