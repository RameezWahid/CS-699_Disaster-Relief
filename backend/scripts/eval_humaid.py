"""Real-text check (spec 4.9): the exported classifier on held-out HumAID test tweets.

English only and mapped to our six classes, so it tests language understanding, not Mumbai
geocoding or Hinglish/Marathi. HumAID has no rescue class. Writes results/humaid_test.json.

usage: python -m scripts.eval_humaid
"""

import csv
import json

from scripts.classifier import LABELS, Classifier
from scripts.config import DATA_DIR, REPO_DIR
from scripts.metrics import per_class_report
from scripts.text import normalize


def main() -> None:
    with open(DATA_DIR / "training/humaid_test.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    model = Classifier()
    predicted = [model.classify(normalize(r["text"]))["label"] for r in rows]
    report = per_class_report(LABELS, [r["label"] for r in rows], predicted)
    out = REPO_DIR / "results/humaid_test.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    for label in LABELS:
        print(f"{label:<14} {report[label]}")
    print(f"macro-F1 over present classes: {report['macro_f1']} ({len(rows)} tweets)")


if __name__ == "__main__":
    main()
