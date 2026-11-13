from scripts.metrics import per_class_report

LABELS = ["rescue", "medical", "food", "other"]


def test_perfect_predictions_score_one():
    gold = ["rescue", "medical", "food", "other"]
    report = per_class_report(LABELS, gold, gold)
    assert report["macro_f1"] == 1.0
    assert all(report[label]["f1"] == 1.0 for label in LABELS)


def test_per_class_precision_recall_and_support():
    gold = ["rescue", "rescue", "medical", "food"]
    predicted = ["rescue", "medical", "medical", "rescue"]
    report = per_class_report(LABELS, gold, predicted)
    assert report["rescue"] == {"p": 0.5, "r": 0.5, "f1": 0.5, "n": 2}
    assert report["medical"] == {"p": 0.5, "r": 1.0, "f1": 0.667, "n": 1}
    assert report["food"] == {"p": 0.0, "r": 0.0, "f1": 0.0, "n": 1}


def test_macro_f1_ignores_classes_absent_from_gold():
    gold = ["rescue", "medical"]
    report = per_class_report(LABELS, gold, gold)
    assert report["other"]["n"] == 0
    assert report["macro_f1"] == 1.0
