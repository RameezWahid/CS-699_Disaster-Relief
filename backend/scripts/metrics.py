"""Classification metrics shared by training and the evaluation harness (no sklearn needed)."""


def per_class_report(labels: list[str], gold: list[str], predicted: list[str]) -> dict:
    """Precision, recall, F1 and support per class, plus macro-F1 over classes present in gold."""
    report = {}
    for label in labels:
        pairs = list(zip(gold, predicted, strict=True))
        tp = sum(g == label and p == label for g, p in pairs)
        fp = sum(g != label and p == label for g, p in pairs)
        fn = sum(g == label and p != label for g, p in pairs)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        report[label] = {
            "p": round(precision, 3),
            "r": round(recall, 3),
            "f1": round(f1, 3),
            "n": tp + fn,
        }
    present = [label for label in labels if report[label]["n"]]
    report["macro_f1"] = round(sum(report[label]["f1"] for label in present) / len(present), 3)
    return report
