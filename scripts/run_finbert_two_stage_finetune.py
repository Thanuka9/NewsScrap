from __future__ import annotations

"""
Two-stage FinBERT fine-tuning for Sri Lanka banking-supervision relevance.

Tasks
-----
stage-a:
    NOISE vs KEEP

stage-b:
    WATCH vs RELEVANT

Guardrails
----------
* Training reads ONLY train.csv and validation.csv.
* test.csv is read ONLY with explicit --evaluate-test, after checkpoint selection.
* The existing v3 three-class model is not modified.
* The 200-row AI-assisted historical disagreement review is NOT training data.
* Labels remain AI-assisted provisional, not independently human-gold.
* The inherited September test split has already been viewed in earlier
  development and is therefore developmental comparison only.
* Dataset hashes, preprocessing settings, scheduler settings, package versions,
  class weights, validation history and LR traces are recorded.
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

TWO_STAGE_ROOT = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v4_two_stage"
)

OUTPUT_BASE = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "relevance_two_stage"
    / "supervisory_relevance_v4_two_stage"
)

TASKS: dict[str, dict[str, Any]] = {
    "stage-a": {
        "task_name": "STAGE_A_NOISE_VS_KEEP",
        "labels": ("NOISE", "KEEP"),
        "focus_label": "KEEP",
        "dataset_root": TWO_STAGE_ROOT / "stage_a_noise_vs_keep",
        "output_root": OUTPUT_BASE / "stage_a_noise_vs_keep",
        "description": "NOISE vs KEEP",
    },
    "stage-b": {
        "task_name": "STAGE_B_WATCH_VS_RELEVANT",
        "labels": ("WATCH", "RELEVANT"),
        "focus_label": "RELEVANT",
        "dataset_root": TWO_STAGE_ROOT / "stage_b_watch_vs_relevant",
        "output_root": OUTPUT_BASE / "stage_b_watch_vs_relevant",
        "description": "WATCH vs RELEVANT",
    },
}


def text(value: Any) -> str:
    return str(value or "").strip()


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
    """Hash model-relevant row content and order."""
    digest = hashlib.sha256()
    fields = (
        "dataset_row_id",
        "source_url",
        "story_group_id",
        "target_relevance_label",
        "original_target_relevance_label",
        "headline",
        "article_text",
        "published_at",
    )
    for row in rows:
        for name in fields:
            digest.update(str(row.get(name, "")).encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()


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


def validate_dataset_manifest(
    dataset_root: Path,
    task: dict[str, Any],
) -> dict[str, Any] | None:
    manifest = read_dataset_manifest(dataset_root)
    if manifest is None:
        return None

    recorded_task = text(manifest.get("task_name"))
    expected_task = task["task_name"]

    if recorded_task and recorded_task != expected_task:
        raise RuntimeError(
            "Dataset task mismatch: "
            f"expected {expected_task!r}, manifest has {recorded_task!r}"
        )

    status = text(manifest.get("status")).upper()
    if status and status not in {"PASS", "SUCCESS"}:
        raise RuntimeError(
            f"Dataset manifest status is not successful: {status and status!r}; "
            "expected PASS or SUCCESS"
        )

    leakage = manifest.get("story_group_leakage_count")
    if leakage not in (None, 0):
        raise RuntimeError(
            f"Dataset manifest reports story-group leakage: {leakage}"
        )

    return manifest


def validate_splits(
    train: list[dict[str, str]],
    validation: list[dict[str, str]],
    labels: tuple[str, ...],
) -> dict[str, Any]:
    required = {
        "dataset_row_id",
        "source_url",
        "story_group_id",
        "target_relevance_label",
        "headline",
        "article_text",
        "published_at",
        "label_source",
        "original_target_relevance_label",
        "two_stage_task",
        "label_mapping_version",
    }

    if not train or not validation:
        raise ValueError("Training and validation must both be nonempty")

    allowed = set(labels)

    for split_name, rows in (("train", train), ("validation", validation)):
        row_ids: set[str] = set()
        urls: set[str] = set()

        for index, row in enumerate(rows, 1):
            missing = required - set(row)
            if missing:
                raise ValueError(
                    f"{split_name} row {index}: missing {sorted(missing)}"
                )

            label = text(row.get("target_relevance_label")).upper()
            if label not in allowed:
                raise ValueError(
                    f"{split_name} row {index}: invalid task label {label!r}; "
                    f"expected one of {labels}"
                )

            row_id = text(row.get("dataset_row_id"))
            url = text(row.get("source_url"))
            group = text(row.get("story_group_id"))

            if not row_id:
                raise ValueError(f"{split_name} row {index}: dataset_row_id is blank")
            if row_id in row_ids:
                raise ValueError(
                    f"{split_name}: duplicate dataset_row_id {row_id!r}"
                )
            row_ids.add(row_id)

            if not url:
                raise ValueError(f"{split_name} row {index}: source_url is blank")
            if url in urls:
                raise ValueError(f"{split_name}: duplicate source_url {url!r}")
            urls.add(url)

            if not group:
                raise ValueError(
                    f"{split_name} row {index}: story_group_id is blank"
                )

            if not (
                text(row.get("headline"))
                or text(row.get("article_text"))
            ):
                raise ValueError(f"{split_name} row {index}: empty article")

        for label in labels:
            if not any(
                text(row.get("target_relevance_label")).upper() == label
                for row in rows
            ):
                raise ValueError(
                    f"{split_name} has no {label} examples"
                )

    train_groups = {text(r["story_group_id"]) for r in train}
    val_groups = {text(r["story_group_id"]) for r in validation}
    group_overlap = train_groups & val_groups
    if group_overlap:
        raise ValueError(
            f"Story-group leakage: {sorted(group_overlap)[:10]}"
        )

    train_urls = {text(r["source_url"]) for r in train}
    val_urls = {text(r["source_url"]) for r in validation}
    if train_urls & val_urls:
        raise ValueError("Same URL appears in train and validation")

    train_ids = {text(r["dataset_row_id"]) for r in train}
    val_ids = {text(r["dataset_row_id"]) for r in validation}
    if train_ids & val_ids:
        raise ValueError("Same dataset_row_id appears in train and validation")

    return {
        "train_rows": len(train),
        "validation_rows": len(validation),
        "train_counts": dict(
            Counter(
                text(r["target_relevance_label"]).upper()
                for r in train
            )
        ),
        "validation_counts": dict(
            Counter(
                text(r["target_relevance_label"]).upper()
                for r in validation
            )
        ),
        "train_sha256": digest_rows(train),
        "validation_sha256": digest_rows(validation),
        "train_val_group_overlap": len(group_overlap),
        "train_val_url_overlap": len(train_urls & val_urls),
        "train_val_row_id_overlap": len(train_ids & val_ids),
        "provenance": dict(
            Counter(
                text(r.get("label_source")) or "UNSPECIFIED"
                for r in train + validation
            )
        ),
    }


def validate_test_against_development(
    train: list[dict[str, str]],
    validation: list[dict[str, str]],
    test: list[dict[str, str]],
    labels: tuple[str, ...],
) -> None:
    if not test:
        raise RuntimeError("Empty test set")

    allowed = set(labels)
    required = {
        "dataset_row_id",
        "source_url",
        "story_group_id",
        "target_relevance_label",
        "headline",
        "article_text",
        "published_at",
    }

    test_ids: set[str] = set()
    test_urls: set[str] = set()

    for index, row in enumerate(test, 1):
        missing = required - set(row)
        if missing:
            raise RuntimeError(
                f"test row {index}: missing {sorted(missing)}"
            )

        label = text(row.get("target_relevance_label")).upper()
        if label not in allowed:
            raise RuntimeError(
                f"test row {index}: invalid test label {label!r}"
            )

        row_id = text(row.get("dataset_row_id"))
        url = text(row.get("source_url"))
        group = text(row.get("story_group_id"))

        if not row_id or not url or not group:
            raise RuntimeError(
                f"test row {index}: blank row id / URL / story group"
            )

        if row_id in test_ids:
            raise RuntimeError("Duplicate dataset_row_id in test split")
        test_ids.add(row_id)

        if url in test_urls:
            raise RuntimeError("Duplicate source_url in test split")
        test_urls.add(url)

    development_groups = {
        text(r["story_group_id"])
        for r in train + validation
    }
    test_groups = {
        text(r["story_group_id"])
        for r in test
    }
    if development_groups & test_groups:
        raise RuntimeError("Test story-group leakage")

    development_urls = {
        text(r["source_url"])
        for r in train + validation
    }
    if development_urls & test_urls:
        raise RuntimeError("Test URL leakage")

    development_ids = {
        text(r["dataset_row_id"])
        for r in train + validation
    }
    if development_ids & test_ids:
        raise RuntimeError("Test dataset_row_id leakage")


# ---------------------------------------------------------------------------
# Text windowing / document aggregation
# ---------------------------------------------------------------------------

def spaced_indices(total: int, maximum: int) -> list[int]:
    """Keep windows from beginning, middle and end, matching v3 trainer."""
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
    value: str,
    *,
    max_length: int,
    stride: int,
    maximum: int,
) -> list[dict]:
    if not value.strip():
        return []

    payload = tokenizer(
        value,
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
        item = {
            key: values[index]
            for key, values in payload.items()
        }
        item["token_weight"] = float(
            sum(item["attention_mask"])
        )
        windows.append(item)

    return windows


def encode_article(
    tokenizer,
    row: dict[str, str],
    *,
    max_length: int,
    stride: int,
    max_body_chunks: int,
) -> dict[str, Any]:
    return {
        "headline": encode_windows(
            tokenizer,
            text(row.get("headline")),
            max_length=max_length,
            stride=stride,
            maximum=1,
        ),
        "body": encode_windows(
            tokenizer,
            text(row.get("article_text")),
            max_length=max_length,
            stride=stride,
            maximum=max_body_chunks,
        ),
        "label": text(row.get("target_relevance_label")).upper(),
        "row": row,
    }


def model_article_logits(
    model,
    item: dict[str, Any],
    device,
    torch,
    *,
    headline_weight: float,
    body_weight: float,
):
    """
    Differentiable document-level aggregation.

    Within headline/body, windows are token-count weighted.
    Headline/body are then combined using the configured side weights.
    This intentionally mirrors the frozen v3 trainer.
    """
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
                key: torch.tensor(
                    [value],
                    dtype=torch.long,
                    device=device,
                )
                for key, value in window.items()
                if key != "token_weight"
            }
            logits.append(
                model(**features).logits.squeeze(0)
            )
            weights.append(window["token_weight"])

        token_weights = torch.tensor(
            weights,
            device=device,
            dtype=logits[0].dtype,
        )
        normalized = token_weights / token_weights.sum()
        side_logits = torch.stack(logits, dim=0)
        pooled = (
            side_logits
            * normalized[:, None]
        ).sum(dim=0)

        sides.append((pooled, side_weight))

    if not sides:
        raise ValueError("Document has no usable text")

    denominator = sum(weight for _, weight in sides)

    return sum(
        value * (weight / denominator)
        for value, weight in sides
    )


# ---------------------------------------------------------------------------
# Metrics / exports
# ---------------------------------------------------------------------------

def classification_metrics(
    y_true: list[str],
    y_pred: list[str],
    labels: tuple[str, ...],
    focus_label: str,
) -> dict[str, Any]:
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
        labels=list(labels),
        output_dict=True,
        zero_division=0,
    )

    payload: dict[str, Any] = {
        "accuracy": float(
            accuracy_score(y_true, y_pred)
        ),
        "balanced_accuracy": float(
            balanced_accuracy_score(y_true, y_pred)
        ),
        "macro_f1": float(
            f1_score(
                y_true,
                y_pred,
                labels=list(labels),
                average="macro",
                zero_division=0,
            )
        ),
        "focus_label": focus_label,
        "focus_f1": float(
            report[focus_label]["f1-score"]
        ),
        "focus_precision": float(
            report[focus_label]["precision"]
        ),
        "focus_recall": float(
            report[focus_label]["recall"]
        ),
        "per_class": {
            key: report[key]
            for key in labels
        },
        "confusion_matrix": confusion_matrix(
            y_true,
            y_pred,
            labels=list(labels),
        ).tolist(),
        "label_order": list(labels),
    }

    for label in labels:
        slug = label.lower()
        payload[f"{slug}_f1"] = float(
            report[label]["f1-score"]
        )
        payload[f"{slug}_precision"] = float(
            report[label]["precision"]
        )
        payload[f"{slug}_recall"] = float(
            report[label]["recall"]
        )

    return payload


def evaluate(
    model,
    items: list[dict[str, Any]],
    device,
    torch,
    *,
    labels: tuple[str, ...],
    focus_label: str,
    headline_weight: float,
    body_weight: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    model.eval()

    predictions: list[str] = []
    truth: list[str] = []
    export: list[dict[str, Any]] = []

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

            probabilities = (
                torch.softmax(logits, dim=-1)
                .cpu()
                .tolist()
            )

            predicted_id = int(
                max(
                    range(len(labels)),
                    key=lambda i: probabilities[i],
                )
            )
            predicted = labels[predicted_id]

            predictions.append(predicted)
            truth.append(item["label"])

            row = item["row"]

            out: dict[str, Any] = {
                "dataset_row_id": row.get(
                    "dataset_row_id",
                    "",
                ),
                "published_at": row["published_at"],
                "headline": row["headline"],
                "source_url": row["source_url"],
                "story_group_id": row["story_group_id"],
                "true_label": item["label"],
                "predicted_label": predicted,
                "correct": item["label"] == predicted,
            }

            for index, label in enumerate(labels):
                out[f"p_{label.lower()}"] = (
                    probabilities[index]
                )

            export.append(out)

    return (
        classification_metrics(
            truth,
            predictions,
            labels,
            focus_label,
        ),
        export,
    )


def write_predictions(
    path: Path,
    rows: list[dict[str, Any]],
    labels: tuple[str, ...],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fields = [
        "dataset_row_id",
        "published_at",
        "headline",
        "source_url",
        "story_group_id",
        "true_label",
        "predicted_label",
        *[
            f"p_{label.lower()}"
            for label in labels
        ],
        "correct",
    ]

    with path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_history_csv(
    path: Path,
    history: list[dict[str, Any]],
    labels: tuple[str, ...],
) -> None:
    fields = [
        "epoch",
        "train_mean_loss",
        "validation_accuracy",
        "validation_balanced_accuracy",
        "validation_macro_f1",
        "focus_label",
        "validation_focus_f1",
        "validation_focus_precision",
        "validation_focus_recall",
        *[
            f"validation_{label.lower()}_f1"
            for label in labels
        ],
        "optimizer_steps_completed",
        "lr_at_epoch_end",
        "is_best_epoch",
    ]

    with path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
        )
        writer.writeheader()

        for row in history:
            metrics = row["validation"]

            out = {
                "epoch": row["epoch"],
                "train_mean_loss": row["train_mean_loss"],
                "validation_accuracy": metrics["accuracy"],
                "validation_balanced_accuracy": metrics[
                    "balanced_accuracy"
                ],
                "validation_macro_f1": metrics[
                    "macro_f1"
                ],
                "focus_label": metrics["focus_label"],
                "validation_focus_f1": metrics[
                    "focus_f1"
                ],
                "validation_focus_precision": metrics[
                    "focus_precision"
                ],
                "validation_focus_recall": metrics[
                    "focus_recall"
                ],
                "optimizer_steps_completed": row[
                    "optimizer_steps_completed"
                ],
                "lr_at_epoch_end": row[
                    "lr_at_epoch_end"
                ],
                "is_best_epoch": row.get(
                    "is_best_epoch",
                    False,
                ),
            }

            for label in labels:
                out[
                    f"validation_{label.lower()}_f1"
                ] = metrics[
                    f"{label.lower()}_f1"
                ]

            writer.writerow(out)


def write_lr_trace(
    path: Path,
    rows: list[dict[str, Any]],
) -> None:
    fields = [
        "optimizer_step",
        "epoch",
        "article_position",
        "learning_rate_before_step",
        "learning_rate_after_scheduler_step",
    ]

    with path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
        )
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Environment / model helpers
# ---------------------------------------------------------------------------

def load_settings() -> dict[str, Any]:
    """
    Read the same base FinBERT config used by the current pipeline.
    """
    import yaml

    path = (
        PROJECT_ROOT
        / "config"
        / "intelligence"
        / "ml.yaml"
    )

    if not path.is_file():
        raise FileNotFoundError(
            f"FinBERT settings file not found: {path}"
        )

    payload = yaml.safe_load(
        path.read_text(
            encoding="utf-8-sig"
        )
    )

    if (
        not isinstance(payload, dict)
        or "finbert" not in payload
    ):
        raise RuntimeError(
            f"Missing 'finbert' section in {path}"
        )

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

        if (
            device.type == "cuda"
            and not torch.cuda.is_available()
        ):
            raise RuntimeError(
                "CUDA requested but unavailable"
            )

        if (
            device.type == "mps"
            and not (
                hasattr(torch.backends, "mps")
                and torch.backends.mps.is_available()
            )
        ):
            raise RuntimeError(
                "MPS requested but unavailable"
            )

        return device

    if torch.cuda.is_available():
        return torch.device("cuda")

    if (
        hasattr(torch.backends, "mps")
        and torch.backends.mps.is_available()
    ):
        return torch.device("mps")

    return torch.device("cpu")


def configure_reproducibility(
    torch,
    np,
    set_seed,
    seed: int,
) -> dict[str, Any]:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    set_seed(seed)

    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

    requested = False
    enabled = False

    try:
        torch.use_deterministic_algorithms(
            True,
            warn_only=True,
        )
        requested = True
        enabled = bool(
            torch.are_deterministic_algorithms_enabled()
        )
    except (AttributeError, TypeError):
        pass

    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False

    return {
        "seed": seed,
        "pythonhashseed_at_runtime": os.environ.get(
            "PYTHONHASHSEED"
        ),
        "deterministic_algorithms_requested": requested,
        "deterministic_algorithms_enabled": enabled,
        "cudnn_deterministic": bool(
            getattr(
                getattr(
                    torch.backends,
                    "cudnn",
                    object(),
                ),
                "deterministic",
                False,
            )
        ),
        "cudnn_benchmark": bool(
            getattr(
                getattr(
                    torch.backends,
                    "cudnn",
                    object(),
                ),
                "benchmark",
                False,
            )
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
        "torch_version": getattr(
            torch,
            "__version__",
            None,
        ),
        "transformers_version": package_version(
            "transformers"
        ),
        "numpy_version": package_version(
            "numpy"
        ),
        "scikit_learn_version": package_version(
            "scikit-learn"
        ),
        "pyyaml_version": package_version(
            "PyYAML"
        ),
        "cuda_available": bool(
            torch.cuda.is_available()
        ),
        "cuda_version": getattr(
            getattr(
                torch,
                "version",
                None,
            ),
            "cuda",
            None,
        ),
        "mps_available": bool(
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ),
    }


def output_has_training_artifacts(
    output_root: Path,
) -> bool:
    if not output_root.exists():
        return False
    if not output_root.is_dir():
        return True
    return any(output_root.iterdir())


def reset_classifier_head(
    model,
    cfg,
    torch,
) -> None:
    """
    Reset the classification head because FinBERT's original
    sentiment classes are unrelated to either two-stage task.
    """
    classifier = getattr(
        model,
        "classifier",
        None,
    )

    if not isinstance(
        classifier,
        torch.nn.Linear,
    ):
        raise RuntimeError(
            "Expected BERT Linear classifier head; "
            "inspect model before continuing"
        )

    if classifier.out_features != cfg.num_labels:
        raise RuntimeError(
            "Classifier output dimension does not match "
            f"task labels: {classifier.out_features} != {cfg.num_labels}"
        )

    torch.nn.init.normal_(
        classifier.weight,
        mean=0.0,
        std=cfg.initializer_range,
    )

    if classifier.bias is not None:
        torch.nn.init.zeros_(
            classifier.bias
        )


def model_load_options(
    settings: dict[str, Any],
) -> dict[str, Any]:
    """
    Honour the pipeline's local/offline loading settings.
    """
    options: dict[str, Any] = {}

    if "local_files_only" in settings:
        options["local_files_only"] = bool(
            settings["local_files_only"]
        )

    if "use_safetensors" in settings:
        options["use_safetensors"] = bool(
            settings["use_safetensors"]
        )

    return options


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fine-tune ProsusAI/finbert for the two-stage "
            "Sri Lanka banking-supervision relevance experiment."
        )
    )

    parser.add_argument(
        "--task",
        choices=tuple(TASKS),
        required=True,
        help=(
            "stage-a = NOISE vs KEEP; "
            "stage-b = WATCH vs RELEVANT"
        ),
    )

    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=None,
    )

    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
    )

    parser.add_argument(
        "--validate-only",
        action="store_true",
        help=(
            "Check train/validation only. "
            "Does not import FinBERT and does not read test.csv."
        ),
    )

    parser.add_argument(
        "--evaluate-test",
        action="store_true",
        help=(
            "Explicitly evaluate the already-selected checkpoint "
            "on the inherited developmental test split. "
            "Do not use during tuning."
        ),
    )

    parser.add_argument(
        "--epochs",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--scheduler-epochs",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--learning-rate",
        type=float,
        default=1e-5,
    )

    parser.add_argument(
        "--weight-decay",
        type=float,
        default=0.01,
    )

    parser.add_argument(
        "--warmup-ratio",
        type=float,
        default=0.10,
    )

    parser.add_argument(
        "--max-body-chunks",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--gradient-accumulation",
        type=int,
        default=4,
    )

    parser.add_argument(
        "--max-grad-norm",
        type=float,
        default=1.0,
    )

    parser.add_argument(
        "--patience",
        type=int,
        default=5,
        help=(
            "Default 5 so the fixed 5-epoch experiment normally "
            "runs the complete schedule."
        ),
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    parser.add_argument(
        "--device",
        choices=(
            "auto",
            "cpu",
            "cuda",
            "mps",
        ),
        default="auto",
    )

    args = parser.parse_args()

    if args.validate_only and args.evaluate_test:
        parser.error(
            "--validate-only cannot be combined with --evaluate-test"
        )

    if args.epochs < 1:
        parser.error("--epochs must be >= 1")

    if args.scheduler_epochs < 1:
        parser.error(
            "--scheduler-epochs must be >= 1"
        )

    if args.scheduler_epochs < args.epochs:
        parser.error(
            "--scheduler-epochs cannot be smaller than --epochs"
        )

    if args.gradient_accumulation < 1:
        parser.error(
            "--gradient-accumulation must be >= 1"
        )

    if args.patience < 1:
        parser.error("--patience must be >= 1")

    if args.learning_rate <= 0:
        parser.error(
            "--learning-rate must be > 0"
        )

    if args.weight_decay < 0:
        parser.error(
            "--weight-decay must be >= 0"
        )

    if not 0 <= args.warmup_ratio < 1:
        parser.error(
            "--warmup-ratio must satisfy 0 <= ratio < 1"
        )

    if args.max_body_chunks < 1:
        parser.error(
            "--max-body-chunks must be >= 1"
        )

    if args.max_grad_norm <= 0:
        parser.error(
            "--max-grad-norm must be > 0"
        )

    return args


def resolve_experiment_paths(
    args: argparse.Namespace,
    task: dict[str, Any],
) -> tuple[Path, Path]:
    dataset_root = (
        args.dataset_root
        if args.dataset_root is not None
        else task["dataset_root"]
    )

    output_root = (
        args.output_root
        if args.output_root is not None
        else (
            task["output_root"]
            / (
                "finbert_"
                f"lr{args.learning_rate:g}_"
                f"chunks{args.max_body_chunks}_"
                f"sched{args.scheduler_epochs}_"
                f"seed{args.seed}"
            )
        )
    )

    if not dataset_root.is_absolute():
        dataset_root = (
            PROJECT_ROOT
            / dataset_root
        )

    if not output_root.is_absolute():
        output_root = (
            PROJECT_ROOT
            / output_root
        )

    return (
        dataset_root.resolve(),
        output_root.resolve(),
    )


def main() -> None:
    args = parse_args()
    task = TASKS[args.task]

    labels: tuple[str, ...] = task[
        "labels"
    ]
    focus_label: str = task[
        "focus_label"
    ]

    label2id = {
        name: index
        for index, name in enumerate(labels)
    }

    id2label = {
        index: name
        for name, index in label2id.items()
    }

    dataset_root, output_root = (
        resolve_experiment_paths(
            args,
            task,
        )
    )

    if not dataset_root.is_dir():
        raise FileNotFoundError(
            dataset_root
        )

    dataset_manifest = (
        validate_dataset_manifest(
            dataset_root,
            task,
        )
    )

    print("=" * 96)
    print(
        "FINBERT TWO-STAGE SUPERVISORY RELEVANCE"
    )
    print("=" * 96)
    print(
        f"Task         : {args.task} — "
        f"{task['description']}"
    )
    print(
        f"Labels       : {list(labels)}"
    )
    print(
        f"Focus label  : {focus_label}"
    )
    print(
        f"Dataset      : {dataset_root}"
    )
    print(
        f"Output       : {output_root}"
    )
    print()

    # ---------------------------------------------------------------
    # TRAIN / VALIDATION AUDIT.
    # Test is intentionally untouched unless --evaluate-test.
    # ---------------------------------------------------------------

    if not args.evaluate_test:
        train = read_csv(
            dataset_path(
                dataset_root,
                "train.csv",
            )
        )

        validation = read_csv(
            dataset_path(
                dataset_root,
                "validation.csv",
            )
        )

        audit = validate_splits(
            train,
            validation,
            labels,
        )

        print(
            "DATA AUDIT",
            json.dumps(
                audit,
                indent=2,
            ),
        )

        if args.validate_only:
            print(
                "VALIDATION-ONLY PASS; "
                "test.csv was NOT read."
            )
            return

        if output_has_training_artifacts(
            output_root
        ):
            raise RuntimeError(
                "Output already contains training artifacts. "
                "Choose a NEW --output-root so a previous "
                "experiment is never overwritten. "
                f"Existing output: {output_root}"
            )

    else:
        run_path = (
            output_root
            / "run_manifest.json"
        )

        if not run_path.exists():
            raise FileNotFoundError(
                "Fine-tuning manifest not found: "
                "train/select a checkpoint first"
            )

        manifest = json.loads(
            run_path.read_text(
                encoding="utf-8"
            )
        )

        if (
            manifest.get("dataset_root")
            != str(dataset_root)
        ):
            raise RuntimeError(
                "Dataset root does not match "
                "the selected checkpoint"
            )

        if manifest.get("task") != args.task:
            raise RuntimeError(
                "Task does not match selected checkpoint"
            )

        train = read_csv(
            dataset_path(
                dataset_root,
                "train.csv",
            )
        )

        validation = read_csv(
            dataset_path(
                dataset_root,
                "validation.csv",
            )
        )

        audit = validate_splits(
            train,
            validation,
            labels,
        )

        manifest_audit = (
            manifest.get("audit")
            or {}
        )

        if (
            audit["train_sha256"]
            != manifest_audit.get(
                "train_sha256"
            )
            or audit["validation_sha256"]
            != manifest_audit.get(
                "validation_sha256"
            )
        ):
            raise RuntimeError(
                "Train/validation content changed "
                "since checkpoint selection"
            )

        test = read_csv(
            dataset_path(
                dataset_root,
                "test.csv",
            )
        )

        validate_test_against_development(
            train,
            validation,
            test,
            labels,
        )

        if (
            output_root
            / "test_metrics.json"
        ).exists():
            raise RuntimeError(
                "Test already evaluated for this run. "
                "Do not repeatedly tune on test."
            )

    # Heavy ML stack is imported only after data/guardrail checks.
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
    max_length = int(
        settings.get(
            "max_length",
            512,
        )
    )
    stride = int(
        settings.get(
            "stride",
            96,
        )
    )
    headline_weight = float(
        settings.get(
            "headline_weight",
            0.35,
        )
    )
    body_weight = float(
        settings.get(
            "body_weight",
            0.65,
        )
    )

    total_weight = (
        headline_weight
        + body_weight
    )

    if total_weight <= 0:
        raise RuntimeError(
            "Headline/body weights must sum "
            "to a positive value"
        )

    if max_length < 2:
        raise RuntimeError(
            "max_length must be >= 2"
        )

    if not 0 <= stride < max_length:
        raise RuntimeError(
            "stride must satisfy 0 <= stride < max_length"
        )

    headline_weight /= total_weight
    body_weight /= total_weight

    reproducibility = (
        configure_reproducibility(
            torch,
            np,
            set_seed,
            args.seed,
        )
    )

    device = resolve_device(
        torch,
        args.device,
    )

    print(
        f"DEVICE: {device}; "
        f"model: {model_name}; "
        f"body windows/article <= "
        f"{args.max_body_chunks}"
    )

    if device.type == "cpu":
        print(
            "WARNING: Fine-tuning on CPU may take a long time."
        )

    load_options = model_load_options(
        settings
    )

    # ---------------------------------------------------------------
    # Explicit inherited-test evaluation.
    # ---------------------------------------------------------------

    if args.evaluate_test:
        recorded = manifest

        expected_fields = {
            "model_name": model_name,
            "max_length": max_length,
            "stride": stride,
            "max_body_chunks": (
                args.max_body_chunks
            ),
            "headline_weight": (
                headline_weight
            ),
            "body_weight": body_weight,
            "label_order": list(labels),
            "focus_label": focus_label,
        }

        mismatches = {
            key: {
                "recorded": recorded.get(key),
                "requested": value,
            }
            for key, value in expected_fields.items()
            if recorded.get(key) != value
        }

        if mismatches:
            raise RuntimeError(
                "Inference preprocessing does not "
                "match checkpoint: "
                + json.dumps(
                    mismatches,
                    indent=2,
                )
            )

        checkpoint = (
            output_root
            / "best_model"
        )

        if not (
            checkpoint
            / "config.json"
        ).is_file():
            raise FileNotFoundError(
                f"Checkpoint not found: {checkpoint}"
            )

        tokenizer = (
            AutoTokenizer.from_pretrained(
                str(checkpoint),
                local_files_only=True,
            )
        )

        model = (
            AutoModelForSequenceClassification
            .from_pretrained(
                str(checkpoint),
                local_files_only=True,
            )
        )

        model.to(device)

        encoded_test = [
            encode_article(
                tokenizer,
                row,
                max_length=max_length,
                stride=stride,
                max_body_chunks=(
                    args.max_body_chunks
                ),
            )
            for row in test
        ]

        metrics, predictions = evaluate(
            model,
            encoded_test,
            device,
            torch,
            labels=labels,
            focus_label=focus_label,
            headline_weight=(
                headline_weight
            ),
            body_weight=(
                body_weight
            ),
        )

        test_payload = {
            "task": args.task,
            "task_name": task["task_name"],
            "label_order": list(labels),
            "focus_label": focus_label,
            "metrics": metrics,
            "test_rows": len(test),
            "test_sha256": digest_rows(test),
            "evaluated_utc": (
                datetime.now(
                    timezone.utc
                ).isoformat()
            ),
            "warning": (
                "Inherited, previously viewed, "
                "AI-assisted provisional September "
                "test: developmental comparison only, "
                "NOT an independent gold holdout."
            ),
        }

        (
            output_root
            / "test_metrics.json"
        ).write_text(
            json.dumps(
                test_payload,
                indent=2,
            ),
            encoding="utf-8",
        )

        write_predictions(
            output_root
            / "test_predictions.csv",
            predictions,
            labels,
        )

        print(
            "TEST macro-F1:",
            round(
                metrics["macro_f1"],
                4,
            ),
            f"{focus_label} F1:",
            round(
                metrics["focus_f1"],
                4,
            ),
        )

        print(
            "Test metrics:",
            output_root
            / "test_metrics.json",
        )
        return

    # ---------------------------------------------------------------
    # Training mode.
    # ---------------------------------------------------------------

    tokenizer_kwargs = {
        key: value
        for key, value in load_options.items()
        if key == "local_files_only"
    }

    tokenizer = (
        AutoTokenizer.from_pretrained(
            model_name,
            **tokenizer_kwargs,
        )
    )

    cfg = AutoConfig.from_pretrained(
        model_name,
        **tokenizer_kwargs,
    )

    cfg.num_labels = len(labels)
    cfg.id2label = id2label
    cfg.label2id = label2id
    cfg.problem_type = (
        "single_label_classification"
    )

    model_kwargs = dict(
        load_options
    )
    model_kwargs[
        "ignore_mismatched_sizes"
    ] = True

    model = (
        AutoModelForSequenceClassification
        .from_pretrained(
            model_name,
            config=cfg,
            **model_kwargs,
        )
    )

    # FinBERT's original sentiment head is not the target task.
    # For 2 labels, Transformers constructs a 2-way head via
    # ignore_mismatched_sizes; reset it explicitly for auditability.
    reset_classifier_head(
        model,
        cfg,
        torch,
    )

    model.to(device)

    print(
        "FinBERT sentiment head RESET for",
        task["description"],
    )

    train_items = [
        encode_article(
            tokenizer,
            row,
            max_length=max_length,
            stride=stride,
            max_body_chunks=(
                args.max_body_chunks
            ),
        )
        for row in train
    ]

    validation_items = [
        encode_article(
            tokenizer,
            row,
            max_length=max_length,
            stride=stride,
            max_body_chunks=(
                args.max_body_chunks
            ),
        )
        for row in validation
    ]

    counts = Counter(
        text(
            row[
                "target_relevance_label"
            ]
        ).upper()
        for row in train
    )

    class_weight_values = {
        label: (
            len(train)
            / (
                len(labels)
                * counts[label]
            )
        )
        for label in labels
    }

    class_weights = torch.tensor(
        [
            class_weight_values[label]
            for label in labels
        ],
        dtype=torch.float32,
        device=device,
    )

    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=args.learning_rate,
        weight_decay=args.weight_decay,
    )

    steps_per_epoch = math.ceil(
        len(train_items)
        / args.gradient_accumulation
    )

    scheduler_total_steps = (
        steps_per_epoch
        * args.scheduler_epochs
    )

    warmup_steps = int(
        scheduler_total_steps
        * args.warmup_ratio
    )

    if args.warmup_ratio > 0:
        warmup_steps = max(
            1,
            warmup_steps,
        )

    if warmup_steps >= scheduler_total_steps:
        raise RuntimeError(
            "Warmup steps must be smaller "
            "than total scheduler steps"
        )

    schedule = (
        get_linear_schedule_with_warmup(
            optimizer,
            num_warmup_steps=(
                warmup_steps
            ),
            num_training_steps=(
                scheduler_total_steps
            ),
        )
    )

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    print(
        "SCHEDULER:",
        json.dumps(
            {
                "training_epochs_requested": (
                    args.epochs
                ),
                "scheduler_epochs": (
                    args.scheduler_epochs
                ),
                "steps_per_epoch": (
                    steps_per_epoch
                ),
                "scheduler_total_steps": (
                    scheduler_total_steps
                ),
                "warmup_ratio": (
                    args.warmup_ratio
                ),
                "warmup_steps": (
                    warmup_steps
                ),
                "initial_learning_rate": (
                    args.learning_rate
                ),
            },
            indent=2,
        ),
    )

    print(
        "CLASS WEIGHTS:",
        json.dumps(
            class_weight_values,
            indent=2,
        ),
    )

    history: list[
        dict[str, Any]
    ] = []

    lr_trace: list[
        dict[str, Any]
    ] = []

    # Same selection philosophy as v3:
    # highest validation macro-F1, then task focus-label F1.
    best_score = (
        -1.0,
        -1.0,
    )
    best_epoch: int | None = None
    stale = 0
    optimizer_steps_completed = 0

    for epoch in range(
        1,
        args.epochs + 1,
    ):
        model.train()

        order = list(
            range(
                len(train_items)
            )
        )

        random.Random(
            args.seed + epoch
        ).shuffle(order)

        epoch_loss = 0.0

        optimizer.zero_grad(
            set_to_none=True
        )

        for position, index in enumerate(
            order,
            1,
        ):
            item = train_items[index]

            logits = model_article_logits(
                model,
                item,
                device,
                torch,
                headline_weight=(
                    headline_weight
                ),
                body_weight=(
                    body_weight
                ),
            )

            label_id = torch.tensor(
                [
                    label2id[
                        item["label"]
                    ]
                ],
                device=device,
                dtype=torch.long,
            )

            loss = F.cross_entropy(
                logits.unsqueeze(0),
                label_id,
                weight=class_weights,
                reduction="sum",
            )

            group_index = (
                (position - 1)
                // args.gradient_accumulation
            )

            group_size = min(
                args.gradient_accumulation,
                len(order)
                - group_index
                * args.gradient_accumulation,
            )

            (
                loss
                / group_size
            ).backward()

            epoch_loss += float(
                loss.detach().cpu()
            )

            should_step = (
                position
                % args.gradient_accumulation
                == 0
                or position == len(order)
            )

            if should_step:
                lr_before = float(
                    optimizer
                    .param_groups[0][
                        "lr"
                    ]
                )

                torch.nn.utils.clip_grad_norm_(
                    model.parameters(),
                    args.max_grad_norm,
                )

                optimizer.step()
                schedule.step()

                optimizer.zero_grad(
                    set_to_none=True
                )

                optimizer_steps_completed += 1

                lr_after = float(
                    optimizer
                    .param_groups[0][
                        "lr"
                    ]
                )

                lr_trace.append(
                    {
                        "optimizer_step": (
                            optimizer_steps_completed
                        ),
                        "epoch": epoch,
                        "article_position": (
                            position
                        ),
                        "learning_rate_before_step": (
                            lr_before
                        ),
                        "learning_rate_after_scheduler_step": (
                            lr_after
                        ),
                    }
                )

            if (
                position % 20 == 0
                or position == len(order)
            ):
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
            labels=labels,
            focus_label=focus_label,
            headline_weight=(
                headline_weight
            ),
            body_weight=(
                body_weight
            ),
        )

        score = (
            metrics["macro_f1"],
            metrics["focus_f1"],
        )

        improved = score > best_score

        row = {
            "epoch": epoch,
            "train_mean_loss": (
                epoch_loss
                / len(order)
            ),
            "validation": metrics,
            "optimizer_steps_completed": (
                optimizer_steps_completed
            ),
            "lr_at_epoch_end": float(
                optimizer
                .param_groups[0][
                    "lr"
                ]
            ),
            "is_best_epoch": (
                bool(improved)
            ),
        }

        history.append(row)

        print(
            f"EPOCH {epoch}: "
            f"loss={row['train_mean_loss']:.4f} "
            f"val macro-F1={score[0]:.4f} "
            f"val {focus_label}-F1={score[1]:.4f} "
            f"val {focus_label}-recall="
            f"{metrics['focus_recall']:.4f} "
            f"lr={row['lr_at_epoch_end']:.8g}",
            flush=True,
        )

        if improved:
            best_score = score
            best_epoch = epoch
            stale = 0

            checkpoint = (
                output_root
                / "best_model"
            )

            checkpoint.mkdir(
                parents=True,
                exist_ok=True,
            )

            model.save_pretrained(
                str(checkpoint),
                safe_serialization=True,
            )

            tokenizer.save_pretrained(
                str(checkpoint)
            )

            write_predictions(
                output_root
                / "best_validation_predictions.csv",
                predictions,
                labels,
            )

            (
                output_root
                / "best_validation_metrics.json"
            ).write_text(
                json.dumps(
                    metrics,
                    indent=2,
                ),
                encoding="utf-8",
            )

        else:
            stale += 1

            if stale >= args.patience:
                print(
                    "Early stopping: validation "
                    f"did not improve for "
                    f"{args.patience} consecutive "
                    "epoch(s)"
                )
                break

    if best_epoch is None:
        raise RuntimeError(
            "Training finished without "
            "selecting a checkpoint"
        )

    for row in history:
        row["is_best_epoch"] = (
            row["epoch"]
            == best_epoch
        )

    write_history_csv(
        output_root
        / "training_history.csv",
        history,
        labels,
    )

    write_lr_trace(
        output_root
        / "lr_trace.csv",
        lr_trace,
    )

    dataset_manifest_path = (
        dataset_root
        / "manifest.json"
    )

    run_manifest = {
        "status": "SUCCESS",
        "created_utc": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),
        "experiment": (
            "experimental_finbert_two_stage_"
            "supervisory_relevance"
        ),
        "task": args.task,
        "task_name": task["task_name"],
        "task_description": task[
            "description"
        ],
        "scope": (
            "SRI_LANKA_BANKING_SUPERVISION"
        ),
        "human_gold": False,
        "training_data_policy": (
            "Derived only from "
            "supervisory_relevance_v3_reviewed "
            "via v4 two-stage mapping. "
            "The 200-row AI-assisted historical "
            "disagreement adjudication is excluded."
        ),
        "dataset_root": str(
            dataset_root
        ),
        "dataset_manifest": (
            dataset_manifest
        ),
        "dataset_manifest_sha256": (
            sha256_file(
                dataset_manifest_path
            )
            if dataset_manifest_path.is_file()
            else None
        ),
        "audit": audit,
        "model_name": model_name,
        "label_order": list(labels),
        "focus_label": focus_label,
        "selection_rule": (
            "Highest validation macro-F1; "
            f"tie-break by validation {focus_label} F1"
        ),
        "max_length": max_length,
        "stride": stride,
        "headline_weight": (
            headline_weight
        ),
        "body_weight": body_weight,
        "max_body_chunks": (
            args.max_body_chunks
        ),
        "epochs_requested": (
            args.epochs
        ),
        "epochs_completed": (
            len(history)
        ),
        "scheduler_epochs": (
            args.scheduler_epochs
        ),
        "steps_per_epoch": (
            steps_per_epoch
        ),
        "scheduler_total_steps": (
            scheduler_total_steps
        ),
        "warmup_ratio": (
            args.warmup_ratio
        ),
        "warmup_steps": (
            warmup_steps
        ),
        "optimizer_steps_completed": (
            optimizer_steps_completed
        ),
        "best_epoch": best_epoch,
        "best_validation_macro_f1": (
            best_score[0]
        ),
        "best_validation_focus_f1": (
            best_score[1]
        ),
        "learning_rate": (
            args.learning_rate
        ),
        "weight_decay": (
            args.weight_decay
        ),
        "gradient_accumulation": (
            args.gradient_accumulation
        ),
        "max_grad_norm": (
            args.max_grad_norm
        ),
        "patience": args.patience,
        "class_weights": (
            class_weight_values
        ),
        "seed": args.seed,
        "device": str(device),
        "base_model_load_options": (
            load_options
        ),
        "reproducibility": (
            reproducibility
        ),
        "environment": (
            environment_manifest(
                torch
            )
        ),
        "test_read_during_training": False,
        "provenance_note": (
            "AI-assisted provisional labels, "
            "including scope-reviewed provisional "
            "labels. Not independently human-gold "
            "and not a deployable supervisory model."
        ),
        "evaluation_note": (
            "The inherited September test split "
            "has already been viewed in earlier "
            "development and cannot count as an "
            "independent final promotion holdout."
        ),
        "history": history,
        "artifacts": {
            "best_model": str(
                output_root
                / "best_model"
            ),
            "best_validation_metrics": str(
                output_root
                / "best_validation_metrics.json"
            ),
            "best_validation_predictions": str(
                output_root
                / "best_validation_predictions.csv"
            ),
            "training_history_csv": str(
                output_root
                / "training_history.csv"
            ),
            "lr_trace_csv": str(
                output_root
                / "lr_trace.csv"
            ),
        },
    }

    (
        output_root
        / "run_manifest.json"
    ).write_text(
        json.dumps(
            run_manifest,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        "FINETUNE COMPLETE; checkpoint:",
        output_root
        / "best_model",
    )

    print(
        "Best epoch:",
        best_epoch,
        "validation macro-F1:",
        round(
            best_score[0],
            4,
        ),
        f"validation {focus_label}-F1:",
        round(
            best_score[1],
            4,
        ),
    )

    print(
        "Scheduler horizon:",
        args.scheduler_epochs,
        "epochs; warmup steps:",
        warmup_steps,
        "/",
        scheduler_total_steps,
    )

    print(
        "Training history:",
        output_root
        / "training_history.csv",
    )

    print(
        "LR trace:",
        output_root
        / "lr_trace.csv",
    )

    print(
        "Test has NOT been read. "
        "Freeze both Stage A and Stage B "
        "before any optional inherited-test comparison."
    )


if __name__ == "__main__":
    main()
