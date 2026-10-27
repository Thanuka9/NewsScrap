from __future__ import annotations

"""
Build a blind Stage-B boundary review packet from the latest v2 audit.

Purpose
-------
Create a small, high-value review set for WATCH vs RELEVANT boundary adjudication.

Guardrails
----------
- Reads only the existing Stage-B v2 audit outputs.
- Does not open test.csv.
- Does not change any labels.
- Does not retrain any model.
- Produces a BLIND file without current target/model labels and a KEY file
  containing the hidden current labels/predictions/selection reasons.
"""

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_AUDIT_BASE = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "stage_b_boundary_audit_v2"
)

DEFAULT_OUTPUT_BASE = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "stage_b_boundary_review"
)

TZ = ZoneInfo("Asia/Colombo")


def norm(value) -> str:
    return str(value or "").strip()


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


def latest_audit_dir(base: Path) -> Path:
    if not base.is_dir():
        raise FileNotFoundError(base)

    candidates = sorted(
        [
            path
            for path in base.iterdir()
            if path.is_dir()
            and (path / "stage_b_boundary_audit_all_v2.csv").is_file()
        ],
        key=lambda p: p.name,
    )

    if not candidates:
        raise FileNotFoundError(
            f"No completed v2 audit folders found under {base}"
        )

    return candidates[-1]


def float_or_none(value):
    try:
        return float(norm(value))
    except ValueError:
        return None


def selection_reasons(row: dict) -> list[str]:
    reasons = []

    true_label = norm(row.get("target_relevance_label")).upper()
    predicted = norm(row.get("predicted_label")).upper()
    bucket = norm(row.get("scope_bucket"))

    if predicted and predicted != true_label:
        reasons.append("VALIDATION_MISCLASSIFICATION")

    if bucket == "INSURANCE_ADJACENT":
        reasons.append("INSURANCE_ADJACENT_ALL_WATCH_CHECK")

    if bucket == "AML_CFT_DIRECT":
        reasons.append("AML_CFT_DIRECT_ALL_RELEVANT_CHECK")

    if bucket == "GENERIC_GOVERNANCE_CYBER":
        reasons.append("GENERIC_GOVERNANCE_CYBER_CHECK")

    if (
        bucket == "BANK_LFC_PRUDENTIAL"
        and true_label == "WATCH"
    ):
        reasons.append("BANK_LFC_PRUDENTIAL_WATCH_EXCEPTION")

    if (
        bucket == "CBSL_DIRECT"
        and true_label == "WATCH"
    ):
        reasons.append("CBSL_DIRECT_WATCH_EXCEPTION")

    if (
        bucket == "OTHER"
        and true_label == "RELEVANT"
    ):
        reasons.append("OTHER_RELEVANT_EXCEPTION")

    p_watch = float_or_none(row.get("p_watch"))
    p_relevant = float_or_none(row.get("p_relevant"))

    if (
        predicted
        and p_watch is not None
        and p_relevant is not None
        and abs(p_watch - p_relevant) < 0.20
    ):
        reasons.append("LOW_MARGIN_VALIDATION_CASE")

    return reasons


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create a blind Stage-B boundary adjudication packet "
            "from the latest v2 audit."
        )
    )
    parser.add_argument(
        "--audit-base",
        type=Path,
        default=DEFAULT_AUDIT_BASE,
    )
    parser.add_argument(
        "--output-base",
        type=Path,
        default=DEFAULT_OUTPUT_BASE,
    )
    args = parser.parse_args()

    audit_base = args.audit_base
    output_base = args.output_base

    if not audit_base.is_absolute():
        audit_base = PROJECT_ROOT / audit_base
    if not output_base.is_absolute():
        output_base = PROJECT_ROOT / output_base

    audit_dir = latest_audit_dir(audit_base)
    source_path = audit_dir / "stage_b_boundary_audit_all_v2.csv"

    rows, _ = read_csv(source_path)

    selected = []
    reason_counts = {}

    for row in rows:
        reasons = selection_reasons(row)
        if not reasons:
            continue

        out = dict(row)
        out["selection_reasons"] = ";".join(reasons)
        selected.append(out)

        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    # Stable, audit-friendly order: validation errors first, then split/date/headline.
    selected.sort(
        key=lambda row: (
            0 if "VALIDATION_MISCLASSIFICATION" in row["selection_reasons"] else 1,
            0 if norm(row.get("audit_split")) == "validation" else 1,
            norm(row.get("published_at")),
            norm(row.get("headline")).lower(),
        )
    )

    timestamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    output_root = output_base / timestamp
    output_root.mkdir(parents=True, exist_ok=False)

    blind_rows = []
    key_rows = []

    for index, row in enumerate(selected, start=1):
        review_id = f"SB_BOUNDARY_{index:03d}"

        blind_rows.append(
            {
                "review_id": review_id,
                "source_name": norm(row.get("source_name")),
                "headline": norm(row.get("headline")),
                "article_text": norm(row.get("article_text")),
                "published_at": norm(row.get("published_at")),
                "source_url": norm(row.get("source_url")),
                "primary_entities": norm(row.get("primary_entities")),
                "all_entities": norm(row.get("all_entities")),
                "reviewer": "",
                "review_label": "",
                "review_confidence": "",
                "review_notes": "",
            }
        )

        key_rows.append(
            {
                "review_id": review_id,
                "dataset_row_id": norm(row.get("dataset_row_id")),
                "audit_split": norm(row.get("audit_split")),
                "current_target_label": norm(
                    row.get("target_relevance_label")
                ),
                "model_predicted_label": norm(
                    row.get("predicted_label")
                ),
                "p_watch": norm(row.get("p_watch")),
                "p_relevant": norm(row.get("p_relevant")),
                "scope_bucket": norm(row.get("scope_bucket")),
                "selection_reasons": row["selection_reasons"],
                "current_label_source": norm(row.get("label_source")),
                "current_review_status": norm(row.get("review_status")),
                "current_review_notes": norm(row.get("review_notes")),
                "scope_review_status": norm(row.get("scope_review_status")),
                "scope_review_scope": norm(row.get("scope_review_scope")),
                "scope_review_rationale": norm(
                    row.get("scope_review_rationale")
                ),
                "source_url": norm(row.get("source_url")),
                "headline": norm(row.get("headline")),
            }
        )

    blind_path = output_root / "stage_b_boundary_BLIND.csv"
    key_path = output_root / "stage_b_boundary_KEY.csv"

    write_csv(
        blind_path,
        blind_rows,
        [
            "review_id",
            "source_name",
            "headline",
            "article_text",
            "published_at",
            "source_url",
            "primary_entities",
            "all_entities",
            "reviewer",
            "review_label",
            "review_confidence",
            "review_notes",
        ],
    )

    write_csv(
        key_path,
        key_rows,
        [
            "review_id",
            "dataset_row_id",
            "audit_split",
            "current_target_label",
            "model_predicted_label",
            "p_watch",
            "p_relevant",
            "scope_bucket",
            "selection_reasons",
            "current_label_source",
            "current_review_status",
            "current_review_notes",
            "scope_review_status",
            "scope_review_scope",
            "scope_review_rationale",
            "source_url",
            "headline",
        ],
    )

    summary = {
        "status": "PASS",
        "created_at": datetime.now(TZ).isoformat(),
        "source_audit_dir": str(audit_dir),
        "source_rows": len(rows),
        "selected_rows": len(selected),
        "test_rows_read": 0,
        "selection_reason_counts": dict(sorted(reason_counts.items())),
        "blind_file": str(blind_path),
        "key_file": str(key_path),
        "review_labels_allowed": ["WATCH", "RELEVANT"],
        "instructions": [
            "Review BLIND file without opening KEY.",
            "Assign WATCH or RELEVANT using the agreed supervisory scope.",
            "Do not use model probabilities or current labels during adjudication.",
            "After adjudication, join to KEY by review_id for comparison.",
            "Do not automatically overwrite training labels from this packet.",
        ],
    }

    summary_path = output_root / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    print("=" * 96)
    print("STAGE-B BLIND BOUNDARY REVIEW PACKET COMPLETE")
    print("=" * 96)
    print(f"Source audit          : {audit_dir}")
    print(f"Source rows           : {len(rows)}")
    print(f"Selected review rows  : {len(selected)}")
    print("Test rows read        : 0")
    print()
    print("SELECTION REASONS")
    for reason, count in sorted(reason_counts.items()):
        print(f"  {reason:<42} {count}")
    print()
    print(f"BLIND : {blind_path}")
    print(f"KEY   : {key_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
