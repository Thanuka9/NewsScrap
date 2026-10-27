
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from contextlib import redirect_stdout, redirect_stderr
from concurrent.futures import ProcessPoolExecutor, as_completed
import os
from datetime import date, datetime
import json
from pathlib import Path
import subprocess
import sys
import traceback
from zoneinfo import ZoneInfo

import run_intelligence_stage1 as s1


# ============================================================
# PROJECT
# ============================================================

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

SUMMARY_ROOT = (
    PROJECT_ROOT
    / "data"
    / "backfill_runs"
)

STAGE1_ROOT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "stage1"
)

ML_SCRIPT = (
    PROJECT_ROOT
    / "scripts"
    / "run_intelligence_ml.py"
)

SRI_LANKA_TZ = ZoneInfo(
    "Asia/Colombo"
)


# ============================================================
# CLI
# ============================================================

def parse_cli_date(
    value: str,
) -> date:

    return (
        datetime.strptime(
            value,
            "%Y-%m-%d",
        )
        .date()
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Historical intelligence runner. "
            "Stage-1 can process the full requested range with one "
            "staged-corpus scan; ML can then be run separately or together."
        )
    )

    parser.add_argument(
        "--start-date",
        required=True,
        help="YYYY-MM-DD",
    )

    parser.add_argument(
        "--end-date",
        required=True,
        help="YYYY-MM-DD",
    )

    mode = parser.add_mutually_exclusive_group()

    mode.add_argument(
        "--stage1-only",
        action="store_true",
        help=(
            "Run historical Stage-1 only. "
            "Recommended before rebuilding FinBERT outputs."
        ),
    )

    mode.add_argument(
        "--ml-only",
        action="store_true",
        help=(
            "Run Phase-2 ML only for successful Stage-1 manifests "
            "already present in the requested publication window."
        ),
    )

    mode.add_argument(
        "--full",
        action="store_true",
        help=(
            "Run historical Stage-1 first, then Phase-2 ML."
        ),
    )


    parser.add_argument(
        "--ml-workers",
        type=int,
        default=1,
        help=(
            "Persistent ML workers. Default: 1. "
            "Use 1 for GPU or memory-constrained systems. "
            "Use >1 only for CPU systems with sufficient RAM."
        ),
    )

    parser.add_argument(
        "--ml-cpu-threads-per-worker",
        type=int,
        default=0,
        help=(
            "Optional OpenMP/MKL thread cap for each ML subprocess. "
            "0 leaves the environment unchanged. "
            "Useful when --ml-workers > 1 to avoid CPU oversubscription."
        ),
    )

    return parser.parse_args()


# ============================================================
# EFFECTIVE PUBLICATION DATE
# ============================================================

def effective_date_and_method(
    *,
    path: Path,
    payload: dict,
) -> tuple[
    str | None,
    str | None,
]:

    # 1) authoritative published_at
    value = s1.published_date(
        payload
    )

    if value is not None:

        return (
            value,
            "published_at",
        )

    # 2) legacy collection_run_date fallback
    collection_date = payload.get(
        "collection_run_date"
    )

    if (
        collection_date
        and s1.DATE_PATTERN.match(
            str(
                collection_date
            )
        )
    ):

        return (
            str(
                collection_date
            ),
            "collection_run_date_fallback",
        )

    # 3) legacy path-date fallback
    value = s1.path_date(
        path
    )

    if value is not None:

        return (
            value,
            "path_date_fallback",
        )

    return (
        None,
        None,
    )


# ============================================================
# ONE-SCAN HISTORICAL STAGE-1 DISCOVERY
# ============================================================

def discover_stage1_range(
    *,
    start_date: str,
    end_date: str,
) -> tuple[
    dict[
        str,
        list[Path],
    ],
    dict[
        str,
        dict,
    ],
    dict,
]:
    """
    Scan staged JSON once.

    For every article:
      - determine authoritative/effective publication date
      - keep only requested range
      - deduplicate by the same Stage-1 article identity
      - retain highest article version, then newest mtime

    This replaces calling the single-date Stage-1 discovery scan
    hundreds of times during historical processing.
    """

    latest_by_date_and_article = {}

    scanned_files = 0
    valid_json_files = 0
    load_failures = 0

    source_scanned = {
        source: 0
        for source in s1.ACTIVE_SOURCE_FOLDERS
    }

    source_matched_by_date = defaultdict(
        lambda: {
            source: 0
            for source in s1.ACTIVE_SOURCE_FOLDERS
        }
    )

    matched_files_by_date = defaultdict(
        int
    )

    match_methods_by_date = defaultdict(
        lambda: {
            "published_at": 0,
            "collection_run_date_fallback": 0,
            "path_date_fallback": 0,
        }
    )

    for source_folder in (
        s1.ACTIVE_SOURCE_FOLDERS
    ):

        source_root = (
            s1.STAGED_ROOT
            / source_folder
        )

        if not source_root.exists():

            continue

        for path in source_root.rglob(
            "*.json"
        ):

            scanned_files += 1

            source_scanned[
                source_folder
            ] += 1

            try:

                payload = s1.load_json(
                    path
                )

            except Exception:

                load_failures += 1

                continue

            if not isinstance(
                payload,
                dict,
            ):

                continue

            valid_json_files += 1

            (
                effective_date,
                match_method,
            ) = effective_date_and_method(
                path=path,
                payload=payload,
            )

            if effective_date is None:

                continue

            if not (
                start_date
                <= effective_date
                <= end_date
            ):

                continue

            matched_files_by_date[
                effective_date
            ] += 1

            source_matched_by_date[
                effective_date
            ][
                source_folder
            ] += 1

            if match_method:

                match_methods_by_date[
                    effective_date
                ][
                    match_method
                ] += 1

            identity = s1.article_identity(
                path=path,
                payload=payload,
                source_folder=(
                    source_folder
                ),
            )

            candidate_version = (
                s1.version_number(
                    path,
                    payload,
                )
            )

            candidate_mtime = (
                path.stat()
                .st_mtime
            )

            key = (
                effective_date,
                identity,
            )

            existing = (
                latest_by_date_and_article
                .get(
                    key
                )
            )

            if existing is None:

                latest_by_date_and_article[
                    key
                ] = {
                    "path": path,
                    "version": (
                        candidate_version
                    ),
                    "mtime": (
                        candidate_mtime
                    ),
                }

                continue

            if (
                candidate_version
                > existing[
                    "version"
                ]
            ):

                latest_by_date_and_article[
                    key
                ] = {
                    "path": path,
                    "version": (
                        candidate_version
                    ),
                    "mtime": (
                        candidate_mtime
                    ),
                }

                continue

            if (
                candidate_version
                == existing[
                    "version"
                ]
                and candidate_mtime
                > existing[
                    "mtime"
                ]
            ):

                latest_by_date_and_article[
                    key
                ] = {
                    "path": path,
                    "version": (
                        candidate_version
                    ),
                    "mtime": (
                        candidate_mtime
                    ),
                }

    selected_by_date = (
        defaultdict(
            list
        )
    )

    for (
        (
            publication_date,
            _identity,
        ),
        item,
    ) in (
        latest_by_date_and_article
        .items()
    ):

        selected_by_date[
            publication_date
        ].append(
            item[
                "path"
            ]
        )

    for publication_date in (
        selected_by_date
    ):

        selected_by_date[
            publication_date
        ] = sorted(
            selected_by_date[
                publication_date
            ],
            key=lambda value: str(
                value
            ),
        )

    diagnostics_by_date = {}

    for (
        publication_date,
        paths,
    ) in selected_by_date.items():

        source_counts = {}

        for source in (
            s1.ACTIVE_SOURCE_FOLDERS
        ):

            source_counts[
                source
            ] = {
                "scanned": (
                    source_scanned[
                        source
                    ]
                ),
                "matched": (
                    source_matched_by_date[
                        publication_date
                    ][
                        source
                    ]
                ),
            }

        diagnostics_by_date[
            publication_date
        ] = {
            "scanned_json_files": (
                scanned_files
            ),
            "valid_json_files": (
                valid_json_files
            ),
            "matched_json_files": (
                matched_files_by_date[
                    publication_date
                ]
            ),
            "unique_articles_selected": (
                len(
                    paths
                )
            ),
            "load_failures": (
                load_failures
            ),
            "match_methods": (
                match_methods_by_date[
                    publication_date
                ]
            ),
            "source_counts": (
                source_counts
            ),
            "historical_discovery_mode": (
                "ONE_CORPUS_SCAN"
            ),
        }

    global_diagnostics = {
        "scanned_json_files": (
            scanned_files
        ),
        "valid_json_files": (
            valid_json_files
        ),
        "load_failures": (
            load_failures
        ),
        "source_counts": (
            source_scanned
        ),
        "publication_dates_selected": (
            len(
                selected_by_date
            )
        ),
        "unique_articles_selected": sum(
            len(
                paths
            )
            for paths
            in selected_by_date.values()
        ),
    }

    return (
        dict(
            selected_by_date
        ),
        diagnostics_by_date,
        global_diagnostics,
    )


# ============================================================
# PROCESS ONE STAGE-1 DATE
# ============================================================

def run_stage1_date(
    *,
    target_date: str,
    article_files: list[Path],
    discovery_diagnostics: dict,
    config: dict,
) -> dict:

    # Rebuild the authoritative date output just like the current
    # single-date Stage-1 runner.
    s1.reset_stage1_output(
        target_date
    )

    s1.REVIEW_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    review_path = (
        s1.REVIEW_ROOT
        / (
            f"stage1_review_"
            f"{target_date}.csv"
        )
    )

    # Preserve analyst-entered review annotations.
    existing_annotations = (
        s1.load_existing_review_annotations(
            review_path
        )
    )

    review_rows = []

    analysis_paths = []

    include_count = 0
    watch_count = 0
    exclude_count = 0
    failed_count = 0

    for path in article_files:

        try:

            article = (
                s1.load_json(
                    path
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
                    "HEADLINE_MISSING"
                )

            if not article_text:

                raise ValueError(
                    "ARTICLE_TEXT_MISSING"
                )

            if not source_url:

                raise ValueError(
                    "SOURCE_URL_MISSING"
                )

            analysis = (
                s1.analyse_stage1(
                    headline=(
                        headline
                    ),
                    article_text=(
                        article_text
                    ),
                    config=(
                        config
                    ),
                )
            )

            output_path = (
                s1.save_analysis(
                    run_date=(
                        target_date
                    ),
                    input_path=(
                        path
                    ),
                    article=(
                        article
                    ),
                    analysis=(
                        analysis
                    ),
                )
            )

            analysis_paths.append(
                output_path
            )

            if (
                analysis.gate_decision
                == "INCLUDE_CANDIDATE"
            ):

                include_count += 1

            elif (
                analysis.gate_decision
                == "WATCH"
            ):

                watch_count += 1

            else:

                exclude_count += 1

            institutions_text = (
                "; ".join(
                    analysis.institutions
                )
            )

            financial_terms = []

            for (
                category,
                terms,
            ) in (
                analysis
                .matched_financial_terms
                .items()
            ):

                financial_terms.append(
                    f"{category}:"
                    + "|".join(
                        terms
                    )
                )

            pr_terms = []

            for (
                category,
                terms,
            ) in (
                analysis
                .matched_pr_terms
                .items()
            ):

                pr_terms.append(
                    f"{category}:"
                    + "|".join(
                        terms
                    )
                )

            previous_review = (
                existing_annotations.get(
                    source_url,
                    {},
                )
            )

            review_rows.append(
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
                    "financial_score": (
                        analysis
                        .financial_relevance_score
                    ),
                    "financial_label": (
                        analysis
                        .financial_relevance_label
                    ),
                    "pr_score": (
                        analysis
                        .pr_noise_score
                    ),
                    "pr_label": (
                        analysis
                        .pr_noise_label
                    ),
                    "article_type_hint": (
                        analysis
                        .article_type_hint
                    ),
                    "gate_decision": (
                        analysis
                        .gate_decision
                    ),
                    "institutions": (
                        institutions_text
                    ),
                    "matched_financial_terms": (
                        "; ".join(
                            financial_terms
                        )
                    ),
                    "matched_pr_terms": (
                        "; ".join(
                            pr_terms
                        )
                    ),
                    "pr_counter_signals": (
                        "; ".join(
                            analysis
                            .pr_counter_signals
                        )
                    ),
                    "human_label": (
                        previous_review.get(
                            "human_label",
                            "",
                        )
                    ),
                    "review_notes": (
                        previous_review.get(
                            "review_notes",
                            "",
                        )
                    ),
                    "analysis_file": (
                        str(
                            output_path
                        )
                    ),
                }
            )

        except Exception as exc:

            failed_count += 1

            print(
                f"    FAILED: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    fieldnames = [
        "source_name",
        "headline",
        "published_at",
        "source_url",
        "financial_score",
        "financial_label",
        "pr_score",
        "pr_label",
        "article_type_hint",
        "gate_decision",
        "institutions",
        "matched_financial_terms",
        "matched_pr_terms",
        "pr_counter_signals",
        "human_label",
        "review_notes",
        "analysis_file",
    ]

    with review_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=(
                fieldnames
            ),
        )

        writer.writeheader()

        writer.writerows(
            review_rows
        )

    manifest_path = (
        s1.write_stage1_manifest(
            run_date=(
                target_date
            ),
            rules_version=(
                config.get(
                    "version"
                )
            ),
            discovery_diagnostics=(
                discovery_diagnostics
            ),
            analysis_paths=(
                analysis_paths
            ),
            failed_count=(
                failed_count
            ),
        )
    )

    return {
        "publication_date": (
            target_date
        ),
        "articles": (
            len(
                review_rows
            )
        ),
        "include_candidates": (
            include_count
        ),
        "watch": (
            watch_count
        ),
        "excluded_noise": (
            exclude_count
        ),
        "failures": (
            failed_count
        ),
        "review_file": (
            str(
                review_path
            )
        ),
        "stage1_manifest": (
            str(
                manifest_path
            )
        ),
    }


# ============================================================
# HISTORICAL STAGE-1 RANGE
# ============================================================

def run_stage1_range(
    *,
    start_date: date,
    end_date: date,
) -> tuple[
    list[dict],
    list[str],
    dict,
]:

    print()

    print(
        "=" * 80
    )

    print(
        "HISTORICAL STAGE-1 "
        "— ONE CORPUS SCAN"
    )

    print(
        "=" * 80
    )

    print(
        f"Start date      : "
        f"{start_date}"
    )

    print(
        f"End date        : "
        f"{end_date}"
    )

    print(
        "Active sources  : "
        + ", ".join(
            s1.ACTIVE_SOURCE_FOLDERS
        )
    )

    (
        selected_by_date,
        diagnostics_by_date,
        global_diagnostics,
    ) = discover_stage1_range(
        start_date=(
            start_date.isoformat()
        ),
        end_date=(
            end_date.isoformat()
        ),
    )

    publication_dates = sorted(
        selected_by_date
    )

    print()

    print(
        "DISCOVERY COMPLETE"
    )

    print(
        f"  JSON scanned   : "
        f"{global_diagnostics['scanned_json_files']}"
    )

    print(
        f"  Valid JSON     : "
        f"{global_diagnostics['valid_json_files']}"
    )

    print(
        f"  Load failures  : "
        f"{global_diagnostics['load_failures']}"
    )

    print(
        f"  Dates selected : "
        f"{len(publication_dates)}"
    )

    print(
        f"  Articles       : "
        f"{global_diagnostics['unique_articles_selected']}"
    )

    config = (
        s1.load_stage1_config()
    )

    results = []

    for (
        index,
        target_date,
    ) in enumerate(
        publication_dates,
        start=1,
    ):

        article_files = (
            selected_by_date[
                target_date
            ]
        )

        print()

        print(
            f"[STAGE1 "
            f"{index}/"
            f"{len(publication_dates)}] "
            f"{target_date} "
            f"articles="
            f"{len(article_files)}"
        )

        result = run_stage1_date(
            target_date=(
                target_date
            ),
            article_files=(
                article_files
            ),
            discovery_diagnostics=(
                diagnostics_by_date[
                    target_date
                ]
            ),
            config=(
                config
            ),
        )

        results.append(
            result
        )

        print(
            "    "
            f"include="
            f"{result['include_candidates']} "
            f"watch="
            f"{result['watch']} "
            f"noise="
            f"{result['excluded_noise']} "
            f"failures="
            f"{result['failures']}"
        )

    return (
        results,
        publication_dates,
        global_diagnostics,
    )


# ============================================================
# SUCCESSFUL STAGE-1 DATES
# ============================================================

def successful_stage1_dates(
    *,
    start_date: date,
    end_date: date,
) -> list[str]:

    dates = []

    if not STAGE1_ROOT.exists():

        return dates

    for path in STAGE1_ROOT.glob(
        "*/stage1_manifest.json"
    ):

        try:

            manifest = json.loads(
                path.read_text(
                    encoding="utf-8-sig"
                )
            )

        except Exception:

            continue

        publication_date = str(
            manifest.get(
                "publication_date",
                "",
            )
            or ""
        )

        if not publication_date:

            continue

        try:

            value = parse_cli_date(
                publication_date
            )

        except ValueError:

            continue

        if not (
            start_date
            <= value
            <= end_date
        ):

            continue

        if (
            manifest.get(
                "status"
            )
            != "SUCCESS"
        ):

            continue

        dates.append(
            publication_date
        )

    return sorted(
        set(
            dates
        )
    )


# ============================================================
# PHASE-2 ML — PERSISTENT WORKERS
# ============================================================

ML_LOG_ROOT = (
    SUMMARY_ROOT
    / "ml_logs"
)

# These are process-local globals. Each persistent worker loads
# the transformer models ONCE, then reuses them for many dates.
_PERSISTENT_ML_RUNNER = None
_PERSISTENT_ML_PIPELINE = None
_PERSISTENT_ML_MODEL_META = None


def _physical_or_logical_cpu_count() -> int:
    """
    Prefer physical cores when psutil is available.
    Fall back to logical CPU count.
    """

    try:

        import psutil

        value = (
            psutil.cpu_count(
                logical=False
            )
        )

        if value:

            return int(
                value
            )

    except Exception:

        pass

    return int(
        os.cpu_count()
        or 1
    )


def _resolve_threads_per_worker(
    *,
    workers: int,
    requested: int,
) -> int:
    """
    requested > 0:
        use exactly that many CPU threads per worker.

    requested == 0:
        divide available physical/logical cores across workers.

    This avoids the previous oversubscription problem where
    every PyTorch process could try to use all CPU cores.
    """

    if requested < 0:

        raise ValueError(
            "--ml-cpu-threads-per-worker "
            "must be >= 0"
        )

    if requested > 0:

        return int(
            requested
        )

    available = (
        _physical_or_logical_cpu_count()
    )

    return max(
        1,
        int(
            available
            // max(
                workers,
                1,
            )
        ),
    )


def _init_persistent_ml_worker(
    cpu_threads_per_worker: int,
) -> None:
    """
    Worker initializer.

    Critical performance difference from the earlier version:
    both transformer models are loaded ONCE per worker process,
    not once per publication date.
    """

    global _PERSISTENT_ML_RUNNER
    global _PERSISTENT_ML_PIPELINE
    global _PERSISTENT_ML_MODEL_META

    thread_value = str(
        int(
            cpu_threads_per_worker
        )
    )

    # Prevent 3 workers x "all CPU cores" oversubscription.
    os.environ[
        "OMP_NUM_THREADS"
    ] = thread_value

    os.environ[
        "MKL_NUM_THREADS"
    ] = thread_value

    os.environ[
        "OPENBLAS_NUM_THREADS"
    ] = thread_value

    os.environ[
        "NUMEXPR_NUM_THREADS"
    ] = thread_value

    os.environ[
        "TOKENIZERS_PARALLELISM"
    ] = "false"

    # Historical inference should be reproducible and should
    # not make one Hugging Face network request per worker/date.
    os.environ[
        "HF_HUB_OFFLINE"
    ] = "1"

    os.environ[
        "TRANSFORMERS_OFFLINE"
    ] = "1"

    os.environ[
        "HF_HUB_DISABLE_TELEMETRY"
    ] = "1"

    # Import after thread/offline environment is set.
    import torch

    torch.set_num_threads(
        int(
            cpu_threads_per_worker
        )
    )

    try:

        torch.set_num_interop_threads(
            1
        )

    except RuntimeError:

        # PyTorch only allows this to be configured before
        # inter-op work has started. Safe to leave unchanged
        # if another imported dependency already initialized it.
        pass

    import run_intelligence_ml as ml_runner

    pipeline = (
        ml_runner
        .ArticleMLPipeline()
    )

    # Load fine-tuned relevance model now.
    model_meta = (
        pipeline
        .validate_required_models(
            load_model=True
        )
    )

    # Force base ProsusAI/finbert to load now too.
    # FinBertEngine is lazy by design.
    _ = (
        pipeline
        .finbert_engine
        .device
    )

    # run_intelligence_ml.main() constructs ArticleMLPipeline()
    # for each call. Replace only this module-level factory in
    # the worker with a factory returning the already-loaded
    # process-local pipeline.
    ml_runner.ArticleMLPipeline = (
        lambda: pipeline
    )

    _PERSISTENT_ML_RUNNER = (
        ml_runner
    )

    _PERSISTENT_ML_PIPELINE = (
        pipeline
    )

    _PERSISTENT_ML_MODEL_META = (
        model_meta
    )


def _run_one_ml_date_persistent(
    target_date: str,
) -> dict:
    """
    Process one date using models already resident in this worker.

    run_intelligence_ml.py remains the authoritative date-level
    writer, so ML JSON structure and manifests do not change.
    """

    if (
        _PERSISTENT_ML_RUNNER
        is None
        or _PERSISTENT_ML_PIPELINE
        is None
    ):

        raise RuntimeError(
            "Persistent ML worker was not initialized."
        )

    ML_LOG_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    log_path = (
        ML_LOG_ROOT
        / f"ml_{target_date}.log"
    )

    original_argv = (
        list(
            sys.argv
        )
    )

    return_code = 0

    error_text = None

    try:

        sys.argv = [
            str(
                ML_SCRIPT
            ),
            "--date",
            target_date,
        ]

        with log_path.open(
            "w",
            encoding="utf-8",
        ) as log_file:

            with redirect_stdout(
                log_file
            ), redirect_stderr(
                log_file
            ):

                try:

                    _PERSISTENT_ML_RUNNER.main()

                except SystemExit as exc:

                    code = (
                        exc.code
                    )

                    if (
                        code is None
                        or code == 0
                    ):

                        return_code = 0

                    else:

                        return_code = int(
                            code
                        )

                except Exception as exc:

                    return_code = 1

                    error_text = (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    )

                    traceback.print_exc()

    finally:

        sys.argv = (
            original_argv
        )

    return {
        "publication_date": (
            target_date
        ),
        "ml_return_code": (
            return_code
        ),
        "status": (
            "SUCCESS"
            if return_code == 0
            else "ML_FAILED"
        ),
        "log_file": (
            str(
                log_path
            )
        ),
        "error": (
            error_text
        ),
    }


def _tail_text(
    path: Path,
    *,
    line_count: int = 25,
) -> str:

    try:

        lines = (
            path.read_text(
                encoding="utf-8",
                errors="replace",
            )
            .splitlines()
        )

    except Exception as exc:

        return (
            f"Could not read log: "
            f"{type(exc).__name__}: {exc}"
        )

    return "\n".join(
        lines[
            -line_count:
        ]
    )



def _cuda_available_safely() -> bool:
    """
    Detect whether device:auto will choose CUDA.

    Multi-process workers are not supported on one CUDA device
    because every worker would load both transformer models onto
    the same GPU.
    """

    try:

        import torch

        return bool(
            torch.cuda.is_available()
        )

    except Exception:

        return False


def run_ml_dates(
    publication_dates: list[str],
    *,
    workers: int = 1,
    cpu_threads_per_worker: int = 0,
) -> list[dict]:
    """
    Run historical ML with persistent model workers.

    Unlike the previous implementation:
      - worker processes persist
      - base FinBERT loads once per worker
      - fine-tuned relevance model loads once per worker
      - CPU threads are divided across workers
      - Hugging Face network access is disabled
    """

    if workers < 1:

        raise ValueError(
            "--ml-workers must be >= 1"
        )

    publication_dates = list(
        publication_dates
    )

    total = len(
        publication_dates
    )

    if not publication_dates:

        return []

    resolved_threads = (
        _resolve_threads_per_worker(
            workers=(
                workers
            ),
            requested=(
                cpu_threads_per_worker
            ),
        )
    )

    print()

    print(
        "PHASE-2 ML EXECUTION "
        "— PERSISTENT WORKERS"
    )

    print(
        f"  Dates              : "
        f"{total}"
    )

    print(
        f"  Persistent workers : "
        f"{workers}"
    )

    print(
        f"  CPU threads/worker : "
        f"{resolved_threads}"
    )

    print(
        "  Model loads        : "
        f"{workers} total "
        "(once per worker)"
    )

    print(
        "  Hugging Face mode  : "
        "OFFLINE"
    )

    print(
        f"  Logs               : "
        f"{ML_LOG_ROOT}"
    )

    print()

    # ========================================================
    # SAFE SINGLE-WORKER MODE
    #
    # This is the preferred path for GPU inference and for
    # memory-constrained systems. It avoids ProcessPool entirely.
    # Both transformer models are loaded once in THIS process and
    # reused across every publication date.
    # ========================================================

    if workers == 1:

        print(
            "  Execution mode      : "
            "DIRECT PERSISTENT PROCESS"
        )

        print()

        _init_persistent_ml_worker(
            resolved_threads
        )

        results = []

        for (
            index,
            target_date,
        ) in enumerate(
            publication_dates,
            start=1,
        ):

            item = (
                _run_one_ml_date_persistent(
                    target_date
                )
            )

            results.append(
                item
            )

            print(
                f"[ML DONE "
                f"{index}/"
                f"{total}] "
                f"{target_date} "
                f"{item['status']} "
                f"return_code="
                f"{item['ml_return_code']}"
            )

            if (
                item[
                    "status"
                ]
                != "SUCCESS"
            ):

                if item.get(
                    "error"
                ):

                    print(
                        "    "
                        f"{item['error']}"
                    )

                if item.get(
                    "log_file"
                ):

                    print(
                        "    "
                        f"log="
                        f"{item['log_file']}"
                    )

                    print()

                    print(
                        _tail_text(
                            Path(
                                item[
                                    "log_file"
                                ]
                            )
                        )
                    )

                    print()

        return results

    # ========================================================
    # MULTI-PROCESS MODE
    # ========================================================

    if _cuda_available_safely():

        raise RuntimeError(
            "Multiple persistent ML workers are unsafe with "
            "device:auto on a CUDA system because every worker "
            "would load both transformer models onto the same "
            "GPU. Re-run with --ml-workers 1."
        )

    print(
        "  Execution mode      : "
        "MULTI-PROCESS PERSISTENT"
    )

    print(
        "  WARNING             : "
        "each worker holds its own copy of both transformer models"
    )

    print()

    results = []

    completed = 0

    # CPU-only multi-process mode. Each process owns one
    # persistent transformer pipeline and reuses it across dates.
    with ProcessPoolExecutor(
        max_workers=(
            workers
        ),
        initializer=(
            _init_persistent_ml_worker
        ),
        initargs=(
            resolved_threads,
        ),
    ) as executor:

        future_to_date = {
            executor.submit(
                _run_one_ml_date_persistent,
                target_date,
            ): target_date

            for target_date
            in publication_dates
        }

        for future in as_completed(
            future_to_date
        ):

            target_date = (
                future_to_date[
                    future
                ]
            )

            completed += 1

            try:

                item = (
                    future.result()
                )

            except Exception as exc:

                item = {
                    "publication_date": (
                        target_date
                    ),
                    "ml_return_code": -1,
                    "status": "ML_FAILED",
                    "log_file": "",
                    "error": (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                }

            results.append(
                item
            )

            print(
                f"[ML DONE "
                f"{completed}/"
                f"{total}] "
                f"{target_date} "
                f"{item['status']} "
                f"return_code="
                f"{item['ml_return_code']}"
            )

            if (
                item[
                    "status"
                ]
                != "SUCCESS"
            ):

                if item.get(
                    "error"
                ):

                    print(
                        "    "
                        f"{item['error']}"
                    )

                if item.get(
                    "log_file"
                ):

                    print(
                        "    "
                        f"log="
                        f"{item['log_file']}"
                    )

                    print()

                    print(
                        _tail_text(
                            Path(
                                item[
                                    "log_file"
                                ]
                            )
                        )
                    )

                    print()

    return sorted(
        results,
        key=lambda item: (
            item[
                "publication_date"
            ]
        ),
    )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    args = parse_args()

    if args.ml_workers < 1:

        raise ValueError(
            "--ml-workers must be >= 1"
        )

    if args.ml_cpu_threads_per_worker < 0:

        raise ValueError(
            "--ml-cpu-threads-per-worker must be >= 0"
        )

    start_date = (
        parse_cli_date(
            args.start_date
        )
    )

    end_date = (
        parse_cli_date(
            args.end_date
        )
    )

    if (
        start_date
        > end_date
    ):

        raise ValueError(
            "--start-date must be "
            "<= --end-date"
        )

    if args.ml_only:

        mode = "ML_ONLY"

    elif args.full:

        mode = "FULL"

    else:

        # Safer default for historical work:
        # Stage-1 first, inspect it, then explicitly run ML.
        mode = "STAGE1_ONLY"

    started_at = (
        datetime.now(
            SRI_LANKA_TZ
        )
    )

    print()

    print(
        "=" * 80
    )

    print(
        "BANK SUPERVISION NEWS "
        "HISTORICAL INTELLIGENCE"
    )

    print(
        "=" * 80
    )

    print(
        f"Mode             : "
        f"{mode}"
    )

    print(
        f"Start date       : "
        f"{start_date}"
    )

    print(
        f"End date         : "
        f"{end_date}"
    )

    stage1_results = []

    ml_results = []

    global_stage1_diagnostics = None

    publication_dates = []

    if mode in {
        "STAGE1_ONLY",
        "FULL",
    }:

        (
            stage1_results,
            publication_dates,
            global_stage1_diagnostics,
        ) = run_stage1_range(
            start_date=(
                start_date
            ),
            end_date=(
                end_date
            ),
        )

        stage1_failures = sum(
            item[
                "failures"
            ]
            for item in (
                stage1_results
            )
        )

        if stage1_failures > 0:

            print()

            print(
                "Stage-1 contains failures. "
                "ML will NOT start."
            )

            mode = (
                "STAGE1_FAILED"
            )

    if (
        mode == "ML_ONLY"
    ):

        publication_dates = (
            successful_stage1_dates(
                start_date=(
                    start_date
                ),
                end_date=(
                    end_date
                ),
            )
        )

        print()

        print(
            f"Successful Stage-1 "
            f"dates found: "
            f"{len(publication_dates)}"
        )

        ml_results = (
            run_ml_dates(
                publication_dates,
                workers=(
                    args.ml_workers
                ),
                cpu_threads_per_worker=(
                    args.ml_cpu_threads_per_worker
                ),
            )
        )

    elif (
        mode == "FULL"
    ):

        ml_results = (
            run_ml_dates(
                publication_dates,
                workers=(
                    args.ml_workers
                ),
                cpu_threads_per_worker=(
                    args.ml_cpu_threads_per_worker
                ),
            )
        )

    completed_at = (
        datetime.now(
            SRI_LANKA_TZ
        )
    )

    SUMMARY_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary_path = (
        SUMMARY_ROOT
        / (
            "historical_intelligence_"
            f"{started_at.strftime('%Y%m%d_%H%M%S')}"
            ".json"
        )
    )

    stage1_totals = {
        "dates_processed": (
            len(
                stage1_results
            )
        ),
        "articles": sum(
            item[
                "articles"
            ]
            for item
            in stage1_results
        ),
        "include_candidates": sum(
            item[
                "include_candidates"
            ]
            for item
            in stage1_results
        ),
        "watch": sum(
            item[
                "watch"
            ]
            for item
            in stage1_results
        ),
        "excluded_noise": sum(
            item[
                "excluded_noise"
            ]
            for item
            in stage1_results
        ),
        "failures": sum(
            item[
                "failures"
            ]
            for item
            in stage1_results
        ),
    }

    ml_success = sum(
        item[
            "status"
        ]
        == "SUCCESS"
        for item
        in ml_results
    )

    ml_failed = (
        len(
            ml_results
        )
        - ml_success
    )

    payload = {
        "started_at": (
            started_at
            .isoformat()
        ),
        "completed_at": (
            completed_at
            .isoformat()
        ),
        "requested_mode": (
            "ML_ONLY"
            if args.ml_only
            else (
                "FULL"
                if args.full
                else "STAGE1_ONLY"
            )
        ),
        "final_mode": (
            mode
        ),
        "start_date": (
            start_date
            .isoformat()
        ),
        "end_date": (
            end_date
            .isoformat()
        ),
        "active_sources": (
            s1.ACTIVE_SOURCE_FOLDERS
        ),
        "publication_dates": (
            publication_dates
        ),
        "stage1_discovery": (
            global_stage1_diagnostics
        ),
        "stage1_totals": (
            stage1_totals
        ),
        "stage1_results": (
            stage1_results
        ),
        "ml_totals": {
            "dates_attempted": (
                len(
                    ml_results
                )
            ),
            "successful": (
                ml_success
            ),
            "failed": (
                ml_failed
            ),
        },
        "ml_execution": {
            "worker_mode": (
                "PERSISTENT_MODEL_REUSE"
            ),
            "workers": (
                args.ml_workers
            ),
            "requested_cpu_threads_per_worker": (
                args.ml_cpu_threads_per_worker
            ),
            "offline_mode": True,
            "log_root": (
                str(
                    ML_LOG_ROOT
                )
            ),
        },

        "ml_results": (
            ml_results
        ),
    }

    summary_path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()

    print(
        "=" * 80
    )

    print(
        "HISTORICAL INTELLIGENCE "
        "RUN FINISHED"
    )

    print(
        "=" * 80
    )

    if stage1_results:

        print(
            f"Stage-1 dates      : "
            f"{stage1_totals['dates_processed']}"
        )

        print(
            f"Stage-1 articles   : "
            f"{stage1_totals['articles']}"
        )

        print(
            f"Include candidates : "
            f"{stage1_totals['include_candidates']}"
        )

        print(
            f"Watch              : "
            f"{stage1_totals['watch']}"
        )

        print(
            f"Excluded as noise  : "
            f"{stage1_totals['excluded_noise']}"
        )

        print(
            f"Stage-1 failures   : "
            f"{stage1_totals['failures']}"
        )

    if ml_results:

        print(
            f"ML dates attempted : "
            f"{len(ml_results)}"
        )

        print(
            f"ML successful      : "
            f"{ml_success}"
        )

        print(
            f"ML failed          : "
            f"{ml_failed}"
        )

    print(
        f"Summary            : "
        f"{summary_path}"
    )

    if (
        stage1_totals[
            "failures"
        ]
        > 0
        or ml_failed
        > 0
    ):

        raise SystemExit(
            1
        )


if __name__ == "__main__":

    main()
