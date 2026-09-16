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


# ============================================================
# PROJECT
# ============================================================

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

COLLECTION_SCRIPT = (
    PROJECT_ROOT
    / "scripts"
    / "run_collection.py"
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


# ============================================================
# SCHEDULE
# ============================================================

SCHEDULE_HOURS = "8-17"

SCHEDULE_MINUTE = 0


# ============================================================
# LOGGING
# ============================================================


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

    # --------------------------------------------------------
    # CONSOLE
    # --------------------------------------------------------

    console_handler = logging.StreamHandler(
        sys.stdout
    )

    console_handler.setFormatter(
        formatter
    )

    logger.addHandler(
        console_handler
    )

    # --------------------------------------------------------
    # ROTATING LOG FILE
    # --------------------------------------------------------

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


# ============================================================
# STATUS FILE
# ============================================================


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
        "updated_at": (
            now.isoformat()
        ),
        "timezone": (
            "Asia/Colombo"
        ),
        "schedule": {
            "days": (
                "Monday-Friday"
            ),
            "hours": (
                "08:00-17:00"
            ),
            "minute": 0,
        },
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

    # Windows-safe replacement.
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

            if temp_path.exists():
                temp_path.unlink()

        except OSError:
            pass


# ============================================================
# MASTER COLLECTION JOB
# ============================================================


def run_collection_job() -> None:
    """
    Execute the master collection runner.

    Source selection remains controlled by registry.yaml.

    The scheduler does not know or care which newspapers
    are active.
    """

    started_at = datetime.now(
        SRI_LANKA_TZ
    )

    LOGGER.info(
        "=" * 70
    )

    LOGGER.info(
        "Scheduled collection starting"
    )

    LOGGER.info(
        "Started at: %s",
        started_at.isoformat(),
    )

    LOGGER.info(
        "Collector: %s",
        COLLECTION_SCRIPT,
    )

    # ========================================================
    # COLLECTION SCRIPT EXISTS?
    # ========================================================

    if not COLLECTION_SCRIPT.exists():

        completed_at = datetime.now(
            SRI_LANKA_TZ
        )

        LOGGER.error(
            "Master collection script "
            "does not exist: %s",
            COLLECTION_SCRIPT,
        )

        write_scheduler_status(
            status="ERROR",
            last_run_started_at=(
                started_at.isoformat()
            ),
            last_run_completed_at=(
                completed_at.isoformat()
            ),
            last_run_result=(
                "COLLECTION_SCRIPT_MISSING"
            ),
        )

        return

    # ========================================================
    # EXECUTE
    # ========================================================

    try:

        result = subprocess.run(
            [
                sys.executable,
                str(
                    COLLECTION_SCRIPT
                ),
            ],

            cwd=PROJECT_ROOT,

            check=False,
        )

        completed_at = datetime.now(
            SRI_LANKA_TZ
        )

        # ====================================================
        # SUCCESS
        # ====================================================

        if result.returncode == 0:

            LOGGER.info(
                "Scheduled collection "
                "completed successfully."
            )

            LOGGER.info(
                "Return code: %s",
                result.returncode,
            )

            write_scheduler_status(
                status="RUNNING",
                last_run_started_at=(
                    started_at.isoformat()
                ),
                last_run_completed_at=(
                    completed_at.isoformat()
                ),
                last_run_result=(
                    "SUCCESS"
                ),
                last_return_code=(
                    result.returncode
                ),
            )

        # ====================================================
        # NON-ZERO RETURN CODE
        # ====================================================

        else:

            LOGGER.error(
                "Master collection returned "
                "non-zero code: %s",
                result.returncode,
            )

            write_scheduler_status(
                status="RUNNING",
                last_run_started_at=(
                    started_at.isoformat()
                ),
                last_run_completed_at=(
                    completed_at.isoformat()
                ),
                last_run_result=(
                    "FAILED"
                ),
                last_return_code=(
                    result.returncode
                ),
            )

    # ========================================================
    # EXECUTION ERROR
    # ========================================================

    except Exception as exc:

        completed_at = datetime.now(
            SRI_LANKA_TZ
        )

        LOGGER.exception(
            "Scheduler could not execute "
            "master collection."
        )

        write_scheduler_status(
            status="RUNNING",
            last_run_started_at=(
                started_at.isoformat()
            ),
            last_run_completed_at=(
                completed_at.isoformat()
            ),
            last_run_result=(
                "EXECUTION_ERROR"
            ),
            error_type=(
                type(exc).__name__
            ),
            error_message=str(exc),
        )

    LOGGER.info(
        "=" * 70
    )


# ============================================================
# SCHEDULER EVENT LISTENER
# ============================================================


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


# ============================================================
# NEXT RUN CALCULATION
# ============================================================


def get_next_run_time(
    scheduler: BlockingScheduler,
) -> datetime | None:
    """
    Calculate the next scheduled occurrence directly from
    each job trigger.

    This deliberately avoids Job.next_run_time because pending
    APScheduler jobs may not expose that attribute until the
    scheduler has actually started.
    """

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


# ============================================================
# PRINT SCHEDULE
# ============================================================


def print_schedule(
    scheduler: BlockingScheduler,
) -> None:

    next_run = get_next_run_time(
        scheduler
    )

    print()
    print("=" * 72)

    print(
        "BANK SUPERVISION NEWS SCHEDULER"
    )

    print("=" * 72)

    print(
        "Timezone : Asia/Colombo"
    )

    print(
        "Days     : Monday-Friday"
    )

    print(
        "Runs     : 08:00 through 17:00 hourly"
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
            f"Next run : "
            f"{next_run.isoformat()}"
        )

    else:

        print(
            "Next run : "
            "Unable to calculate"
        )

    print()

    print(
        "Press Ctrl+C to stop scheduler."
    )

    print("=" * 72)

    print()


# ============================================================
# MAIN
# ============================================================


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Bank Supervision News "
            "Collection Scheduler"
        )
    )

    parser.add_argument(
        "--run-now",
        action="store_true",
        help=(
            "Run collection once immediately "
            "before entering the normal schedule."
        ),
    )

    args = parser.parse_args()

    # ========================================================
    # OPTIONAL IMMEDIATE RUN
    # ========================================================

    if args.run_now:

        LOGGER.info(
            "Immediate run requested."
        )

        run_collection_job()

    # ========================================================
    # BUILD SCHEDULER
    # ========================================================

    scheduler = BlockingScheduler(
        timezone=SRI_LANKA_TZ,

        job_defaults={
            # Prevent overlap if a collection takes longer
            # than expected.
            "max_instances": 1,

            # If multiple scheduled runs are missed while the
            # program is temporarily unavailable, combine them.
            "coalesce": True,

            # Scheduled run may still execute if delayed by
            # up to 30 minutes.
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
        run_collection_job,
        trigger=trigger,
        id="bank_news_collection",
        name=(
            "Bank Supervision "
            "News Collection"
        ),
        replace_existing=True,
    )

    scheduler.add_listener(
        scheduler_event_listener,
        EVENT_JOB_EXECUTED
        | EVENT_JOB_ERROR
        | EVENT_JOB_MISSED,
    )

    # ========================================================
    # CALCULATE NEXT RUN BEFORE START
    # ========================================================

    next_run = get_next_run_time(
        scheduler
    )

    # ========================================================
    # STATUS
    # ========================================================

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

    # ========================================================
    # DISPLAY
    # ========================================================

    print_schedule(
        scheduler
    )

    # ========================================================
    # START
    # ========================================================

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