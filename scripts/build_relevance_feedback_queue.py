
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

ML_ROOT = PROJECT_ROOT / "data" / "processed" / "ml"
REPORT_ROOT = PROJECT_ROOT / "data" / "processed" / "reports"

FEEDBACK_ROOT = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "relevance_feedback"
)

LEDGER_PATH = (
    FEEDBACK_ROOT
    / "relevance_feedback_ledger.jsonl"
)


def safe_text(value):
    return str(value or "").strip()


def read_json(path: Path):
    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def stage_a_from_payload(payload):
    relevance = (
        (payload.get("ml", {}) or {})
        .get(
            "supervisory_relevance",
            {},
        )
        or {}
    )

    stage_a = (
        relevance.get("stage_a", {})
        or {}
    )

    stage_b = (
        relevance.get("stage_b", {})
        or {}
    )

    return {
        "stage_a_label": safe_text(
            stage_a.get("predicted_label")
        ),
        "stage_a_confidence": stage_a.get(
            "confidence"
        ),
        "operational_keep": stage_a.get(
            "operational_keep",
            relevance.get(
                "operational_keep"
            ),
        ),
        "stage_b_executed": relevance.get(
            "stage_b_executed"
        ),
        "stage_b_label": safe_text(
            stage_b.get("predicted_label")
        ),
        "stage_b_confidence": stage_b.get(
            "confidence"
        ),
    }


def ml_index(publication_date: str):
    root = ML_ROOT / publication_date

    index = {}

    if not root.is_dir():
        raise RuntimeError(
            f"ML root missing: {root}"
        )

    for path in root.rglob("*.json"):

        if path.name == "ml_manifest.json":
            continue

        try:
            payload = read_json(path)
        except Exception:
            continue

        url = safe_text(
            payload.get("source_url")
        )

        headline = safe_text(
            payload.get("headline")
            or payload.get("title")
        )

        if not url:
            continue

        model = stage_a_from_payload(
            payload
        )

        index[url] = {
            "headline": headline,
            "source_name": safe_text(
                payload.get(
                    "source_name"
                )
            ),
            "ml_file": str(
                path.relative_to(
                    PROJECT_ROOT
                )
            ),
            **model,
        }

    return index


def load_final_audit(
    publication_date: str,
):
    path = (
        REPORT_ROOT
        / publication_date
        / "window_1d"
        / "stakeholder_brief_v2_audit.json"
    )

    if not path.is_file():
        raise RuntimeError(
            f"Final audit missing: {path}"
        )

    payload = read_json(path)

    if payload.get("status") != "SUCCESS":
        raise RuntimeError(
            "Final brief audit is not SUCCESS"
        )

    if (
        (payload.get("qa") or {})
        .get("status")
        != "PASS"
    ):
        raise RuntimeError(
            "Final brief QA is not PASS"
        )

    return path, payload


def existing_keys():
    keys = set()

    if not LEDGER_PATH.is_file():
        return keys

    for line in LEDGER_PATH.read_text(
        encoding="utf-8"
    ).splitlines():

        if not line.strip():
            continue

        try:
            row = json.loads(line)
        except Exception:
            continue

        keys.add(
            (
                safe_text(
                    row.get(
                        "publication_date"
                    )
                ),
                safe_text(
                    row.get("source_url")
                ),
                safe_text(
                    row.get(
                        "feedback_type"
                    )
                ),
            )
        )

    return keys


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--date",
        required=True,
    )

    args = parser.parse_args()

    publication_date = (
        args.date.strip()
    )

    feedback_dir = (
        FEEDBACK_ROOT
        / publication_date
    )

    feedback_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    audit_path, final = (
        load_final_audit(
            publication_date
        )
    )

    ml = ml_index(
        publication_date
    )

    rows = []

    missing_ml = []

    for article in final.get(
        "audit",
        [],
    ):

        url = safe_text(
            article.get("source_url")
        )

        model = ml.get(url)

        if model is None:
            missing_ml.append(url)
            continue

        stage_a = safe_text(
            model.get(
                "stage_a_label"
            )
        ).upper()

        final_relevant = (
            safe_text(
                article.get("section")
            ).upper()
            != "EXCLUDE"
        )

        feedback_type = ""

        provisional_label = ""

        if (
            stage_a == "NOISE"
            and final_relevant
        ):
            feedback_type = (
                "STAGE_A_NOISE_RESCUED"
            )

            provisional_label = (
                "KEEP"
            )

        elif (
            stage_a == "KEEP"
            and not final_relevant
        ):
            feedback_type = (
                "STAGE_A_KEEP_REJECTED"
            )

            provisional_label = (
                "NOISE"
            )

        confidence = (
            model.get(
                "stage_a_confidence"
            )
        )

        try:
            confidence_f = float(
                confidence
            )
        except Exception:
            confidence_f = None

        low_confidence = (
            confidence_f is not None
            and confidence_f < 0.70
        )

        # Only disagreements or genuinely
        # uncertain Stage-A decisions enter
        # the learning queue.
        if (
            not feedback_type
            and not low_confidence
        ):
            continue

        if not feedback_type:
            feedback_type = (
                "LOW_CONFIDENCE"
            )

            provisional_label = (
                stage_a
            )

        reason = safe_text(
            article.get("reason")
        )

        # Strong final-rule evidence is
        # useful but still provisional.
        auto_training_candidate = (
            feedback_type
            in {
                "STAGE_A_NOISE_RESCUED",
                "STAGE_A_KEEP_REJECTED",
            }
        )

        rows.append({
            "publication_date": (
                publication_date
            ),
            "source_name": safe_text(
                article.get(
                    "source_name"
                )
            ),
            "headline": safe_text(
                article.get(
                    "headline"
                )
            ),
            "source_url": url,

            "stage_a_label": stage_a,
            "stage_a_confidence": (
                confidence_f
            ),
            "operational_keep": (
                model.get(
                    "operational_keep"
                )
            ),

            "stage_b_executed": (
                model.get(
                    "stage_b_executed"
                )
            ),
            "stage_b_label": (
                model.get(
                    "stage_b_label"
                )
            ),
            "stage_b_confidence": (
                model.get(
                    "stage_b_confidence"
                )
            ),

            "final_section": safe_text(
                article.get("section")
            ),
            "final_category": safe_text(
                article.get("category")
            ),
            "final_reason": reason,
            "final_importance": safe_text(
                article.get(
                    "importance"
                )
            ),

            "feedback_type": (
                feedback_type
            ),

            "provisional_target_label": (
                provisional_label
            ),

            "label_status": (
                "AI_RULE_ASSISTED_PROVISIONAL"
            ),

            "auto_training_candidate": (
                auto_training_candidate
            ),

            "approved_for_training": False,

            "reviewer": "",
            "review_notes": "",

            "ml_file": (
                model.get("ml_file")
            ),

            "created_at": (
                datetime.now()
                .astimezone()
                .isoformat()
            ),
        })

    rows.sort(
        key=lambda row: (
            row["feedback_type"],
            -(
                row[
                    "stage_a_confidence"
                ]
                or 0.0
            ),
            row["headline"],
        )
    )

    csv_path = (
        feedback_dir
        / "relevance_feedback_queue.csv"
    )

    json_path = (
        feedback_dir
        / "relevance_feedback_queue.json"
    )

    fields = [
        "publication_date",
        "source_name",
        "headline",
        "source_url",

        "stage_a_label",
        "stage_a_confidence",
        "operational_keep",

        "stage_b_executed",
        "stage_b_label",
        "stage_b_confidence",

        "final_section",
        "final_category",
        "final_reason",
        "final_importance",

        "feedback_type",
        "provisional_target_label",
        "label_status",
        "auto_training_candidate",

        "approved_for_training",
        "reviewer",
        "review_notes",

        "ml_file",
        "created_at",
    ]

    with csv_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fields,
        )

        writer.writeheader()

        writer.writerows(rows)

    json_path.write_text(
        json.dumps(
            {
                "status": "SUCCESS",
                "publication_date": (
                    publication_date
                ),
                "final_audit": str(
                    audit_path.relative_to(
                        PROJECT_ROOT
                    )
                ),
                "feedback_rows": len(rows),
                "noise_rescued": sum(
                    row[
                        "feedback_type"
                    ]
                    == (
                        "STAGE_A_NOISE_RESCUED"
                    )
                    for row in rows
                ),
                "keep_rejected": sum(
                    row[
                        "feedback_type"
                    ]
                    == (
                        "STAGE_A_KEEP_REJECTED"
                    )
                    for row in rows
                ),
                "low_confidence": sum(
                    row[
                        "feedback_type"
                    ]
                    == "LOW_CONFIDENCE"
                    for row in rows
                ),
                "missing_ml_rows": len(
                    missing_ml
                ),
                "queue_file": str(
                    csv_path.relative_to(
                        PROJECT_ROOT
                    )
                ),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Persistent ledger.
    FEEDBACK_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    seen = existing_keys()

    added = 0

    with LEDGER_PATH.open(
        "a",
        encoding="utf-8",
    ) as file:

        for row in rows:

            key = (
                row[
                    "publication_date"
                ],
                row["source_url"],
                row["feedback_type"],
            )

            if key in seen:
                continue

            file.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                )
                + "\n"
            )

            seen.add(key)

            added += 1

    print("=" * 100)
    print(
        "RELEVANCE FEEDBACK QUEUE COMPLETE"
    )
    print("=" * 100)

    print(
        f"Publication date       : "
        f"{publication_date}"
    )

    print(
        f"Feedback rows          : "
        f"{len(rows)}"
    )

    print(
        "Stage-A NOISE rescued : "
        f"{sum(r['feedback_type'] == 'STAGE_A_NOISE_RESCUED' for r in rows)}"
    )

    print(
        "Stage-A KEEP rejected : "
        f"{sum(r['feedback_type'] == 'STAGE_A_KEEP_REJECTED' for r in rows)}"
    )

    print(
        "Low-confidence only   : "
        f"{sum(r['feedback_type'] == 'LOW_CONFIDENCE' for r in rows)}"
    )

    print(
        f"Ledger rows added      : "
        f"{added}"
    )

    print(
        f"Missing ML matches     : "
        f"{len(missing_ml)}"
    )

    print(
        f"Queue                  : "
        f"{csv_path}"
    )

    print(
        f"Ledger                 : "
        f"{LEDGER_PATH}"
    )


if __name__ == "__main__":
    main()
