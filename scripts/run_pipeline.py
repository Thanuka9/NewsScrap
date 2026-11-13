from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import os
import re
from pathlib import Path
import subprocess
from _runtime import run_process, locked_main
from bank_intel.common.execution import run_logged_process
import sys
import time
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRI_LANKA_TZ = ZoneInfo("Asia/Colombo")

SCRIPTS = PROJECT_ROOT / "scripts"

COLLECTION_SCRIPT = SCRIPTS / "run_collection.py"
STAGE1_SCRIPT = SCRIPTS / "run_intelligence_stage1.py"
ML_SCRIPT = SCRIPTS / "run_intelligence_ml.py"
CORPUS_SCRIPT = SCRIPTS / "run_ml_corpus_analysis.py"
TAXONOMY_SCRIPT = SCRIPTS / "run_supervisory_taxonomy.py"
REPORT_SCRIPT = SCRIPTS / "build_financial_brief_v2.py"
RECOVERY_AUDIT_SCRIPT = SCRIPTS / "recover_collection_gaps.py"
SOURCE_FLOW_SCRIPT = SCRIPTS / "audit_news_flow.py"
INTEGRATED_RELEASE_SCRIPT = SCRIPTS / "run_integrated_release.py"

RUN_SUMMARY_ROOT = PROJECT_ROOT / "data" / "run_summaries"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the complete daily bank-supervision news pipeline: "
            "collection -> Stage 1 -> Phase 2 ML -> Phase 3 corpus analysis "
            "-> supervisory taxonomy -> final HTML brief."
        )
    )

    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument(
        "--date",
        help=(
            "Publication/end date YYYY-MM-DD. Defaults to today's "
            "Asia/Colombo date."
        ),
    )

    date_group.add_argument(
        "--from-date",
        help=(
            "Start date YYYY-MM-DD for the reporting window. "
            "The end date is calculated from --days."
        ),
    )

    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help=(
            "Inclusive number of days in the corpus/taxonomy/report "
            "window. With --date, the window ends on that date; with "
            "--from-date, it starts on that date. Default: 1. Earlier "
            "dates in a multi-day window must already have Phase-2 outputs."
        ),
    )

    parser.add_argument(
        "--skip-collection",
        action="store_true",
        help=(
            "Do not run the live collectors. Use for a historical date "
            "that has already been staged, or when collection was run "
            "separately."
        ),
    )
    parser.add_argument('--collection-timeout-seconds', type=int, default=7200,
                        help='Total collector-stage time limit (default: 7200 seconds).')
    parser.add_argument('--resume-final', action='store_true',
                        help='Reuse verified Stage 1, ML, corpus and taxonomy; run final reporting only.')

    parser.add_argument(
        "--with-diagnostics",
        action="store_true",
        help=(
            "Also run corpus clustering, similarity pairs and the supervisory "
            "taxonomy. These are diagnostics; the edition does not use them."
        ),
    )

    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "Allow a date with zero Stage-1 articles. By default an empty "
            "date is treated as a coverage problem and the pipeline stops."
        ),
    )

    return parser.parse_args()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"{label} missing: {path}")


def run_step(
    *,
    name: str,
    command: list[str],
    steps: list[dict],
    timeout: int = 3600,
    review_exit: bool = False,
) -> int:
    print()
    print("=" * 92)
    print(name)
    print("=" * 92)
    print("Command:", " ".join(command), flush=True)

    started = time.perf_counter()
    started_at = datetime.now(SRI_LANKA_TZ)
    log_path = PROJECT_ROOT/'data/logs/stages'/(
        started_at.strftime('%Y%m%d_%H%M%S_%f')+'_'+re.sub(r'[^a-z0-9]+','_',name.lower()).strip('_')+'.log')
    print('Stage log:', log_path, flush=True)
    from bank_intel.common.reliability import atomic_json
    state_path = PROJECT_ROOT/'data/runtime/current_step.json'
    state = {'name': name, 'pid': os.getpid(), 'started_at': started_at.isoformat(),
             'log_path': str(log_path), 'status': 'RUNNING', 'command': command}
    atomic_json(state_path, state)

    try:
        result = run_logged_process(command, cwd=PROJECT_ROOT, timeout=timeout,
                                    log_path=log_path, runner=run_process)
    except (Exception, KeyboardInterrupt) as exc:
        failed = {'name': name, 'command': command, 'started_at': started_at.isoformat(),
            'completed_at': datetime.now(SRI_LANKA_TZ).isoformat(),
            'elapsed_seconds': round(time.perf_counter()-started, 3),
            'return_code': None, 'status': 'TIMED_OUT' if isinstance(exc, subprocess.TimeoutExpired) else 'FAILED',
            'timeout_seconds': timeout, 'error': str(exc), 'log_path': str(log_path)}
        steps.append(failed)
        atomic_json(state_path, {**state, **failed})
        raise

    completed_at = datetime.now(SRI_LANKA_TZ)
    elapsed = time.perf_counter() - started

    step = {
        "name": name,
        "command": command,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "elapsed_seconds": round(elapsed, 3),
        "return_code": result.returncode,
        "status": "SUCCESS" if result.returncode == 0 else "REVIEW_REQUIRED" if review_exit and result.returncode == 2 else "FAILED",
        "log_path": str(log_path),
    }
    steps.append(step)
    if result.returncode:
        step['error_tail'] = result.output_tail
    atomic_json(state_path, {**state, **step})

    if result.returncode != 0 and not (review_exit and result.returncode == 2):
        raise RuntimeError(
            f"{name} failed with return code {result.returncode}. Log: {log_path}\n"
            + '\n'.join(result.output_tail[-12:])
        )
    return result.returncode


def validate_date(value: str) -> str:
    datetime.strptime(value, "%Y-%m-%d")
    return value


def resolve_target_date(
    *,
    date_value: str | None,
    from_date_value: str | None,
    days: int,
    today: str,
) -> str:
    if from_date_value is None:
        return validate_date(date_value or today)

    start_date = datetime.strptime(
        validate_date(from_date_value),
        "%Y-%m-%d",
    ).date()
    return (start_date + timedelta(days=days - 1)).isoformat()



# NEWS_PHASE2_STRUCTURAL_CONTRACT_V3_MANIFEST_SCHEMA

def validate_phase2_structural_contract(target_date: str) -> dict:
    """
    Validate the ACTUAL Phase-2 production schema.

    Authority:
        ml_manifest.json -> articles[] -> analysis_file

    Article schema:
        analysis["ml"]["supervisory_relevance"]
    """

    import json as _p2_json
    from pathlib import Path as _P2Path

    ml_root = (
        PROJECT_ROOT
        / "data"
        / "processed"
        / "ml"
        / target_date
    )

    manifest_path = (
        ml_root
        / "ml_manifest.json"
    )

    if not manifest_path.is_file():
        raise RuntimeError(
            "PHASE-2 CONTRACT GATE FAILED: "
            f"manifest missing: {manifest_path}"
        )

    manifest = _p2_json.loads(
        manifest_path.read_text(
            encoding="utf-8-sig"
        )
    )

    if str(
        manifest.get("status", "")
    ).upper() != "SUCCESS":
        raise RuntimeError(
            "PHASE-2 CONTRACT GATE FAILED: "
            "ML manifest status is not SUCCESS"
        )

    articles = (
        manifest.get("articles", [])
        or []
    )

    expected_count = int(
        manifest.get(
            "article_count",
            len(articles),
        )
        or len(articles)
    )

    if expected_count <= 0:
        raise RuntimeError(
            "PHASE-2 CONTRACT GATE FAILED: "
            "manifest article_count is zero"
        )

    if len(articles) != expected_count:
        raise RuntimeError(
            "PHASE-2 CONTRACT GATE FAILED: "
            f"manifest articles={len(articles)} "
            f"but article_count={expected_count}"
        )

    operational_distribution = {
        "KEEP": 0,
        "NOISE": 0,
    }

    stage_b_distribution = {
        "WATCH": 0,
        "RELEVANT": 0,
    }

    stage_b_executed_count = 0
    checked = 0
    errors = []

    for index, item in enumerate(
        articles,
        start=1,
    ):
        raw_path = str(
            item.get(
                "analysis_file",
                "",
            )
            or ""
        ).strip()

        if not raw_path:
            errors.append(
                f"ARTICLE_{index}:"
                "MISSING_ANALYSIS_FILE"
            )
            continue

        analysis_path = _P2Path(
            raw_path
        )

        if not analysis_path.is_absolute():
            analysis_path = (
                PROJECT_ROOT
                / analysis_path
            )

        if not analysis_path.is_file():
            errors.append(
                f"ARTICLE_{index}:"
                f"ANALYSIS_FILE_MISSING:"
                f"{analysis_path}"
            )
            continue

        try:
            analysis = _p2_json.loads(
                analysis_path.read_text(
                    encoding="utf-8-sig"
                )
            )
        except Exception as exc:
            errors.append(
                f"ARTICLE_{index}:"
                f"UNREADABLE_ANALYSIS:"
                f"{type(exc).__name__}"
            )
            continue

        ml = (
            analysis.get("ml", {})
            or {}
        )

        relevance = (
            ml.get(
                "supervisory_relevance",
                {},
            )
            or {}
        )

        if not relevance:
            errors.append(
                f"ARTICLE_{index}:"
                "SUPERVISORY_RELEVANCE_MISSING"
            )
            continue

        architecture = str(
            relevance.get(
                "architecture",
                "",
            )
            or ""
        ).upper()

        if architecture != "TWO_STAGE":
            errors.append(
                f"ARTICLE_{index}:"
                f"INVALID_ARCHITECTURE:"
                f"{architecture!r}"
            )
            continue

        stage_a = (
            relevance.get(
                "stage_a",
                {},
            )
            or {}
        )

        stage_a_status = str(
            stage_a.get(
                "status",
                "",
            )
            or ""
        ).upper()

        stage_a_label = str(
            stage_a.get(
                "predicted_label",
                "",
            )
            or ""
        ).upper()

        if stage_a_status != "SUCCESS":
            errors.append(
                f"ARTICLE_{index}:"
                f"STAGE_A_STATUS:"
                f"{stage_a_status!r}"
            )
            continue

        if stage_a_label not in {
            "KEEP",
            "NOISE",
        }:
            errors.append(
                f"ARTICLE_{index}:"
                f"INVALID_STAGE_A_LABEL:"
                f"{stage_a_label!r}"
            )
            continue

        operational_distribution[
            stage_a_label
        ] += 1

        operational_keep = (
            relevance.get(
                "operational_keep"
            )
        )

        if not isinstance(
            operational_keep,
            bool,
        ):
            errors.append(
                f"ARTICLE_{index}:"
                "OPERATIONAL_KEEP_NOT_BOOL"
            )

        elif (
            operational_keep
            != (
                stage_a_label
                == "KEEP"
            )
        ):
            errors.append(
                f"ARTICLE_{index}:"
                "STAGE_A_OPERATIONAL_MISMATCH"
            )

        stage_b_executed = (
            relevance.get(
                "stage_b_executed"
            )
        )

        if not isinstance(
            stage_b_executed,
            bool,
        ):
            errors.append(
                f"ARTICLE_{index}:"
                "STAGE_B_EXECUTED_NOT_BOOL"
            )

        elif stage_b_executed:

            stage_b_executed_count += 1

            stage_b = (
                relevance.get(
                    "stage_b",
                    {},
                )
                or {}
            )

            stage_b_status = str(
                stage_b.get(
                    "status",
                    "",
                )
                or ""
            ).upper()

            stage_b_label = str(
                stage_b.get(
                    "predicted_label",
                    "",
                )
                or ""
            ).upper()

            if (
                stage_b_status
                != "SUCCESS"
            ):
                errors.append(
                    f"ARTICLE_{index}:"
                    f"STAGE_B_STATUS:"
                    f"{stage_b_status!r}"
                )

            if stage_b_label not in {
                "WATCH",
                "RELEVANT",
            }:
                errors.append(
                    f"ARTICLE_{index}:"
                    f"INVALID_STAGE_B_LABEL:"
                    f"{stage_b_label!r}"
                )
            else:
                stage_b_distribution[
                    stage_b_label
                ] += 1

        checked += 1

    if checked != expected_count:
        errors.append(
            "CHECKED_COUNT_MISMATCH:"
            f"checked={checked},"
            f"expected={expected_count}"
        )

    # -----------------------------------------
    # Cross-check the original/raw model summary
    # -----------------------------------------

    summary = (
        manifest.get(
            "supervisory_two_stage_summary",
            {},
        )
        or {}
    )

    summary_stage_a = (
        summary.get(
            "stage_a",
            {},
        )
        or {}
    )

    raw_distribution = (
        manifest.get(
            "raw_stage_a_distribution"
        )
        or summary_stage_a.get(
            "prediction_distribution"
        )
        or {}
    )

    raw_keep = int(
        raw_distribution.get(
            "KEEP",
            0,
        )
        or 0
    )

    raw_noise = int(
        raw_distribution.get(
            "NOISE",
            0,
        )
        or 0
    )

    if (
        raw_keep
        + raw_noise
        != expected_count
    ):
        errors.append(
            "RAW_STAGE_A_COUNT_MISMATCH:"
            f"keep={raw_keep},"
            f"noise={raw_noise},"
            f"expected={expected_count}"
        )

    # -----------------------------------------
    # Cross-check operational materialisation
    # -----------------------------------------

    manifest_operational = (
        manifest.get(
            "operational_stage_a_distribution"
        )
        or {}
    )

    if manifest_operational:

        expected_op_keep = int(
            manifest_operational.get(
                "KEEP",
                0,
            )
            or 0
        )

        expected_op_noise = int(
            manifest_operational.get(
                "NOISE",
                0,
            )
            or 0
        )

        if (
            expected_op_keep
            != operational_distribution[
                "KEEP"
            ]
            or expected_op_noise
            != operational_distribution[
                "NOISE"
            ]
        ):
            errors.append(
                "OPERATIONAL_DISTRIBUTION_MISMATCH:"
                f"manifest="
                f"{manifest_operational},"
                f"articles="
                f"{operational_distribution}"
            )

    # -----------------------------------------
    # Stage-B executes from original/raw Stage A.
    # Do NOT force it to equal operational KEEP.
    # -----------------------------------------

    summary_stage_b = (
        summary.get(
            "stage_b",
            {},
        )
        or {}
    )

    expected_stage_b = (
        summary_stage_b.get(
            "executed_count"
        )
    )

    if expected_stage_b is not None:

        expected_stage_b = int(
            expected_stage_b
            or 0
        )

        if (
            expected_stage_b
            != stage_b_executed_count
        ):
            errors.append(
                "STAGE_B_EXECUTION_MISMATCH:"
                f"manifest={expected_stage_b},"
                f"articles="
                f"{stage_b_executed_count}"
            )

    if errors:
        raise RuntimeError(
            "PHASE-2 CONTRACT GATE FAILED: "
            + "; ".join(
                errors[:25]
            )
        )

    return {
        "status": "PASS",
        "records_checked": checked,
        "manifest_article_count":
            expected_count,
        "raw_stage_a_distribution": {
            "KEEP": raw_keep,
            "NOISE": raw_noise,
        },
        "operational_stage_a_distribution":
            operational_distribution,
        "stage_b_executed":
            stage_b_executed_count,
        "stage_b_distribution":
            stage_b_distribution,
    }




# NEWS_PRODUCTION_READINESS_V1_IMPORT
from bank_intel.intelligence.production_policy import (
    apply_phase2_production_policy,
    apply_taxonomy_evidence_policy,
    export_seed_candidates,
)

def main() -> None:
    args = parse_args()

    if args.days < 1:
        raise ValueError("--days must be >= 1")

    now = datetime.now(SRI_LANKA_TZ)
    today = now.date().isoformat()

    target_date = resolve_target_date(
        date_value=args.date,
        from_date_value=args.from_date,
        days=args.days,
        today=today,
    )

    if args.resume_final:
        from resume_final_edition import main as resume_final
        raise SystemExit(resume_final(['--date', target_date, '--days', str(args.days)]))

    from bank_intel.publication.contract import sent_record
    if sent_record(PROJECT_ROOT, target_date):
        print('[ALREADY_SENT] Immutable edition retained. Use run_collection.py to collect later arrivals.')
        return

    # NEWS_SENT_EDITION_IMMUTABILITY_V1: protect the actually emailed
    # report before any collection, staging or report generation occurs.
    # Skip-collection is not an override for an immutable sent edition.
    from news_publication_history import assert_edition_not_sent
    assert_edition_not_sent(
        PROJECT_ROOT / 'data' / 'processed' / 'reports',
        datetime.strptime(target_date, '%Y-%m-%d').date(),
    )

    if target_date != today and not args.skip_collection:
        raise RuntimeError(
            "The live master collector is designed for the current "
            "operational window. For a historical test date, stage that "
            "date first and run this pipeline with --skip-collection."
        )

    required_scripts = (
        COLLECTION_SCRIPT,
        STAGE1_SCRIPT,
        ML_SCRIPT,
        CORPUS_SCRIPT,
        TAXONOMY_SCRIPT,
        REPORT_SCRIPT,
        RECOVERY_AUDIT_SCRIPT,
        INTEGRATED_RELEASE_SCRIPT,
    )

    for script in required_scripts:
        require_file(script, "Required script")

    pending_report = PROJECT_ROOT/'data/processed/reports'/target_date/f'window_{args.days}d'
    pending_report.mkdir(parents=True, exist_ok=True)
    for filename in (f'Daily Financial Sector News Brief {target_date}.html', 'supervisory_digest.html',
                             f'Daily Financial Sector News Brief {target_date} Interactive.html',
                             f'Daily Financial Sector News Brief {target_date} Interactive.validation.json'):
        (pending_report/filename).unlink(missing_ok=True)
    from bank_intel.common.reliability import atomic_json
    atomic_json(pending_report/'report_manifest.json', {'status':'RUNNING', 'publication_date':target_date})

    steps: list[dict] = []
    pipeline_started = datetime.now(SRI_LANKA_TZ)
    final_status = "FAILED"
    error_message = None

    RUN_SUMMARY_ROOT.mkdir(parents=True, exist_ok=True)

    summary_path = (
        RUN_SUMMARY_ROOT
        / f"news_pipeline_{pipeline_started.strftime('%Y%m%d_%H%M%S')}.json"
    )

    try:
        if not args.skip_collection:
            run_step(
                name="1/6 COLLECTION",
                command=[
                    sys.executable,
                    str(COLLECTION_SCRIPT),
                ],
                steps=steps,
                timeout=args.collection_timeout_seconds,
            )
        else:
            print()
            print("=" * 92)
            print("COLLECTION SKIPPED")
            print("=" * 92)
            print(
                "Using staged articles already present for publication date:",
                target_date,
            )

        run_step(
            name="2/6 STAGE 1 RULE INTELLIGENCE",
            command=[
                sys.executable,
                str(STAGE1_SCRIPT),
                "--date",
                target_date,
            ],
            steps=steps,
        )

        stage1_manifest_path = (
            PROJECT_ROOT
            / "data"
            / "processed"
            / "stage1"
            / target_date
            / "stage1_manifest.json"
        )
        require_file(stage1_manifest_path, "Stage-1 manifest")
        stage1_manifest = load_json(stage1_manifest_path)

        if stage1_manifest.get("status") != "SUCCESS":
            raise RuntimeError(
                "Stage-1 manifest is not SUCCESS: "
                f"{stage1_manifest.get('status')}"
            )

        stage1_count = int(stage1_manifest.get("article_count", 0) or 0)

        if stage1_count == 0 and not args.allow_empty:
            raise RuntimeError(
                f"No staged articles matched publication date {target_date}. "
                "This is treated as a coverage issue. Check collection before "
                "continuing, or use --allow-empty only when zero news is "
                "confirmed to be genuine."
            )

        if stage1_count == 0:
            print()
            print("No articles for this date; downstream report generation skipped.")
            final_status = "SUCCESS_EMPTY"
            return

        run_step(
            name="3/6 PHASE 2 ML + ENTITY INTELLIGENCE",
            command=[
                sys.executable,
                str(ML_SCRIPT),
                "--date",
                target_date,
            ],
            steps=steps,
        )

        ml_manifest_path = (
            PROJECT_ROOT
            / "data"
            / "processed"
            / "ml"
            / target_date
            / "ml_manifest.json"
        )
        require_file(ml_manifest_path, "Phase-2 ML manifest")
        ml_manifest = load_json(ml_manifest_path)
        ml_count = int(ml_manifest.get("article_count", 0) or 0)

        if ml_count != stage1_count:
            raise RuntimeError(
                "Stage-1 / Phase-2 count mismatch: "
                f"Stage-1={stage1_count}, Phase-2={ml_count}"
            )


        # NEWS_PRODUCTION_READINESS_V1_PHASE2
        production_policy_summary = (
            apply_phase2_production_policy(
                target_date
            )
        )

        print()

        print(
            "Production Stage-A policy : "
            f"{production_policy_summary}"
        )

        phase2_contract = validate_phase2_structural_contract(target_date)
        print(f"Phase-2 structural QA    : {phase2_contract}")

        # Preserve articles from a held Monday edition in Tuesday's recovery
        # audit; do not claim that a held edition was previously distributed.
        from bank_intel.publication.backlog import earliest_unpublished_day
        report_root_for_backlog = PROJECT_ROOT / 'data' / 'processed' / 'reports'
        earliest_backlog = earliest_unpublished_day(
            report_root_for_backlog, datetime.strptime(target_date, '%Y-%m-%d').date(),
            days=int(args.days))
        lookback_for_recovery = max(1, (datetime.strptime(target_date, '%Y-%m-%d').date()
                                         - earliest_backlog).days - (int(args.days) - 1))
        print(f'[UNSENT-EDITION COVERAGE] publication dates {earliest_backlog} through {target_date}; '
              f'recovery lookback={lookback_for_recovery} day(s)')

        # NEWS_COLLECTION_COMPLETENESS_GATE_V1
        run_step(
            name="COLLECTION COMPLETENESS AUDIT",
            command=[
                sys.executable,
                str(RECOVERY_AUDIT_SCRIPT),
                "--end-date",
                target_date,
                "--days",
                str(args.days),
                "--late-arrival-days",
                str(lookback_for_recovery),
            ],
            steps=steps,
        )

        completeness_manifest = (
            PROJECT_ROOT
            / "data"
            / "processed"
            / "collection_completeness"
            / target_date
            / f"window_{args.days}d"
            / "collection_completeness_manifest.json"
        )
        require_file(completeness_manifest, "Collection completeness manifest")
        completeness = load_json(completeness_manifest)

        if completeness.get("status") != "PASS":
            raise RuntimeError(
                "COLLECTION COMPLETENESS FAILED: one or more audit listing checks failed"
            )

        recovered_count = int(completeness.get("recovered_article_count", 0) or 0)
        if recovered_count != 0:
            raise RuntimeError(
                "COLLECTION COMPLETENESS FAILED: "
                f"{recovered_count} material article(s) are absent from the normal collection/ML path. "
                "Publication is blocked; the report will not inject them manually."
            )

        # Corpus clustering (K-means/HDBSCAN), similarity pairs and the
        # supervisory taxonomy feed no part of the governed edition: story
        # selection, event matching and sections are decided by the publication
        # rules. Run them only as an opt-in diagnostic, off the daily path.
        if args.with_diagnostics:
            run_step(
                name="4/6 PHASE 3 CORPUS INTELLIGENCE",
                command=[
                    sys.executable,
                    str(CORPUS_SCRIPT),
                    "--end-date",
                    target_date,
                    "--days",
                    str(args.days),
                ],
                steps=steps,
            )

            corpus_root = (
                PROJECT_ROOT
                / "data"
                / "processed"
                / "ml_corpus"
                / target_date
                / f"window_{args.days}d"
            )
            corpus_manifest_path = corpus_root / "corpus_manifest.json"
            require_file(corpus_manifest_path, "Phase-3 corpus manifest")

            run_step(
                name="5/6 SUPERVISORY TAXONOMY",
                command=[
                    sys.executable,
                    str(TAXONOMY_SCRIPT),
                    "--end-date",
                    target_date,
                    "--days",
                    str(args.days),
                ],
                steps=steps,
            )

            taxonomy_root = (
                PROJECT_ROOT
                / "data"
                / "processed"
                / "taxonomy"
                / target_date
                / f"window_{args.days}d"
            )
            taxonomy_manifest_path = taxonomy_root / "taxonomy_manifest.json"
            require_file(taxonomy_manifest_path, "Taxonomy manifest")


            # NEWS_PRODUCTION_READINESS_V1_TAXONOMY
            taxonomy_evidence_summary = (
                apply_taxonomy_evidence_policy(
                    target_date,
                    int(
                        args.days
                    ),
                )
            )

            seed_export_summary = (
                export_seed_candidates(
                    target_date,
                    int(
                        args.days
                    ),
                )
            )

            print()

            print(
                "Taxonomy evidence policy : "
                f"{taxonomy_evidence_summary}"
            )

            print(
                "Human seed candidates     : "
                f"{seed_export_summary}"
            )

        else:
            print("\nCorpus clustering and taxonomy diagnostics skipped (use --with-diagnostics).")

        # NEWS_SOURCE_FRESHNESS_GATE_V1: block before final HTML is created.
        from audit_news_flow import source_keys_for_date, EXPECTED_SURFACES, iso_date
        gaps=[];recorded=[]
        for source in source_keys_for_date(target_date):
            file=PROJECT_ROOT/'data'/'source_status'/f'{source}.json'
            if not file.is_file():
                gaps.append('MISSING_SOURCE:'+source); continue
            try:
                obj=load_json(file)
                day=iso_date(obj.get('completed_at'))
                surfaces = obj.get('discovery_surfaces') or []
                names = set()
                for surface in surfaces:
                    if isinstance(surface, str):
                        name = surface.strip().casefold()
                    elif isinstance(surface, dict):
                        name = str(
                            surface.get('name') or
                            surface.get('listing_name') or ''
                        ).strip().casefold()
                    else:
                        name = ''
                    if name:
                        names.add(name)
                if not day or day.isoformat()!=target_date: gaps.append('STALE_SOURCE:'+source)
                if obj.get('execution_status')!='SUCCESS': gaps.append('FAILED_SOURCE:'+source)
                if len(names)<EXPECTED_SURFACES[source]: gaps.append('INCOMPLETE_SURFACES:'+source)
                if source=='daily_mirror':
                    required={'business','homepage','breaking news','top stories','latest news'}
                    missing=required-names
                    if missing: gaps.append('MISSING_DM_SECTIONS:'+','.join(sorted(missing)))
                # Recorded per-URL fetch and listing failures do not make the
                # source's coverage unknown; the signed coverage worksheet in
                # the release gate holds the edition until each is accepted.
                if obj.get('unresolved_fetch_urls') or int(obj.get('fetch_failures') or 0): recorded.append('UNRESOLVED_FETCHES:'+source)
                if obj.get('failed_discovery_surfaces'): recorded.append('FAILED_SURFACE:'+source)
            except (OSError,ValueError,TypeError) as exc:
                gaps.append('INVALID_SOURCE_STATUS:'+source+':'+type(exc).__name__)
        if gaps: raise RuntimeError('SEVEN-SOURCE PUBLICATION GATE: '+'; '.join(gaps))
        if recorded: print('[COVERAGE EXCEPTIONS FOR SIGNED REVIEW]','; '.join(recorded))

        run_step(
            name="6/6 FINAL FINANCIAL BRIEF",
            command=[
                sys.executable,
                str(REPORT_SCRIPT),
                "--end-date",
                target_date,
                "--days",
                str(args.days),
            ],
            steps=steps,
        )

        report_root = (
            PROJECT_ROOT
            / "data"
            / "processed"
            / "reports"
            / target_date
            / f"window_{args.days}d"
        )

        brief_name = f"Daily Financial Sector News Brief {target_date}"
        final_html = report_root / f"{brief_name} Source Preview.html"
        report_manifest_path = report_root / "legacy_report_manifest.json"

        require_file(final_html, "Final HTML brief")
        require_file(report_manifest_path, "Report manifest")

        report_manifest = load_json(report_manifest_path)

        if report_manifest.get("status") != "SUCCESS":
            raise RuntimeError(
                "Report manifest is not SUCCESS: "
                f"{report_manifest.get('status')}"
            )

        report_qa = report_manifest.get("qa") or {}
        if report_qa.get("status") not in ("PASS", "PASS_WITH_PREVIEW_WARNINGS"):
            raise RuntimeError(
                "Report QA is not PASS: "
                + str(report_qa.get("failures"))
            )
        if report_qa.get("preview_warnings"):
            print("Source-preview warnings (governed edition has its own QA):", report_qa["preview_warnings"])

        production_alias = report_root / "legacy_source_preview.html"
        require_file(production_alias, "Production alias")

        if final_html.read_bytes() != production_alias.read_bytes():
            raise RuntimeError(
                "PUBLICATION PARITY FAILED: named brief and supervisory_digest.html differ"
            )

        published_html = final_html.read_text(encoding="utf-8")
        if "�" in published_html or re.search(
            r"""class=["']dot["'][^>]*>\s*\?\s*</span>""",
            published_html,
            flags=re.I,
        ):
            raise RuntimeError("PUBLISHED HTML CONTAINS BROKEN TEXT/SYMBOL MARKERS")

        # Final report is not accepted until current-run articles can be
        # reconciled against the final review and previous passing edition.
        # In --skip-collection mode, statuses may be from an earlier run;
        # create the audit for review without enforcing their freshness.
        flow_command = [
            sys.executable, str(SOURCE_FLOW_SCRIPT),
            "--end-date", target_date, "--days", str(args.days),
        ]
        # --skip-collection is NOT permission to waive source coverage.
        flow_command.append("--enforce")
        run_step(
            name="7/7 SOURCE COVERAGE AND CROSS-DAY AUDIT",
            command=flow_command,
            steps=steps,
        )

        # NEWS_OCT26_INTEGRATED_RELEASE: legacy builder produces the source
        # article/audit population only; integrated edition is authoritative.
        # Existing collection, Stage-1, fine-tuned Stage-A/B, Phase-3 and
        # taxonomy are preserved. Never run a standalone candidate alongside
        # the production edition or publish the legacy builder's HTML.
        integrated_cmd = [sys.executable, str(INTEGRATED_RELEASE_SCRIPT),
                          '--date', target_date, '--days', str(args.days),
                          '--project-root', str(PROJECT_ROOT)]
        print("\n=== INTEGRATED ARTICLE / MODEL / SENT / RELEASE GATE ===")
        integrated_code=run_step(name='INTEGRATED EDITION AND INDEPENDENT RELEASE GATE',
                                 command=integrated_cmd,steps=steps,review_exit=True)
        if integrated_code == 2:
            final_status = 'REVIEW_REQUIRED'
            status_file=report_root/'integrated_release_result.json'
            error_message=('Independent editorial/coverage QA HOLD. See '+str(status_file))
            print('\nNEWS PIPELINE REVIEW REQUIRED — no sendable HTML generated')
            print(error_message)
            raise SystemExit(2)
        if integrated_code != 0:
            raise RuntimeError('Integrated release failed: '+str(integrated_code))
        # The named and alias files are now generated only by integrated
        # release after independent QA; existing published locks remain.
        final_html = report_root / f"{brief_name}.html"
        production_alias = report_root / "supervisory_digest.html"
        report_manifest_path = report_root / "report_manifest.json"
        from bank_intel.publication.contract import validate_release
        validate_release(PROJECT_ROOT, target_date)
        run_step(name="FINAL GOVERNED SOURCE FLOW", command=flow_command, steps=steps)
        require_file(final_html, 'Integrated final attachment')
        require_file(production_alias, 'Integrated production alias')
        if final_html.read_bytes()!=production_alias.read_bytes():
            raise RuntimeError('INTEGRATED_ATTACHMENT_ALIAS_MISMATCH')

        # Browser dashboard derives only from the final, independently checked
        # edition. It must not replace QA-bound bytes or distributed archives.
        interactive_html = report_root / f"{brief_name} Interactive.html"
        run_step(
            name="INTERACTIVE FINANCIAL BRIEF",
            command=[sys.executable,
                     str(PROJECT_ROOT / "scripts" / "build_interactive_brief.py"),
                     "--report", str(final_html), "--output", str(interactive_html)],
            steps=steps,
        )
        require_file(interactive_html, "Interactive financial brief")
        interactive_validation = interactive_html.with_suffix(".validation.json")
        require_file(interactive_validation, "Interactive brief validation")
        dashboard_receipt = json.loads(interactive_validation.read_text(encoding="utf-8"))
        governed_manifest = json.loads(report_manifest_path.read_text(encoding="utf-8"))
        if (not dashboard_receipt.get("source_unchanged")
                or dashboard_receipt.get("source_sha256") != governed_manifest.get("sha256")
                or dashboard_receipt.get("stories") != governed_manifest.get("consolidated_event_count")):
            raise RuntimeError("INTERACTIVE_BRIEF_SOURCE_OR_COUNT_MISMATCH")
        governed_manifest["interactive_dashboard"] = str(interactive_html)
        governed_manifest["interactive_dashboard_validation"] = str(interactive_validation)
        governed_manifest["interactive_dashboard_role"] = "BROWSER_READING_VIEW_OF_QA_PASSED_EDITION"
        atomic_json(report_manifest_path, governed_manifest)
        # Stable target for the Financial Brief desktop shortcut.
        import shutil as _dashboard_shutil
        latest_dashboard = PROJECT_ROOT / "data" / "delivery" / "latest" / "financial_brief_interactive.html"
        latest_dashboard.parent.mkdir(parents=True, exist_ok=True)
        latest_dashboard_tmp = latest_dashboard.with_suffix(".tmp")
        _dashboard_shutil.copyfile(interactive_html, latest_dashboard_tmp)
        latest_dashboard_tmp.replace(latest_dashboard)


        final_status = "SUCCESS"

        print()
        print("=" * 92)
        print("NEWS PIPELINE COMPLETE")
        print("=" * 92)
        print(f"Publication date     : {target_date}")
        print(f"Window days          : {args.days}")
        print(f"Articles analysed    : {stage1_count}")
        print(f"Final attachment     : {final_html}")
        print(f"Interactive dashboard: {interactive_html}")
        print(f"Report manifest      : {report_manifest_path}")

        learning_review = report_root / "model_learning_review.csv"
        if learning_review.is_file():
            print(f"Internal ML review   : {learning_review}")

        print()
        print(
            f"External/stakeholder file: {brief_name}.html\n"
            f"Browser dashboard: {brief_name} Interactive.html\n"
            "JSON, article CSV, manifests and model-learning review remain "
            "internal audit/QA outputs."
        )

    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        print()
        print("=" * 92)
        print("NEWS PIPELINE FAILED")
        print("=" * 92)
        print(error_message)
        raise

    finally:
        if final_status != 'SUCCESS':
            for filename in (f'Daily Financial Sector News Brief {target_date}.html', 'supervisory_digest.html',
                             f'Daily Financial Sector News Brief {target_date} Interactive.html',
                             f'Daily Financial Sector News Brief {target_date} Interactive.validation.json'):
                (pending_report/filename).unlink(missing_ok=True)
            if final_status != 'REVIEW_REQUIRED':
                atomic_json(pending_report/'report_manifest.json', {'status':'FAILED', 'publication_date':target_date, 'error':error_message})
        completed = datetime.now(SRI_LANKA_TZ)

        payload = {
            "status": final_status,
            "error": error_message,
            "started_at": pipeline_started.isoformat(),
            "completed_at": completed.isoformat(),
            "target_publication_date": target_date,
            "days": args.days,
            "collection_skipped": bool(args.skip_collection),
            "steps": steps,
        }

        summary_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print()
        print(f"Pipeline run summary : {summary_path}")


if __name__ == "__main__":
    locked_main(main)
