import csv

import pytest

pytest.importorskip("torch")
pytest.importorskip("transformers")

from scripts.classifier import LABELS  # noqa: E402
from scripts.train_classifier import read  # noqa: E402


def test_read_normalizes_text_and_indexes_labels(tmp_path):
    path = tmp_path / "train.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["text", "label", "style", "source"])
        writer.writeheader()
        for text, label in [("HELP  stuck at Kurla", "rescue"), ("need medicine", "medical")]:
            writer.writerow({"text": text, "label": label, "style": "en", "source": "synthetic"})
    texts, labels = read(path)
    assert labels == [LABELS.index("rescue"), LABELS.index("medical")]
    assert texts[0] == "help stuck at kurla"  # normalized the same way as at inference
