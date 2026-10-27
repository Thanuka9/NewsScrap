from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_STAGE_B = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v4_two_stage"
    / "stage_b_watch_vs_relevant"
)

DEFAULT_PREDICTIONS = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "relevance_two_stage"
    / "supervisory_relevance_v4_two_stage"
    / "stage_b_watch_vs_relevant"
    / "finbert_lr1e-05_chunks5_sched5_seed42"
    / "best_validation_predictions.csv"
)

DEFAULT_OUTPUT_BASE = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "stage_b_boundary_audit"
)

TZ = ZoneInfo("Asia/Colombo")
VALID_LABELS = {"WATCH", "RELEVANT"}

CATEGORY_RULES = {
    "INSURANCE_ONLY": [
        r"\binsurance\b",
        r"\binsurer\b",
        r"\blife insurance\b",
        r"\bgeneral insurance\b",
        r"\bassurance\b",
    ],
    "AML_CFT": [
        r"\baml\b",
        r"\bcft\b",
        r"money laundering",
        r"terrorist financing",
        r"terrorism financing",
        r"financial intelligence unit",
        r"\bfiu\b",
        r"proceeds of crime",
        r"sanctions screening",
        r"beneficial ownership",
    ],
    "GOVERNANCE_CYBER_PDPA": [
        r"\bcyber\b",
        r"cybersecurity",
        r"data protection",
        r"\bpdpa\b",
        r"personal data protection",
        r"board governance",
        r"corporate governance",
        r"director liability",
        r"directors?['’]?\s+liability",
        r"operational resilience",
        r"business continuity",
    ],
    "CAPITAL_RATING_FUNDING": [
        r"capital raising",
        r"capital raise",
        r"equity capital",
        r"rights issue",
        r"capital adequacy",
        r"\bcar\b",
        r"tier\s*[12]",
        r"subordinated debt",
        r"debenture",
        r"credit rating",
        r"investment[- ]grade",
        r"\brating\b",
        r"funding",
        r"liquidity",
        r"liquid assets",
    ],
    "DIRECT_SUPERVISORY_ACTION": [
        r"central bank",
        r"\bcbsl\b",
        r"monetary board",
        r"supervis",
        r"regulat",
        r"administrator",
        r"liquidator",
        r"liquidation",
        r"resolution",
        r"licen[cs]e",
        r"revok",
        r"suspend",
        r"intervention",
        r"directions? issued",
        r"warning",
        r"pyramid scheme",
        r"failed bank",
        r"failed finance",
    ],
}

BANK_LFC_PATTERNS = [
    r"\bbank\b",
    r"\bbanking\b",
    r"finance plc",
    r"finance company",
    r"licensed finance",
    r"\blfc\b",
    r"microfinance",
]

CBSL_PATTERNS = [
    r"central bank of sri lanka",
    r"\bcbsl\b",
    r"monetary board",
]

INSURANCE_PATTERNS = [
    r"insurance",
    r"assurance",
    r"sanasa life",
]

DIRECT_PRUDENTIAL_PATTERNS = [
    r"capital adequacy",
    r"liquidity",
    r"asset quality",
    r"non[- ]performing",
    r"\bnpl\b",
    r"provision",
    r"solvency",
    r"minimum capital",
    r"tier\s*[12]",
    r"resolution",
    r"liquidation",
    r"administrator",
    r"regulatory action",
    r"supervisory action",
    r"licen[cs]e",
    r"revok",
    r"suspend",
    r"pyramid scheme",
    r"money laundering",
    r"terrorist financing",
]


def norm(value) -> str:
    return str(value or "").strip()


def lower_blob(row: dict) -> str:
    return " ".join(
        norm(row.get(key))
        for key in (
            "headline",
            "article_text",
            "primary_entities",
            "all_entities",
            "target_topic_labels",
            "review_notes",
            "scope_review_rationale",
        )
    ).lower()


def any_match(blob: str, patterns: list[str]) -> bool:
    return any(re.search(p, blob, flags=re.I) for p in patterns)


def category_tags(blob: str) -> list[str]:
    tags = [
        category
        for category, patterns in CATEGORY_RULES.items()
        if any_match(blob, patterns)
    ]
    return tags or ["OTHER"]


def evidence_flags(row: dict) -> dict:
    blob = lower_blob(row)
    has_bank_lfc = any_match(blob, BANK_LFC_PATTERNS)
    has_cbsl = any_match(blob, CBSL_PATTERNS)
    has_insurance = any_match(blob, INSURANCE_PATTERNS)
    has_direct_prudential = any_match(blob, DIRECT_PRUDENTIAL_PATTERNS)
    has_aml = any_match(blob, CATEGORY_RULES["AML_CFT"])
    has_governance_cyber = any_match(blob, CATEGORY_RULES["GOVERNANCE_CYBER_PDPA"])
    has_capital_rating = any_match(blob, CATEGORY_RULES["CAPITAL_RATING_FUNDING"])

    insurance_only_candidate = (
        has_insurance and not has_bank_lfc and not has_cbsl and not has_aml
    )

    direct_supervisory_candidate = (
        has_cbsl
        or has_aml
        or (has_bank_lfc and has_direct_prudential)
    )

    return {
        "has_bank_lfc_signal": has_bank_lfc,
        "has_cbsl_signal": has_cbsl,
        "has_insurance_signal": has_insurance,
        "has_direct_prudential_signal": has_direct_prudential,
        "has_aml_cft_signal": has_aml,
        "has_governance_cyber_signal": has_governance_cyber,
        "has_capital_rating_signal": has_capital_rating,
        "insurance_only_candidate": insurance_only_candidate,
        "direct_supervisory_candidate": direct_supervisory_candidate,
    }


def read_csv(path: Path) -> tuple[list[dict], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_predictions(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    rows, _ = read_csv(path)
    return {
        norm(row.get("dataset_row_id")): row
        for row in rows
        if norm(row.get("dataset_row_id"))
    }


def score_review_priority(row: dict) -> tuple[int, list[str]]:
    score = 0
    reasons = []

    true_label = norm(row.get("target_relevance_label")).upper()
    predicted = norm(row.get("predicted_label")).upper()

    if predicted and predicted != true_label:
        score += 100
        reasons.append("VALIDATION_MISCLASSIFICATION")

    if row.get("insurance_only_candidate"):
        score += 40
        reasons.append("INSURANCE_ONLY_SCOPE_BOUNDARY")

    if row.get("has_aml_cft_signal"):
        score += 40
        reasons.append("AML_CFT_SCOPE_BOUNDARY")

    if row.get("has_governance_cyber_signal"):
        score += 30
        reasons.append("GOVERNANCE_CYBER_SCOPE_BOUNDARY")

    if row.get("has_capital_rating_signal"):
        score += 25
        reasons.append("CAPITAL_RATING_SCOPE_BOUNDARY")

    if row.get("direct_supervisory_candidate"):
        score += 20
        reasons.append("DIRECT_SUPERVISORY_SIGNAL")

    if true_label == "WATCH" and row.get("direct_supervisory_candidate"):
        score += 30
        reasons.append("WATCH_WITH_DIRECT_SUPERVISORY_SIGNAL")

    if true_label == "RELEVANT" and row.get("insurance_only_candidate"):
        score += 30
        reasons.append("RELEVANT_INSURANCE_ONLY_REVIEW")

    try:
        p_watch = float(row.get("p_watch") or "")
        p_relevant = float(row.get("p_relevant") or "")
        margin = abs(p_watch - p_relevant)
        row["validation_probability_margin"] = margin
        if predicted and margin < 0.20:
            score += 20
            reasons.append("LOW_MARGIN_VALIDATION_PREDICTION")
    except ValueError:
        row["validation_probability_margin"] = ""

    return score, reasons


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build Stage-B WATCH/RELEVANT boundary audit "
            "without reading test.csv or changing labels."
        )
    )
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_STAGE_B)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--output-base", type=Path, default=DEFAULT_OUTPUT_BASE)
    args = parser.parse_args()

    dataset_root = args.dataset_root
    predictions_path = args.predictions
    output_base = args.output_base

    if not dataset_root.is_absolute():
        dataset_root = PROJECT_ROOT / dataset_root
    if not predictions_path.is_absolute():
        predictions_path = PROJECT_ROOT / predictions_path
    if not output_base.is_absolute():
        output_base = PROJECT_ROOT / output_base

    train_path = dataset_root / "train.csv"
    validation_path = dataset_root / "validation.csv"

    if not train_path.is_file():
        raise FileNotFoundError(train_path)
    if not validation_path.is_file():
        raise FileNotFoundError(validation_path)

    # Intentionally do NOT open test.csv.
    train, fields = read_csv(train_path)
    validation, val_fields = read_csv(validation_path)

    for split_name, rows in (("train", train), ("validation", validation)):
        for i, row in enumerate(rows, start=1):
            label = norm(row.get("target_relevance_label")).upper()
            if label not in VALID_LABELS:
                raise ValueError(
                    f"{split_name} row {i}: invalid Stage-B label {label!r}"
                )

    predictions = load_predictions(predictions_path)

    timestamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    output_root = output_base / timestamp
    output_root.mkdir(parents=True, exist_ok=False)

    combined = []
    category_counter = Counter()
    label_category_counter = Counter()
    misclassified = []

    for split_name, rows in (("train", train), ("validation", validation)):
        for row in rows:
            out = dict(row)
            out["audit_split"] = split_name

            blob = lower_blob(row)
            tags = category_tags(blob)
            out["boundary_categories"] = ";".join(tags)
            out.update(evidence_flags(row))

            row_id = norm(row.get("dataset_row_id"))
            pred = predictions.get(row_id) if split_name == "validation" else None

            if pred:
                out["predicted_label"] = norm(pred.get("predicted_label"))
                out["prediction_correct"] = norm(pred.get("correct"))
                out["p_watch"] = norm(pred.get("p_watch"))
                out["p_relevant"] = norm(pred.get("p_relevant"))
            else:
                out["predicted_label"] = ""
                out["prediction_correct"] = ""
                out["p_watch"] = ""
                out["p_relevant"] = ""

            priority, reasons = score_review_priority(out)
            out["boundary_review_priority"] = priority
            out["boundary_review_reasons"] = ";".join(reasons)

            combined.append(out)

            label = norm(row.get("target_relevance_label")).upper()
            for tag in tags:
                category_counter[tag] += 1
                label_category_counter[(label, tag)] += 1

            if out["predicted_label"] and out["predicted_label"].upper() != label:
                misclassified.append(out)

    review_queue = sorted(
        combined,
        key=lambda r: (
            -int(r["boundary_review_priority"]),
            0 if norm(r.get("predicted_label")) else 1,
            norm(r.get("headline")).lower(),
        ),
    )

    focused_queue = [
        row
        for row in review_queue
        if int(row["boundary_review_priority"]) > 0
        or row["boundary_categories"] != "OTHER"
    ]

    output_fields = list(dict.fromkeys(
        fields
        + val_fields
        + [
            "audit_split",
            "boundary_categories",
            "has_bank_lfc_signal",
            "has_cbsl_signal",
            "has_insurance_signal",
            "has_direct_prudential_signal",
            "has_aml_cft_signal",
            "has_governance_cyber_signal",
            "has_capital_rating_signal",
            "insurance_only_candidate",
            "direct_supervisory_candidate",
            "predicted_label",
            "prediction_correct",
            "p_watch",
            "p_relevant",
            "validation_probability_margin",
            "boundary_review_priority",
            "boundary_review_reasons",
        ]
    ))

    write_csv(
        output_root / "stage_b_boundary_audit_all.csv",
        combined,
        output_fields,
    )
    write_csv(
        output_root / "stage_b_boundary_review_queue.csv",
        focused_queue,
        output_fields,
    )
    write_csv(
        output_root / "stage_b_validation_misclassifications.csv",
        misclassified,
        output_fields,
    )

    summary_rows = []
    for category in sorted(category_counter):
        summary_rows.append(
            {
                "boundary_category": category,
                "total": category_counter[category],
                "watch": label_category_counter[("WATCH", category)],
                "relevant": label_category_counter[("RELEVANT", category)],
            }
        )

    write_csv(
        output_root / "stage_b_boundary_category_summary.csv",
        summary_rows,
        ["boundary_category", "total", "watch", "relevant"],
    )

    flags_to_count = [
        "insurance_only_candidate",
        "direct_supervisory_candidate",
        "has_aml_cft_signal",
        "has_governance_cyber_signal",
        "has_capital_rating_signal",
    ]

    flag_summary = []
    for flag in flags_to_count:
        subset = [row for row in combined if row.get(flag)]
        flag_summary.append(
            {
                "flag": flag,
                "total": len(subset),
                "watch": sum(
                    norm(r.get("target_relevance_label")).upper() == "WATCH"
                    for r in subset
                ),
                "relevant": sum(
                    norm(r.get("target_relevance_label")).upper() == "RELEVANT"
                    for r in subset
                ),
                "validation_misclassified": sum(
                    bool(norm(r.get("predicted_label")))
                    and norm(r.get("predicted_label")).upper()
                    != norm(r.get("target_relevance_label")).upper()
                    for r in subset
                ),
            }
        )

    write_csv(
        output_root / "stage_b_boundary_flag_summary.csv",
        flag_summary,
        ["flag", "total", "watch", "relevant", "validation_misclassified"],
    )

    summary = {
        "status": "PASS",
        "created_at": datetime.now(TZ).isoformat(),
        "purpose": (
            "Stage-B scope-boundary audit only; no labels changed, "
            "no model training, no test.csv access."
        ),
        "dataset_root": str(dataset_root),
        "train_rows": len(train),
        "validation_rows": len(validation),
        "test_rows_read": 0,
        "prediction_file_found": predictions_path.is_file(),
        "prediction_rows_loaded": len(predictions),
        "validation_misclassifications": len(misclassified),
        "category_counts": dict(sorted(category_counter.items())),
        "label_category_counts": {
            f"{label}|{category}": count
            for (label, category), count in sorted(label_category_counter.items())
        },
        "review_queue_rows": len(focused_queue),
        "notes": [
            "Rules are review aids, not final supervisory labels.",
            "Insurance-only candidate means insurance signal without bank/LFC/CBSL/AML signal.",
            "Direct supervisory candidate means CBSL/AML or bank/LFC plus direct prudential language.",
            "Do not use rule-derived flags as gold labels without review.",
        ],
    }

    (output_root / "summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    print("=" * 96)
    print("STAGE-B BOUNDARY AUDIT COMPLETE")
    print("=" * 96)
    print(f"Train rows                    : {len(train)}")
    print(f"Validation rows               : {len(validation)}")
    print("Test rows read                : 0")
    print(f"Prediction file found         : {predictions_path.is_file()}")
    print(f"Prediction rows loaded        : {len(predictions)}")
    print(f"Validation misclassifications : {len(misclassified)}")
    print(f"Focused review queue          : {len(focused_queue)}")
    print()
    print("CATEGORY COUNTS")
    for key, value in sorted(category_counter.items()):
        print(f"  {key:<30} {value}")
    print()
    print("FLAG SUMMARY")
    for row in flag_summary:
        print(
            f"  {row['flag']:<32} "
            f"total={row['total']:<3} "
            f"WATCH={row['watch']:<3} "
            f"RELEVANT={row['relevant']:<3} "
            f"val_errors={row['validation_misclassified']}"
        )
    print()
    print(f"Output root: {output_root}")


if __name__ == "__main__":
    main()
