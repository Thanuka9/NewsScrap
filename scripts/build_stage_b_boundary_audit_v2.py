from __future__ import annotations

"""
Stage-B boundary audit v2.

This version fixes the overly broad scope detection in v1 by separating:
1) entity evidence (primary_entities / all_entities),
2) headline + lead evidence,
3) full-text topic evidence.

IMPORTANT:
- train.csv + validation.csv only
- test.csv is never opened
- no labels are changed
- no model is trained
- existing Stage-B baseline remains untouched
"""

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
    / "stage_b_boundary_audit_v2"
)

TZ = ZoneInfo("Asia/Colombo")
VALID_LABELS = {"WATCH", "RELEVANT"}


def norm(value) -> str:
    return str(value or "").strip()


def first_chars(value: str, n: int = 1200) -> str:
    return norm(value)[:n]


def match_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, flags=re.I) for p in patterns)


def matched_terms(text: str, patterns: dict[str, str]) -> list[str]:
    out = []
    for name, pattern in patterns.items():
        if re.search(pattern, text, flags=re.I):
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# Scope evidence rules
# ---------------------------------------------------------------------------

CBSL_ENTITY_PATTERNS = [
    r"\bcentral bank of sri lanka\b",
    r"\bcbsl\b",
    r"\bmonetary board\b",
]

# IMPORTANT: applied to entity fields, not the whole article body.
BANK_LFC_ENTITY_PATTERNS = [
    r"\bbank\b",
    r"\bbanking\b",
    r"\bfinance\b",
    r"\bfinancial services\b",
    r"\bmicrofinance\b",
    r"\bleasing\b",
]

# Applied to entity fields + headline.
INSURANCE_PATTERNS = [
    r"\binsurance\b",
    r"\bassurance\b",
    r"\binsurer\b",
    r"\bsanasa life\b",
]

AML_PATTERNS = {
    "AML": r"\baml\b",
    "CFT": r"\bcft\b",
    "money_laundering": r"money laundering",
    "terrorist_financing": r"terror(?:ist|ism)\s+financing",
    "FIU": r"financial intelligence unit|\bfiu\b",
    "proceeds_of_crime": r"proceeds of crime",
    "beneficial_ownership": r"beneficial ownership",
}

GOV_CYBER_PATTERNS = {
    "cyber": r"\bcyber(?:security)?\b",
    "PDPA": r"\bpdpa\b",
    "data_protection": r"data protection|personal data protection",
    "director_liability": r"director(?:s)?['’]?\s+(?:personal\s+)?liability",
    "corporate_governance": r"corporate governance|board governance",
    "operational_resilience": r"operational resilience|business continuity",
}

CAPITAL_RATING_PATTERNS = {
    "capital_adequacy": r"capital adequacy",
    "capital_raise": r"capital rais(?:e|ing)|equity capital|rights issue",
    "tier_capital": r"tier\s*[12]",
    "subordinated_debt": r"subordinated debt",
    "rating": r"credit rating|investment[- ]grade|\brating\b",
    "liquidity": r"\bliquidity\b|liquid assets",
    "funding": r"\bfunding\b",
}

DIRECT_ACTION_PATTERNS = {
    "warning": r"\bwarn(?:s|ed|ing)?\b",
    "administrator": r"\badministrator\b",
    "liquidator": r"\bliquidator\b",
    "liquidation": r"\bliquidation\b",
    "resolution": r"\bresolution\b",
    "licence": r"\blicen[cs](?:e|ed|ing)\b",
    "revoke": r"\brevok(?:e|ed|es|ing)\b",
    "suspend": r"\bsuspend(?:ed|s|ing)?\b",
    "direction": r"\bdirection(?:s)?\b",
    "intervention": r"\bintervention\b",
    "pyramid_scheme": r"pyramid scheme",
}

PRUDENTIAL_PATTERNS = {
    "capital_adequacy": r"capital adequacy",
    "liquidity": r"\bliquidity\b",
    "asset_quality": r"asset quality",
    "NPL": r"non[- ]performing|\bnpl\b",
    "solvency": r"\bsolvency\b",
    "minimum_capital": r"minimum capital",
    "tier_capital": r"tier\s*[12]",
    "resolution": r"\bresolution\b",
    "liquidation": r"\bliquidation\b",
    "administrator": r"\badministrator\b",
}


def read_csv(path: Path) -> tuple[list[dict], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
            extrasaction="ignore",
        )
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


def derive_evidence(row: dict) -> dict:
    headline = norm(row.get("headline"))
    body_lead = first_chars(row.get("article_text", ""), 1200)

    entity_blob = " ".join(
        [
            norm(row.get("primary_entities")),
            norm(row.get("all_entities")),
        ]
    )

    headline_lead = f"{headline} {body_lead}"
    headline_entity = f"{headline} {entity_blob}"

    has_cbsl_entity = match_any(entity_blob, CBSL_ENTITY_PATTERNS)
    has_cbsl_headline = match_any(headline, CBSL_ENTITY_PATTERNS)

    # Entity-scope signal ONLY: avoids generic word "bank" anywhere in article body.
    has_bank_lfc_entity = match_any(
        entity_blob,
        BANK_LFC_ENTITY_PATTERNS,
    )

    has_insurance_scope = match_any(
        headline_entity,
        INSURANCE_PATTERNS,
    )

    aml_terms = matched_terms(
        headline_lead,
        AML_PATTERNS,
    )
    gov_terms = matched_terms(
        headline_lead,
        GOV_CYBER_PATTERNS,
    )
    capital_terms = matched_terms(
        headline_lead,
        CAPITAL_RATING_PATTERNS,
    )
    action_terms = matched_terms(
        headline_lead,
        DIRECT_ACTION_PATTERNS,
    )
    prudential_terms = matched_terms(
        headline_lead,
        PRUDENTIAL_PATTERNS,
    )

    has_aml = bool(aml_terms)
    has_governance_cyber = bool(gov_terms)
    has_capital_rating = bool(capital_terms)
    has_direct_action = bool(action_terms)
    has_prudential = bool(prudential_terms)

    insurance_without_bank_lfc_cbsl = (
        has_insurance_scope
        and not has_bank_lfc_entity
        and not has_cbsl_entity
        and not has_cbsl_headline
        and not has_aml
    )

    aml_cft_direct_policy = has_aml

    bank_lfc_prudential = (
        has_bank_lfc_entity
        and (
            has_prudential
            or has_direct_action
        )
    )

    cbsl_direct = (
        has_cbsl_entity
        or has_cbsl_headline
    )

    direct_supervisory_candidate = (
        cbsl_direct
        or aml_cft_direct_policy
        or bank_lfc_prudential
    )

    generic_governance_cyber = (
        has_governance_cyber
        and not cbsl_direct
        and not has_aml
        and not bank_lfc_prudential
    )

    # Mutually useful scope bucket for review.
    if insurance_without_bank_lfc_cbsl:
        scope_bucket = "INSURANCE_ADJACENT"
    elif aml_cft_direct_policy:
        scope_bucket = "AML_CFT_DIRECT"
    elif cbsl_direct:
        scope_bucket = "CBSL_DIRECT"
    elif bank_lfc_prudential:
        scope_bucket = "BANK_LFC_PRUDENTIAL"
    elif generic_governance_cyber:
        scope_bucket = "GENERIC_GOVERNANCE_CYBER"
    elif has_capital_rating:
        scope_bucket = "CAPITAL_RATING_ADJACENT"
    else:
        scope_bucket = "OTHER"

    return {
        "has_cbsl_entity": has_cbsl_entity,
        "has_cbsl_headline": has_cbsl_headline,
        "has_bank_lfc_entity": has_bank_lfc_entity,
        "has_insurance_scope": has_insurance_scope,
        "has_aml_cft_signal": has_aml,
        "has_governance_cyber_signal": has_governance_cyber,
        "has_capital_rating_signal": has_capital_rating,
        "has_direct_action_signal": has_direct_action,
        "has_prudential_signal": has_prudential,
        "insurance_without_bank_lfc_cbsl": insurance_without_bank_lfc_cbsl,
        "aml_cft_direct_policy": aml_cft_direct_policy,
        "bank_lfc_prudential": bank_lfc_prudential,
        "cbsl_direct": cbsl_direct,
        "direct_supervisory_candidate": direct_supervisory_candidate,
        "generic_governance_cyber": generic_governance_cyber,
        "scope_bucket": scope_bucket,
        "aml_terms": ";".join(aml_terms),
        "governance_cyber_terms": ";".join(gov_terms),
        "capital_rating_terms": ";".join(capital_terms),
        "direct_action_terms": ";".join(action_terms),
        "prudential_terms": ";".join(prudential_terms),
        "scope_entity_evidence": entity_blob,
    }


def review_priority(row: dict) -> tuple[int, list[str]]:
    score = 0
    reasons = []

    true_label = norm(row.get("target_relevance_label")).upper()
    predicted = norm(row.get("predicted_label")).upper()

    if predicted and predicted != true_label:
        score += 100
        reasons.append("VALIDATION_MISCLASSIFICATION")

    if row.get("insurance_without_bank_lfc_cbsl"):
        score += 50
        reasons.append("INSURANCE_ADJACENT_BOUNDARY")

    if row.get("aml_cft_direct_policy"):
        score += 50
        reasons.append("AML_CFT_DIRECT_BOUNDARY")

    if row.get("generic_governance_cyber"):
        score += 40
        reasons.append("GENERIC_GOVERNANCE_CYBER_BOUNDARY")

    if row.get("bank_lfc_prudential"):
        score += 35
        reasons.append("BANK_LFC_PRUDENTIAL_BOUNDARY")

    if row.get("cbsl_direct"):
        score += 35
        reasons.append("CBSL_DIRECT_BOUNDARY")

    if row.get("has_capital_rating_signal"):
        score += 20
        reasons.append("CAPITAL_RATING_TOPIC")

    if (
        true_label == "WATCH"
        and row.get("direct_supervisory_candidate")
    ):
        score += 25
        reasons.append("WATCH_WITH_DIRECT_SCOPE_SIGNAL")

    if (
        true_label == "RELEVANT"
        and row.get("insurance_without_bank_lfc_cbsl")
    ):
        score += 25
        reasons.append("RELEVANT_INSURANCE_ADJACENT_REVIEW")

    try:
        p_watch = float(row.get("p_watch") or "")
        p_rel = float(row.get("p_relevant") or "")
        margin = abs(p_watch - p_rel)
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
            "Stage-B boundary audit v2 with entity-scoped "
            "supervisory signals and no test access."
        )
    )

    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=DEFAULT_STAGE_B,
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        default=DEFAULT_PREDICTIONS,
    )
    parser.add_argument(
        "--output-base",
        type=Path,
        default=DEFAULT_OUTPUT_BASE,
    )

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

    # Explicitly do NOT open test.csv.
    train, fields = read_csv(train_path)
    validation, validation_fields = read_csv(validation_path)

    for split_name, rows in (
        ("train", train),
        ("validation", validation),
    ):
        for i, row in enumerate(rows, start=1):
            label = norm(row.get("target_relevance_label")).upper()
            if label not in VALID_LABELS:
                raise ValueError(
                    f"{split_name} row {i}: invalid label {label!r}"
                )

    predictions = load_predictions(predictions_path)

    timestamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    output_root = output_base / timestamp
    output_root.mkdir(parents=True, exist_ok=False)

    combined = []
    misclassified = []

    bucket_counts = Counter()
    bucket_label_counts = Counter()
    flag_counts = Counter()
    flag_label_counts = Counter()

    flags_for_summary = [
        "insurance_without_bank_lfc_cbsl",
        "aml_cft_direct_policy",
        "bank_lfc_prudential",
        "cbsl_direct",
        "generic_governance_cyber",
        "direct_supervisory_candidate",
        "has_capital_rating_signal",
    ]

    for split_name, rows in (
        ("train", train),
        ("validation", validation),
    ):
        for row in rows:
            out = dict(row)
            out["audit_split"] = split_name
            out.update(derive_evidence(row))

            row_id = norm(row.get("dataset_row_id"))
            pred = (
                predictions.get(row_id)
                if split_name == "validation"
                else None
            )

            if pred:
                out["predicted_label"] = norm(
                    pred.get("predicted_label")
                )
                out["prediction_correct"] = norm(
                    pred.get("correct")
                )
                out["p_watch"] = norm(
                    pred.get("p_watch")
                )
                out["p_relevant"] = norm(
                    pred.get("p_relevant")
                )
            else:
                out["predicted_label"] = ""
                out["prediction_correct"] = ""
                out["p_watch"] = ""
                out["p_relevant"] = ""

            priority, reasons = review_priority(out)
            out["boundary_review_priority"] = priority
            out["boundary_review_reasons"] = ";".join(reasons)

            label = norm(
                out.get("target_relevance_label")
            ).upper()

            bucket = out["scope_bucket"]
            bucket_counts[bucket] += 1
            bucket_label_counts[(bucket, label)] += 1

            for flag in flags_for_summary:
                if out.get(flag):
                    flag_counts[flag] += 1
                    flag_label_counts[(flag, label)] += 1

            if (
                out["predicted_label"]
                and out["predicted_label"].upper() != label
            ):
                misclassified.append(out)

            combined.append(out)

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
    ]

    extra_fields = [
        "audit_split",
        "scope_bucket",
        "has_cbsl_entity",
        "has_cbsl_headline",
        "has_bank_lfc_entity",
        "has_insurance_scope",
        "has_aml_cft_signal",
        "has_governance_cyber_signal",
        "has_capital_rating_signal",
        "has_direct_action_signal",
        "has_prudential_signal",
        "insurance_without_bank_lfc_cbsl",
        "aml_cft_direct_policy",
        "bank_lfc_prudential",
        "cbsl_direct",
        "direct_supervisory_candidate",
        "generic_governance_cyber",
        "aml_terms",
        "governance_cyber_terms",
        "capital_rating_terms",
        "direct_action_terms",
        "prudential_terms",
        "scope_entity_evidence",
        "predicted_label",
        "prediction_correct",
        "p_watch",
        "p_relevant",
        "validation_probability_margin",
        "boundary_review_priority",
        "boundary_review_reasons",
    ]

    output_fields = list(
        dict.fromkeys(
            fields
            + validation_fields
            + extra_fields
        )
    )

    write_csv(
        output_root / "stage_b_boundary_audit_all_v2.csv",
        combined,
        output_fields,
    )
    write_csv(
        output_root / "stage_b_boundary_review_queue_v2.csv",
        focused_queue,
        output_fields,
    )
    write_csv(
        output_root / "stage_b_validation_misclassifications_v2.csv",
        misclassified,
        output_fields,
    )

    bucket_rows = []

    for bucket in sorted(bucket_counts):
        bucket_rows.append(
            {
                "scope_bucket": bucket,
                "total": bucket_counts[bucket],
                "watch": bucket_label_counts[(bucket, "WATCH")],
                "relevant": bucket_label_counts[(bucket, "RELEVANT")],
                "validation_misclassified": sum(
                    1
                    for row in misclassified
                    if row["scope_bucket"] == bucket
                ),
            }
        )

    write_csv(
        output_root / "stage_b_scope_bucket_summary_v2.csv",
        bucket_rows,
        [
            "scope_bucket",
            "total",
            "watch",
            "relevant",
            "validation_misclassified",
        ],
    )

    flag_rows = []

    for flag in flags_for_summary:
        flag_rows.append(
            {
                "flag": flag,
                "total": flag_counts[flag],
                "watch": flag_label_counts[(flag, "WATCH")],
                "relevant": flag_label_counts[(flag, "RELEVANT")],
                "validation_misclassified": sum(
                    1
                    for row in misclassified
                    if row.get(flag)
                ),
            }
        )

    write_csv(
        output_root / "stage_b_scope_flag_summary_v2.csv",
        flag_rows,
        [
            "flag",
            "total",
            "watch",
            "relevant",
            "validation_misclassified",
        ],
    )

    summary = {
        "status": "PASS",
        "version": "stage-b-boundary-audit-v2",
        "created_at": datetime.now(TZ).isoformat(),
        "train_rows": len(train),
        "validation_rows": len(validation),
        "test_rows_read": 0,
        "prediction_file_found": predictions_path.is_file(),
        "prediction_rows_loaded": len(predictions),
        "validation_misclassifications": len(misclassified),
        "focused_review_queue_rows": len(focused_queue),
        "scope_bucket_counts": dict(
            sorted(bucket_counts.items())
        ),
        "notes": [
            "v2 separates entity evidence from article-body topic evidence.",
            "Generic occurrences of 'bank' in article text no longer create a bank/LFC scope signal.",
            "Insurance-adjacent is a review flag only, not an automatic WATCH label.",
            "AML/CFT is a review flag only, not an automatic RELEVANT label.",
            "No test.csv access and no training occurred.",
        ],
    }

    (
        output_root
        / "summary_v2.json"
    ).write_text(
        json.dumps(
            summary,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("=" * 96)
    print("STAGE-B BOUNDARY AUDIT v2 COMPLETE")
    print("=" * 96)
    print(f"Train rows                     : {len(train)}")
    print(f"Validation rows                : {len(validation)}")
    print("Test rows read                 : 0")
    print(f"Prediction rows loaded         : {len(predictions)}")
    print(f"Validation misclassifications  : {len(misclassified)}")
    print(f"Focused review queue           : {len(focused_queue)}")
    print()
    print("SCOPE BUCKETS")
    for row in bucket_rows:
        print(
            f"  {row['scope_bucket']:<28} "
            f"total={row['total']:<3} "
            f"WATCH={row['watch']:<3} "
            f"RELEVANT={row['relevant']:<3} "
            f"val_errors={row['validation_misclassified']}"
        )
    print()
    print("SCOPE FLAGS")
    for row in flag_rows:
        print(
            f"  {row['flag']:<34} "
            f"total={row['total']:<3} "
            f"WATCH={row['watch']:<3} "
            f"RELEVANT={row['relevant']:<3} "
            f"val_errors={row['validation_misclassified']}"
        )
    print()
    print(f"Output root: {output_root}")


if __name__ == "__main__":
    main()
