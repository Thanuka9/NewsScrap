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
from transformers import AutoModelForSequenceClassification, AutoTokenizer

LABELS = ("NOISE", "KEEP")
FOCUS_LABEL = "KEEP"

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

TESTS = {
    "OLD_GENERAL_TEST": (
        PROJECT_ROOT
        / "data"
        / "training"
        / "datasets"
        / "supervisory_relevance_v4_two_stage"
        / "stage_a_noise_vs_keep"
        / "test.csv"
    ),
    "BSD_TEST": (
        PROJECT_ROOT
        / "data"
        / "training"
        / "datasets"
        / "supervisory_relevance_v6_bank_supervision_candidate_two_stage"
        / "stage_a_noise_vs_keep"
        / "test.csv"
    ),
}

OUT_ROOT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "relevance_two_stage"
    / "comparisons"
    / "frozen_stage_a_vs_v7_final_tests"
)

OUT_ROOT.mkdir(parents=True, exist_ok=True)


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def text(v):
    return str(v or "").strip()


def div(a, b):
    return a / b if b else 0.0


def metrics(y_true, y_pred):
    per_class = {}

    for label in LABELS:
        tp = sum(
            t == label and p == label
            for t, p in zip(y_true, y_pred)
        )
        fp = sum(
            t != label and p == label
            for t, p in zip(y_true, y_pred)
        )
        fn = sum(
            t == label and p != label
            for t, p in zip(y_true, y_pred)
        )

        precision = div(tp, tp + fp)
        recall = div(tp, tp + fn)

        f1 = (
            div(2 * precision * recall, precision + recall)
            if precision + recall
            else 0.0
        )

        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": sum(t == label for t in y_true),
        }

    accuracy = div(
        sum(t == p for t, p in zip(y_true, y_pred)),
        len(y_true),
    )

    macro_f1 = sum(
        per_class[x]["f1"] for x in LABELS
    ) / 2

    balanced_accuracy = sum(
        per_class[x]["recall"] for x in LABELS
    ) / 2

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
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "balanced_accuracy": balanced_accuracy,
        "per_class": per_class,
        "confusion_matrix": confusion,
    }


def predict(run_root, rows, name):
    manifest = load_json(
        run_root / "run_manifest.json"
    )

    checkpoint = run_root / "best_model"

    print(f"Loading {name}")
    print(f"  {checkpoint}")

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

    result = [
        text(p["predicted_label"]).upper()
        for p in predictions
    ]

    del model
    del tokenizer
    gc.collect()

    return result


def p(x):
    return f"{x:.4f}"


all_summary = {}

print("=" * 105)
print("FINAL LOCKED TEST — FROZEN STAGE A vs V7")
print("=" * 105)
print("No training or tuning is performed.")
print("These test results are final evaluation evidence.")
print()

for test_name, path in TESTS.items():

    print()
    print("=" * 105)
    print(test_name)
    print("=" * 105)
    print(f"Dataset : {path}")

    if not path.exists():
        raise FileNotFoundError(path)

    rows = read_csv(path)

    truth = [
        text(r.get("target_relevance_label")).upper()
        for r in rows
    ]

    invalid = set(truth) - set(LABELS)

    if invalid:
        raise RuntimeError(
            f"{test_name}: invalid labels {sorted(invalid)}"
        )

    print(f"Rows    : {len(rows)}")
    print(f"Labels  : {dict(Counter(truth))}")

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

    old = metrics(truth, old_pred)
    new = metrics(truth, v7_pred)

    print()
    print(
        f"{'Metric':<24}"
        f"{'Frozen Stage A':>18}"
        f"{'V7':>18}"
        f"{'Delta':>18}"
    )
    print("-" * 78)

    values = [
        ("Accuracy", old["accuracy"], new["accuracy"]),
        ("Macro-F1", old["macro_f1"], new["macro_f1"]),
        (
            "Balanced accuracy",
            old["balanced_accuracy"],
            new["balanced_accuracy"],
        ),
        (
            "KEEP precision",
            old["per_class"]["KEEP"]["precision"],
            new["per_class"]["KEEP"]["precision"],
        ),
        (
            "KEEP recall",
            old["per_class"]["KEEP"]["recall"],
            new["per_class"]["KEEP"]["recall"],
        ),
        (
            "KEEP F1",
            old["per_class"]["KEEP"]["f1"],
            new["per_class"]["KEEP"]["f1"],
        ),
        (
            "NOISE recall",
            old["per_class"]["NOISE"]["recall"],
            new["per_class"]["NOISE"]["recall"],
        ),
        (
            "NOISE F1",
            old["per_class"]["NOISE"]["f1"],
            new["per_class"]["NOISE"]["f1"],
        ),
    ]

    for label, a, b in values:
        print(
            f"{label:<24}"
            f"{p(a):>18}"
            f"{p(b):>18}"
            f"{p(b-a):>18}"
        )

    print()
    print("Frozen confusion:")
    print(json.dumps(
        old["confusion_matrix"],
        indent=2,
    ))

    print("V7 confusion:")
    print(json.dumps(
        new["confusion_matrix"],
        indent=2,
    ))

    article_rows = []
    categories = Counter()

    for i, row in enumerate(rows):
        actual = truth[i]
        op = old_pred[i]
        np = v7_pred[i]

        oc = op == actual
        nc = np == actual

        if oc and nc:
            category = "BOTH_CORRECT"
        elif oc and not nc:
            category = "OLD_CORRECT_V7_WRONG"
        elif not oc and nc:
            category = "OLD_WRONG_V7_CORRECT"
        else:
            category = "BOTH_WRONG"

        categories[category] += 1

        article_rows.append({
            "dataset": test_name,
            "dataset_row_id": text(
                row.get("dataset_row_id")
            ),
            "story_group_id": text(
                row.get("story_group_id")
            ),
            "headline": text(
                row.get("headline")
            ),
            "source_name": text(
                row.get("source_name")
            ),
            "source_url": text(
                row.get("source_url")
            ),
            "truth": actual,
            "frozen_prediction": op,
            "v7_prediction": np,
            "comparison_category": category,
        })

    print()
    print("ARTICLE COMPARISON")
    print(
        "OLD_WRONG_V7_CORRECT :",
        categories["OLD_WRONG_V7_CORRECT"],
    )
    print(
        "OLD_CORRECT_V7_WRONG :",
        categories["OLD_CORRECT_V7_WRONG"],
    )
    print(
        "BOTH_WRONG           :",
        categories["BOTH_WRONG"],
    )
    print(
        "BOTH_CORRECT         :",
        categories["BOTH_CORRECT"],
    )

    for category in [
        "OLD_WRONG_V7_CORRECT",
        "OLD_CORRECT_V7_WRONG",
        "BOTH_WRONG",
    ]:
        selected = [
            x for x in article_rows
            if x["comparison_category"] == category
        ]

        if not selected:
            continue

        print()
        print("-" * 105)
        print(category)
        print("-" * 105)

        for x in selected:
            headline = x["headline"]

            if len(headline) > 125:
                headline = headline[:122] + "..."

            print(
                f"Truth={x['truth']:<8} "
                f"OLD={x['frozen_prediction']:<8} "
                f"V7={x['v7_prediction']:<8} | "
                f"{headline}"
            )

    csv_path = (
        OUT_ROOT
        / f"{test_name.lower()}_article_comparison.csv"
    )

    with csv_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        fields = list(article_rows[0].keys())

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()
        writer.writerows(article_rows)

    all_summary[test_name] = {
        "dataset": str(
            path.relative_to(PROJECT_ROOT)
        ),
        "rows": len(rows),
        "truth_counts": dict(Counter(truth)),
        "frozen_stage_a": old,
        "v7_stage_a": new,
        "delta_v7_minus_frozen": {
            "accuracy": (
                new["accuracy"] - old["accuracy"]
            ),
            "macro_f1": (
                new["macro_f1"] - old["macro_f1"]
            ),
            "balanced_accuracy": (
                new["balanced_accuracy"]
                - old["balanced_accuracy"]
            ),
            "keep_precision": (
                new["per_class"]["KEEP"]["precision"]
                - old["per_class"]["KEEP"]["precision"]
            ),
            "keep_recall": (
                new["per_class"]["KEEP"]["recall"]
                - old["per_class"]["KEEP"]["recall"]
            ),
            "keep_f1": (
                new["per_class"]["KEEP"]["f1"]
                - old["per_class"]["KEEP"]["f1"]
            ),
        },
        "article_categories": dict(categories),
    }


summary_path = OUT_ROOT / "final_test_comparison.json"

with summary_path.open(
    "w",
    encoding="utf-8",
) as f:
    json.dump(
        {
            "status": "FINAL_TEST_COMPLETE",
            "policy": (
                "Locked one-time final evaluation. "
                "Do not tune models using these test results."
            ),
            "results": all_summary,
        },
        f,
        indent=2,
    )

print()
print("=" * 105)
print("FINAL TEST COMPARISON COMPLETE")
print("=" * 105)
print(f"Output  : {OUT_ROOT}")
print(f"Summary : {summary_path}")
print()
print("IMPORTANT: Do not retrain/tune v7 based on these test results.")
