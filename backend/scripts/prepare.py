"""Build train/val/test CSVs for the classifier from HumAID (real English) and synthetic data.

HumAID label mapping follows spec 4.2. HumAID text stays in data/ (git-ignored): its terms allow
research use only and forbid redistributing tweet text.

usage: python -m scripts.prepare
"""

import csv
import glob
import random
from collections import Counter

from scripts.config import DATA_DIR
from scripts.extract import NEEDS, labels_present
from scripts.text import normalize, tokens

SEED = 20261004
HUMAID_GLOB = str(DATA_DIR / "raw/events_set1/*/*_{part}.tsv")
SYNTHETIC_FILE = DATA_DIR / "training/synthetic.csv"
OUT_DIR = DATA_DIR / "training"
VAL_SHARE = 0.1
# HumAID is ~60% info-like classes; capping keeps the model from learning "everything is noise".
HUMAID_CAP = {"info_noise": 4000, "medical": 2500}
ROAD_WORDS = {
    "road",
    "roads",
    "street",
    "highway",
    "bridge",
    "traffic",
    "route",
    "closed",
    "blocked",
    "flyover",
}
DIRECT = {
    "injured_or_dead_people": "medical",
    "displaced_people_and_evacuations": "shelter",
}
INFO_LIKE = {
    "caution_and_advice",
    "not_humanitarian",
    "other_relevant_information",
    "rescue_volunteering_or_donation_effort",
    "sympathy_and_support",
    "missing_or_found_people",
}


def map_humaid(label: str, text: str) -> str | None:
    """Our label for a HumAID tweet, or None to drop it."""
    if label in DIRECT:
        return DIRECT[label]
    if label in INFO_LIKE:
        return "info_noise"
    words = tokens(normalize(text))
    if label == "infrastructure_and_utility_damage":
        return "road_blocked" if ROAD_WORDS & set(words) else "info_noise"
    if label == "requests_or_urgent_needs":
        needs = set(labels_present(words, NEEDS))
        # HumAID has no "trapped, send a boat" class, so rescue never comes from here (spec 4.2).
        if needs & {"medicine", "doctor"}:
            return "medical"
        if needs & {"shelter", "blanket"}:
            return "shelter"
        if needs & {"food", "water"}:
            return "food_water"
        return None  # "please donate"-style requests carry no usable need
    return None  # dont_know_cant_judge


def load_humaid(part: str) -> list[dict]:
    rows = []
    for path in sorted(glob.glob(HUMAID_GLOB.format(part=part))):
        with open(path, encoding="utf-8") as f:
            for record in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                label = map_humaid(record["class_label"], record["tweet_text"])
                if label:
                    rows.append(
                        {
                            "text": record["tweet_text"],
                            "label": label,
                            "style": "en",
                            "source": "humaid",
                        }
                    )
    return rows


def cap(rows: list[dict], rng: random.Random) -> list[dict]:
    by_label: dict[str, list[dict]] = {}
    for row in rows:
        by_label.setdefault(row["label"], []).append(row)
    capped = []
    for label, group in by_label.items():
        rng.shuffle(group)
        capped += group[: HUMAID_CAP.get(label, len(group))]
    return capped


def write(name: str, rows: list[dict]) -> None:
    with open(OUT_DIR / name, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["text", "label", "style", "source"])
        writer.writeheader()
        writer.writerows(rows)
    counts = Counter(r["label"] for r in rows)
    print(f"{name}: {len(rows)} rows {dict(sorted(counts.items()))}")


def main() -> None:
    rng = random.Random(SEED)
    with open(SYNTHETIC_FILE, encoding="utf-8") as f:
        synthetic = list(csv.DictReader(f))
    humaid = cap(load_humaid("train") + load_humaid("dev"), rng)
    pool = humaid + synthetic
    rng.shuffle(pool)
    n_val = int(len(pool) * VAL_SHARE)
    write("val.csv", pool[:n_val])
    write("train.csv", pool[n_val:])
    # Held-out real English, mapped the same way: the spec's "real-text check" (not the gold set).
    write("humaid_test.csv", load_humaid("test"))


if __name__ == "__main__":
    main()
