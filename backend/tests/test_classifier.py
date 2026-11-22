from types import SimpleNamespace

import numpy as np

from scripts import classifier


def test_missing_model_falls_back_to_rules(monkeypatch, tmp_path):
    monkeypatch.setattr(classifier, "MODEL_DIR", tmp_path)
    classifier.load_classifier.cache_clear()
    try:
        assert classifier.load_classifier() is None
    finally:
        classifier.load_classifier.cache_clear()


def test_classify_returns_the_softmax_argmax():
    model = classifier.Classifier.__new__(classifier.Classifier)
    model.labels = classifier.LABELS
    model.tokenizer = SimpleNamespace(
        encode=lambda text: SimpleNamespace(ids=[1, 2], attention_mask=[1, 1], type_ids=[0, 0])
    )
    logits = np.array([[0.0, 3.0, 0.0, 0.0, 0.0, 1.0]], dtype=np.float32)
    model.session = SimpleNamespace(run=lambda outputs, inputs: [logits])
    result = model.classify("need medicine near kurla")
    assert result["label"] == "medical"
    assert result["source"] == "muril"
    assert result["p"] == 0.749  # e^3 / (e^3 + e^1 + 4)


def test_real_model_if_trained():
    classifier.load_classifier.cache_clear()
    model = classifier.load_classifier()
    if model is None:
        return
    assert model.classify("we are stuck on the roof water rising send boat")["label"] in model.labels
