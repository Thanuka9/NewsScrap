from pathlib import Path
import csv
import gc
import json
import sys
from collections import Counter

PROJECT_ROOT = Path.cwd()
SCRIPTS = PROJECT_ROOT / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from run_finbert_two_stage_finetune import encode_article, evaluate

import torch
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
)

LABELS = ("NOISE", "KEEP")
FOCUS_LABEL = "KEEP"

BSD_VALIDATION = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v6_bank_supervision_candidate_two_stage"
    / "stage_a_noise_vs_keep"
    / "validation.csv"
)

OLD_RUN = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "relevance_two_stage"
    / "supervisory_relevance_v4_two_stage"
    / "stage_a_noise_vs_keep"
    / "finbert_lr1e-05_chunks5_sched5_seed42"
)

V7_RUN = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "relevance_two_stage"
    / "supervisory_relevance_v7_augmented_stage_a"
    / "finbert_lr1e-05_chunks5_sched5_seed42"
)

OUT_ROOT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "relevance_two_stage"
    / "comparisons"
    / "frozen_stage_a_vs_v7_on_bsd_validation"
)

OUT_ROOT.mkdir(parents=True, exist_ok=True)


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def safe_text(value):
    return str(value or "").strip()


def divide(a, b):
    return a / b if b else 0.0


def classification_metrics(y_true, y_pred):
    per_class = {}

    for label in LABELS:
        tp = sum(
            1 for t, p in zip(y_true, y_pred)
            if t == label and p == label
        )
        fp = sum(
            1 for t, p in zip(y_true, y_pred)
            if t != label and p == label
        )
        fn = sum(
            1 for t, p in zip(y_true, y_pred)
            if t == label and p != label
        )

        precision = divide(tp, tp + fp)
        recall = divide(tp, tp + fn)
        f1 = (
            divide(2 * precision * recall, precision + recall)
            if precision + recall
            else 0.0
        )

        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": sum(1 for t in y_true if t == label),
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }

    accuracy = divide(
        sum(t == p for t, p in zip(y_true, y_pred)),
        len(y_true),
    )

    macro_f1 = sum(
        per_class[label]["f1"] for label in LABELS
    ) / len(LABELS)

    balanced_accuracy = sum(
        per_class[label]["recall"] for label in LABELS
    ) / len(LABELS)

    confusion = {
        "actual_NOISE_pred_NOISE": sum(
            t == "NOISE" and p == "NOISE"
            for t, p in zip(y_true, y_pred)
        ),
        "actual_NOISE_pred_KEEP": sum(
            t == "NOISE" and p == "KEEP"
            for t, p in zip(y_true, y_pred)
        ),
        "actual_KEEP_pred_NOISE": sum(
            t == "KEEP" and p == "NOISE"
            for t, p in zip(y_true, y_pred)
        ),
        "actual_KEEP_pred_KEEP": sum(
            t == "KEEP" and p == "KEEP"
            for t, p in zip(y_true, y_pred)
        ),
    }

    return {
        "rows": len(y_true),
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "balanced_accuracy": balanced_accuracy,
        "per_class": per_class,
        "confusion_matrix": confusion,
    }


def predict(run_root, rows, name):
    manifest_path = run_root / "run_manifest.json"
    checkpoint = run_root / "best_model"

    required = [
        manifest_path,
        checkpoint / "config.json",
    ]

    for path in required:
        if not path.exists():
            raise FileNotFoundError(path)

    manifest = load_json(manifest_path)

    print()
    print("=" * 100)
    print(f"LOADING {name}")
    print("=" * 100)
    print(f"Run        : {run_root}")
    print(f"Checkpoint : {checkpoint}")

    tokenizer = AutoTokenizer.from_pretrained(
        str(checkpoint),
        local_files_only=True,
    )

    model = AutoModelForSequenceClassification.from_pretrained(
        str(checkpoint),
        local_files_only=True,
    )

    device = torch.device("cpu")
    model.to(device)
    model.eval()

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

    _, predictions = evaluate(
        model,
        items,
        device,
        torch,
        labels=LABELS,
        focus_label=FOCUS_LABEL,
        headline_weight=float(manifest["headline_weight"]),
        body_weight=float(manifest["body_weight"]),
    )

    labels = [
        safe_text(pred["predicted_label"]).upper()
        for pred in predictions
    ]

    del model
    del tokenizer
    gc.collect()

    return labels


print("=" * 100)
print("FROZEN STAGE A vs V7 - BSD VALIDATION COMPARISON")
print("=" * 100)

print(f"BSD validation : {BSD_VALIDATION}")
print("Test files     : WILL NOT BE READ")
print("Device         : CPU")
print()

if not BSD_VALIDATION.exists():
    raise FileNotFoundError(BSD_VALIDATION)

rows = read_csv(BSD_VALIDATION)

if len(rows) != 33:
    raise RuntimeError(
        f"Expected exactly 33 BSD validation rows, found {len(rows)}"
    )

truth = [
    safe_text(row.get("target_relevance_label")).upper()
    for row in rows
]

invalid = sorted(set(truth) - set(LABELS))

if invalid:
    raise RuntimeError(
        f"Unexpected validation labels: {invalid}"
    )

print(f"Rows           : {len(rows)}")
print(f"Truth labels   : {dict(Counter(truth))}")

old_pred = predict(
    OLD_RUN,
    rows,
    "CURRENT FROZEN STAGE A",
)

v7_pred = predict(
    V7_RUN,
    rows,
    "V7 AUGMENTED STAGE A",
)

old_metrics = classification_metrics(
    truth,
    old_pred,
)

v7_metrics = classification_metrics(
    truth,
    v7_pred,
)


def pct(x):
    return f"{x:.4f}"


print()
print("=" * 100)
print("BSD VALIDATION RESULTS")
print("=" * 100)

print(
    f"{'Metric':<24}"
    f"{'Frozen Stage A':>18}"
    f"{'V7':>18}"
    f"{'Delta':>18}"
)

print("-" * 78)

metrics_to_show = [
    ("Accuracy", "accuracy"),
    ("Macro-F1", "macro_f1"),
    ("Balanced accuracy", "balanced_accuracy"),
]

for label, key in metrics_to_show:
    old = old_metrics[key]
    new = v7_metrics[key]

    print(
        f"{label:<24}"
        f"{pct(old):>18}"
        f"{pct(new):>18}"
        f"{pct(new - old):>18}"
    )

for label, cls, metric in [
    ("KEEP precision", "KEEP", "precision"),
    ("KEEP recall", "KEEP", "recall"),
    ("KEEP F1", "KEEP", "f1"),
    ("NOISE precision", "NOISE", "precision"),
    ("NOISE recall", "NOISE", "recall"),
    ("NOISE F1", "NOISE", "f1"),
]:
    old = old_metrics["per_class"][cls][metric]
    new = v7_metrics["per_class"][cls][metric]

    print(
        f"{label:<24}"
        f"{pct(old):>18}"
        f"{pct(new):>18}"
        f"{pct(new - old):>18}"
    )

print()
print("FROZEN CONFUSION MATRIX")
print(json.dumps(
    old_metrics["confusion_matrix"],
    indent=2,
))

print()
print("V7 CONFUSION MATRIX")
print(json.dumps(
    v7_metrics["confusion_matrix"],
    indent=2,
))

comparison_rows = []

categories = Counter()

for i, row in enumerate(rows):
    truth_label = truth[i]
    old_label = old_pred[i]
    v7_label = v7_pred[i]

    old_correct = old_label == truth_label
    v7_correct = v7_label == truth_label

    if old_correct and v7_correct:
        category = "BOTH_CORRECT"
    elif old_correct and not v7_correct:
        category = "OLD_CORRECT_V7_WRONG"
    elif not old_correct and v7_correct:
        category = "OLD_WRONG_V7_CORRECT"
    else:
        category = "BOTH_WRONG"

    categories[category] += 1

    comparison_rows.append({
        "dataset_row_id": safe_text(row.get("dataset_row_id")),
        "story_group_id": safe_text(row.get("story_group_id")),
        "headline": safe_text(row.get("headline")),
        "source_name": safe_text(row.get("source_name")),
        "source_url": safe_text(row.get("source_url")),
        "truth": truth_label,
        "frozen_prediction": old_label,
        "v7_prediction": v7_label,
        "frozen_correct": old_correct,
        "v7_correct": v7_correct,
        "comparison_category": category,
    })

print()
print("=" * 100)
print("ARTICLE-LEVEL COMPARISON")
print("=" * 100)

for category in [
    "OLD_WRONG_V7_CORRECT",
    "OLD_CORRECT_V7_WRONG",
    "BOTH_WRONG",
    "BOTH_CORRECT",
]:
    print(f"{category:<28}: {categories[category]}")

for category in [
    "OLD_WRONG_V7_CORRECT",
    "OLD_CORRECT_V7_WRONG",
    "BOTH_WRONG",
]:
    selected = [
        r for r in comparison_rows
        if r["comparison_category"] == category
    ]

    if not selected:
        continue

    print()
    print("-" * 100)
    print(category)
    print("-" * 100)

    for row in selected:
        headline = row["headline"]

        if len(headline) > 120:
            headline = headline[:117] + "..."

        print(
            f"Truth={row['truth']:<8} "
            f"OLD={row['frozen_prediction']:<8} "
            f"V7={row['v7_prediction']:<8} | "
            f"{headline}"
        )

csv_path = OUT_ROOT / "article_level_comparison.csv"

fields = [
    "dataset_row_id",
    "story_group_id",
    "headline",
    "source_name",
    "source_url",
    "truth",
    "frozen_prediction",
    "v7_prediction",
    "frozen_correct",
    "v7_correct",
    "comparison_category",
]

with csv_path.open(
    "w",
    encoding="utf-8-sig",
    newline="",
) as f:
    writer = csv.DictWriter(
        f,
        fieldnames=fields,
    )
    writer.writeheader()
    writer.writerows(comparison_rows)

summary = {
    "status": "PASS",
    "evaluation_dataset": str(
        BSD_VALIDATION.relative_to(PROJECT_ROOT)
    ),
    "evaluation_rows": len(rows),
    "test_read": False,
    "truth_counts": dict(Counter(truth)),
    "frozen_stage_a": old_metrics,
    "v7_augmented_stage_a": v7_metrics,
    "delta_v7_minus_frozen": {
        "accuracy": (
            v7_metrics["accuracy"]
            - old_metrics["accuracy"]
        ),
        "macro_f1": (
            v7_metrics["macro_f1"]
            - old_metrics["macro_f1"]
        ),
        "balanced_accuracy": (
            v7_metrics["balanced_accuracy"]
            - old_metrics["balanced_accuracy"]
        ),
        "keep_precision": (
            v7_metrics["per_class"]["KEEP"]["precision"]
            - old_metrics["per_class"]["KEEP"]["precision"]
        ),
        "keep_recall": (
            v7_metrics["per_class"]["KEEP"]["recall"]
            - old_metrics["per_class"]["KEEP"]["recall"]
        ),
        "keep_f1": (
            v7_metrics["per_class"]["KEEP"]["f1"]
            - old_metrics["per_class"]["KEEP"]["f1"]
        ),
    },
    "article_categories": dict(categories),
}

json_path = OUT_ROOT / "comparison_summary.json"

with json_path.open(
    "w",
    encoding="utf-8",
) as f:
    json.dump(
        summary,
        f,
        indent=2,
    )

print()
print("=" * 100)
print("COMPARISON COMPLETE")
print("=" * 100)

print(f"CSV     : {csv_path}")
print(f"Summary : {json_path}")
print("Test    : NOT READ")
