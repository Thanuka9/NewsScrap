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

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from bank_intel.common.collection_window import (
    get_collection_window,
    publication_dates_for_window,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COLLECTION_SCRIPT = PROJECT_ROOT / "scripts" / "run_collection.py"
INTELLIGENCE_SCRIPT = PROJECT_ROOT / "scripts" / "run_intelligence_stage1.py"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "scheduler.log"
SCHEDULER_STATUS_FILE = PROJECT_ROOT / "data" / "scheduler_status.json"
SRI_LANKA_TZ = ZoneInfo("Asia/Colombo")
SCHEDULE_HOURS = "8-17"
SCHEDULE_MINUTE = 0


def configure_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("bank_intel_scheduler")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


LOGGER = configure_logging()


def write_scheduler_status(*, status: str, **extra) -> None:
    SCHEDULER_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(SRI_LANKA_TZ)
    payload = {
        "scheduler_status": status,
        "updated_at": now.isoformat(),
        "timezone": "Asia/Colombo",
        "schedule": {"days": "Monday-Friday", "hours": "08:00-17:00", "minute": 0},
        **extra,
    }
    temp_path = SCHEDULER_STATUS_FILE.parent / f".{SCHEDULER_STATUS_FILE.name}.{uuid.uuid4().hex}.tmp"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    temp_path.write_text(text, encoding="utf-8")
    try:
        os.replace(temp_path, SCHEDULER_STATUS_FILE)
    except PermissionError:
        SCHEDULER_STATUS_FILE.write_text(text, encoding="utf-8")
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def collection_window_dates(run_datetime: datetime) -> list[str]:
    """Publication dates Stage-1 must inspect after this collection run."""
    return publication_dates_for_window(get_collection_window(run_datetime))


def run_intelligence_job(*, target_date: str) -> dict:
    """Run Stage-1 for one actual publication date."""
    started_at = datetime.now(SRI_LANKA_TZ)

    if not INTELLIGENCE_SCRIPT.exists():
        return {
            "target_date": target_date,
            "result": "INTELLIGENCE_SCRIPT_MISSING",
            "return_code": None,
        }

    LOGGER.info("Stage-1 intelligence starting for publication date %s", target_date)
    try:
        result = subprocess.run(
            [sys.executable, str(INTELLIGENCE_SCRIPT), "--date", target_date],
            cwd=PROJECT_ROOT,
            check=False,
        )
        completed_at = datetime.now(SRI_LANKA_TZ)
        outcome = "SUCCESS" if result.returncode == 0 else "FAILED"
        if result.returncode == 0:
            LOGGER.info("Stage-1 intelligence completed for %s", target_date)
        else:
            LOGGER.error(
                "Stage-1 intelligence failed for %s with code %s",
                target_date,
                result.returncode,
            )
        return {
            "target_date": target_date,
            "result": outcome,
            "return_code": result.returncode,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
        }
    except Exception as exc:
        LOGGER.exception("Could not execute Stage-1 intelligence for %s", target_date)
        return {
            "target_date": target_date,
            "result": "EXECUTION_ERROR",
            "return_code": None,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }


def run_collection_job() -> None:
    started_at = datetime.now(SRI_LANKA_TZ)
    LOGGER.info("=" * 70)
    LOGGER.info("Scheduled collection starting")
    LOGGER.info("Started at: %s", started_at.isoformat())
    LOGGER.info("Collector: %s", COLLECTION_SCRIPT)

    if not COLLECTION_SCRIPT.exists():
        completed_at = datetime.now(SRI_LANKA_TZ)
        LOGGER.error("Master collection script does not exist: %s", COLLECTION_SCRIPT)
        write_scheduler_status(
            status="ERROR",
            last_run_started_at=started_at.isoformat(),
            last_run_completed_at=completed_at.isoformat(),
            last_run_result="COLLECTION_SCRIPT_MISSING",
        )
        return

    try:
        result = subprocess.run([sys.executable, str(COLLECTION_SCRIPT)], cwd=PROJECT_ROOT, check=False)
        completed_at = datetime.now(SRI_LANKA_TZ)

        if result.returncode == 0:
            LOGGER.info("Scheduled collection completed successfully.")
            LOGGER.info("Return code: %s", result.returncode)

            intelligence_results = []
            for target_date in collection_window_dates(started_at):
                intelligence_results.append(run_intelligence_job(target_date=target_date))

            intelligence_failed = any(
                item.get("result") != "SUCCESS"
                for item in intelligence_results
            )
            final_result = (
                "COLLECTION_SUCCESS_INTELLIGENCE_FAILED"
                if intelligence_failed
                else "SUCCESS"
            )
            completed_at = datetime.now(SRI_LANKA_TZ)
            write_scheduler_status(
                status="RUNNING",
                last_run_started_at=started_at.isoformat(),
                last_run_completed_at=completed_at.isoformat(),
                last_run_result=final_result,
                last_return_code=result.returncode,
                intelligence_results=intelligence_results,
            )
        else:
            LOGGER.error("Master collection returned non-zero code: %s", result.returncode)
            write_scheduler_status(
                status="RUNNING",
                last_run_started_at=started_at.isoformat(),
                last_run_completed_at=completed_at.isoformat(),
                last_run_result="FAILED",
                last_return_code=result.returncode,
            )
    except Exception as exc:
        completed_at = datetime.now(SRI_LANKA_TZ)
        LOGGER.exception("Scheduler could not execute master collection.")
        write_scheduler_status(
            status="RUNNING",
            last_run_started_at=started_at.isoformat(),
            last_run_completed_at=completed_at.isoformat(),
            last_run_result="EXECUTION_ERROR",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    LOGGER.info("=" * 70)


def scheduler_event_listener(event) -> None:
    if event.code == EVENT_JOB_EXECUTED:
        LOGGER.info("Scheduler job event: EXECUTED")
    elif event.code == EVENT_JOB_MISSED:
        LOGGER.warning("Scheduler job event: MISSED")
    elif event.code == EVENT_JOB_ERROR:
        LOGGER.error("Scheduler job event: ERROR")


def get_next_run_time(scheduler: BlockingScheduler) -> datetime | None:
    now = datetime.now(SRI_LANKA_TZ)
    next_times = []
    for job in scheduler.get_jobs():
        try:
            next_time = job.trigger.get_next_fire_time(None, now)
        except Exception:
            next_time = None
        if next_time is not None:
            next_times.append(next_time)
    return min(next_times) if next_times else None


def print_schedule(scheduler: BlockingScheduler) -> None:
    next_run = get_next_run_time(scheduler)
    print("\n" + "=" * 72)
    print("BANK SUPERVISION NEWS SCHEDULER")
    print("=" * 72)
    print("Timezone : Asia/Colombo")
    print("Days     : Monday-Friday")
    print("Runs     : 08:00 through 17:00 hourly\n")
    print("Scheduled times:")
    for hour in range(8, 18):
        print(f"    {hour:02d}:00")
    print(f"\nNext run : {next_run.isoformat() if next_run else 'Unable to calculate'}")
    print("\nEach successful collection is followed by Stage-1 intelligence")
    print("for every actual publication date in the collection lookback window.")
    print("\nPress Ctrl+C to stop scheduler.")
    print("=" * 72 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank Supervision News Collection + Intelligence Scheduler")
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run collection and publication-date intelligence once before entering the normal schedule.",
    )
    args = parser.parse_args()

    if args.run_now:
        LOGGER.info("Immediate run requested.")
        run_collection_job()

    scheduler = BlockingScheduler(
        timezone=SRI_LANKA_TZ,
        job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": 1800},
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
        name="Bank Supervision News Collection + Intelligence",
        replace_existing=True,
    )
    scheduler.add_listener(
        scheduler_event_listener,
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
    )

    next_run = get_next_run_time(scheduler)
    write_scheduler_status(
        status="RUNNING",
        scheduler_started_at=datetime.now(SRI_LANKA_TZ).isoformat(),
        next_run=next_run.isoformat() if next_run else None,
    )
    print_schedule(scheduler)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("Scheduler shutdown requested.")
    finally:
        try:
            write_scheduler_status(
                status="STOPPED",
                scheduler_stopped_at=datetime.now(SRI_LANKA_TZ).isoformat(),
            )
        except Exception:
            pass
        LOGGER.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
