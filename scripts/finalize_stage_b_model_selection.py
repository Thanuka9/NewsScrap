
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
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

HOLDOUT = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v6_stage_b_hard_positive"
    / "fresh_boundary_holdout.csv"
)

MODEL_RUNS = {
    "BASELINE_V4": PROJECT_ROOT / "data" / "processed" / "relevance_two_stage"
    / "supervisory_relevance_v4_two_stage" / "stage_b_watch_vs_relevant"
    / "finbert_lr1e-05_chunks5_sched5_seed42",

    "TARGETED_V5": PROJECT_ROOT / "data" / "processed" / "relevance_two_stage"
    / "supervisory_relevance_v5_stage_b_targeted" / "stage_b_watch_vs_relevant"
    / "finbert_lr1e-05_chunks5_sched5_seed42",

    "HARD_POSITIVE_V6": PROJECT_ROOT / "data" / "processed" / "relevance_two_stage"
    / "supervisory_relevance_v6_stage_b_hard_positive" / "stage_b_watch_vs_relevant"
    / "finbert_lr1e-05_chunks5_sched5_seed42",
}

OUTPUT_BASE = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "stage_b_final_model_selection"
)


def norm(x: Any) -> str:
    return str(x or "").strip()


def read_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_manifest(run_root: Path):
    path = run_root / "run_manifest.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def signature(manifest):
    return {
        "label_order": manifest.get("label_order"),
        "max_length": manifest.get("max_length"),
        "stride": manifest.get("stride"),
        "max_body_chunks": manifest.get("max_body_chunks"),
        "headline_weight": manifest.get("headline_weight"),
        "body_weight": manifest.get("body_weight"),
    }


def validate_holdout(rows):
    if len(rows) != 20:
        raise RuntimeError(f"Expected 20 holdout rows, found {len(rows)}")

    counts = {"WATCH": 0, "RELEVANT": 0}
    for i, row in enumerate(rows, start=1):
        label = norm(row.get("target_relevance_label")).upper()
        if label not in LABELS:
            raise RuntimeError(f"row {i}: invalid label {label}")
        counts[label] += 1

    if counts != {"WATCH": 10, "RELEVANT": 10}:
        raise RuntimeError(f"Expected 10/10 holdout, found {counts}")


def run_one(
    run_root,
    manifest,
    rows,
    torch,
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


def winner_key(metrics):
    # PREDECLARED before holdout evaluation.
    return (
        float(metrics["macro_f1"]),
        float(metrics["focus_f1"]),
        float(metrics["balanced_accuracy"]),
        float(metrics["accuracy"]),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--holdout",
        type=Path,
        default=HOLDOUT,
    )
    parser.add_argument(
        "--output-base",
        type=Path,
        default=OUTPUT_BASE,
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda", "mps"),
        default="auto",
    )
    args = parser.parse_args()

    holdout = args.holdout if args.holdout.is_absolute() else PROJECT_ROOT / args.holdout
    output_base = args.output_base if args.output_base.is_absolute() else PROJECT_ROOT / args.output_base

    rows = read_csv(holdout)
    validate_holdout(rows)

    manifests = {name: load_manifest(path) for name, path in MODEL_RUNS.items()}
    sigs = {name: signature(m) for name, m in manifests.items()}

    if len({json.dumps(v, sort_keys=True) for v in sigs.values()}) != 1:
        raise RuntimeError(
            "Preprocessing mismatch:\n" + json.dumps(sigs, indent=2)
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

    print("=" * 104)
    print("FINAL STAGE-B MODEL SELECTION — FRESH 20-ROW HOLDOUT")
    print("=" * 104)
    print("Holdout rows        : 20")
    print("WATCH / RELEVANT    : 10 / 10")
    print("Inherited test read : 0")
    print("Training            : 0")
    print("Threshold tuning    : 0")
    print("Winner rule         : macro-F1 > RELEVANT-F1 > balanced accuracy > accuracy")
    print()

    metrics_by_model = {}
    preds_by_model = {}

    for name, run_root in MODEL_RUNS.items():
        metrics, preds = run_one(
            run_root,
            manifests[name],
            rows,
            torch,
            AutoModelForSequenceClassification,
            AutoTokenizer,
            device,
        )
        metrics_by_model[name] = metrics
        preds_by_model[name] = {
            p["dataset_row_id"]: p
            for p in preds
        }

    ranking = sorted(
        metrics_by_model.keys(),
        key=lambda n: winner_key(metrics_by_model[n]),
        reverse=True,
    )
    selected = ranking[0]

    ts = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    out = output_base / ts
    out.mkdir(parents=True, exist_ok=False)

    metric_rows = []
    for rank, name in enumerate(ranking, start=1):
        m = metrics_by_model[name]
        metric_rows.append(
            {
                "rank": rank,
                "model": name,
                "accuracy": m["accuracy"],
                "balanced_accuracy": m["balanced_accuracy"],
                "macro_f1": m["macro_f1"],
                "relevant_precision": m["focus_precision"],
                "relevant_recall": m["focus_recall"],
                "relevant_f1": m["focus_f1"],
                "confusion_matrix": json.dumps(m["confusion_matrix"]),
                "best_epoch": manifests[name].get("best_epoch"),
                "run_root": str(MODEL_RUNS[name]),
            }
        )

    write_csv(
        out / "model_metrics.csv",
        metric_rows,
        [
            "rank","model","accuracy","balanced_accuracy","macro_f1",
            "relevant_precision","relevant_recall","relevant_f1",
            "confusion_matrix","best_epoch","run_root"
        ],
    )

    article_rows = []
    for src in rows:
        row_id = src["dataset_row_id"]
        r = {
            "dataset_row_id": row_id,
            "augmentation_review_id": src.get("augmentation_review_id",""),
            "true_label": src["target_relevance_label"],
            "headline": src["headline"],
            "source_url": src["source_url"],
        }

        for name in MODEL_RUNS:
            p = preds_by_model[name][row_id]
            prefix = name.lower()
            r[f"{prefix}_prediction"] = p["predicted_label"]
            r[f"{prefix}_correct"] = p["correct"]
            r[f"{prefix}_p_watch"] = p.get("p_watch","")
            r[f"{prefix}_p_relevant"] = p.get("p_relevant","")

        article_rows.append(r)

    article_fields = [
        "dataset_row_id","augmentation_review_id","true_label"
    ]
    for name in MODEL_RUNS:
        prefix = name.lower()
        article_fields += [
            f"{prefix}_prediction",
            f"{prefix}_correct",
            f"{prefix}_p_watch",
            f"{prefix}_p_relevant",
        ]
    article_fields += ["headline","source_url"]

    write_csv(
        out / "article_predictions.csv",
        article_rows,
        article_fields,
    )

    summary = {
        "status": "PASS",
        "holdout_rows": 20,
        "holdout_counts": {"WATCH":10, "RELEVANT":10},
        "inherited_test_rows_read": 0,
        "training_performed": False,
        "threshold_tuning_performed": False,
        "selection_rule": [
            "macro_f1",
            "relevant_f1",
            "balanced_accuracy",
            "accuracy",
        ],
        "ranking": ranking,
        "selected_candidate": selected,
        "metrics": metrics_by_model,
        "methodology_note": (
            "This 20-row holdout is used once for Stage-B model selection. "
            "After this run it is inspected and must not be used for further tuning."
        ),
    }

    (out / "final_model_selection.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    print("MODEL RANKING")
    print("-" * 104)
    for rank, name in enumerate(ranking, start=1):
        m = metrics_by_model[name]
        print(
            f"{rank}. {name:<18} "
            f"acc={m['accuracy']:.4f} "
            f"bal_acc={m['balanced_accuracy']:.4f} "
            f"macroF1={m['macro_f1']:.4f} "
            f"R_prec={m['focus_precision']:.4f} "
            f"R_rec={m['focus_recall']:.4f} "
            f"R_F1={m['focus_f1']:.4f}"
        )
        print(f"   confusion={m['confusion_matrix']}")

    print()
    print(f"SELECTED STAGE-B CANDIDATE : {selected}")
    print("Inherited test.csv was NOT read.")
    print("This 20-row holdout is now INSPECTED after this run.")
    print(f"Output: {out}")


if __name__ == "__main__":
    main()
