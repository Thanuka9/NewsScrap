from __future__ import annotations

import argparse
from pathlib import Path

from bank_intel.intelligence.review_category_learning import (
    enrich_review_csv,
    ingest_approved_csv,
    render_review_guide,
)

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add financial-sector categorisation feedback fields to a review worksheet."
    )
    parser.add_argument("--review-csv", required=True)
    parser.add_argument(
        "--ingest-approved",
        action="store_true",
        help="After enrichment, upsert only explicitly approved human labels into gold.",
    )
    args = parser.parse_args()

    path = Path(args.review_csv).resolve()
    result = enrich_review_csv(path)
    guide = render_review_guide(path)

    print("=" * 100)
    print("REVIEW CATEGORY LEARNING")
    print("=" * 100)
    print(f"Review CSV       : {path}")
    print(f"Rows             : {result['rows']}")
    print(f"Category guide   : {guide}")
    print(f"Suggested buckets: {result['suggested_sections']}")

    if args.ingest_approved:
        ingest = ingest_approved_csv(path)
        print()
        print(f"Approved ingested: {ingest['ingested']}")
        print(f"Invalid skipped  : {ingest['skipped_invalid']}")
        print(f"Human gold rows  : {ingest['gold_rows']}")
        print(f"Gold file        : {ingest['gold_file']}")
    else:
        print()
        print("No human labels were ingested.")
        print("Fill the human fields, set approved_for_training=TRUE, then rerun with --ingest-approved.")

if __name__ == "__main__":
    main()
