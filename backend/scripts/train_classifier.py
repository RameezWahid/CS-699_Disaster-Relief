"""Fine-tune google/muril-base-cased for the six report classes, then export int8 ONNX (spec 4.2).

Runs on a 4 GB GPU (GTX 1650) with fp16 autocast. Model selection uses data/training/val.csv
only; the hand-written gold set is never read here.

Resumable: a checkpoint is written every CHECKPOINT_EVERY steps and at each epoch boundary.
Re-running the same command after a stop or shutdown continues from the last checkpoint;
--fresh discards it and starts over.

Needs torch and transformers (training only; inference does not).

usage: python -m scripts.train_classifier [--fresh | --export-only]
"""

import csv
import json
import math
import random
import sys
import time

import numpy as np
import onnxruntime
import torch
from onnxruntime.quantization import QuantType, quantize_dynamic
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from scripts.classifier import LABELS, MAX_TOKENS, MODEL_DIR
from scripts.config import DATA_DIR
from scripts.metrics import per_class_report
from scripts.text import normalize

BASE_MODEL = "google/muril-base-cased"
TRAIN_FILE = DATA_DIR / "training/train.csv"
VAL_FILE = DATA_DIR / "training/val.csv"
SEED = 20261004
# Two epochs keep a full run near one hour on a GTX 1650 (three were projected at ~1.75 h).
# If validation macro-F1 in val_report.json looks short of target, raise this and use --fresh.
EPOCHS = 2
BATCH = 16
LEARNING_RATE = 3e-5
WARMUP_SHARE = 0.1
# About 6-7 minutes of GTX 1650 time; the most work a shutdown can lose.
CHECKPOINT_EVERY = 200
# The int8 model must reproduce PyTorch's predictions on at least this share of val messages.
MIN_EXPORT_AGREEMENT = 0.95
CHECKPOINT_FILE = MODEL_DIR / "checkpoint.pt"


def read(path) -> tuple[list[str], list[int]]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [normalize(r["text"]) for r in rows], [LABELS.index(r["label"]) for r in rows]


def predict(model, tokenizer, texts: list[str], device) -> list[int]:
    model.eval()
    predictions = []
    with torch.no_grad():
        for start in range(0, len(texts), 64):
            batch = tokenizer(
                texts[start : start + 64],
                truncation=True,
                max_length=MAX_TOKENS,
                padding=True,
                return_tensors="pt",
            ).to(device)
            with torch.autocast(device.type, dtype=torch.float16, enabled=device.type == "cuda"):
                logits = model(**batch).logits
            predictions += logits.argmax(-1).tolist()
    return predictions


def save_checkpoint(
    model, optimizer, scheduler, scaler, epoch: int, next_step: int, order: list[int], best: float
) -> None:
    """Everything needed to continue exactly where training stopped, written atomically."""
    state = {
        # The frozen word embeddings never change; they are reloaded from the base model.
        "model": {k: v for k, v in model.state_dict().items() if "word_embeddings" not in k},
        "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict(),
        "scaler": scaler.state_dict(),
        "epoch": epoch,
        "next_step": next_step,
        "order": order,
        "best": best,
        "python_rng": random.getstate(),
        "torch_rng": torch.get_rng_state(),
        "cuda_rng": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    partial = CHECKPOINT_FILE.with_suffix(".partial")
    torch.save(state, partial)
    partial.replace(CHECKPOINT_FILE)  # a shutdown mid-save leaves the old checkpoint intact


def train(fresh: bool) -> None:
    random.seed(SEED)
    torch.manual_seed(SEED)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(
        BASE_MODEL,
        num_labels=len(LABELS),
        id2label=dict(enumerate(LABELS)),
        label2id={label: i for i, label in enumerate(LABELS)},
    ).to(device)

    # The word-embedding table is 151M of MuRIL's 237M parameters (197K-token vocabulary). Freezing
    # it halves training memory so it fits a 4 GB GPU, and 18K examples are too few to improve
    # the pretrained multilingual embeddings anyway.
    model.base_model.embeddings.word_embeddings.weight.requires_grad = False
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(
        f"training {trainable / 1e6:.0f}M of "
        f"{sum(p.numel() for p in model.parameters()) / 1e6:.0f}M parameters",
        flush=True,
    )

    train_texts, train_labels = read(TRAIN_FILE)
    val_texts, val_labels = read(VAL_FILE)
    counts = np.bincount(train_labels, minlength=len(LABELS))
    # Inverse square-root frequency: lifts rare classes (rescue) without swamping common ones.
    weights = torch.tensor((counts.max() / counts) ** 0.5, dtype=torch.float32, device=device)
    loss_fn = torch.nn.CrossEntropyLoss(weight=weights)

    steps = EPOCHS * math.ceil(len(train_texts) / BATCH)
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=LEARNING_RATE
    )
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer,
        lambda s: min(
            (s + 1) / (WARMUP_SHARE * steps), max(0.0, (steps - s) / (steps * (1 - WARMUP_SHARE)))
        ),
    )
    scaler = torch.amp.GradScaler(enabled=device.type == "cuda")
    order = list(range(len(train_texts)))
    best, start_epoch, start_step = -1.0, 0, 0
    if fresh:
        CHECKPOINT_FILE.unlink(missing_ok=True)
    if CHECKPOINT_FILE.exists():
        state = torch.load(CHECKPOINT_FILE, weights_only=False)
        model.load_state_dict(state["model"], strict=False)
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        scaler.load_state_dict(state["scaler"])
        random.setstate(state["python_rng"])
        torch.set_rng_state(state["torch_rng"])
        if state["cuda_rng"]:
            torch.cuda.set_rng_state_all(state["cuda_rng"])
        best, start_epoch, start_step = state["best"], state["epoch"], state["next_step"]
        order = state["order"]
        print(f"resuming at epoch {start_epoch + 1} step {start_step}", flush=True)
    for epoch in range(start_epoch, EPOCHS):
        model.train()
        resuming = epoch == start_epoch and start_step > 0
        if not resuming:
            random.shuffle(order)
        started, running, done = time.time(), 0.0, 0
        for step, start in enumerate(range(0, len(order), BATCH)):
            if resuming and step < start_step:
                continue  # already trained before the checkpoint
            ids = order[start : start + BATCH]
            batch = tokenizer(
                [train_texts[i] for i in ids],
                truncation=True,
                max_length=MAX_TOKENS,
                padding=True,
                return_tensors="pt",
            ).to(device)
            labels = torch.tensor([train_labels[i] for i in ids], device=device)
            with torch.autocast(device.type, dtype=torch.float16, enabled=device.type == "cuda"):
                loss = loss_fn(model(**batch).logits.float(), labels)
            optimizer.zero_grad()
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()
            scheduler.step()
            running, done = running + loss.item(), done + 1
            if (step + 1) % CHECKPOINT_EVERY == 0:
                save_checkpoint(model, optimizer, scheduler, scaler, epoch, step + 1, order, best)
                print(
                    f"epoch {epoch + 1} step {step + 1} loss {running / done:.3f} "
                    f"(checkpoint saved)",
                    flush=True,
                )
        report = per_class_report(
            LABELS,
            [LABELS[i] for i in val_labels],
            [LABELS[i] for i in predict(model, tokenizer, val_texts, device)],
        )
        print(
            f"epoch {epoch + 1} done in {time.time() - started:.0f}s; val {json.dumps(report)}",
            flush=True,
        )
        if report["macro_f1"] > best:
            best = report["macro_f1"]
            model.save_pretrained(MODEL_DIR / "hf")
            tokenizer.save_pretrained(MODEL_DIR / "hf")
            (MODEL_DIR / "val_report.json").write_text(json.dumps(report, indent=1))
        save_checkpoint(model, optimizer, scheduler, scaler, epoch + 1, 0, order, best)
    print(f"best val macro-F1 {best}")


def export() -> None:
    """Export the best checkpoint to ONNX, quantize to int8, and check it agrees with PyTorch."""
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_DIR / "hf").eval()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR / "hf")
    sample = tokenizer(["Hindmata pe 5 log phase hain"], return_tensors="pt")
    fp32 = MODEL_DIR / "model_fp32.onnx"
    torch.onnx.export(
        model,
        (sample["input_ids"], sample["attention_mask"], sample["token_type_ids"]),
        fp32,
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["logits"],
        dynamic_axes={
            name: {0: "batch", 1: "tokens"}
            for name in ("input_ids", "attention_mask", "token_type_ids")
        },
        opset_version=17,
        dynamo=False,
    )
    # Per-channel scales are essential: with one scale per weight tensor, MuRIL's outlier weights
    # crush the rest and validation accuracy fell from 0.97 to 0.46. Per-channel keeps 0.96.
    quantize_dynamic(fp32, MODEL_DIR / "model.onnx", weight_type=QuantType.QInt8, per_channel=True)
    fp32.unlink()
    tokenizer.backend_tokenizer.save(str(MODEL_DIR / "tokenizer.json"))
    (MODEL_DIR / "labels.json").write_text(json.dumps(LABELS))

    val_texts, _ = read(VAL_FILE)
    texts = val_texts[:300]
    session = onnxruntime.InferenceSession(str(MODEL_DIR / "model.onnx"))
    encoded = tokenizer(
        texts, truncation=True, max_length=MAX_TOKENS, padding=True, return_tensors="np"
    )
    onnx_pred = session.run(
        None,
        {k: encoded[k].astype(np.int64) for k in ("input_ids", "attention_mask", "token_type_ids")},
    )[0].argmax(-1)
    torch_pred = predict(model, tokenizer, texts, torch.device("cpu"))
    agreement = float(np.mean(onnx_pred == np.array(torch_pred)))
    print(f"int8 ONNX agrees with PyTorch on {agreement:.1%} of {len(texts)} val messages")
    if agreement < MIN_EXPORT_AGREEMENT:
        raise SystemExit(
            f"quantized model disagrees with PyTorch too often ({agreement:.1%}); " "not using it"
        )


if __name__ == "__main__":
    if "--export-only" not in sys.argv:
        train(fresh="--fresh" in sys.argv)
    export()
    CHECKPOINT_FILE.unlink(missing_ok=True)  # finished; the next run starts fresh
