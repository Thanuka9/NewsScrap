from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import uuid
from zoneinfo import ZoneInfo

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
)
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger


# NEWS_FULL_PIPELINE_SCHEDULER_V1
# The scheduler has one responsibility:
# invoke the same governed run_pipeline.py path that passed production QA.
#
# It must NOT maintain a second, partial intelligence workflow.


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

PIPELINE_SCRIPT = (
    PROJECT_ROOT
    / "scripts"
    / "run_pipeline.py"
)

LOG_DIR = (
    PROJECT_ROOT
    / "logs"
)

LOG_FILE = (
    LOG_DIR
    / "scheduler.log"
)

SCHEDULER_STATUS_FILE = (
    PROJECT_ROOT
    / "data"
    / "scheduler_status.json"
)

SRI_LANKA_TZ = ZoneInfo(
    "Asia/Colombo"
)

SCHEDULE_HOURS = "8-17"
SCHEDULE_MINUTE = 0


def configure_logging() -> logging.Logger:
    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    logger = logging.getLogger(
        "bank_intel_scheduler"
    )

    logger.setLevel(
        logging.INFO
    )

    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    )

    console_handler = logging.StreamHandler(
        sys.stdout
    )
    console_handler.setFormatter(
        formatter
    )
    logger.addHandler(
        console_handler
    )

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        formatter
    )
    logger.addHandler(
        file_handler
    )

    return logger


LOGGER = configure_logging()


def write_scheduler_status(
    *,
    status: str,
    **extra,
) -> None:
    SCHEDULER_STATUS_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    now = datetime.now(
        SRI_LANKA_TZ
    )

    payload = {
        "scheduler_status": status,
        "updated_at": now.isoformat(),
        "timezone": "Asia/Colombo",
        "schedule": {
            "days": "Monday-Friday",
            "hours": "08:00-17:00",
            "minute": 0,
        },
        "execution_contract": (
            "FULL_RUN_PIPELINE_V1"
        ),
        **extra,
    }

    temp_path = (
        SCHEDULER_STATUS_FILE
        .parent
        / (
            f".{SCHEDULER_STATUS_FILE.name}."
            f"{uuid.uuid4().hex}.tmp"
        )
    )

    temp_path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    try:
        os.replace(
            temp_path,
            SCHEDULER_STATUS_FILE,
        )
    except PermissionError:
        SCHEDULER_STATUS_FILE.write_text(
            json.dumps(
                payload,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        try:
            temp_path.unlink(
                missing_ok=True
            )
        except OSError:
            pass


def load_json(path: Path) -> dict:
    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as file:
        payload = json.load(file)

    if not isinstance(
        payload,
        dict,
    ):
        raise RuntimeError(
            f"Expected JSON object: {path}"
        )

    return payload


def validate_pipeline_outputs(run_date: str) -> dict:
    from _runtime import ROOT
    from bank_intel.publication.contract import validate_release
    return validate_release(PROJECT_ROOT, run_date)


def run_pipeline_job() -> dict:
    """
    Execute the exact full production pipeline.

    The pipeline itself owns:
        collection
        Stage 1
        Phase 2 ML
        Phase 3 corpus intelligence
        taxonomy
        final financial brief
        collection-completeness QA
        final report QA

    The scheduler performs an additional publication gate after
    run_pipeline.py returns success.
    """
    started_at = datetime.now(
        SRI_LANKA_TZ
    )
    run_date = started_at.date().isoformat()

    LOGGER.info(
        "=" * 78
    )
    LOGGER.info(
        "Scheduled FULL news pipeline starting"
    )
    LOGGER.info(
        "Started at: %s",
        started_at.isoformat(),
    )
    LOGGER.info(
        "Pipeline: %s",
        PIPELINE_SCRIPT,
    )

    if not PIPELINE_SCRIPT.is_file():
        write_scheduler_status(
            status="RUNNING",
            last_run_started_at=(
                started_at.isoformat()
            ),
            last_run_completed_at=(
                datetime.now(
                    SRI_LANKA_TZ
                ).isoformat()
            ),
            last_run_result=(
                "PIPELINE_SCRIPT_MISSING"
            ),
        )

        raise RuntimeError(
            "Pipeline script missing: "
            f"{PIPELINE_SCRIPT}"
        )

    from _runtime import run_process
    result = run_process(
        [
            sys.executable,
            str(
                PIPELINE_SCRIPT
            ),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        # The full pipeline takes about an hour; each stage has its own limit.
        timeout=4 * 3600,
    )

    completed_at = datetime.now(
        SRI_LANKA_TZ
    )

    if result.returncode == 2:
        outputs = validate_pipeline_outputs(run_date)
        if outputs.get('status') != 'REVIEW_REQUIRED':
            raise RuntimeError('Review exit code without a held edition')
        write_scheduler_status(status='RUNNING', last_run_result='REVIEW_REQUIRED', last_return_code=2,
                               last_run_completed_at=completed_at.isoformat(), last_run_summary=outputs)
        return outputs

    if result.returncode != 0:
        write_scheduler_status(
            status="RUNNING",
            last_run_started_at=(
                started_at.isoformat()
            ),
            last_run_completed_at=(
                completed_at.isoformat()
            ),
            last_run_result=(
                "PIPELINE_FAILED"
            ),
            last_return_code=(
                result.returncode
            ),
        )

        raise RuntimeError(
            "Full production pipeline failed "
            f"with return code {result.returncode}"
        )

    try:
        outputs = validate_pipeline_outputs(
            run_date
        )
    except Exception as exc:
        write_scheduler_status(
            status="RUNNING",
            last_run_started_at=(
                started_at.isoformat()
            ),
            last_run_completed_at=(
                completed_at.isoformat()
            ),
            last_run_result=(
                "OUTPUT_GATE_FAILED"
            ),
            last_return_code=(
                result.returncode
            ),
            error_type=(
                type(exc).__name__
            ),
            error_message=(
                str(exc)
            ),
        )
        raise

    if outputs.get('status') == 'ALREADY_SENT':
        write_scheduler_status(status='RUNNING', last_run_result='ALREADY_SENT', last_return_code=0,
                               last_run_completed_at=completed_at.isoformat(), last_run_summary=outputs)
        return outputs

    summary = {
        "run_date": run_date,
        "started_at": (
            started_at.isoformat()
        ),
        "completed_at": (
            completed_at.isoformat()
        ),
        "return_code": (
            result.returncode
        ),
        **outputs,
    }

    write_scheduler_status(
        status="RUNNING",
        last_run_started_at=(
            started_at.isoformat()
        ),
        last_run_completed_at=(
            completed_at.isoformat()
        ),
        last_run_result="SUCCESS",
        last_return_code=(
            result.returncode
        ),
        last_run_summary=summary,
    )

    LOGGER.info(
        "Full production pipeline completed "
        "and scheduler output gate PASSED."
    )
    LOGGER.info(
        "Articles reviewed: %s",
        outputs[
            "article_count"
        ],
    )
    LOGGER.info(
        "Articles retained: %s",
        outputs[
            "retained_article_count"
        ],
    )
    LOGGER.info(
        "Consolidated events: %s",
        outputs[
            "consolidated_event_count"
        ],
    )
    LOGGER.info(
        "Final report: %s",
        outputs[
            "named_report"
        ],
    )
    LOGGER.info(
        "=" * 78
    )

    return summary


def scheduler_event_listener(
    event,
) -> None:
    if event.code == EVENT_JOB_EXECUTED:
        LOGGER.info(
            "Scheduler job event: EXECUTED"
        )
    elif event.code == EVENT_JOB_MISSED:
        LOGGER.warning(
            "Scheduler job event: MISSED"
        )
    elif event.code == EVENT_JOB_ERROR:
        LOGGER.error(
            "Scheduler job event: ERROR"
        )


def get_next_run_time(
    scheduler: BlockingScheduler,
) -> datetime | None:
    now = datetime.now(
        SRI_LANKA_TZ
    )

    next_times = []

    for job in scheduler.get_jobs():
        try:
            next_time = (
                job.trigger
                .get_next_fire_time(
                    None,
                    now,
                )
            )
        except Exception:
            next_time = None

        if next_time is not None:
            next_times.append(
                next_time
            )

    if not next_times:
        return None

    return min(
        next_times
    )


def print_schedule(
    scheduler: BlockingScheduler,
) -> None:
    next_run = get_next_run_time(
        scheduler
    )

    print()
    print("=" * 78)
    print(
        "BANK SUPERVISION NEWS "
        "FULL-PIPELINE SCHEDULER"
    )
    print("=" * 78)
    print(
        "Timezone : Asia/Colombo"
    )
    print(
        "Days     : Monday-Friday"
    )
    print(
        "Runs     : 08:00 through 17:00 hourly"
    )
    print(
        "Contract : run_pipeline.py "
        "(collection -> final QA/report)"
    )
    print()

    print(
        "Scheduled times:"
    )

    for hour in range(
        8,
        18,
    ):
        print(
            f"    {hour:02d}:00"
        )

    print()

    if next_run is not None:
        print(
            "Next run : "
            f"{next_run.isoformat()}"
        )
    else:
        print(
            "Next run : "
            "Unable to calculate"
        )

    print()
    print(
        "A scheduled run is successful only when "
        "the full pipeline exits 0 AND final report "
        "QA/output parity pass."
    )
    print()
    print(
        "Press Ctrl+C to stop scheduler."
    )
    print("=" * 78)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Bank Supervision News "
            "Full Production Pipeline Scheduler"
        )
    )

    mode = parser.add_mutually_exclusive_group()

    mode.add_argument(
        "--run-now",
        action="store_true",
        help=(
            "Run the full pipeline immediately, "
            "then continue into the normal schedule."
        ),
    )

    mode.add_argument(
        "--run-once",
        action="store_true",
        help=(
            "Run the full pipeline once and exit. "
            "Use this for scheduler acceptance testing."
        ),
    )

    args = parser.parse_args()

    if args.run_once:
        LOGGER.info(
            "One-shot scheduler acceptance run requested."
        )

        try:
            summary = run_pipeline_job()
        except Exception:
            write_scheduler_status(
                status="STOPPED",
                scheduler_mode=(
                    "RUN_ONCE"
                ),
                last_run_result=(
                    "FAILED"
                ),
                scheduler_stopped_at=(
                    datetime.now(
                        SRI_LANKA_TZ
                    ).isoformat()
                ),
            )
            raise

        write_scheduler_status(
            status="STOPPED",
            scheduler_mode=(
                "RUN_ONCE"
            ),
            last_run_result=(
                "SUCCESS"
            ),
            last_run_summary=(
                summary
            ),
            scheduler_stopped_at=(
                datetime.now(
                    SRI_LANKA_TZ
                ).isoformat()
            ),
        )

        print()
        print("=" * 78)
        print(
            "SCHEDULER RUN-ONCE ACCEPTANCE: PASS"
        )
        print("=" * 78)
        print(
            "Publication date :",
            summary[
                "run_date"
            ],
        )
        print(
            "Articles reviewed:",
            summary[
                "article_count"
            ],
        )
        print(
            "Retained         :",
            summary[
                "retained_article_count"
            ],
        )
        print(
            "Events           :",
            summary[
                "consolidated_event_count"
            ],
        )
        print(
            "Final QA         :",
            summary[
                "qa_status"
            ],
        )
        print(
            "Final report     :",
            summary[
                "named_report"
            ],
        )
        print("=" * 78)
        return

    if args.run_now:
        LOGGER.info(
            "Immediate full-pipeline run requested."
        )
        run_pipeline_job()

    scheduler = BlockingScheduler(
        timezone=SRI_LANKA_TZ,
        job_defaults={
            "max_instances": 1,
            "coalesce": True,
            "misfire_grace_time": 1800,
        },
    )

    trigger = CronTrigger(
        day_of_week="mon-fri",
        hour=SCHEDULE_HOURS,
        minute=SCHEDULE_MINUTE,
        second=0,
        timezone=SRI_LANKA_TZ,
    )

    scheduler.add_job(
        run_pipeline_job,
        trigger=trigger,
        id="bank_news_full_pipeline",
        name=(
            "Bank Supervision News "
            "Full Production Pipeline"
        ),
        replace_existing=True,
    )

    scheduler.add_listener(
        scheduler_event_listener,
        EVENT_JOB_EXECUTED
        | EVENT_JOB_ERROR
        | EVENT_JOB_MISSED,
    )

    next_run = get_next_run_time(
        scheduler
    )

    write_scheduler_status(
        status="RUNNING",
        scheduler_started_at=(
            datetime.now(
                SRI_LANKA_TZ
            ).isoformat()
        ),
        next_run=(
            next_run.isoformat()
            if next_run
            else None
        ),
    )

    print_schedule(
        scheduler
    )

    try:
        scheduler.start()
    except (
        KeyboardInterrupt,
        SystemExit,
    ):
        LOGGER.info(
            "Scheduler shutdown requested."
        )
    finally:
        try:
            write_scheduler_status(
                status="STOPPED",
                scheduler_stopped_at=(
                    datetime.now(
                        SRI_LANKA_TZ
                    ).isoformat()
                ),
            )
        except Exception:
            pass

        LOGGER.info(
            "Scheduler stopped."
        )


if __name__ == "__main__":
    main()
