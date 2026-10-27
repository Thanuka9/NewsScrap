from __future__ import annotations

"""
FINAL frozen two-stage relevance evaluation.

Purpose
-------
Document the final frozen Stage-A and selected Stage-B checkpoints on their
inherited test splits AFTER model selection is complete.

This script:
- performs NO training
- performs NO threshold tuning
- performs NO model selection
- reads each inherited test split once for final documentation
- uses the exact trainer encode_article/evaluate functions
- writes metrics, predictions, hashes, and a final evaluation manifest

IMPORTANT:
The test results are documentation only. They MUST NOT be used to reopen
hyperparameter/model selection.
"""

import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from run_finbert_two_stage_finetune import (  # noqa: E402
    encode_article,
    evaluate,
    load_dependencies,
    resolve_device,
)

TZ = ZoneInfo("Asia/Colombo")

STAGE_A_DATASET = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v4_two_stage"
    / "stage_a_noise_vs_keep"
)

STAGE_A_RUN = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "relevance_two_stage"
    / "supervisory_relevance_v4_two_stage"
    / "stage_a_noise_vs_keep"
    / "finbert_lr1e-05_chunks5_sched5_seed42"
)

STAGE_B_DATASET = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v5_stage_b_targeted"
)

STAGE_B_RUN = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "relevance_two_stage"
    / "supervisory_relevance_v5_stage_b_targeted"
    / "stage_b_watch_vs_relevant"
    / "finbert_lr1e-05_chunks5_sched5_seed42"
)

FINAL_SELECTION_JSON = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "stage_b_final_model_selection"
    / "20261012_115346"
    / "final_model_selection.json"
)

OUTPUT_BASE = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "final_two_stage_model_evaluation"
)

TASKS = {
    "STAGE_A": {
        "dataset_root": STAGE_A_DATASET,
        "run_root": STAGE_A_RUN,
        "labels": ("NOISE", "KEEP"),
        "focus_label": "KEEP",
    },
    "STAGE_B": {
        "dataset_root": STAGE_B_DATASET,
        "run_root": STAGE_B_RUN,
        "labels": ("WATCH", "RELEVANT"),
        "focus_label": "RELEVANT",
    },
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)

    return json.loads(path.read_text(encoding="utf-8"))


def validate_selection_record() -> dict[str, Any]:
    selection = load_json(FINAL_SELECTION_JSON)

    selected = selection.get("selected_candidate")

    if selected != "TARGETED_V5":
        raise RuntimeError(
            "Final Stage-B selection record does not select TARGETED_V5: "
            f"{selected!r}"
        )

    return selection


def validate_test_rows(
    rows: list[dict[str, str]],
    labels: tuple[str, str],
    task_name: str,
) -> dict[str, int]:
    if not rows:
        raise RuntimeError(f"{task_name}: empty test split")

    counts = {label: 0 for label in labels}

    for index, row in enumerate(rows, start=1):
        label = str(
            row.get("target_relevance_label")
            or row.get("label")
            or ""
        ).strip().upper()

        if label not in labels:
            raise RuntimeError(
                f"{task_name} test row {index}: invalid label {label!r}"
            )

        counts[label] += 1

        if not str(row.get("headline") or "").strip():
            raise RuntimeError(
                f"{task_name} test row {index}: missing headline"
            )

        if not str(row.get("article_text") or "").strip():
            raise RuntimeError(
                f"{task_name} test row {index}: missing article_text"
            )

    return counts


def load_checkpoint(
    run_root: Path,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    device,
):
    checkpoint = run_root / "best_model"

    if not (checkpoint / "config.json").is_file():
        raise FileNotFoundError(
            f"Missing checkpoint: {checkpoint}"
        )

    tokenizer = AutoTokenizer.from_pretrained(
        str(checkpoint),
        local_files_only=True,
    )

    model = AutoModelForSequenceClassification.from_pretrained(
        str(checkpoint),
        local_files_only=True,
    )

    model.to(device)

    return model, tokenizer


def evaluate_task(
    *,
    task_name: str,
    dataset_root: Path,
    run_root: Path,
    labels: tuple[str, str],
    focus_label: str,
    torch,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    device,
):
    test_path = dataset_root / "test.csv"
    manifest_path = run_root / "run_manifest.json"

    if not test_path.is_file():
        raise FileNotFoundError(test_path)

    manifest = load_json(manifest_path)
    test_sha256 = sha256_file(test_path)
    rows = read_csv(test_path)

    label_counts = validate_test_rows(
        rows,
        labels,
        task_name,
    )

    model, tokenizer = load_checkpoint(
        run_root,
        AutoModelForSequenceClassification,
        AutoTokenizer,
        device,
    )

    items = [
        encode_article(
            tokenizer,
            row,
            max_length=int(manifest["max_length"]),
            stride=int(manifest["stride"]),
            max_body_chunks=int(manifest["max_body_chunks"]),
        )
        for row in rows
    ]

    metrics, predictions = evaluate(
        model,
        items,
        device,
        torch,
        labels=labels,
        focus_label=focus_label,
        headline_weight=float(manifest["headline_weight"]),
        body_weight=float(manifest["body_weight"]),
    )

    return {
        "task_name": task_name,
        "dataset_root": str(dataset_root),
        "run_root": str(run_root),
        "checkpoint": str(run_root / "best_model"),
        "test_path": str(test_path),
        "test_sha256": test_sha256,
        "test_rows": len(rows),
        "test_label_counts": label_counts,
        "labels": list(labels),
        "focus_label": focus_label,
        "best_epoch": manifest.get("best_epoch"),
        "metrics": metrics,
        "predictions": predictions,
        "preprocessing": {
            "max_length": manifest.get("max_length"),
            "stride": manifest.get("stride"),
            "max_body_chunks": manifest.get("max_body_chunks"),
            "headline_weight": manifest.get("headline_weight"),
            "body_weight": manifest.get("body_weight"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Final documentation-only evaluation of frozen Stage-A and "
            "selected Stage-B checkpoints."
        )
    )

    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda", "mps"),
        default="auto",
    )

    args = parser.parse_args()

    selection = validate_selection_record()

    (
        np,
        torch,
        AutoConfig,
        AutoModelForSequenceClassification,
        AutoTokenizer,
        get_linear_schedule_with_warmup,
        set_seed,
    ) = load_dependencies()

    device = resolve_device(
        torch,
        args.device,
    )

    print("=" * 104)
    print("FINAL FROZEN TWO-STAGE RELEVANCE EVALUATION")
    print("=" * 104)
    print("Training               : NO")
    print("Threshold tuning       : NO")
    print("Model selection        : ALREADY FROZEN")
    print("Selected Stage B       : TARGETED_V5")
    print("Evaluation purpose     : FINAL DOCUMENTATION ONLY")
    print(f"Device                 : {device}")
    print()

    results = {}

    for task_name, spec in TASKS.items():
        result = evaluate_task(
            task_name=task_name,
            dataset_root=spec["dataset_root"],
            run_root=spec["run_root"],
            labels=spec["labels"],
            focus_label=spec["focus_label"],
            torch=torch,
            AutoModelForSequenceClassification=AutoModelForSequenceClassification,
            AutoTokenizer=AutoTokenizer,
            device=device,
        )

        results[task_name] = result

    timestamp = datetime.now(TZ).strftime(
        "%Y%m%d_%H%M%S"
    )

    output_root = OUTPUT_BASE / timestamp
    output_root.mkdir(
        parents=True,
        exist_ok=False,
    )

    summary_rows = []

    for task_name, result in results.items():
        m = result["metrics"]

        summary_rows.append(
            {
                "task": task_name,
                "test_rows": result["test_rows"],
                "accuracy": m["accuracy"],
                "balanced_accuracy": m["balanced_accuracy"],
                "macro_f1": m["macro_f1"],
                "focus_label": result["focus_label"],
                "focus_precision": m["focus_precision"],
                "focus_recall": m["focus_recall"],
                "focus_f1": m["focus_f1"],
                "confusion_matrix": json.dumps(
                    m["confusion_matrix"]
                ),
                "best_epoch": result["best_epoch"],
                "test_sha256": result["test_sha256"],
                "checkpoint": result["checkpoint"],
            }
        )

        predictions = result["predictions"]

        if predictions:
            write_csv(
                output_root
                / f"{task_name.lower()}_test_predictions.csv",
                predictions,
                list(predictions[0].keys()),
            )

    write_csv(
        output_root / "final_test_metrics.csv",
        summary_rows,
        [
            "task",
            "test_rows",
            "accuracy",
            "balanced_accuracy",
            "macro_f1",
            "focus_label",
            "focus_precision",
            "focus_recall",
            "focus_f1",
            "confusion_matrix",
            "best_epoch",
            "test_sha256",
            "checkpoint",
        ],
    )

    final_manifest = {
        "status": "PASS",
        "created_at": datetime.now(
            TZ
        ).isoformat(),
        "training_performed": False,
        "threshold_tuning_performed": False,
        "model_selection_reopened": False,
        "stage_b_selection_record": str(
            FINAL_SELECTION_JSON
        ),
        "stage_b_selected_candidate": (
            selection["selected_candidate"]
        ),
        "stage_a": {
            key: value
            for key, value in results["STAGE_A"].items()
            if key != "predictions"
        },
        "stage_b": {
            key: value
            for key, value in results["STAGE_B"].items()
            if key != "predictions"
        },
        "methodology_note": (
            "Inherited test splits were evaluated only after Stage-B model "
            "selection had been frozen. These results are documentation only "
            "and must not be used to reopen tuning or model selection."
        ),
        "production_status": (
            "FROZEN_MODEL_CANDIDATES_PENDING_PRODUCTION_INFERENCE_PARITY_QA"
        ),
    }

    (
        output_root
        / "final_frozen_model_evaluation.json"
    ).write_text(
        json.dumps(
            final_manifest,
            indent=2,
        ),
        encoding="utf-8",
    )

    for task_name in ("STAGE_A", "STAGE_B"):
        result = results[task_name]
        m = result["metrics"]

        print(task_name)
        print(
            f"  test rows          : {result['test_rows']}"
        )
        print(
            f"  label counts       : {result['test_label_counts']}"
        )
        print(
            f"  accuracy           : {m['accuracy']:.4f}"
        )
        print(
            f"  balanced accuracy  : {m['balanced_accuracy']:.4f}"
        )
        print(
            f"  macro-F1           : {m['macro_f1']:.4f}"
        )
        print(
            f"  {result['focus_label']} precision"
            f" : {m['focus_precision']:.4f}"
        )
        print(
            f"  {result['focus_label']} recall"
            f"    : {m['focus_recall']:.4f}"
        )
        print(
            f"  {result['focus_label']} F1"
            f"        : {m['focus_f1']:.4f}"
        )
        print(
            f"  confusion          : {m['confusion_matrix']}"
        )
        print(
            f"  best epoch         : {result['best_epoch']}"
        )
        print()

    print("FINAL MODEL STATUS")
    print("  Stage A            : FROZEN")
    print("  Stage B            : TARGETED_V5 — FROZEN")
    print(
        "  Next gate          : production inference parity QA"
    )
    print()
    print(
        "Do NOT use these test results to reopen model selection."
    )
    print(f"Output: {output_root}")


if __name__ == "__main__":
    main()
