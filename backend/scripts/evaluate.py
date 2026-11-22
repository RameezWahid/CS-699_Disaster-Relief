"""Score a scenario replay against its planted ground truth (spec M9).

usage:
  python -m scripts.evaluate results/<scenario id>/truth.json

Reads what the pipeline made of each replayed message (category, location, headcount and the
incident it joined) and writes results/<scenario id>/scores.json with:

  triage      per-class precision, recall and F1 of the report category
  clustering  B-cubed precision, recall and F1 of reports grouped into incidents, plus how many
              planted incidents were found, split, or merged with another
  location    distance from each located report to the true spot (median, share within 250 m)
  headcount   share of reports whose extracted headcount equals the planted one
  noise       share of noise messages kept apart from every planted incident (chatter may form
              its own info incidents; it must not join a real one)
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

import psycopg

from scripts.config import DATABASE_URL
from scripts.messages import CLASSES
from scripts.metrics import per_class_report

NEAR_M = 250


def root_incident(incident_id, merged_into: dict):
    """Follow merged_into links to the incident a report finally belongs to."""
    seen = set()
    while incident_id in merged_into and merged_into[incident_id] and incident_id not in seen:
        seen.add(incident_id)
        incident_id = merged_into[incident_id]
    return incident_id


def bcubed(pairs: list[tuple]) -> dict:
    """B-cubed scores for (true cluster, predicted cluster) pairs; None predicted = alone."""
    pairs = [(t, p if p is not None else ("alone", n)) for n, (t, p) in enumerate(pairs)]
    by_true, by_pred = Counter(t for t, _ in pairs), Counter(p for _, p in pairs)
    both = Counter(pairs)
    precision = sum(both[(t, p)] / by_pred[p] for t, p in pairs) / len(pairs)
    recall = sum(both[(t, p)] / by_true[t] for t, p in pairs) / len(pairs)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"p": round(precision, 3), "r": round(recall, 3), "f1": round(f1, 3)}


def incident_outcomes(pairs: list[tuple]) -> dict:
    """Found: most reports share one incident. Split: spread over several. Merged: that
    incident also holds most of another planted incident's reports."""
    clusters = defaultdict(Counter)
    for true, predicted in pairs:
        if predicted is not None:
            clusters[true][predicted] += 1
    sizes = Counter(t for t, _ in pairs)
    main = {}
    for true, size in sizes.items():
        if clusters[true]:
            predicted, count = clusters[true].most_common(1)[0]
            if count * 2 > size:
                main[true] = predicted
    owners = Counter(main.values())
    return {
        "planted": len(sizes),
        "found": len(main),
        "split": sum(len(clusters[t]) > 1 for t in sizes),
        "merged": sum(owners[p] > 1 for p in main.values()),
        "missed": sorted(t for t in sizes if t not in main),
    }


def score(rows: list[dict]) -> dict:
    """Scores for rows of {truth, category, incident, distance_m, headcount}."""
    processed = [r for r in rows if r["category"] is not None]
    planted = [r for r in processed if r["truth"]["incident"] is not None]
    noise = [r for r in processed if r["truth"]["incident"] is None]
    pairs = [(r["truth"]["incident"], r["incident"]) for r in planted]
    real = {r["incident"] for r in planted if r["incident"] is not None}
    distances = [r["distance_m"] for r in planted if r["distance_m"] is not None]
    with_count = [r for r in planted if r["truth"].get("headcount", 1) > 1]
    return {
        "messages": len(rows),
        "processed": len(processed),
        "triage": per_class_report(
            CLASSES,
            [r["truth"]["category"] for r in processed],
            [r["category"] for r in processed],
        ),
        "clustering": {**bcubed(pairs), **incident_outcomes(pairs)} if pairs else {},
        "location": {
            "located": len(distances),
            "of": len(planted),
            "median_m": round(median(distances)) if distances else None,
            f"within_{NEAR_M}m": (
                round(sum(d <= NEAR_M for d in distances) / len(distances), 3)
                if distances
                else None
            ),
        },
        "headcount": {
            "exact": (
                round(
                    sum(r["headcount"] == r["truth"]["headcount"] for r in with_count)
                    / len(with_count),
                    3,
                )
                if with_count
                else None
            ),
            "of": len(with_count),
        },
        "noise": {
            "kept_apart": (
                round(sum(r["incident"] not in real for r in noise) / len(noise), 3)
                if noise
                else None
            ),
            "of": len(noise),
        },
    }


def load(conn: psycopg.Connection, run_id: str) -> list[dict]:
    """One row per replayed message with what the pipeline extracted (None if unprocessed)."""
    merged_into = dict(conn.execute("SELECT id, merged_into FROM incidents").fetchall())
    rows = conn.execute(
        """
        SELECT i.payload->'truth', r.category, r.incident_id, r.headcount,
               CASE WHEN r.geom IS NOT NULL AND i.payload->'truth' ? 'lat' THEN
                 ST_Distance(r.geom::geography, ST_SetSRID(ST_MakePoint(
                   (i.payload->'truth'->>'lon')::float, (i.payload->'truth'->>'lat')::float),
                   4326)::geography)
               END
          FROM inbox i LEFT JOIN reports r ON r.inbox_id = i.id
         WHERE i.source = 'replay' AND i.source_msg_id LIKE %s
         ORDER BY i.id
        """,
        (f"{run_id}:%",),
    ).fetchall()
    return [
        {
            "truth": truth,
            "category": category,
            "incident": root_incident(incident, merged_into) if incident else None,
            "headcount": headcount,
            "distance_m": distance,
        }
        for truth, category, incident, headcount, distance in rows
    ]


def summary(scores: dict) -> str:
    clustering, location = scores["clustering"], scores["location"]
    return "\n".join(
        [
            f"processed {scores['processed']}/{scores['messages']} messages",
            f"triage macro-F1 {scores['triage']['macro_f1']}",
            f"clustering B-cubed F1 {clustering.get('f1')}: found {clustering.get('found')}"
            f"/{clustering.get('planted')}, split {clustering.get('split')},"
            f" merged {clustering.get('merged')}, missed {clustering.get('missed')}",
            f"location median {location['median_m']} m,"
            f" {location[f'within_{NEAR_M}m']} within {NEAR_M} m"
            f" ({location['located']}/{location['of']} located)",
            f"headcount exact {scores['headcount']['exact']}",
            f"noise kept apart from real incidents {scores['noise']['kept_apart']}",
        ]
    )


def main(truth_path: Path) -> None:
    truth = json.loads(truth_path.read_text())
    with psycopg.connect(DATABASE_URL) as conn:
        scores = score(load(conn, truth["run_id"]))
    scores = {"scenario": truth["scenario"], "run_id": truth["run_id"], **scores}
    (truth_path.parent / "scores.json").write_text(json.dumps(scores, indent=1))
    print(summary(scores))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]))
