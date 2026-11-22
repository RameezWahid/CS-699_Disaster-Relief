"""Synthetic labelled flood messages in English, Hinglish, Hindi, and Marathi (spec 4.2, 5.5).

Templates x real gazetteer places x headcount forms x vulnerability x noise. Every row is marked
synthetic. Templates need review by fluent speakers; spot-check 10% of the output by hand.
This is training data only: the hand-written gold set is never generated or touched here.

usage: python -m scripts.synth <out.csv> [rows_per_style_and_class]
"""

import csv
import random
import sys

import psycopg

from scripts.config import DATABASE_URL
from scripts.messages import (
    CLASSES,
    STYLES,
    VULNERABLE,
    add_noise,
    headcount_phrase,
    templates_for,
)

SEED = 20261004


def is_devanagari(text: str) -> bool:
    return any("\u0900" <= c <= "\u097f" for c in text)


def plausible_place(name: str) -> bool:
    """Names a person might type: short, no company suffixes or address punctuation."""
    lowered = name.lower()
    return (
        len(name.split()) <= 3
        and "," not in name
        and not any(w in lowered for w in ("pvt", "ltd", "llp", "school-", "hospital,"))
    )


def load_places() -> dict[str, list[str]]:
    """Latin and Devanagari place names from the gazetteer (chronic spots weighted up)."""
    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute("""
            SELECT p.kind, pn.name FROM places p JOIN place_names pn ON pn.place_id = p.id
             WHERE p.kind IN ('chronic_spot', 'locality', 'landmark')
            """).fetchall()
    rows = [(kind, name) for kind, name in rows if plausible_place(name)]
    # Chronic spots are weighted up: they are where flood reports cluster.
    weighted = rows + [(k, n) for k, n in rows if k == "chronic_spot"] * 5
    return {
        "latin": [n for _, n in weighted if not is_devanagari(n)],
        "deva": [n for _, n in weighted if is_devanagari(n)],
    }


def generate(rows_per_cell: int) -> list[dict]:
    rng = random.Random(SEED)
    places = load_places()
    out = []
    for style in STYLES:
        for label in CLASSES:
            templates = templates_for(style, label, "train")
            for _ in range(rows_per_cell):
                template = rng.choice(templates)
                deva_style = style in ("hi", "mr")
                # Devanagari writers often type English place names in Latin script too.
                pool = places["deva"] if deva_style and rng.random() < 0.7 else places["latin"]
                count, _ = headcount_phrase(style, rng)
                text = template.format(p=rng.choice(pool), n=count, v=rng.choice(VULNERABLE[style]))
                out.append(
                    {
                        "text": add_noise(text, style, rng),
                        "label": label,
                        "style": style,
                        "source": "synthetic",
                    }
                )
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    per_cell = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    rows = generate(per_cell)
    with open(sys.argv[1], "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["text", "label", "style", "source"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} synthetic rows to {sys.argv[1]}")
