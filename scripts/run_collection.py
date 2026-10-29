from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
from _runtime import run_process, locked_main
import sys
import time
import uuid
from zoneinfo import ZoneInfo

import yaml

from bank_intel.common.source_status import (
    current_status_path,
    load_source_status,
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

REGISTRY_FILE = (
    PROJECT_ROOT
    / "config"
    / "sources"
    / "registry.yaml"
)

RUN_SUMMARY_DIR = (
    PROJECT_ROOT
    / "data"
    / "run_summaries"
)

RUN_SUMMARY_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

SRI_LANKA_TZ = ZoneInfo(
    "Asia/Colombo"
)


# NEWS_FAIL_CLOSED_COLLECTION_V1
REQUIRED_PRODUCTION_SOURCES = {
    "daily_ft",
    "ceylon_today",
    "daily_news",
    "daily_mirror",
    "island",
    "newsfirst",
    "the_morning",
}


SOURCE_SCRIPTS = {

    "daily_ft": (
        PROJECT_ROOT
        / "scripts"
        / "run_daily_ft.py"
    ),

    "ceylon_today": (
        PROJECT_ROOT
        / "scripts"
        / "run_ceylon_today.py"
    ),

    "daily_mirror": (
        PROJECT_ROOT
        / "scripts"
        / "run_daily_mirror.py"
    ),

    "island": (
        PROJECT_ROOT
        / "scripts"
        / "run_island.py"
    ),
}


def load_registry() -> dict:

    if not REGISTRY_FILE.exists():

        raise FileNotFoundError(
            "Source registry does not exist:\n"
            f"{REGISTRY_FILE}"
        )

    with REGISTRY_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:

        data = yaml.safe_load(
            file
        )

    sources = data.get(
        "sources"
    )

    if not isinstance(
        sources,
        dict,
    ):

        raise ValueError(
            "registry.yaml must contain "
            "a 'sources' mapping."
        )

    return sources


def run_source(
    *,
    source_key: str,
    source_config: dict,
    run_id: str,
) -> dict:

    source_name = (
        source_config.get(
            "name",
            source_key,
        )
    )

    started_at = datetime.now(
        SRI_LANKA_TZ
    )

    print()
    print()
    print("#" * 80)

    print(
        f"SOURCE: {source_name}"
    )

    print("#" * 80)

    acquisition_method = (
        str(source_config.get("acquisition_method", ""))
        .strip()
        .upper()
    )

    if acquisition_method == "GENERIC":
        script = (
            PROJECT_ROOT
            / "scripts"
            / "run_generic_sources.py"
        )
        command = [
            sys.executable,
            str(script),
            "--source",
            source_key,
            "--stage",
            "--production",
            "--limit",
            str(int(source_config.get("generic_limit", 100) or 100)),
        ]
    else:
        script = SOURCE_SCRIPTS.get(
            source_key
        )
        command = (
            [
                sys.executable,
                str(script),
            ]
            if script is not None
            else None
        )

    if script is None:

        return {
            "source_key": source_key,
            "source_name": source_name,
            "execution_status": (
                "FAILED"
            ),
            "health_status": "FAILED",
            "content_status": (
                "NO_COLLECTOR_IMPLEMENTED"
            ),
        }

    if not script.exists():

        return {
            "source_key": source_key,
            "source_name": source_name,
            "execution_status": (
                "FAILED"
            ),
            "health_status": "FAILED",
            "content_status": (
                "COLLECTOR_SCRIPT_MISSING"
            ),
        }

    # Remove the current status before execution.
    #
    # This ensures we never accidentally read a stale
    # previous-run status if the new collector crashes
    # before writing anything.

    status_path = (
        current_status_path(
            source_key
        )
    )

    try:

        if status_path.exists():
            status_path.unlink()

    except OSError:
        pass

    print(
        "Running:\n"
        + (
            " ".join(str(value) for value in command)
            if command
            else str(script)
        )
    )

    print()

    env = os.environ.copy()

    env[
        "BANK_INTEL_RUN_ID"
    ] = run_id

    try:

        result = run_process(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            check=False,
        )

        return_code = (
            result.returncode
        )

    except Exception as exc:

        return {
            "source_key": source_key,
            "source_name": source_name,
            "execution_status": (
                "FAILED"
            ),
            "health_status": "FAILED",
            "content_status": (
                "EXECUTION_ERROR"
            ),
            "return_code": None,
            "error_type": (
                type(exc).__name__
            ),
            "error_message": str(exc),
        }

    # ========================================================
    # READ STRUCTURED STATUS
    # ========================================================

    status = load_source_status(
        source_key
    )

    if status is None:

        return {
            "source_key": source_key,
            "source_name": source_name,
            "execution_status": (
                "FAILED"
            ),
            "health_status": "FAILED",
            "content_status": (
                "STATUS_FILE_MISSING"
            ),
            "return_code": (
                return_code
            ),
        }

    # Never accept status left by another run.

    if status.get(
        "run_id"
    ) != run_id:

        return {
            "source_key": source_key,
            "source_name": source_name,
            "execution_status": (
                "FAILED"
            ),
            "health_status": "FAILED",
            "content_status": (
                "STATUS_RUN_ID_MISMATCH"
            ),
            "return_code": (
                return_code
            ),
            "received_run_id": (
                status.get(
                    "run_id"
                )
            ),
        }

    status[
        "return_code"
    ] = return_code

    status[
        "master_started_at"
    ] = started_at.isoformat()

    # A non-zero process can never count as successful,
    # regardless of what a malformed status file says.

    if return_code != 0:

        status[
            "execution_status"
        ] = "FAILED"

        status[
            "health_status"
        ] = "FAILED"

    return status



# NEWS_SOURCE_RETRY_AND_TOLERANCE_V1
# A transient website/browser failure must not kill a production run immediately.
# Each failed ACTIVE source gets two fresh-process retries. Persistent failure still
# blocks the pipeline, so this improves resilience without weakening fail-closed QA.
def _open_failures(result: dict) -> int:
    return len(result.get("unresolved_fetch_urls") or []) + len(result.get("failed_discovery_surfaces") or [])


def recover_partial_source(*, source_key: str, source_config: dict, run_id: str, result: dict,
                           wait_seconds: int = 60) -> dict:
    """A successful run can still leave failed pages (a publisher's brief
    outage). Collectors skip staged articles, so one delayed re-run retries
    only what failed; keep whichever run left fewer open failures."""
    before = _open_failures(result)
    if not before:
        return result
    print(f"\n[PARTIAL RECOVERY] {source_key}: {before} failed page(s); retrying in {wait_seconds}s")
    time.sleep(wait_seconds)
    second = run_source(source_key=source_key, source_config=source_config, run_id=run_id)
    after = _open_failures(second) if second.get("execution_status") == "SUCCESS" else before + 1
    chosen = second if after < before else result
    chosen["partial_recovery"] = {"failures_before": before, "failures_after": min(before, after),
                                  "recovery_run_used": chosen is second}
    # Coverage review reads the status file, which the re-run overwrote.
    from bank_intel.common.source_status import write_source_status
    write_source_status(source_key=source_key, payload=chosen)
    print(f"[PARTIAL RECOVERY] {source_key}: open failures {before} -> {min(before, after)}")
    return chosen


def run_source_with_retry(
    *,
    source_key: str,
    source_config: dict,
    run_id: str,
) -> dict:
    waits = (15, 45)
    max_attempts = 1 + len(waits)
    last_result = None

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            wait_seconds = waits[attempt - 2]
            print()
            print(
                f"[SOURCE RETRY] {source_key}: waiting {wait_seconds}s "
                f"before attempt {attempt}/{max_attempts}"
            )
            time.sleep(wait_seconds)

        result = run_source(
            source_key=source_key,
            source_config=source_config,
            run_id=run_id,
        )
        result["master_attempt"] = attempt
        result["master_attempts_allowed"] = max_attempts
        last_result = result

        if result.get("execution_status") == "SUCCESS":
            result["recovered_after_retry"] = attempt > 1
            if attempt > 1:
                print(
                    f"[SOURCE RECOVERED] {source_key}: "
                    f"SUCCESS on attempt {attempt}/{max_attempts}"
                )
            return recover_partial_source(source_key=source_key, source_config=source_config,
                                          run_id=run_id, result=result)

        print(
            f"[SOURCE ATTEMPT FAILED] {source_key}: "
            f"attempt {attempt}/{max_attempts} | "
            f"content={result.get('content_status')} | "
            f"error={result.get('error_type', '')}: "
            f"{result.get('error_message', '')}"
        )

    assert last_result is not None
    last_result["recovered_after_retry"] = False
    return last_result


def is_blocking_degraded(item: dict) -> bool:
    """Return True only for degraded states that threaten collection completeness.

    PARTIAL_ERROR caused only by a small number of invalid candidate extractions is
    tolerated. Hard listing/fetch failures, zero-candidate degraded runs, or a source
    where every candidate is invalid still block publication.
    """
    health = str(item.get("health_status") or "").upper()
    execution = str(item.get("execution_status") or "").upper()

    if execution != "SUCCESS":
        return True

    if health == "HEALTHY":
        return False

    if health == "FAILED":
        return True

    candidates = int(item.get("candidate_urls", 0) or 0)
    fetch_failures = int(item.get("fetch_failures", 0) or 0)
    invalid = int(
        item.get("invalid_extraction", item.get("invalid_extractions", 0)) or 0
    )

    listing = item.get("listing")
    if isinstance(listing, dict):
        if bool(listing.get("listing_partial_error")):
            return True
        if int(listing.get("listing_pages_failed", 0) or 0) > 0:
            return True
        if bool(listing.get("listing_blocked")):
            return True
        if str(listing.get("listing_error") or "").strip():
            return True

    if int(item.get("truncated_discovery_urls", 0) or 0) > 0:
        return True

    if item.get("unresolved_fetch_urls"):
        return True

    if candidates <= 0:
        return True

    if fetch_failures > 0:
        return True

    if invalid >= candidates:
        return True

    # Otherwise this is a usable run with some rejected/bad candidate pages.
    return False

def save_run_summary(
    *,
    run_id: str,
    started_at: datetime,
    completed_at: datetime,
    results: list[dict],
) -> Path:

    path = (
        RUN_SUMMARY_DIR
        / (
            f"collection_run_"
            f"{started_at.strftime('%Y%m%d_%H%M%S')}"
            f".json"
        )
    )

    successful = sum(
        1
        for item in results
        if item.get(
            "execution_status"
        ) == "SUCCESS"
    )

    failed = (
        len(results)
        - successful
    )

    healthy = sum(
        1
        for item in results
        if item.get(
            "health_status"
        ) == "HEALTHY"
    )

    partial_error = sum(
        1
        for item in results
        if item.get(
            "health_status"
        ) == "PARTIAL_ERROR"
    )

    payload = {
        "run_id": run_id,

        "started_at": (
            started_at.isoformat()
        ),

        "completed_at": (
            completed_at.isoformat()
        ),

        "active_sources_run": (
            len(results)
        ),

        "successful_sources": (
            successful
        ),

        "failed_sources": (
            failed
        ),

        "healthy_sources": (
            healthy
        ),

        "partial_error_sources": (
            partial_error
        ),

        "sources": results,
    }

    path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return path


def main():

    started_at = datetime.now(
        SRI_LANKA_TZ
    )

    run_id = (
        started_at.strftime(
            "%Y%m%d_%H%M%S"
        )
        + "_"
        + uuid.uuid4().hex[:8]
    )

    print()
    print("=" * 80)

    print(
        "BANK SUPERVISION NEWS "
        "MASTER COLLECTION RUN"
    )

    print("=" * 80)

    print(
        f"Run ID : "
        f"{run_id}"
    )

    print(
        f"Started: "
        f"{started_at.isoformat()}"
    )

    print(
        f"Python : "
        f"{sys.executable}"
    )

    sources = load_registry()

    print()
    print(
        "SOURCE REGISTRY"
    )

    print("-" * 80)

    for source_key, config in (
        sources.items()
    ):

        print(
            f"{source_key:18} "
            f"enabled="
            f"{str(config.get('enabled', False)):5} "
            f"status="
            f"{config.get('production_status', 'UNKNOWN')}"
        )

    active_sources = []

    for source_key, config in (
        sources.items()
    ):

        enabled = bool(
            config.get(
                "enabled",
                False,
            )
        )

        production_status = (
            str(
                config.get(
                    "production_status",
                    "",
                )
            )
            .upper()
        )

        if (
            enabled
            and production_status
            == "ACTIVE"
        ):

            active_sources.append(
                (
                    source_key,
                    config,
                )
            )

    print()
    print(
        f"Active collectors: "
        f"{len(active_sources)}"
    )

    active_keys = {source_key for source_key, _ in active_sources}
    missing_required = sorted(REQUIRED_PRODUCTION_SOURCES - active_keys)
    if missing_required:
        raise RuntimeError(
            "PRODUCTION SOURCE GATE FAILED. Missing ACTIVE sources: "
            + ", ".join(missing_required)
        )

    results = []

    for (
        source_key,
        source_config,
    ) in active_sources:

        result = run_source_with_retry(
            source_key=source_key,
            source_config=(
                source_config
            ),
            run_id=run_id,
        )

        results.append(
            result
        )

    completed_at = datetime.now(
        SRI_LANKA_TZ
    )

    summary_path = save_run_summary(
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        results=results,
    )

    print()
    print()
    print("=" * 80)

    print(
        "MASTER COLLECTION COMPLETE"
    )

    print("=" * 80)

    for item in results:

        source_name = item.get(
            "source_name",
            item.get(
                "source_key",
                "UNKNOWN",
            ),
        )

        execution = item.get(
            "execution_status",
            "UNKNOWN",
        )

        health = item.get(
            "health_status",
            "UNKNOWN",
        )

        content = item.get(
            "content_status",
            "UNKNOWN",
        )

        print()
        print(
            f"{source_name}"
        )

        print(
            f"  Execution : "
            f"{execution}"
        )

        print(
            f"  Health    : "
            f"{health}"
        )

        print(
            f"  Content   : "
            f"{content}"
        )

        if (
            "candidate_urls"
            in item
        ):

            print(
                f"  Candidates: "
                f"{item.get('candidate_urls', 0)}"
            )

            print(
                f"  New       : "
                f"{item.get('new_articles', 0)}"
            )

            print(
                f"  Late      : "
                f"{item.get('late_discoveries', 0)}"
            )

            print(
                f"  Changed   : "
                f"{item.get('changed_articles', 0)}"
            )

            print(
                f"  Failures  : "
                f"{item.get('fetch_failures', 0)}"
            )

    successful = sum(
        1
        for item in results
        if item.get(
            "execution_status"
        ) == "SUCCESS"
    )

    failed = (
        len(results)
        - successful
    )

    degraded = [
        item
        for item in results
        if item.get("health_status") != "HEALTHY"
    ]

    blocking_degraded = [
        item
        for item in degraded
        if is_blocking_degraded(item)
    ]

    tolerated_degraded = [
        item
        for item in degraded
        if not is_blocking_degraded(item)
    ]

    print()
    print("-" * 80)

    print(
        f"Sources run       : "
        f"{len(results)}"
    )

    print(
        f"Successful        : "
        f"{successful}"
    )

    print(
        f"Failed            : "
        f"{failed}"
    )

    print(
        f"Degraded health   : "
        f"{len(degraded)}"
    )

    if tolerated_degraded:
        print(
            f"Tolerated partial : "
            f"{len(tolerated_degraded)}"
        )

    print()

    print(
        f"Master summary:\n"
        f"{summary_path}"
    )

    if failed > 0:
        print()
        print("BLOCKED: one or more ACTIVE sources failed.")
        raise SystemExit(1)

    if tolerated_degraded:
        print()
        print("WARNING: tolerated PARTIAL_ERROR source health (usable collection retained).")
        for item in tolerated_degraded:
            print(
                "  - "
                + str(item.get("source_name", item.get("source_key", "UNKNOWN")))
                + ": candidates="
                + str(item.get("candidate_urls", 0))
                + ", invalid="
                + str(item.get("invalid_extraction", item.get("invalid_extractions", 0)))
                + ", fetch_failures="
                + str(item.get("fetch_failures", 0))
            )

    if blocking_degraded:
        print()
        print("BLOCKED: one or more ACTIVE sources have completeness-threatening degraded health.")
        for item in blocking_degraded:
            print(
                "  - "
                + str(item.get("source_name", item.get("source_key", "UNKNOWN")))
                + ": "
                + str(item.get("health_status"))
            )
        raise SystemExit(2)


if __name__ == "__main__":
    main()