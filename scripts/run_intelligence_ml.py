from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
from zoneinfo import ZoneInfo

import numpy as np

from bank_intel.intelligence.finbert_engine import (
    load_ml_config,
)

from bank_intel.intelligence.ml_pipeline import (
    ArticleMLPipeline,
)

from bank_intel.intelligence.production_policy import (
    apply_relevance_policy,
)

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

STAGE1_ROOT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "stage1"
)

ML_ROOT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "ml"
)

SRI_LANKA_TZ = ZoneInfo(
    "Asia/Colombo"
)


STAGE1_TO_STAGE_A = {
    "INCLUDE_CANDIDATE": "KEEP",
    "WATCH": "KEEP",
    "EXCLUDE_NOISE": "NOISE",
}

STAGE1_TO_STAGE_B = {
    "INCLUDE_CANDIDATE": "RELEVANT",
    "WATCH": "WATCH",
}


def load_json(
    path: Path,
) -> dict:

    return json.loads(
        path.read_text(
            encoding="utf-8-sig"
        )
    )


def safe_slug(
    value: str,
) -> str:

    value = re.sub(
        r"[^a-zA-Z0-9]+",
        "_",
        str(
            value
        ),
    )

    return (
        value
        .strip("_")
        .lower()
        or "unknown"
    )


def resolve_project_path(
    value: str,
) -> Path:

    path = Path(
        value
    )

    if not path.is_absolute():

        path = (
            PROJECT_ROOT
            / path
        )

    return path


def stage1_files_for_date(
    target_date: str,
) -> tuple[
    list[
        Path
    ],
    dict,
]:

    root = (
        STAGE1_ROOT
        / target_date
    )

    manifest_path = (
        root
        / "stage1_manifest.json"
    )

    if not manifest_path.exists():

        raise RuntimeError(
            "Stage-1 manifest is missing for "
            f"{target_date}. Re-run: "
            "python scripts/run_intelligence_stage1.py "
            f"--date {target_date}"
        )

    manifest = (
        load_json(
            manifest_path
        )
    )

    if (
        manifest.get(
            "publication_date"
        )
        != target_date
    ):

        raise RuntimeError(
            "Stage-1 manifest publication date "
            "does not match requested date."
        )

    if (
        manifest.get(
            "status"
        )
        != "SUCCESS"
    ):

        raise RuntimeError(
            "Stage-1 manifest is not successful: "
            f"{manifest.get('status')}"
        )

    files = []

    for value in (
        manifest.get(
            "analysis_files",
            [],
        )
        or []
    ):

        path = (
            resolve_project_path(
                str(
                    value
                )
            )
        )

        if not path.exists():

            raise FileNotFoundError(
                "Stage-1 analysis listed in manifest "
                "does not exist: "
                f"{path}"
            )

        files.append(
            path
        )

    if (
        len(
            files
        )
        != int(
            manifest.get(
                "article_count",
                len(
                    files
                ),
            )
        )
    ):

        raise RuntimeError(
            "Stage-1 manifest article_count does "
            "not match analysis_files."
        )

    return (
        files,
        manifest,
    )


def resolve_input_article(
    stage1_payload: dict,
) -> Path:

    value = str(
        stage1_payload.get(
            "input_file",
            "",
        )
        or ""
    ).strip()

    if not value:

        raise ValueError(
            "STAGE1_INPUT_FILE_MISSING"
        )

    path = (
        resolve_project_path(
            value
        )
    )

    if not path.exists():

        raise FileNotFoundError(
            "Staged article does not exist: "
            f"{path}"
        )

    return path


def reset_ml_date_output(
    target_date: str,
) -> Path:

    root = (
        ML_ROOT
        / target_date
    )

    if root.exists():

        shutil.rmtree(
            root
        )

    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    return root


def relative_project_path(
    path: Path,
) -> str:

    try:

        return str(
            path.relative_to(
                PROJECT_ROOT
            )
        )

    except ValueError:

        return str(
            path
        )


def build_stage_a_comparison(
    *,
    stage1_decision: (
        str
        | None
    ),
    model_result: dict,
) -> dict:

    stage1_decision = str(
        stage1_decision
        or ""
    ).strip().upper()

    stage1_target = (
        STAGE1_TO_STAGE_A
        .get(
            stage1_decision
        )
    )

    status = str(
        model_result.get(
            "status",
            "",
        )
        or ""
    ).strip().upper()

    prediction = str(
        model_result.get(
            "predicted_label",
            "",
        )
        or ""
    ).strip().upper()

    if (
        status != "SUCCESS"
        or not prediction
    ):

        return {
            "stage1_decision": stage1_decision,
            "stage1_binary_target": stage1_target,
            "model_prediction": (
                prediction
                or None
            ),
            "model_confidence": (
                model_result.get(
                    "confidence"
                )
            ),
            "agreement": None,
            "comparison_status": (
                "MODEL_UNAVAILABLE"
            ),
            "review_priority": "HIGH",
        }

    agreement = (
        stage1_target
        == prediction
    )

    return {
        "stage1_decision": stage1_decision,
        "stage1_binary_target": stage1_target,
        "model_prediction": prediction,
        "model_confidence": (
            model_result.get(
                "confidence"
            )
        ),
        "agreement": agreement,
        "comparison_status": (
            "AGREE"
            if agreement
            else "DISAGREE"
        ),
        "review_priority": (
            "NONE"
            if agreement
            else "HIGH"
        ),
    }


def build_stage_b_comparison(
    *,
    stage1_decision: (
        str
        | None
    ),
    model_result: dict,
) -> dict:

    stage1_decision = str(
        stage1_decision
        or ""
    ).strip().upper()

    stage1_target = (
        STAGE1_TO_STAGE_B
        .get(
            stage1_decision
        )
    )

    status = str(
        model_result.get(
            "status",
            "",
        )
        or ""
    ).strip().upper()

    prediction = str(
        model_result.get(
            "predicted_label",
            "",
        )
        or ""
    ).strip().upper()

    if status.startswith(
        "SKIPPED_"
    ):

        return {
            "stage1_decision": stage1_decision,
            "stage1_review_target": stage1_target,
            "model_prediction": None,
            "model_confidence": None,
            "agreement": None,
            "comparison_status": status,
            "review_priority": "NONE",
        }

    if (
        status != "SUCCESS"
        or not prediction
    ):

        return {
            "stage1_decision": stage1_decision,
            "stage1_review_target": stage1_target,
            "model_prediction": (
                prediction
                or None
            ),
            "model_confidence": (
                model_result.get(
                    "confidence"
                )
            ),
            "agreement": None,
            "comparison_status": (
                "MODEL_UNAVAILABLE"
            ),
            "review_priority": "MEDIUM",
        }

    if stage1_target is None:

        return {
            "stage1_decision": stage1_decision,
            "stage1_review_target": None,
            "model_prediction": prediction,
            "model_confidence": (
                model_result.get(
                    "confidence"
                )
            ),
            "agreement": None,
            "comparison_status": (
                "NOT_COMPARABLE_STAGE1_NOISE"
            ),
            "review_priority": "NONE",
        }

    agreement = (
        stage1_target
        == prediction
    )

    return {
        "stage1_decision": stage1_decision,
        "stage1_review_target": stage1_target,
        "model_prediction": prediction,
        "model_confidence": (
            model_result.get(
                "confidence"
            )
        ),
        "agreement": agreement,
        "comparison_status": (
            "AGREE"
            if agreement
            else "DISAGREE"
        ),
        "review_priority": (
            "NONE"
            if agreement
            else "MEDIUM"
        ),
    }


def max_review_priority(
    *values: str,
) -> str:

    rank = {
        "NONE": 0,
        "MEDIUM": 1,
        "HIGH": 2,
    }

    cleaned = [
        str(
            value
            or "NONE"
        ).strip().upper()

        for value in values
    ]

    return max(
        cleaned,
        key=lambda value: rank.get(
            value,
            0,
        ),
    )


def write_ml_manifest(
    *,
    target_date: str,
    ml_version: (
        str
        | None
    ),
    stage1_manifest: dict,
    article_outputs: list[
        dict
    ],
    supervisory_models: dict,
    supervisory_summary: dict,
) -> Path:

    root = (
        ML_ROOT
        / target_date
    )

    payload = {
        "manifest_version": 3,

        "created_at": (
            datetime.now(
                SRI_LANKA_TZ
            )
            .isoformat()
        ),

        "publication_date": (
            target_date
        ),

        "status": "SUCCESS",

        "ml_version": (
            ml_version
        ),

        "stage1_rules_version": (
            stage1_manifest.get(
                "rules_version"
            )
        ),

        "article_count": (
            len(
                article_outputs
            )
        ),

        "supervisory_relevance_architecture": (
            "TWO_STAGE"
        ),

        "supervisory_relevance_models": (
            supervisory_models
        ),

        "supervisory_two_stage_summary": (
            supervisory_summary
        ),

        "articles": (
            article_outputs
        ),
    }

    path = (
        root
        / "ml_manifest.json"
    )

    path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return path


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Phase-2 article-level ML: base FinBERT "
            "sentiment/embeddings, frozen Stage-A "
            "NOISE-vs-KEEP filtering, auxiliary Stage-B "
            "WATCH-vs-RELEVANT scoring, and entity intelligence."
        )
    )

    parser.add_argument(
        "--date",
        required=True,
        help=(
            "Actual publication date YYYY-MM-DD already "
            "processed successfully by Stage-1."
        ),
    )

    args = (
        parser.parse_args()
    )

    target_date = (
        args.date
    )

    datetime.strptime(
        target_date,
        "%Y-%m-%d",
    )

    config = (
        load_ml_config()
    )

    (
        stage1_files,
        stage1_manifest,
    ) = (
        stage1_files_for_date(
            target_date
        )
    )

    print()
    print(
        "=" * 88
    )
    print(
        "BANK SUPERVISION NEWS INTELLIGENCE — PHASE 2 ML — TWO STAGE"
    )
    print(
        "=" * 88
    )
    print(
        f"Publication date : {target_date}"
    )
    print(
        f"ML version       : {config.get('version')}"
    )
    print(
        f"Stage-1 articles : {len(stage1_files)}"
    )

    # Preflight BOTH frozen relevance checkpoints before
    # deleting/rebuilding any ML outputs.
    pipeline = (
        ArticleMLPipeline(
            config=config
        )
    )

    supervisory_models = (
        pipeline
        .validate_required_models(
            load_model=True
        )
    )

    stage_a_model = (
        supervisory_models[
            "stage_a"
        ]
    )

    stage_b_model = (
        supervisory_models[
            "stage_b"
        ]
    )

    print()
    print(
        "Stage A model     : "
        f"{stage_a_model.get('model_id')}"
    )
    print(
        "Stage A path      : "
        f"{stage_a_model.get('model_path')}"
    )
    print(
        "Stage A role      : "
        f"{stage_a_model.get('production_role')}"
    )
    print(
        "Stage B model     : "
        f"{stage_b_model.get('model_id')}"
    )
    print(
        "Stage B path      : "
        f"{stage_b_model.get('model_path')}"
    )
    print(
        "Stage B role      : "
        f"{stage_b_model.get('production_role')}"
    )

    date_root = (
        reset_ml_date_output(
            target_date
        )
    )

    embedding_root = (
        date_root
        / "embeddings"
    )

    embedding_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    article_outputs = []

    stage_a_predictions = (
        Counter()
    )

    stage_b_predictions = (
        Counter()
    )

    stage_a_comparisons = (
        Counter()
    )

    stage_b_comparisons = (
        Counter()
    )

    review_priorities = (
        Counter()
    )

    stage_b_executed_count = 0

    if not stage1_files:

        supervisory_summary = {
            "article_count": 0,
            "stage_a": {
                "prediction_distribution": {},
                "operational_keep_count": 0,
                "operational_noise_count": 0,
                "agreement_count": 0,
                "disagreement_count": 0,
            },
            "stage_b": {
                "executed_count": 0,
                "skipped_count": 0,
                "prediction_distribution": {},
                "agreement_count": 0,
                "disagreement_count": 0,
            },
            "review_priority_distribution": {},
        }

        manifest_path = (
            write_ml_manifest(
                target_date=(
                    target_date
                ),
                ml_version=(
                    config.get(
                        "version"
                    )
                ),
                stage1_manifest=(
                    stage1_manifest
                ),
                article_outputs=[],
                supervisory_models=(
                    supervisory_models
                ),
                supervisory_summary=(
                    supervisory_summary
                ),
            )
        )

        print()
        print(
            "No Stage-1 articles exist for "
            "this publication date."
        )
        print(
            f"ML manifest      : {manifest_path}"
        )
        return

    for (
        index,
        stage1_path,
    ) in enumerate(
        stage1_files,
        start=1,
    ):

        stage1_payload = (
            load_json(
                stage1_path
            )
        )

        input_path = (
            resolve_input_article(
                stage1_payload
            )
        )

        article = (
            load_json(
                input_path
            )
        )

        headline = str(
            article.get(
                "headline",
                "",
            )
            or ""
        ).strip()

        article_text = str(
            article.get(
                "article_text",
                "",
            )
            or ""
        ).strip()

        source_url = str(
            article.get(
                "source_url",
                "",
            )
            or ""
        ).strip()

        if not headline:

            raise ValueError(
                "HEADLINE_MISSING: "
                f"{input_path}"
            )

        if not article_text:

            raise ValueError(
                "ARTICLE_TEXT_MISSING: "
                f"{input_path}"
            )

        print()
        print(
            "-" * 88
        )
        print(
            f"[{index}/{len(stage1_files)}]"
        )
        print(
            f"Headline: {headline}"
        )

        (
            ml_result,
            embedding,
        ) = (
            pipeline.analyse(
                headline=(
                    headline
                ),
                article_text=(
                    article_text
                ),
            )
        )

        ml_payload = (
            ml_result.to_dict()
        )

        article_id = (
            str(
                article.get(
                    "content_hash",
                    "",
                )
                or ""
            )[:16]
            or safe_slug(
                input_path.stem
            )
        )

        source_slug = (
            safe_slug(
                article.get(
                    "source_name",
                    "unknown",
                )
            )
        )

        output_folder = (
            date_root
            / source_slug
        )

        output_folder.mkdir(
            parents=True,
            exist_ok=True,
        )

        embedding_path = (
            embedding_root
            / (
                f"{source_slug}_"
                f"{article_id}.npy"
            )
        )

        np.save(
            embedding_path,
            embedding.astype(
                np.float32
            ),
        )

        stage1 = (
            stage1_payload.get(
                "stage1",
                {},
            )
            or {}
        )

        stage1_decision = (
            stage1.get(
                "gate_decision"
            )
        )

        relevance = (
            ml_payload.get(
                "supervisory_relevance",
                {},
            )
            or {}
        )

        stage_a_result = (
            relevance.get(
                "stage_a",
                {},
            )
            or {}
        )

        stage_b_result = (
            relevance.get(
                "stage_b",
                {},
            )
            or {}
        )

        stage_a_comparison = (
            build_stage_a_comparison(
                stage1_decision=(
                    stage1_decision
                ),
                model_result=(
                    stage_a_result
                ),
            )
        )

        stage_b_comparison = (
            build_stage_b_comparison(
                stage1_decision=(
                    stage1_decision
                ),
                model_result=(
                    stage_b_result
                ),
            )
        )

        overall_review_priority = (
            max_review_priority(
                stage_a_comparison.get(
                    "review_priority"
                ),
                stage_b_comparison.get(
                    "review_priority"
                ),
            )
        )

        stage_a_label = (
            stage_a_result.get(
                "predicted_label"
            )
        )

        stage_b_label = (
            stage_b_result.get(
                "predicted_label"
            )
        )

        if stage_a_label:

            stage_a_predictions[
                str(
                    stage_a_label
                )
            ] += 1

        if stage_b_label:

            stage_b_predictions[
                str(
                    stage_b_label
                )
            ] += 1

        if relevance.get(
            "stage_b_executed"
        ):

            stage_b_executed_count += 1

        stage_a_comparisons[
            stage_a_comparison[
                "comparison_status"
            ]
        ] += 1

        stage_b_comparisons[
            stage_b_comparison[
                "comparison_status"
            ]
        ] += 1

        review_priorities[
            overall_review_priority
        ] += 1

        record = {
            "processed_at": (
                datetime.now(
                    SRI_LANKA_TZ
                )
                .isoformat()
            ),

            "ml_version": (
                config.get(
                    "version"
                )
            ),

            "publication_date": (
                target_date
            ),

            "source_name": (
                article.get(
                    "source_name"
                )
            ),

            "headline": (
                headline
            ),

            "published_at": (
                article.get(
                    "published_at"
                )
            ),

            "source_url": (
                source_url
            ),

            "canonical_url": (
                article.get(
                    "canonical_url"
                )
            ),

            "stage1": {
                "gate_decision": (
                    stage1_decision
                ),

                "financial_relevance_score": (
                    stage1.get(
                        "financial_relevance_score"
                    )
                ),

                "financial_relevance_label": (
                    stage1.get(
                        "financial_relevance_label"
                    )
                ),

                "pr_noise_score": (
                    stage1.get(
                        "pr_noise_score"
                    )
                ),

                "pr_noise_label": (
                    stage1.get(
                        "pr_noise_label"
                    )
                ),
            },

            "ml": (
                ml_payload
            ),

            "supervisory_comparison": {
                "stage_a": (
                    stage_a_comparison
                ),
                "stage_b": (
                    stage_b_comparison
                ),
                "overall_review_priority": (
                    overall_review_priority
                ),
            },

            "embedding_file": (
                relative_project_path(
                    embedding_path
                )
            ),
        }

        output_path = (
            output_folder
            / f"{article_id}.json"
        )

        output_path.write_text(
            json.dumps(
                record,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        article_outputs.append(
            {
                "source_name": (
                    article.get(
                        "source_name"
                    )
                ),

                "headline": (
                    headline
                ),

                "published_at": (
                    article.get(
                        "published_at"
                    )
                ),

                "source_url": (
                    source_url
                ),

                "stage_a_prediction": (
                    stage_a_label
                ),

                "stage_a_confidence": (
                    stage_a_result.get(
                        "confidence"
                    )
                ),

                "operational_keep": (
                    relevance.get(
                        "operational_keep"
                    )
                ),

                "operational_filter_action": (
                    relevance.get(
                        "operational_filter_action"
                    )
                ),

                "stage_b_executed": (
                    relevance.get(
                        "stage_b_executed"
                    )
                ),

                "stage_b_prediction": (
                    stage_b_label
                ),

                "stage_b_confidence": (
                    stage_b_result.get(
                        "confidence"
                    )
                ),

                "stage1_stage_a_agreement": (
                    stage_a_comparison.get(
                        "agreement"
                    )
                ),

                "stage1_stage_b_agreement": (
                    stage_b_comparison.get(
                        "agreement"
                    )
                ),

                "review_priority": (
                    overall_review_priority
                ),

                "analysis_file": (
                    relative_project_path(
                        output_path
                    )
                ),

                "embedding_file": (
                    relative_project_path(
                        embedding_path
                    )
                ),
            }
        )

        matches = (
            ml_payload[
                "cbsl_entity_matches"
            ]
        )

        print(
            "FinBERT sentiment: "
            f"{ml_payload['finbert']['sentiment_label']} "
            "("
            f"{ml_payload['finbert']['sentiment_confidence']:.3f}"
            ")"
        )

        print(
            "Stage A filter    : "
            f"{stage_a_label} "
            "("
            f"{float(stage_a_result.get('confidence') or 0.0):.3f}"
            ") "
            f"=> {relevance.get('operational_filter_action')}"
        )

        if relevance.get(
            "stage_b_executed"
        ):

            print(
                "Stage B review    : "
                f"{stage_b_label} "
                "("
                f"{float(stage_b_result.get('confidence') or 0.0):.3f}"
                ") [AUXILIARY]"
            )

        else:

            print(
                "Stage B review    : SKIPPED "
                "(Stage A = NOISE)"
            )

        print(
            "Stage1 vs Stage A : "
            f"{stage_a_comparison['comparison_status']} "
            f"[{stage_a_comparison['review_priority']}]"
        )

        print(
            "Overall review    : "
            f"{overall_review_priority}"
        )

        if matches:

            print(
                "Entities          : "
                + "; ".join(
                    item[
                        "canonical_name"
                    ]

                    for item
                    in matches
                )
            )

        else:

            print(
                "Entities          : "
                "no CBSL-registry match"
            )

    operational_keep_count = int(
        stage_a_predictions[
            "KEEP"
        ]
    )

    operational_noise_count = int(
        stage_a_predictions[
            "NOISE"
        ]
    )

    supervisory_summary = {
        "article_count": (
            len(
                article_outputs
            )
        ),

        "stage_a": {
            "role": (
                "OPERATIONAL_RELEVANCE_FILTER"
            ),

            "prediction_distribution": dict(
                sorted(
                    stage_a_predictions.items()
                )
            ),

            "operational_keep_count": (
                operational_keep_count
            ),

            "operational_noise_count": (
                operational_noise_count
            ),

            "agreement_count": int(
                stage_a_comparisons[
                    "AGREE"
                ]
            ),

            "disagreement_count": int(
                stage_a_comparisons[
                    "DISAGREE"
                ]
            ),

            "model_unavailable_count": int(
                stage_a_comparisons[
                    "MODEL_UNAVAILABLE"
                ]
            ),
        },

        "stage_b": {
            "role": (
                "AUXILIARY_REVIEW_SIGNAL"
            ),

            "automatic_gate": False,

            "executed_count": (
                int(
                    stage_b_executed_count
                )
            ),

            "skipped_count": (
                int(
                    len(
                        article_outputs
                    )
                    - stage_b_executed_count
                )
            ),

            "prediction_distribution": dict(
                sorted(
                    stage_b_predictions.items()
                )
            ),

            "agreement_count": int(
                stage_b_comparisons[
                    "AGREE"
                ]
            ),

            "disagreement_count": int(
                stage_b_comparisons[
                    "DISAGREE"
                ]
            ),

            "not_comparable_stage1_noise_count": int(
                stage_b_comparisons[
                    "NOT_COMPARABLE_STAGE1_NOISE"
                ]
            ),

            "model_unavailable_count": int(
                stage_b_comparisons[
                    "MODEL_UNAVAILABLE"
                ]
            ),
        },

        "review_priority_distribution": dict(
            sorted(
                review_priorities.items()
            )
        ),
    }

    manifest_path = (
        write_ml_manifest(
            target_date=(
                target_date
            ),
            ml_version=(
                config.get(
                    "version"
                )
            ),
            stage1_manifest=(
                stage1_manifest
            ),
            article_outputs=(
                article_outputs
            ),
            supervisory_models=(
                supervisory_models
            ),
            supervisory_summary=(
                supervisory_summary
            ),
        )
    )

    print()
    print(
        "=" * 88
    )
    print(
        "PHASE-2 ARTICLE ML COMPLETE — TWO STAGE"
    )
    print(
        "=" * 88
    )
    print(
        f"Articles analysed : {len(article_outputs)}"
    )
    print(
        "Stage A labels     : "
        f"{supervisory_summary['stage_a']['prediction_distribution']}"
    )
    print(
        "Operational KEEP   : "
        f"{operational_keep_count}"
    )
    print(
        "Operational NOISE  : "
        f"{operational_noise_count}"
    )
    print(
        "Stage B executed   : "
        f"{stage_b_executed_count}"
    )
    print(
        "Stage B labels     : "
        f"{supervisory_summary['stage_b']['prediction_distribution']}"
    )
    print(
        "Review priorities  : "
        f"{supervisory_summary['review_priority_distribution']}"
    )
    print(
        f"ML output root    : {date_root}"
    )
    print(
        f"ML manifest       : {manifest_path}"
    )


if __name__ == "__main__":

    main()
