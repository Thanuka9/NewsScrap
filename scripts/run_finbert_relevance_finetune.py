from __future__ import annotations

"""Experimental document-level 3-class ProsusAI/finbert fine-tuning.

Important guardrails
--------------------
* Training reads ONLY train.csv and validation.csv.
* test.csv is read ONLY when --evaluate-test is explicitly supplied, after a
  checkpoint has already been selected.
* Labels are AI-assisted provisional, not independently human-gold.
* The September test set has already been viewed in earlier development and is
  therefore a developmental comparison only, not an untouched promotion holdout.
* --scheduler-epochs decouples the LR schedule horizon from the number of epochs
  actually executed. This makes controlled duration experiments possible.

The script deliberately records dataset hashes, preprocessing settings, LR
schedule settings, package versions, class weights, per-epoch metrics, and an
optimizer-step LR trace so runs can be compared/audited later.
"""

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import random
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v3_reviewed"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "relevance_finetune"
    / "supervisory_relevance_v3_reviewed"
)

LABELS = ("NOISE", "WATCH", "RELEVANT")
LABEL2ID = {name: i for i, name in enumerate(LABELS)}
ID2LABEL = {i: name for name, i in LABEL2ID.items()}


# ---------------------------------------------------------------------------
# Data / audit helpers
# ---------------------------------------------------------------------------

def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def digest_rows(rows: list[dict[str, str]]) -> str:
    """Hash model-relevant row content and order to bind a run to its data."""
    digest = hashlib.sha256()
    fields = (
        "source_url",
        "story_group_id",
        "target_relevance_label",
        "headline",
        "article_text",
        "published_at",
    )
    for row in rows:
        for name in fields:
            digest.update(str(row.get(name, "")).encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()


def validate_splits(train: list[dict], validation: list[dict]) -> dict[str, Any]:
    required = {
        "source_url",
        "story_group_id",
        "target_relevance_label",
        "headline",
        "article_text",
        "published_at",
        "label_source",
    }

    if not train or not validation:
        raise ValueError("Training and validation must both be nonempty")

    for split_name, rows in (("train", train), ("validation", validation)):
        for index, row in enumerate(rows, 1):
            missing = required - set(row)
            if missing:
                raise ValueError(
                    f"{split_name} row {index}: missing {sorted(missing)}"
                )

            label = (row.get("target_relevance_label") or "").strip()
            if label not in LABEL2ID:
                raise ValueError(
                    f"{split_name} row {index}: invalid relevance label {label!r}"
                )

            if not (row.get("source_url") or "").strip():
                raise ValueError(f"{split_name} row {index}: source_url is blank")

            if not (row.get("story_group_id") or "").strip():
                raise ValueError(f"{split_name} row {index}: story_group_id is blank")

            if not (
                (row.get("headline") or "").strip()
                or (row.get("article_text") or "").strip()
            ):
                raise ValueError(f"{split_name} row {index}: empty article")

    train_groups = {r["story_group_id"] for r in train}
    val_groups = {r["story_group_id"] for r in validation}
    overlap = train_groups & val_groups
    if overlap:
        raise ValueError(f"Story-group leakage: {sorted(overlap)[:10]}")

    train_urls = {r["source_url"] for r in train}
    val_urls = {r["source_url"] for r in validation}

    if len(train_urls) != len(train):
        raise ValueError("Duplicate source_url in training split")
    if len(val_urls) != len(validation):
        raise ValueError("Duplicate source_url in validation split")
    if train_urls & val_urls:
        raise ValueError("Same URL appears in train and validation")

    for label in LABELS:
        if not any(r["target_relevance_label"] == label for r in train):
            raise ValueError(f"Training has no {label} examples")
        if not any(r["target_relevance_label"] == label for r in validation):
            raise ValueError(f"Validation has no {label} examples")

    return {
        "train_rows": len(train),
        "validation_rows": len(validation),
        "train_counts": dict(
            Counter(r["target_relevance_label"] for r in train)
        ),
        "validation_counts": dict(
            Counter(r["target_relevance_label"] for r in validation)
        ),
        "train_sha256": digest_rows(train),
        "validation_sha256": digest_rows(validation),
        "train_val_group_overlap": len(overlap),
        "provenance": dict(
            Counter(
                (r.get("label_source") or "UNSPECIFIED")
                for r in train + validation
            )
        ),
    }


def validate_test_against_development(
    train: list[dict],
    validation: list[dict],
    test: list[dict],
) -> None:
    if not test:
        raise RuntimeError("Empty test set")

    required = {
        "source_url",
        "story_group_id",
        "target_relevance_label",
        "headline",
        "article_text",
        "published_at",
    }

    for index, row in enumerate(test, 1):
        missing = required - set(row)
        if missing:
            raise RuntimeError(f"test row {index}: missing {sorted(missing)}")
        if row["target_relevance_label"] not in LABEL2ID:
            raise RuntimeError(
                f"test row {index}: invalid test label "
                f"{row['target_relevance_label']!r}"
            )
        if not row["source_url"] or not row["story_group_id"]:
            raise RuntimeError(f"test row {index}: blank URL/story group")

    test_urls = {r["source_url"] for r in test}
    if len(test_urls) != len(test):
        raise RuntimeError("Duplicate source_url in test split")

    development_groups = {r["story_group_id"] for r in train + validation}
    test_groups = {r["story_group_id"] for r in test}
    if development_groups & test_groups:
        raise RuntimeError("Test story-group leakage")

    development_urls = {r["source_url"] for r in train + validation}
    if development_urls & test_urls:
        raise RuntimeError("Test URL leakage")


# ---------------------------------------------------------------------------
# Text windowing / document aggregation
# ---------------------------------------------------------------------------

def spaced_indices(total: int, maximum: int) -> list[int]:
    """Keep windows from beginning, middle and end, not just first N."""
    if maximum < 1:
        raise ValueError("max-body-chunks must be >= 1")
    if total <= maximum:
        return list(range(total))
    if maximum == 1:
        return [0]
    return sorted(
        {
            round(i * (total - 1) / (maximum - 1))
            for i in range(maximum)
        }
    )


def encode_windows(
    tokenizer,
    text: str,
    *,
    max_length: int,
    stride: int,
    maximum: int,
) -> list[dict]:
    if not text.strip():
        return []

    payload = tokenizer(
        text,
        max_length=max_length,
        truncation=True,
        stride=stride,
        return_overflowing_tokens=True,
        padding=False,
    )
    payload.pop("overflow_to_sample_mapping", None)

    count = len(payload["input_ids"])
    windows: list[dict] = []

    for index in spaced_indices(count, maximum):
        item = {key: value[index] for key, value in payload.items()}
        item["token_weight"] = float(sum(item["attention_mask"]))
        windows.append(item)

    return windows


def encode_article(
    tokenizer,
    row: dict,
    *,
    max_length: int,
    stride: int,
    max_body_chunks: int,
) -> dict:
    return {
        "headline": encode_windows(
            tokenizer,
            row["headline"],
            max_length=max_length,
            stride=stride,
            maximum=1,
        ),
        "body": encode_windows(
            tokenizer,
            row["article_text"],
            max_length=max_length,
            stride=stride,
            maximum=max_body_chunks,
        ),
        "label": row["target_relevance_label"],
        "row": row,
    }


def model_article_logits(
    model,
    item: dict,
    device,
    torch,
    *,
    headline_weight: float,
    body_weight: float,
):
    """Differentiable document-level logit aggregation, token-weighted per side."""
    sides = []

    for side, side_weight in (
        ("headline", headline_weight),
        ("body", body_weight),
    ):
        windows = item[side]
        if not windows:
            continue

        logits = []
        weights = []

        for window in windows:
            features = {
                key: torch.tensor([value], dtype=torch.long, device=device)
                for key, value in window.items()
                if key != "token_weight"
            }
            logits.append(model(**features).logits.squeeze(0))
            weights.append(window["token_weight"])

        token_weights = torch.tensor(
            weights,
            device=device,
            dtype=logits[0].dtype,
        )
        normalized = token_weights / token_weights.sum()
        side_logits = torch.stack(logits, dim=0)
        pooled = (side_logits * normalized[:, None]).sum(dim=0)
        sides.append((pooled, side_weight))

    if not sides:
        raise ValueError("Document has no usable text")

    denominator = sum(weight for _, weight in sides)
    return sum(value * (weight / denominator) for value, weight in sides)


# ---------------------------------------------------------------------------
# Metrics / exports
# ---------------------------------------------------------------------------

def macro_metrics(y_true: list[str], y_pred: list[str]) -> dict[str, Any]:
    from sklearn.metrics import (
        accuracy_score,
        balanced_accuracy_score,
        classification_report,
        confusion_matrix,
        f1_score,
    )

    report = classification_report(
        y_true,
        y_pred,
        labels=list(LABELS),
        output_dict=True,
        zero_division=0,
    )

    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "macro_f1": float(
            f1_score(
                y_true,
                y_pred,
                labels=list(LABELS),
                average="macro",
                zero_division=0,
            )
        ),
        "relevant_f1": float(report["RELEVANT"]["f1-score"]),
        "relevant_precision": float(report["RELEVANT"]["precision"]),
        "relevant_recall": float(report["RELEVANT"]["recall"]),
        "watch_f1": float(report["WATCH"]["f1-score"]),
        "noise_f1": float(report["NOISE"]["f1-score"]),
        "per_class": {key: report[key] for key in LABELS},
        "confusion_matrix": confusion_matrix(
            y_true,
            y_pred,
            labels=list(LABELS),
        ).tolist(),
        "label_order": list(LABELS),
    }


def evaluate(
    model,
    items: list[dict],
    device,
    torch,
    headline_weight: float,
    body_weight: float,
) -> tuple[dict, list[dict]]:
    model.eval()
    predictions: list[str] = []
    truth: list[str] = []
    export: list[dict] = []

    with torch.inference_mode():
        for item in items:
            logits = model_article_logits(
                model,
                item,
                device,
                torch,
                headline_weight=headline_weight,
                body_weight=body_weight,
            )
            probabilities = torch.softmax(logits, dim=-1).cpu().tolist()
            predicted_id = int(
                max(range(len(LABELS)), key=lambda i: probabilities[i])
            )
            label = LABELS[predicted_id]

            predictions.append(label)
            truth.append(item["label"])

            row = item["row"]
            export.append(
                {
                    "published_at": row["published_at"],
                    "headline": row["headline"],
                    "source_url": row["source_url"],
                    "story_group_id": row["story_group_id"],
                    "true_label": item["label"],
                    "predicted_label": label,
                    "p_noise": probabilities[0],
                    "p_watch": probabilities[1],
                    "p_relevant": probabilities[2],
                    "correct": item["label"] == label,
                }
            )

    return macro_metrics(truth, predictions), export


def write_predictions(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "published_at",
                "headline",
                "source_url",
                "story_group_id",
                "true_label",
                "predicted_label",
                "p_noise",
                "p_watch",
                "p_relevant",
                "correct",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_history_csv(path: Path, history: list[dict]) -> None:
    fields = [
        "epoch",
        "train_mean_loss",
        "validation_accuracy",
        "validation_balanced_accuracy",
        "validation_macro_f1",
        "validation_noise_f1",
        "validation_watch_f1",
        "validation_relevant_f1",
        "validation_relevant_precision",
        "validation_relevant_recall",
        "optimizer_steps_completed",
        "lr_at_epoch_end",
        "is_best_epoch",
    ]

    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()

        for row in history:
            metrics = row["validation"]
            writer.writerow(
                {
                    "epoch": row["epoch"],
                    "train_mean_loss": row["train_mean_loss"],
                    "validation_accuracy": metrics["accuracy"],
                    "validation_balanced_accuracy": metrics[
                        "balanced_accuracy"
                    ],
                    "validation_macro_f1": metrics["macro_f1"],
                    "validation_noise_f1": metrics["noise_f1"],
                    "validation_watch_f1": metrics["watch_f1"],
                    "validation_relevant_f1": metrics["relevant_f1"],
                    "validation_relevant_precision": metrics[
                        "relevant_precision"
                    ],
                    "validation_relevant_recall": metrics[
                        "relevant_recall"
                    ],
                    "optimizer_steps_completed": row[
                        "optimizer_steps_completed"
                    ],
                    "lr_at_epoch_end": row["lr_at_epoch_end"],
                    "is_best_epoch": row.get("is_best_epoch", False),
                }
            )


def write_lr_trace(path: Path, rows: list[dict]) -> None:
    fields = [
        "optimizer_step",
        "epoch",
        "article_position",
        "learning_rate_before_step",
        "learning_rate_after_scheduler_step",
    ]

    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Environment / model helpers
# ---------------------------------------------------------------------------

def load_settings() -> dict:
    """Read the same FinBERT config used by the existing FinBertEngine."""
    import yaml

    path = PROJECT_ROOT / "config" / "intelligence" / "ml.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"FinBERT settings file not found: {path}")

    payload = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or "finbert" not in payload:
        raise RuntimeError(f"Missing 'finbert' section in {path}")
    return payload["finbert"]


def load_dependencies():
    try:
        import numpy as np
        import torch
        from transformers import (
            AutoConfig,
            AutoModelForSequenceClassification,
            AutoTokenizer,
            get_linear_schedule_with_warmup,
            set_seed,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Missing ML dependencies; install requirements-ml.txt, "
            "transformers, torch, numpy and scikit-learn"
        ) from exc

    return (
        np,
        torch,
        AutoConfig,
        AutoModelForSequenceClassification,
        AutoTokenizer,
        get_linear_schedule_with_warmup,
        set_seed,
    )


def resolve_device(torch, choice: str):
    if choice != "auto":
        device = torch.device(choice)
        if device.type == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable")
        if (
            device.type == "mps"
            and not (
                hasattr(torch.backends, "mps")
                and torch.backends.mps.is_available()
            )
        ):
            raise RuntimeError("MPS requested but unavailable")
        return device

    if torch.cuda.is_available():
        return torch.device("cuda")

    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")

    return torch.device("cpu")


def configure_reproducibility(torch, np, set_seed, seed: int) -> dict[str, Any]:
    """Best-effort deterministic setup; record exactly what was requested."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    set_seed(seed)

    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

    deterministic_algorithms_requested = False
    deterministic_algorithms_enabled = False

    try:
        torch.use_deterministic_algorithms(True, warn_only=True)
        deterministic_algorithms_requested = True
        deterministic_algorithms_enabled = bool(
            torch.are_deterministic_algorithms_enabled()
        )
    except (AttributeError, TypeError):
        pass

    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False

    return {
        "seed": seed,
        "pythonhashseed_at_runtime": os.environ.get("PYTHONHASHSEED"),
        "deterministic_algorithms_requested": (
            deterministic_algorithms_requested
        ),
        "deterministic_algorithms_enabled": (
            deterministic_algorithms_enabled
        ),
        "cudnn_deterministic": bool(
            getattr(getattr(torch.backends, "cudnn", object()), "deterministic", False)
        ),
        "cudnn_benchmark": bool(
            getattr(getattr(torch.backends, "cudnn", object()), "benchmark", False)
        ),
    }


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def environment_manifest(torch) -> dict[str, Any]:
    return {
        "python_version": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "torch_version": getattr(torch, "__version__", None),
        "transformers_version": package_version("transformers"),
        "numpy_version": package_version("numpy"),
        "scikit_learn_version": package_version("scikit-learn"),
        "pyyaml_version": package_version("PyYAML"),
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_version": getattr(getattr(torch, "version", None), "cuda", None),
        "mps_available": bool(
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ),
    }


def dataset_path(base: Path, name: str) -> Path:
    path = base / name
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def read_dataset_manifest(dataset_root: Path) -> dict[str, Any] | None:
    path = dataset_root / "manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid dataset manifest JSON: {path}") from exc


def output_has_training_artifacts(output_root: Path) -> bool:
    """Refuse to mix a new experiment with any existing output contents."""
    if not output_root.exists():
        return False
    if not output_root.is_dir():
        return True
    return any(output_root.iterdir())


def reset_classifier_head(model, cfg, torch) -> None:
    """Reset the original FinBERT sentiment head for the relevance task."""
    classifier = getattr(model, "classifier", None)
    if not isinstance(classifier, torch.nn.Linear):
        raise RuntimeError(
            "Expected BERT Linear classifier head; inspect model before continuing"
        )

    torch.nn.init.normal_(
        classifier.weight,
        mean=0.0,
        std=cfg.initializer_range,
    )
    if classifier.bias is not None:
        torch.nn.init.zeros_(classifier.bias)


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fine-tune ProsusAI/finbert for provisional 3-class "
            "Sri Lanka banking-supervision relevance"
        )
    )

    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)

    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Check train/validation only; does not import FinBERT or read test.csv",
    )
    parser.add_argument(
        "--evaluate-test",
        action="store_true",
        help=(
            "Explicitly evaluate the already-selected checkpoint on test.csv. "
            "Do not use this during tuning."
        ),
    )

    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument(
        "--scheduler-epochs",
        type=int,
        default=None,
        help=(
            "Epoch horizon used ONLY to calculate the linear LR schedule. "
            "Defaults to --epochs. For controlled duration experiments, keep "
            "this fixed across runs (for example, 5) while changing --epochs."
        ),
    )
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument(
        "--warmup-ratio",
        type=float,
        default=0.10,
        help="Fraction of scheduler steps used for LR warmup (default: 0.10)",
    )
    parser.add_argument("--max-body-chunks", type=int, default=3)
    parser.add_argument("--gradient-accumulation", type=int, default=4)
    parser.add_argument("--max-grad-norm", type=float, default=1.0)
    parser.add_argument("--patience", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda", "mps"),
        default="auto",
    )

    args = parser.parse_args()

    if args.validate_only and args.evaluate_test:
        parser.error("--validate-only cannot be combined with --evaluate-test")

    if args.epochs < 1:
        parser.error("--epochs must be >= 1")
    if args.scheduler_epochs is not None and args.scheduler_epochs < 1:
        parser.error("--scheduler-epochs must be >= 1")
    if args.gradient_accumulation < 1:
        parser.error("--gradient-accumulation must be >= 1")
    if args.patience < 1:
        parser.error("--patience must be >= 1")
    if args.learning_rate <= 0:
        parser.error("--learning-rate must be > 0")
    if args.weight_decay < 0:
        parser.error("--weight-decay must be >= 0")
    if not 0 <= args.warmup_ratio < 1:
        parser.error("--warmup-ratio must satisfy 0 <= ratio < 1")
    if args.max_body_chunks < 1:
        parser.error("--max-body-chunks must be >= 1")
    if args.max_grad_norm <= 0:
        parser.error("--max-grad-norm must be > 0")

    scheduler_epochs = (
        args.scheduler_epochs
        if args.scheduler_epochs is not None
        else args.epochs
    )

    if scheduler_epochs < args.epochs:
        parser.error(
            "--scheduler-epochs cannot be smaller than --epochs. "
            "Otherwise the LR schedule would reach zero before training ends."
        )

    args.scheduler_epochs = scheduler_epochs
    return args


def main() -> None:
    args = parse_args()

    dataset_root = args.dataset_root.resolve()
    output_root = args.output_root.resolve()

    # ------------------------------------------------------------------
    # TRAIN / VALIDATION AUDIT. Test is intentionally not touched here.
    # ------------------------------------------------------------------
    if not args.evaluate_test:
        train = read_csv(dataset_path(dataset_root, "train.csv"))
        validation = read_csv(dataset_path(dataset_root, "validation.csv"))
        audit = validate_splits(train, validation)

        print("DATA AUDIT", json.dumps(audit, indent=2))

        if args.validate_only:
            return

        if output_has_training_artifacts(output_root):
            raise RuntimeError(
                "Output already contains training artifacts. Choose a NEW "
                "--output-root so a previous experiment is never overwritten. "
                f"Existing output: {output_root}"
            )

    # ------------------------------------------------------------------
    # EXPLICIT TEST EVALUATION. Only reached with --evaluate-test.
    # ------------------------------------------------------------------
    else:
        run_path = output_root / "run_manifest.json"
        if not run_path.exists():
            raise FileNotFoundError(
                "Fine-tuning manifest not found: train/select a checkpoint first"
            )

        manifest = json.loads(run_path.read_text(encoding="utf-8"))

        if manifest.get("dataset_root") != str(dataset_root):
            raise RuntimeError("Dataset root does not match the selected checkpoint")

        train = read_csv(dataset_path(dataset_root, "train.csv"))
        validation = read_csv(dataset_path(dataset_root, "validation.csv"))
        audit = validate_splits(train, validation)

        manifest_audit = manifest.get("audit") or {}
        if (
            audit["train_sha256"] != manifest_audit.get("train_sha256")
            or audit["validation_sha256"]
            != manifest_audit.get("validation_sha256")
        ):
            raise RuntimeError(
                "Train/validation content changed since checkpoint selection"
            )

        test = read_csv(dataset_path(dataset_root, "test.csv"))
        validate_test_against_development(train, validation, test)

        if (output_root / "test_metrics.json").exists():
            raise RuntimeError(
                "Test already evaluated for this run. Do not repeatedly tune on test."
            )

    # Import heavy ML stack only after data/guardrail checks.
    (
        np,
        torch,
        AutoConfig,
        AutoModelForSequenceClassification,
        AutoTokenizer,
        get_linear_schedule_with_warmup,
        set_seed,
    ) = load_dependencies()

    from torch.nn import functional as F

    settings = load_settings()
    model_name = settings["model_name"]
    max_length = int(settings.get("max_length", 512))
    stride = int(settings.get("stride", 96))
    headline_weight = float(settings.get("headline_weight", 0.35))
    body_weight = float(settings.get("body_weight", 0.65))

    total_weight = headline_weight + body_weight
    if total_weight <= 0:
        raise RuntimeError("Headline/body weights must sum to a positive value")
    if max_length < 2:
        raise RuntimeError("max_length must be >= 2")
    if not 0 <= stride < max_length:
        raise RuntimeError("stride must satisfy 0 <= stride < max_length")

    headline_weight /= total_weight
    body_weight /= total_weight

    reproducibility = configure_reproducibility(
        torch,
        np,
        set_seed,
        args.seed,
    )

    device = resolve_device(torch, args.device)

    print(
        f"DEVICE: {device}; model: {model_name}; "
        f"body windows/article <= {args.max_body_chunks}"
    )

    if device.type == "cpu":
        print("WARNING: Fine-tuning on CPU may take a long time. CUDA is recommended.")
    if device.type == "mps":
        print(
            "WARNING: MPS can have different numerical/determinism behaviour than CPU/CUDA."
        )

    # ------------------------------------------------------------------
    # Explicit test evaluation mode
    # ------------------------------------------------------------------
    if args.evaluate_test:
        recorded = manifest

        expected_fields = {
            "model_name": model_name,
            "max_length": max_length,
            "stride": stride,
            "max_body_chunks": args.max_body_chunks,
            "headline_weight": headline_weight,
            "body_weight": body_weight,
        }

        mismatches = {
            key: {"recorded": recorded.get(key), "requested": value}
            for key, value in expected_fields.items()
            if recorded.get(key) != value
        }
        if mismatches:
            raise RuntimeError(
                "Inference preprocessing does not match checkpoint: "
                + json.dumps(mismatches, indent=2)
            )

        checkpoint = output_root / "best_model"
        if not (checkpoint / "config.json").is_file():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint}")

        tokenizer = AutoTokenizer.from_pretrained(str(checkpoint))
        model = AutoModelForSequenceClassification.from_pretrained(
            str(checkpoint),
            use_safetensors=True,
        )
        model.to(device)

        encoded_test = [
            encode_article(
                tokenizer,
                row,
                max_length=max_length,
                stride=stride,
                max_body_chunks=args.max_body_chunks,
            )
            for row in test
        ]

        metrics, predictions = evaluate(
            model,
            encoded_test,
            device,
            torch,
            headline_weight,
            body_weight,
        )

        output_root.mkdir(parents=True, exist_ok=True)

        test_payload = {
            "metrics": metrics,
            "test_rows": len(test),
            "test_sha256": digest_rows(test),
            "evaluated_utc": datetime.now(timezone.utc).isoformat(),
            "warning": (
                "Previously viewed, AI-assisted provisional September test: "
                "developmental comparison only, NOT an independent gold holdout"
            ),
        }

        (output_root / "test_metrics.json").write_text(
            json.dumps(test_payload, indent=2),
            encoding="utf-8",
        )
        write_predictions(output_root / "test_predictions.csv", predictions)

        print(
            "TEST macro-F1:",
            round(metrics["macro_f1"], 4),
            "RELEVANT F1:",
            round(metrics["relevant_f1"], 4),
        )
        print("Test metrics:", output_root / "test_metrics.json")
        return

    # ------------------------------------------------------------------
    # Training mode
    # ------------------------------------------------------------------
    tokenizer = AutoTokenizer.from_pretrained(model_name)

    cfg = AutoConfig.from_pretrained(model_name)
    cfg.num_labels = len(LABELS)
    cfg.id2label = ID2LABEL
    cfg.label2id = LABEL2ID
    cfg.problem_type = "single_label_classification"

    model = AutoModelForSequenceClassification.from_pretrained(
        model_name,
        config=cfg,
        use_safetensors=True,
    )

    # FinBERT's original sentiment head is also 3-way, but the semantics are
    # unrelated to NOISE/WATCH/RELEVANT. Same tensor shape != same task.
    reset_classifier_head(model, cfg, torch)
    model.to(device)

    print("FinBERT sentiment head RESET for supervisory relevance classification")

    train_items = [
        encode_article(
            tokenizer,
            row,
            max_length=max_length,
            stride=stride,
            max_body_chunks=args.max_body_chunks,
        )
        for row in train
    ]
    validation_items = [
        encode_article(
            tokenizer,
            row,
            max_length=max_length,
            stride=stride,
            max_body_chunks=args.max_body_chunks,
        )
        for row in validation
    ]

    counts = Counter(r["target_relevance_label"] for r in train)
    class_weight_values = {
        label: len(train) / (len(LABELS) * counts[label])
        for label in LABELS
    }
    class_weights = torch.tensor(
        [class_weight_values[label] for label in LABELS],
        dtype=torch.float32,
        device=device,
    )

    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=args.learning_rate,
        weight_decay=args.weight_decay,
    )

    steps_per_epoch = math.ceil(
        len(train_items) / args.gradient_accumulation
    )
    scheduler_total_steps = steps_per_epoch * args.scheduler_epochs
    warmup_steps = int(scheduler_total_steps * args.warmup_ratio)

    # Preserve the historical behaviour of having at least one warmup step
    # when warmup_ratio > 0, while allowing true zero warmup when requested.
    if args.warmup_ratio > 0:
        warmup_steps = max(1, warmup_steps)

    if warmup_steps >= scheduler_total_steps:
        raise RuntimeError(
            "Warmup steps must be smaller than total scheduler steps"
        )

    schedule = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=warmup_steps,
        num_training_steps=scheduler_total_steps,
    )

    output_root.mkdir(parents=True, exist_ok=True)

    print(
        "SCHEDULER:",
        json.dumps(
            {
                "training_epochs_requested": args.epochs,
                "scheduler_epochs": args.scheduler_epochs,
                "steps_per_epoch": steps_per_epoch,
                "scheduler_total_steps": scheduler_total_steps,
                "warmup_ratio": args.warmup_ratio,
                "warmup_steps": warmup_steps,
                "initial_learning_rate": args.learning_rate,
            },
            indent=2,
        ),
    )
    print("CLASS WEIGHTS:", json.dumps(class_weight_values, indent=2))

    history: list[dict] = []
    lr_trace: list[dict] = []
    best_score = (-1.0, -1.0)
    best_epoch: int | None = None
    stale = 0
    optimizer_steps_completed = 0

    for epoch in range(1, args.epochs + 1):
        model.train()

        order = list(range(len(train_items)))
        # Independent deterministic article ordering for each epoch.
        random.Random(args.seed + epoch).shuffle(order)

        epoch_loss = 0.0
        optimizer.zero_grad(set_to_none=True)

        for position, index in enumerate(order, 1):
            item = train_items[index]

            logits = model_article_logits(
                model,
                item,
                device,
                torch,
                headline_weight=headline_weight,
                body_weight=body_weight,
            )

            label_id = torch.tensor(
                [LABEL2ID[item["label"]]],
                device=device,
                dtype=torch.long,
            )

            # There is one article per loss call. With reduction='mean', PyTorch
            # would normalize away that single example's class weight. 'sum'
            # preserves the intended balanced class weighting.
            loss = F.cross_entropy(
                logits.unsqueeze(0),
                label_id,
                weight=class_weights,
                reduction="sum",
            )

            # Scale the final partial gradient-accumulation group correctly.
            group_index = (position - 1) // args.gradient_accumulation
            group_size = min(
                args.gradient_accumulation,
                len(order) - group_index * args.gradient_accumulation,
            )

            (loss / group_size).backward()
            epoch_loss += float(loss.detach().cpu())

            should_step = (
                position % args.gradient_accumulation == 0
                or position == len(order)
            )

            if should_step:
                lr_before = float(optimizer.param_groups[0]["lr"])

                torch.nn.utils.clip_grad_norm_(
                    model.parameters(),
                    args.max_grad_norm,
                )
                optimizer.step()
                schedule.step()
                optimizer.zero_grad(set_to_none=True)

                optimizer_steps_completed += 1
                lr_after = float(optimizer.param_groups[0]["lr"])

                lr_trace.append(
                    {
                        "optimizer_step": optimizer_steps_completed,
                        "epoch": epoch,
                        "article_position": position,
                        "learning_rate_before_step": lr_before,
                        "learning_rate_after_scheduler_step": lr_after,
                    }
                )

            if position % 20 == 0 or position == len(order):
                print(
                    f"epoch {epoch}/{args.epochs} "
                    f"article {position}/{len(order)}",
                    flush=True,
                )

        metrics, predictions = evaluate(
            model,
            validation_items,
            device,
            torch,
            headline_weight,
            body_weight,
        )

        score = (metrics["macro_f1"], metrics["relevant_f1"])
        improved = score > best_score

        row = {
            "epoch": epoch,
            "train_mean_loss": epoch_loss / len(order),
            "validation": metrics,
            "optimizer_steps_completed": optimizer_steps_completed,
            "lr_at_epoch_end": float(optimizer.param_groups[0]["lr"]),
            "is_best_epoch": bool(improved),
        }
        history.append(row)

        print(
            f"EPOCH {epoch}: "
            f"loss={row['train_mean_loss']:.4f} "
            f"val macro-F1={score[0]:.4f} "
            f"val RELEVANT-F1={score[1]:.4f} "
            f"val RELEVANT-recall={metrics['relevant_recall']:.4f} "
            f"lr={row['lr_at_epoch_end']:.8g}",
            flush=True,
        )

        if improved:
            best_score = score
            best_epoch = epoch
            stale = 0

            checkpoint = output_root / "best_model"
            checkpoint.mkdir(parents=True, exist_ok=True)

            model.save_pretrained(
                str(checkpoint),
                safe_serialization=True,
            )
            tokenizer.save_pretrained(str(checkpoint))

            write_predictions(
                output_root / "best_validation_predictions.csv",
                predictions,
            )
            (output_root / "best_validation_metrics.json").write_text(
                json.dumps(metrics, indent=2),
                encoding="utf-8",
            )
        else:
            stale += 1
            if stale >= args.patience:
                print(
                    f"Early stopping: validation did not improve for "
                    f"{args.patience} consecutive epoch(s)"
                )
                break

    if best_epoch is None:
        raise RuntimeError("Training finished without selecting a checkpoint")

    # Mark exactly one selected best epoch in the exported history.
    for row in history:
        row["is_best_epoch"] = row["epoch"] == best_epoch

    write_history_csv(output_root / "training_history.csv", history)
    write_lr_trace(output_root / "lr_trace.csv", lr_trace)

    dataset_manifest_path = dataset_root / "manifest.json"
    dataset_manifest = read_dataset_manifest(dataset_root)

    run_manifest = {
        "status": "SUCCESS",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "experiment": "experimental_finbert_supervisory_relevance",
        "scope": "SRI_LANKA_BANKING_SUPERVISION",
        "human_gold": False,
        "dataset_root": str(dataset_root),
        "dataset_manifest": dataset_manifest,
        "dataset_manifest_sha256": (
            sha256_file(dataset_manifest_path)
            if dataset_manifest_path.is_file()
            else None
        ),
        "audit": audit,
        "model_name": model_name,
        "label_order": list(LABELS),
        "selection_rule": (
            "Highest validation macro-F1; tie-break by validation RELEVANT F1"
        ),
        "max_length": max_length,
        "stride": stride,
        "headline_weight": headline_weight,
        "body_weight": body_weight,
        "max_body_chunks": args.max_body_chunks,
        "epochs_requested": args.epochs,
        "epochs_completed": len(history),
        "scheduler_epochs": args.scheduler_epochs,
        "steps_per_epoch": steps_per_epoch,
        "scheduler_total_steps": scheduler_total_steps,
        "warmup_ratio": args.warmup_ratio,
        "warmup_steps": warmup_steps,
        "optimizer_steps_completed": optimizer_steps_completed,
        "best_epoch": best_epoch,
        "best_validation_macro_f1": best_score[0],
        "best_validation_relevant_f1": best_score[1],
        "learning_rate": args.learning_rate,
        "weight_decay": args.weight_decay,
        "gradient_accumulation": args.gradient_accumulation,
        "max_grad_norm": args.max_grad_norm,
        "patience": args.patience,
        "class_weights": class_weight_values,
        "seed": args.seed,
        "device": str(device),
        "reproducibility": reproducibility,
        "environment": environment_manifest(torch),
        "test_read_during_training": False,
        "provenance_note": (
            "AI-assisted provisional labels, including scope-reviewed provisional "
            "labels. Not independently human-gold and not a deployable supervisory "
            "model."
        ),
        "evaluation_note": (
            "The September test set has already been viewed in earlier development; "
            "it cannot count as an independent final promotion holdout."
        ),
        "history": history,
        "artifacts": {
            "best_model": str(output_root / "best_model"),
            "best_validation_metrics": str(
                output_root / "best_validation_metrics.json"
            ),
            "best_validation_predictions": str(
                output_root / "best_validation_predictions.csv"
            ),
            "training_history_csv": str(output_root / "training_history.csv"),
            "lr_trace_csv": str(output_root / "lr_trace.csv"),
        },
    }

    (output_root / "run_manifest.json").write_text(
        json.dumps(run_manifest, indent=2),
        encoding="utf-8",
    )

    print("FINETUNE COMPLETE; checkpoint:", output_root / "best_model")
    print(
        "Best epoch:",
        best_epoch,
        "validation macro-F1:",
        round(best_score[0], 4),
        "validation RELEVANT-F1:",
        round(best_score[1], 4),
    )
    print(
        "Scheduler horizon:",
        args.scheduler_epochs,
        "epochs; warmup steps:",
        warmup_steps,
        "/",
        scheduler_total_steps,
    )
    print("Training history:", output_root / "training_history.csv")
    print("LR trace:", output_root / "lr_trace.csv")
    print(
        "Test has NOT been read. Review validation results before optional "
        "--evaluate-test"
    )


if __name__ == "__main__":
    main()
