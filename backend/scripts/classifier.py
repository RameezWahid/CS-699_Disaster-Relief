"""Six-class report classifier: fine-tuned MuRIL exported to int8 ONNX (spec 4.2 step 3).

Inference needs only onnxruntime and tokenizers. Train with scripts/train_classifier.py; when
no model is present the worker falls back to the rule-based category.
"""

import json
import logging
from functools import cache

import numpy as np
import onnxruntime
from tokenizers import Tokenizer

from scripts.config import DATA_DIR

MODEL_DIR = DATA_DIR / "models/muril-flood"
LABELS = ["rescue", "medical", "food_water", "shelter", "road_blocked", "info_noise"]
# Reports are short: synthetic messages are at most ~25 MuRIL tokens and only ~2% of HumAID tweets
# exceed 64 (they keep their first 64 tokens). Training and inference use the same cap.
MAX_TOKENS = 64

log = logging.getLogger("classifier")


class Classifier:
    """Holds the ONNX session and tokenizer, loaded once per process."""

    def __init__(self) -> None:
        self.labels = json.loads((MODEL_DIR / "labels.json").read_text())
        self.tokenizer = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
        self.tokenizer.enable_truncation(MAX_TOKENS)
        self.session = onnxruntime.InferenceSession(
            str(MODEL_DIR / "model.onnx"), providers=["CPUExecutionProvider"]
        )

    def classify(self, text: str) -> dict:
        """Label and softmax probability for one normalized message."""
        encoding = self.tokenizer.encode(text)
        inputs = {
            "input_ids": np.array([encoding.ids], dtype=np.int64),
            "attention_mask": np.array([encoding.attention_mask], dtype=np.int64),
            "token_type_ids": np.array([encoding.type_ids], dtype=np.int64),
        }
        logits = self.session.run(None, inputs)[0][0]
        probabilities = np.exp(logits - logits.max())
        probabilities /= probabilities.sum()
        best = int(probabilities.argmax())
        return {
            "label": self.labels[best],
            "p": round(float(probabilities[best]), 3),
            "source": "muril",
        }


@cache
def load_classifier() -> Classifier | None:
    """The process-wide classifier, or None when no trained model is present."""
    if not (MODEL_DIR / "model.onnx").exists():
        log.warning("no classifier at %s; using rule-based categories", MODEL_DIR)
        return None
    log.info("loaded classifier from %s", MODEL_DIR)
    return Classifier()
