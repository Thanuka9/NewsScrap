from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from zoneinfo import ZoneInfo

import yaml

from bank_intel.common.source_status import (
    current_status_path,
    load_source_status,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_FILE = PROJECT_ROOT / "config" / "sources" / "registry.yaml"
RUN_SUMMARY_DIR = PROJECT_ROOT / "data" / "run_summaries"
RUN_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
SRI_LANKA_TZ = ZoneInfo("Asia/Colombo")

SOURCE_SCRIPTS = {
    "daily_ft": PROJECT_ROOT / "scripts" / "run_daily_ft.py",
    "ceylon_today": PROJECT_ROOT / "scripts" / "run_ceylon_today.py",
}


def load_registry() -> dict:
    if not REGISTRY_FILE.exists():
        raise FileNotFoundError(f"Source registry does not exist:\n{REGISTRY_FILE}")
    with REGISTRY_FILE.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    sources = data.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("registry.yaml must contain a 'sources' mapping.")
    return sources


def run_source(*, source_key: str, source_config: dict, run_id: str) -> dict:
    source_name = source_config.get("name", source_key)
    started_at = datetime.now(SRI_LANKA_TZ)
    print("\n\n" + "#" * 80)
    print(f"SOURCE: {source_name}")
    print("#" * 80)

    script = SOURCE_SCRIPTS.get(source_key)
    if script is None:
        return {
            "source_key": source_key,
            "source_name": source_name,
            "execution_status": "FAILED",
            "health_status": "FAILED",
            "content_status": "NO_COLLECTOR_IMPLEMENTED",
        }
    if not script.exists():
        return {
            "source_key": source_key,
            "source_name": source_name,
            "execution_status": "FAILED",
            "health_status": "FAILED",
            "content_status": "COLLECTOR_SCRIPT_MISSING",
        }

    status_path = current_status_path(source_key)
    try:
        if status_path.exists():
            status_path.unlink()
    except OSError:
        pass

    print(f"Running:\n{script}\n")
    env = os.environ.copy()
    env["BANK_INTEL_RUN_ID"] = run_id

    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=PROJECT_ROOT,
            env=env,
            check=False,
        )
        return_code = result.returncode
    except Exception as exc:
        return {
            "source_key": source_key,
            "source_name": source_name,
            "execution_status": "FAILED",
            "health_status": "FAILED",
            "content_status": "EXECUTION_ERROR",
            "return_code": None,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }

    status = load_source_status(source_key)
    if status is None:
        return {
            "source_key": source_key,
            "source_name": source_name,
            "execution_status": "FAILED",
            "health_status": "FAILED",
            "content_status": "STATUS_FILE_MISSING",
            "return_code": return_code,
        }
    if status.get("run_id") != run_id:
        return {
            "source_key": source_key,
            "source_name": source_name,
            "execution_status": "FAILED",
            "health_status": "FAILED",
            "content_status": "STATUS_RUN_ID_MISMATCH",
            "return_code": return_code,
            "received_run_id": status.get("run_id"),
        }

    status["return_code"] = return_code
    status["master_started_at"] = started_at.isoformat()
    if return_code != 0:
        status["execution_status"] = "FAILED"
        status["health_status"] = "FAILED"
    return status


def save_run_summary(*, run_id: str, started_at: datetime, completed_at: datetime, results: list[dict]) -> Path:
    path = RUN_SUMMARY_DIR / f"collection_run_{started_at.strftime('%Y%m%d_%H%M%S')}.json"
    successful = sum(1 for item in results if item.get("execution_status") == "SUCCESS")
    failed = len(results) - successful
    healthy = sum(1 for item in results if item.get("health_status") == "HEALTHY")
    partial_error = sum(1 for item in results if item.get("health_status") == "PARTIAL_ERROR")
    payload = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "active_sources_run": len(results),
        "successful_sources": successful,
        "failed_sources": failed,
        "healthy_sources": healthy,
        "partial_error_sources": partial_error,
        "sources": results,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main():
    started_at = datetime.now(SRI_LANKA_TZ)
    run_id = started_at.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    print("\n" + "=" * 80)
    print("BANK SUPERVISION NEWS MASTER COLLECTION RUN")
    print("=" * 80)
    print(f"Run ID : {run_id}")
    print(f"Started: {started_at.isoformat()}")
    print(f"Python : {sys.executable}")

    sources = load_registry()
    print("\nSOURCE REGISTRY")
    print("-" * 80)
    for source_key, config in sources.items():
        print(
            f"{source_key:18} enabled={str(config.get('enabled', False)):5} "
            f"status={config.get('production_status', 'UNKNOWN')}"
        )

    active_sources = []
    for source_key, config in sources.items():
        enabled = bool(config.get("enabled", False))
        production_status = str(config.get("production_status", "")).upper()
        if enabled and production_status == "ACTIVE":
            active_sources.append((source_key, config))

    print(f"\nActive collectors: {len(active_sources)}")
    results = [
        run_source(source_key=source_key, source_config=source_config, run_id=run_id)
        for source_key, source_config in active_sources
    ]

    completed_at = datetime.now(SRI_LANKA_TZ)
    summary_path = save_run_summary(
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        results=results,
    )

    print("\n\n" + "=" * 80)
    print("MASTER COLLECTION COMPLETE")
    print("=" * 80)
    for item in results:
        source_name = item.get("source_name", item.get("source_key", "UNKNOWN"))
        print(f"\n{source_name}")
        print(f"  Execution : {item.get('execution_status', 'UNKNOWN')}")
        print(f"  Health    : {item.get('health_status', 'UNKNOWN')}")
        print(f"  Content   : {item.get('content_status', 'UNKNOWN')}")
        if "candidate_urls" in item:
            print(f"  Candidates: {item.get('candidate_urls', 0)}")
            print(f"  New       : {item.get('new_articles', 0)}")
            print(f"  Late      : {item.get('late_discoveries', 0)}")
            print(f"  Changed   : {item.get('changed_articles', 0)}")
            print(f"  Failures  : {item.get('fetch_failures', 0)}")

    successful = sum(1 for item in results if item.get("execution_status") == "SUCCESS")
    failed = len(results) - successful
    print("\n" + "-" * 80)
    print(f"Sources run       : {len(results)}")
    print(f"Successful        : {successful}")
    print(f"Failed            : {failed}\n")
    print(f"Master summary:\n{summary_path}")

    if failed > 0:
        print("\nWARNING: One or more sources failed. Successful sources were still processed normally.")
        # The scheduler must not launch intelligence against an incomplete
        # collection while reporting the run as fully successful.
        raise SystemExit(1)


if __name__ == "__main__":
    main()
