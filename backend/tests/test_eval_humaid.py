import csv
import json

from scripts import eval_humaid


class FakeClassifier:
    """Calls anything mentioning medicine medical, everything else info_noise."""

    def classify(self, text):
        return {"label": "medical" if "medicine" in text else "info_noise", "p": 1.0}


def test_writes_per_class_report(monkeypatch, tmp_path):
    (tmp_path / "data/training").mkdir(parents=True)
    with open(tmp_path / "data/training/humaid_test.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["text", "label", "style", "source"])
        writer.writeheader()
        for text, label in [
            ("need medicine now", "medical"),
            ("prayers for everyone", "info_noise"),
            ("families moved to camp", "shelter"),
        ]:
            writer.writerow({"text": text, "label": label, "style": "en", "source": "humaid"})
    monkeypatch.setattr(eval_humaid, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(eval_humaid, "REPO_DIR", tmp_path)
    monkeypatch.setattr(eval_humaid, "Classifier", FakeClassifier)
    eval_humaid.main()
    report = json.loads((tmp_path / "results/humaid_test.json").read_text())
    assert report["medical"]["f1"] == 1.0
    assert report["shelter"] == {"p": 0.0, "r": 0.0, "f1": 0.0, "n": 1}
    assert report["rescue"]["n"] == 0  # HumAID has no rescue class; it is left out of macro-F1
    assert report["macro_f1"] == round((1.0 + 0.667 + 0.0) / 3, 3)
