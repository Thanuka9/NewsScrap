from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from run_finbert_two_stage_finetune import (
    encode_article,
    evaluate,
    load_dependencies,
    resolve_device,
)

LABELS = ("WATCH", "RELEVANT")
FOCUS_LABEL = "RELEVANT"
TZ = ZoneInfo("Asia/Colombo")

DEFAULT_HOLDOUT = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v5_stage_b_targeted"
    / "boundary_holdout.csv"
)

DEFAULT_BASELINE = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "relevance_two_stage"
    / "supervisory_relevance_v4_two_stage"
    / "stage_b_watch_vs_relevant"
    / "finbert_lr1e-05_chunks5_sched5_seed42"
)

DEFAULT_TARGETED = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "relevance_two_stage"
    / "supervisory_relevance_v5_stage_b_targeted"
    / "stage_b_watch_vs_relevant"
    / "finbert_lr1e-05_chunks5_sched5_seed42"
)

DEFAULT_OUTPUT_BASE = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "stage_b_model_comparison"
)


def norm(value: Any) -> str:
    return str(value or "").strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def load_manifest(run_root: Path) -> dict[str, Any]:
    path = run_root / "run_manifest.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def validate_holdout(rows: list[dict[str, str]]) -> None:
    if len(rows) != 16:
        raise RuntimeError(f"Expected 16 holdout rows; found {len(rows)}")

    counts = {"WATCH": 0, "RELEVANT": 0}

    for i, row in enumerate(rows, start=1):
        label = norm(row.get("target_relevance_label")).upper()
        if label not in LABELS:
            raise RuntimeError(f"Holdout row {i}: invalid label {label!r}")
        counts[label] += 1

        for field in (
            "dataset_row_id",
            "headline",
            "article_text",
            "source_url",
            "published_at",
        ):
            if not norm(row.get(field)):
                raise RuntimeError(f"Holdout row {i}: blank {field}")

    if counts != {"WATCH": 8, "RELEVANT": 8}:
        raise RuntimeError(f"Expected 8 WATCH + 8 RELEVANT; found {counts}")


def preprocessing_signature(manifest: dict[str, Any]) -> dict[str, Any]:
    sig = {
        "label_order": manifest.get("label_order"),
        "max_length": manifest.get("max_length"),
        "stride": manifest.get("stride"),
        "max_body_chunks": manifest.get("max_body_chunks"),
        "headline_weight": manifest.get("headline_weight"),
        "body_weight": manifest.get("body_weight"),
    }
    return sig


def exact_pvalue(wins: int, losses: int) -> float | None:
    n = wins + losses
    if n == 0:
        return None

    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def load_local_checkpoint(
    run_root: Path,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    device,
):
    checkpoint = run_root / "best_model"

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


def run_evaluation(
    run_root: Path,
    manifest: dict[str, Any],
    rows: list[dict[str, str]],
    torch,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    device,
):
    model, tokenizer = load_local_checkpoint(
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

    return evaluate(
        model,
        items,
        device,
        torch,
        labels=LABELS,
        focus_label=FOCUS_LABEL,
        headline_weight=float(manifest["headline_weight"]),
        body_weight=float(manifest["body_weight"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Stage-B baseline and targeted-v2 on the fresh "
            "16-row boundary holdout only. test.csv is never read."
        )
    )
    parser.add_argument("--holdout", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--baseline-run", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--targeted-run", type=Path, default=DEFAULT_TARGETED)
    parser.add_argument("--output-base", type=Path, default=DEFAULT_OUTPUT_BASE)
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda", "mps"),
        default="auto",
    )
    args = parser.parse_args()

    holdout = args.holdout if args.holdout.is_absolute() else PROJECT_ROOT / args.holdout
    baseline = (
        args.baseline_run
        if args.baseline_run.is_absolute()
        else PROJECT_ROOT / args.baseline_run
    )
    targeted = (
        args.targeted_run
        if args.targeted_run.is_absolute()
        else PROJECT_ROOT / args.targeted_run
    )
    output_base = (
        args.output_base
        if args.output_base.is_absolute()
        else PROJECT_ROOT / args.output_base
    )

    rows = read_csv(holdout)
    validate_holdout(rows)

    baseline_manifest = load_manifest(baseline)
    targeted_manifest = load_manifest(targeted)

    baseline_sig = preprocessing_signature(baseline_manifest)
    targeted_sig = preprocessing_signature(targeted_manifest)

    if baseline_sig != targeted_sig:
        raise RuntimeError(
            "Preprocessing mismatch between baseline and targeted model:\n"
            + json.dumps(
                {
                    "baseline": baseline_sig,
                    "targeted": targeted_sig,
                },
                indent=2,
            )
        )

    (
        np,
        torch,
        AutoConfig,
        AutoModelForSequenceClassification,
        AutoTokenizer,
        get_linear_schedule_with_warmup,
        set_seed,
    ) = load_dependencies()

    device = resolve_device(torch, args.device)

    baseline_metrics, baseline_predictions = run_evaluation(
        baseline,
        baseline_manifest,
        rows,
        torch,
        AutoModelForSequenceClassification,
        AutoTokenizer,
        device,
    )

    targeted_metrics, targeted_predictions = run_evaluation(
        targeted,
        targeted_manifest,
        rows,
        torch,
        AutoModelForSequenceClassification,
        AutoTokenizer,
        device,
    )

    old_by_id = {row["dataset_row_id"]: row for row in baseline_predictions}
    new_by_id = {row["dataset_row_id"]: row for row in targeted_predictions}

    comparison_rows = []
    counts = {
        "both_correct": 0,
        "rescued_by_targeted_v2": 0,
        "regressed_in_targeted_v2": 0,
        "both_wrong": 0,
    }

    for source in rows:
        row_id = source["dataset_row_id"]
        old = old_by_id[row_id]
        new = new_by_id[row_id]

        old_ok = bool(old["correct"])
        new_ok = bool(new["correct"])

        if old_ok and new_ok:
            status = "BOTH_CORRECT"
            counts["both_correct"] += 1
        elif (not old_ok) and new_ok:
            status = "RESCUED_BY_TARGETED_V2"
            counts["rescued_by_targeted_v2"] += 1
        elif old_ok and (not new_ok):
            status = "REGRESSED_IN_TARGETED_V2"
            counts["regressed_in_targeted_v2"] += 1
        else:
            status = "BOTH_WRONG"
            counts["both_wrong"] += 1

        comparison_rows.append(
            {
                "dataset_row_id": row_id,
                "augmentation_review_id": source.get("augmentation_review_id", ""),
                "true_label": source["target_relevance_label"],
                "baseline_prediction": old["predicted_label"],
                "baseline_p_watch": old.get("p_watch", ""),
                "baseline_p_relevant": old.get("p_relevant", ""),
                "targeted_prediction": new["predicted_label"],
                "targeted_p_watch": new.get("p_watch", ""),
                "targeted_p_relevant": new.get("p_relevant", ""),
                "comparison_status": status,
                "headline": source["headline"],
                "source_url": source["source_url"],
            }
        )

    pvalue = exact_pvalue(
        counts["rescued_by_targeted_v2"],
        counts["regressed_in_targeted_v2"],
    )

    timestamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    output_root = output_base / timestamp
    output_root.mkdir(parents=True, exist_ok=False)

    write_csv(
        output_root / "article_comparison.csv",
        comparison_rows,
        [
            "dataset_row_id",
            "augmentation_review_id",
            "true_label",
            "baseline_prediction",
            "baseline_p_watch",
            "baseline_p_relevant",
            "targeted_prediction",
            "targeted_p_watch",
            "targeted_p_relevant",
            "comparison_status",
            "headline",
            "source_url",
        ],
    )

    metric_keys = (
        "accuracy",
        "balanced_accuracy",
        "macro_f1",
        "focus_f1",
        "focus_precision",
        "focus_recall",
    )

    metric_comparison = {}
    for key in metric_keys:
        metric_comparison[key] = {
            "baseline": float(baseline_metrics[key]),
            "targeted_v2": float(targeted_metrics[key]),
            "absolute_change": float(
                targeted_metrics[key] - baseline_metrics[key]
            ),
        }

    summary = {
        "status": "PASS",
        "holdout_rows": 16,
        "holdout_counts": {"WATCH": 8, "RELEVANT": 8},
        "inherited_test_rows_read": 0,
        "baseline_best_epoch": baseline_manifest.get("best_epoch"),
        "targeted_best_epoch": targeted_manifest.get("best_epoch"),
        "preprocessing_signature": baseline_sig,
        "baseline_metrics": baseline_metrics,
        "targeted_metrics": targeted_metrics,
        "metric_comparison": metric_comparison,
        "paired_outcomes": {
            **counts,
            "discordant_pairs": (
                counts["rescued_by_targeted_v2"]
                + counts["regressed_in_targeted_v2"]
            ),
            "exact_two_sided_binomial_pvalue": pvalue,
            "note": (
                "Only 16 rows: treat the p-value as descriptive, "
                "not definitive statistical proof."
            ),
        },
    }

    (output_root / "comparison_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    print("=" * 100)
    print("STAGE-B FRESH BOUNDARY HOLDOUT COMPARISON")
    print("=" * 100)
    print("Holdout rows       : 16")
    print("WATCH / RELEVANT   : 8 / 8")
    print("Inherited test read: 0")
    print()

    print("BASELINE")
    print(f"  Accuracy           : {baseline_metrics['accuracy']:.4f}")
    print(f"  Balanced accuracy  : {baseline_metrics['balanced_accuracy']:.4f}")
    print(f"  Macro-F1           : {baseline_metrics['macro_f1']:.4f}")
    print(f"  RELEVANT F1        : {baseline_metrics['focus_f1']:.4f}")
    print(f"  RELEVANT precision : {baseline_metrics['focus_precision']:.4f}")
    print(f"  RELEVANT recall    : {baseline_metrics['focus_recall']:.4f}")
    print(f"  Confusion matrix   : {baseline_metrics['confusion_matrix']}")
    print()

    print("TARGETED V2")
    print(f"  Accuracy           : {targeted_metrics['accuracy']:.4f}")
    print(f"  Balanced accuracy  : {targeted_metrics['balanced_accuracy']:.4f}")
    print(f"  Macro-F1           : {targeted_metrics['macro_f1']:.4f}")
    print(f"  RELEVANT F1        : {targeted_metrics['focus_f1']:.4f}")
    print(f"  RELEVANT precision : {targeted_metrics['focus_precision']:.4f}")
    print(f"  RELEVANT recall    : {targeted_metrics['focus_recall']:.4f}")
    print(f"  Confusion matrix   : {targeted_metrics['confusion_matrix']}")
    print()

    print("PAIRED ARTICLE OUTCOMES")
    print(f"  Both correct       : {counts['both_correct']}")
    print(f"  Rescued by v2      : {counts['rescued_by_targeted_v2']}")
    print(f"  Regressed in v2    : {counts['regressed_in_targeted_v2']}")
    print(f"  Both wrong         : {counts['both_wrong']}")
    print(f"  Exact paired p     : {pvalue if pvalue is not None else 'N/A'}")
    print()
    print("Inherited test.csv was NOT read.")
    print(f"Output: {output_root}")


if __name__ == "__main__":
    main()
