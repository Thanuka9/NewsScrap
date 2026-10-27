from __future__ import annotations

"""
Numerical trainer-vs-production inference parity test.

This is the FINAL implementation-parity gate. It performs no training,
no threshold tuning, and does not read either inherited test split.

It compares:
  frozen trainer inference
vs
  src/bank_intel/intelligence/relevance_engine.py

for both frozen production components:
  Stage A: NOISE vs KEEP
  Stage B: WATCH vs RELEVANT (TARGETED_V5)

It also adds one long-body stress article per stage so the body exceeds
max_body_chunks and therefore exercises spaced beginning/middle/end
window selection.

PASS requirements:
- predicted labels identical
- headline/body selected chunk counts identical
- every class probability absolute difference <= tolerance
"""

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
SRC_DIR = PROJECT_ROOT / "src"

for path in (SCRIPTS_DIR, SRC_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from run_finbert_two_stage_finetune import (  # noqa: E402
    encode_article,
    evaluate,
    load_dependencies,
    resolve_device,
)
from bank_intel.intelligence.relevance_engine import (  # noqa: E402
    SupervisoryRelevanceEngine,
)

TZ = ZoneInfo("Asia/Colombo")

STAGES = {
    "STAGE_A": {
        "labels": ("NOISE", "KEEP"),
        "focus_label": "KEEP",
        "dataset_root": (
            PROJECT_ROOT
            / "data"
            / "training"
            / "datasets"
            / "supervisory_relevance_v4_two_stage"
            / "stage_a_noise_vs_keep"
        ),
        "run_root": (
            PROJECT_ROOT
            / "data"
            / "processed"
            / "relevance_two_stage"
            / "supervisory_relevance_v4_two_stage"
            / "stage_a_noise_vs_keep"
            / "finbert_lr1e-05_chunks5_sched5_seed42"
        ),
    },
    "STAGE_B": {
        "labels": ("WATCH", "RELEVANT"),
        "focus_label": "RELEVANT",
        "dataset_root": (
            PROJECT_ROOT
            / "data"
            / "training"
            / "datasets"
            / "supervisory_relevance_v5_stage_b_targeted"
        ),
        "run_root": (
            PROJECT_ROOT
            / "data"
            / "processed"
            / "relevance_two_stage"
            / "supervisory_relevance_v5_stage_b_targeted"
            / "stage_b_watch_vs_relevant"
            / "finbert_lr1e-05_chunks5_sched5_seed42"
        ),
    },
}

OUTPUT_BASE = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "production_relevance_numerical_parity"
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fields: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def spaced_indices(total: int, maximum: int) -> list[int]:
    if total <= maximum:
        return list(range(total))
    if maximum == 1:
        return [0]
    return sorted(
        {
            round(i * (total - 1) / (maximum - 1))
            for i in range(maximum)
        }
    )


def select_rows(
    rows: list[dict[str, str]],
    maximum: int,
) -> list[dict[str, str]]:
    if maximum <= 0 or len(rows) <= maximum:
        return list(rows)

    return [
        rows[i]
        for i in spaced_indices(
            len(rows),
            maximum,
        )
    ]


def row_id(
    row: dict[str, str],
    index: int,
) -> str:
    return str(
        row.get("dataset_row_id")
        or row.get("augmentation_review_id")
        or row.get("source_url")
        or f"row_{index:03d}"
    )


def make_stress_row(
    base: dict[str, str],
    labels: tuple[str, str],
    stage_name: str,
) -> dict[str, str]:
    stress = dict(base)

    headline = str(
        base.get("headline")
        or "Numerical parity stress article"
    ).strip()

    body = str(
        base.get("article_text")
        or ""
    ).strip()

    if not body:
        raise RuntimeError(
            f"{stage_name}: cannot build stress row from blank body"
        )

    # Long enough to require substantially more than five 512-token
    # windows for ordinary news prose.
    repeated = "\n\n".join(
        [body] * 12
    )

    stress["dataset_row_id"] = (
        f"PARITY_STRESS_{stage_name}"
    )
    stress["augmentation_review_id"] = ""
    stress["source_url"] = (
        f"parity://{stage_name.lower()}/long-body"
    )
    stress["story_group_id"] = (
        f"PARITY_STRESS_{stage_name}"
    )
    stress["headline"] = (
        f"{headline} [PARITY LONG-BODY STRESS]"
    )
    stress["article_text"] = repeated

    label = str(
        base.get("target_relevance_label")
        or labels[0]
    ).strip().upper()

    if label not in labels:
        label = labels[0]

    stress["target_relevance_label"] = label

    return stress


def trainer_predictions(
    *,
    rows: list[dict[str, str]],
    labels: tuple[str, str],
    focus_label: str,
    manifest: dict[str, Any],
    checkpoint: Path,
    torch,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    device,
):
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

    _, predictions = evaluate(
        model,
        items,
        device,
        torch,
        labels=labels,
        focus_label=focus_label,
        headline_weight=float(manifest["headline_weight"]),
        body_weight=float(manifest["body_weight"]),
    )

    return items, predictions


def build_engine_config(
    *,
    labels: tuple[str, str],
    manifest: dict[str, Any],
    checkpoint: Path,
    stage_name: str,
) -> dict[str, Any]:
    return {
        "supervisory_relevance": {
            "enabled": True,
            "required": True,
            "model_id": (
                f"PARITY_{stage_name}"
            ),
            "model_path": str(checkpoint),
            "tokenizer_name": "",
            "label_order": list(labels),
            "max_length": int(
                manifest["max_length"]
            ),
            "stride": int(
                manifest["stride"]
            ),
            "batch_size": 8,
            "headline_weight": float(
                manifest["headline_weight"]
            ),
            "body_weight": float(
                manifest["body_weight"]
            ),
            "max_body_chunks": int(
                manifest["max_body_chunks"]
            ),
            "chunk_weighting": "token_count",
            "device": "cpu",
            "provenance_status": (
                "PARITY_QA_ONLY"
            ),
            "production_role": (
                "AUXILIARY_REVIEW_SIGNAL"
            ),
        }
    }


def prediction_probability(
    prediction: dict[str, Any],
    label: str,
) -> float:
    key = f"p_{label.lower()}"

    if key not in prediction:
        raise RuntimeError(
            f"Trainer prediction missing {key}: "
            f"{sorted(prediction)}"
        )

    return float(
        prediction[key]
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Numerically compare frozen trainer inference with "
            "production SupervisoryRelevanceEngine."
        )
    )
    parser.add_argument(
        "--device",
        choices=("cpu",),
        default="cpu",
        help=(
            "CPU is required for deterministic parity with the "
            "current production sign-off procedure."
        ),
    )
    parser.add_argument(
        "--rows-per-stage",
        type=int,
        default=8,
        help=(
            "Deterministic validation rows per stage, plus one "
            "long-body stress row. Use 0 for all validation rows."
        ),
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-6,
    )
    args = parser.parse_args()

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
        "cpu",
    )

    timestamp = datetime.now(
        TZ
    ).strftime(
        "%Y%m%d_%H%M%S"
    )

    output_root = (
        OUTPUT_BASE
        / timestamp
    )
    output_root.mkdir(
        parents=True,
        exist_ok=False,
    )

    detail_rows = []
    stage_summaries = {}

    print("=" * 108)
    print("NUMERICAL TRAINER-vs-PRODUCTION RELEVANCE PARITY")
    print("=" * 108)
    print("Training            : NO")
    print("Threshold tuning    : NO")
    print("Inherited test read : 0")
    print(f"Tolerance           : {args.tolerance:g}")
    print("Device              : cpu")
    print()

    total_label_mismatches = 0
    total_chunk_mismatches = 0
    global_max_diff = 0.0

    for stage_name, spec in STAGES.items():

        validation_path = (
            spec["dataset_root"]
            / "validation.csv"
        )

        run_manifest_path = (
            spec["run_root"]
            / "run_manifest.json"
        )

        checkpoint = (
            spec["run_root"]
            / "best_model"
        )

        for path in (
            validation_path,
            run_manifest_path,
            checkpoint / "config.json",
        ):
            if not path.exists():
                raise FileNotFoundError(
                    path
                )

        all_validation = read_csv(
            validation_path
        )

        chosen = select_rows(
            all_validation,
            args.rows_per_stage,
        )

        if not chosen:
            raise RuntimeError(
                f"{stage_name}: no validation rows"
            )

        stress = make_stress_row(
            max(
                chosen,
                key=lambda r: len(
                    str(
                        r.get(
                            "article_text",
                            "",
                        )
                    )
                ),
            ),
            spec["labels"],
            stage_name,
        )

        rows = (
            list(
                chosen
            )
            + [
                stress
            ]
        )

        manifest = load_json(
            run_manifest_path
        )

        items, trainer_preds = trainer_predictions(
            rows=rows,
            labels=spec["labels"],
            focus_label=spec["focus_label"],
            manifest=manifest,
            checkpoint=checkpoint,
            torch=torch,
            AutoModelForSequenceClassification=(
                AutoModelForSequenceClassification
            ),
            AutoTokenizer=AutoTokenizer,
            device=device,
        )

        engine = SupervisoryRelevanceEngine(
            config=build_engine_config(
                labels=spec["labels"],
                manifest=manifest,
                checkpoint=checkpoint,
                stage_name=stage_name,
            )
        )

        engine.validate_configuration(
            load_model=True,
        )

        stage_max_diff = 0.0
        stage_label_mismatches = 0
        stage_chunk_mismatches = 0

        for index, (
            row,
            item,
            trainer_pred,
        ) in enumerate(
            zip(
                rows,
                items,
                trainer_preds,
            ),
            start=1,
        ):

            engine_result = engine.analyse(
                headline=str(
                    row.get(
                        "headline",
                        "",
                    )
                ),
                article_text=str(
                    row.get(
                        "article_text",
                        "",
                    )
                ),
            )

            expected_headline_chunks = len(
                item[
                    "headline"
                ]
            )

            expected_body_chunks = len(
                item[
                    "body"
                ]
            )

            chunk_match = (
                engine_result.headline_chunks
                == expected_headline_chunks
                and engine_result.body_chunks
                == expected_body_chunks
            )

            if not chunk_match:
                stage_chunk_mismatches += 1

            trainer_label = str(
                trainer_pred[
                    "predicted_label"
                ]
            )

            engine_label = str(
                engine_result.predicted_label
            )

            label_match = (
                trainer_label
                == engine_label
            )

            if not label_match:
                stage_label_mismatches += 1

            diffs = {}

            for label in spec["labels"]:

                trainer_prob = prediction_probability(
                    trainer_pred,
                    label,
                )

                engine_prob = float(
                    engine_result.probabilities[
                        label
                    ]
                )

                diff = abs(
                    trainer_prob
                    - engine_prob
                )

                diffs[
                    label
                ] = diff

                stage_max_diff = max(
                    stage_max_diff,
                    diff,
                )

            article_max_diff = max(
                diffs.values()
            )

            detail_rows.append(
                {
                    "stage": stage_name,
                    "row_id": row_id(
                        row,
                        index,
                    ),
                    "is_long_body_stress": (
                        row["dataset_row_id"]
                        == f"PARITY_STRESS_{stage_name}"
                    ),
                    "trainer_prediction": (
                        trainer_label
                    ),
                    "engine_prediction": (
                        engine_label
                    ),
                    "label_match": (
                        label_match
                    ),
                    "trainer_headline_chunks": (
                        expected_headline_chunks
                    ),
                    "engine_headline_chunks": (
                        engine_result.headline_chunks
                    ),
                    "trainer_body_chunks": (
                        expected_body_chunks
                    ),
                    "engine_body_chunks": (
                        engine_result.body_chunks
                    ),
                    "chunk_match": (
                        chunk_match
                    ),
                    "max_probability_abs_diff": (
                        article_max_diff
                    ),
                    "within_tolerance": (
                        article_max_diff
                        <= args.tolerance
                    ),
                    "headline": str(
                        row.get(
                            "headline",
                            "",
                        )
                    ),
                    **{
                        (
                            f"trainer_p_"
                            f"{label.lower()}"
                        ): prediction_probability(
                            trainer_pred,
                            label,
                        )
                        for label in spec[
                            "labels"
                        ]
                    },
                    **{
                        (
                            f"engine_p_"
                            f"{label.lower()}"
                        ): float(
                            engine_result
                            .probabilities[
                                label
                            ]
                        )
                        for label in spec[
                            "labels"
                        ]
                    },
                    **{
                        (
                            f"abs_diff_"
                            f"{label.lower()}"
                        ): diffs[
                            label
                        ]
                        for label in spec[
                            "labels"
                        ]
                    },
                }
            )

        probability_failures = sum(
            1
            for row in detail_rows
            if (
                row["stage"]
                == stage_name
                and not row[
                    "within_tolerance"
                ]
            )
        )

        stage_status = (
            "PASS"
            if (
                stage_label_mismatches == 0
                and stage_chunk_mismatches == 0
                and probability_failures == 0
            )
            else "FAIL"
        )

        stage_summaries[
            stage_name
        ] = {
            "status": stage_status,
            "validation_rows_available": (
                len(
                    all_validation
                )
            ),
            "validation_rows_checked": (
                len(
                    chosen
                )
            ),
            "long_body_stress_rows": 1,
            "rows_checked_total": (
                len(
                    rows
                )
            ),
            "label_mismatches": (
                stage_label_mismatches
            ),
            "chunk_count_mismatches": (
                stage_chunk_mismatches
            ),
            "probability_tolerance_failures": (
                probability_failures
            ),
            "max_probability_abs_diff": (
                stage_max_diff
            ),
            "checkpoint": str(
                checkpoint
            ),
        }

        total_label_mismatches += (
            stage_label_mismatches
        )

        total_chunk_mismatches += (
            stage_chunk_mismatches
        )

        global_max_diff = max(
            global_max_diff,
            stage_max_diff,
        )

        print(stage_name)
        print(
            f"  rows checked             : {len(rows)} "
            f"({len(chosen)} validation + 1 long-body stress)"
        )
        print(
            f"  label mismatches         : {stage_label_mismatches}"
        )
        print(
            f"  chunk-count mismatches   : {stage_chunk_mismatches}"
        )
        print(
            f"  probability failures     : {probability_failures}"
        )
        print(
            f"  max probability abs diff : {stage_max_diff:.12g}"
        )
        print(
            f"  status                   : {stage_status}"
        )
        print()

    probability_failures_total = sum(
        1
        for row in detail_rows
        if not row[
            "within_tolerance"
        ]
    )

    final_pass = (
        total_label_mismatches == 0
        and total_chunk_mismatches == 0
        and probability_failures_total == 0
    )

    output_fields = [
        "stage",
        "row_id",
        "is_long_body_stress",
        "trainer_prediction",
        "engine_prediction",
        "label_match",
        "trainer_headline_chunks",
        "engine_headline_chunks",
        "trainer_body_chunks",
        "engine_body_chunks",
        "chunk_match",
        "max_probability_abs_diff",
        "within_tolerance",
        "headline",
        "trainer_p_noise",
        "engine_p_noise",
        "abs_diff_noise",
        "trainer_p_keep",
        "engine_p_keep",
        "abs_diff_keep",
        "trainer_p_watch",
        "engine_p_watch",
        "abs_diff_watch",
        "trainer_p_relevant",
        "engine_p_relevant",
        "abs_diff_relevant",
    ]

    write_csv(
        output_root
        / "numerical_parity_details.csv",
        detail_rows,
        output_fields,
    )

    summary = {
        "status": (
            "PASS"
            if final_pass
            else "FAIL"
        ),
        "created_at": datetime.now(
            TZ
        ).isoformat(),
        "training_performed": False,
        "threshold_tuning_performed": False,
        "inherited_test_rows_read": 0,
        "device": "cpu",
        "tolerance": args.tolerance,
        "stage_summaries": (
            stage_summaries
        ),
        "global": {
            "rows_checked": len(
                detail_rows
            ),
            "label_mismatches": (
                total_label_mismatches
            ),
            "chunk_count_mismatches": (
                total_chunk_mismatches
            ),
            "probability_tolerance_failures": (
                probability_failures_total
            ),
            "max_probability_abs_diff": (
                global_max_diff
            ),
        },
        "pass_rule": (
            "Zero label mismatches, zero selected chunk-count "
            "mismatches, and all class-probability absolute "
            "differences <= tolerance."
        ),
        "stage_b_role": (
            "TARGETED_V5 remains frozen as AUXILIARY_REVIEW_SIGNAL; "
            "this parity test does not change model-selection status."
        ),
    }

    (
        output_root
        / "numerical_parity_summary.json"
    ).write_text(
        json.dumps(
            summary,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("=" * 108)
    print("FINAL NUMERICAL PARITY STATUS")
    print("=" * 108)
    print(
        f"Rows checked               : {len(detail_rows)}"
    )
    print(
        f"Label mismatches           : {total_label_mismatches}"
    )
    print(
        f"Chunk-count mismatches     : {total_chunk_mismatches}"
    )
    print(
        f"Probability failures       : {probability_failures_total}"
    )
    print(
        f"Maximum probability diff   : {global_max_diff:.12g}"
    )
    print(
        f"Tolerance                  : {args.tolerance:g}"
    )
    print(
        "PARITY STATUS              : "
        + (
            "PASS"
            if final_pass
            else "FAIL"
        )
    )
    print()
    print(
        "Inherited test.csv was NOT read."
    )
    print(
        f"Output: {output_root}"
    )

    if not final_pass:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
