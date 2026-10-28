from __future__ import annotations

from _runtime import locked_main
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from bank_intel.common.source_status import current_status_path

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "scripts" / "run_daily_ft.py"
FRONT_PAGE = "https://www.ft.lk/front-page/44"
SOURCE_KEY = "daily_ft"

COUNT_FIELDS = (
    "candidate_urls",
    "new_articles",
    "late_discoveries",
    "changed_articles",
    "unchanged_articles",
    "wrong_date_skipped",
    "missing_date",
    "invalid_extractions",
    "fetch_failures",
    "recent_duplicates",
)

def read_status() -> dict:
    path = current_status_path(SOURCE_KEY)
    if not path.exists():
        raise RuntimeError(f"Daily FT status file missing: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))

def run_financial_services() -> dict:
    print()
    print("=" * 80)
    print("DAILY FT PASS 1/2: FINANCIAL SERVICES")
    print("=" * 80)
    result = subprocess.run(
        [sys.executable, str(ORIGINAL)],
        cwd=str(ROOT),
        env=os.environ.copy(),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Daily FT Financial Services pass failed with return code {result.returncode}"
        )
    status = read_status()
    if status.get("execution_status") != "SUCCESS":
        raise RuntimeError(f"Daily FT Financial Services status failed: {status}")
    return status

def _front_page_url(page_number: int) -> str:
    # The current daily window is fully covered by the first Front Page listing.
    # A second-page form is supplied only as a defensive fallback.
    if int(page_number) <= 1:
        return FRONT_PAGE
    return f"{FRONT_PAGE}/{int(page_number)}"

def run_front_page() -> dict:
    print()
    print("=" * 80)
    print("DAILY FT PASS 2/2: FRONT PAGE")
    print("=" * 80)

    spec = importlib.util.spec_from_file_location(
        "_daily_ft_front_page_runtime",
        ORIGINAL,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load run_daily_ft.py for Front Page pass")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    # Patch both the source adapter module and any directly-imported globals in
    # the runner. This supports either implementation style.
    from bank_intel.collectors.sources import daily_ft as daily_ft_adapter

    if hasattr(daily_ft_adapter, "build_section_page_url"):
        daily_ft_adapter.build_section_page_url = _front_page_url

    if hasattr(module, "build_section_page_url"):
        module.build_section_page_url = _front_page_url

    # Replace common URL constants if the collector stores the listing URL as a
    # module-level string rather than calling build_section_page_url().
    for obj in (daily_ft_adapter, module):
        for name, value in list(vars(obj).items()):
            if (
                isinstance(value, str)
                and "ft.lk/financial-services/42" in value
            ):
                setattr(obj, name, FRONT_PAGE)

    # Keep the supplementary pass intentionally shallow. Current/yesterday
    # Front Page coverage fits on the first listing and avoids uncertain deep
    # pagination routes.
    for obj in (module, daily_ft_adapter):
        for name in (
            "MAX_PAGES",
            "DISCOVERY_MAX_PAGES",
            "PAGE_LIMIT",
            "MAX_LISTING_PAGES",
        ):
            if hasattr(obj, name):
                try:
                    setattr(obj, name, 1)
                except Exception:
                    pass

    old_argv = sys.argv[:]
    try:
        sys.argv = [str(ORIGINAL)]
        module.main()
    except SystemExit as exc:
        code = exc.code
        if code not in (None, 0):
            raise RuntimeError(
                f"Daily FT Front Page pass exited with code {code}"
            ) from exc
    finally:
        sys.argv = old_argv

    status = read_status()
    if status.get("execution_status") != "SUCCESS":
        raise RuntimeError(f"Daily FT Front Page status failed: {status}")
    return status

def merge_status(first: dict, second: dict) -> dict:
    merged = dict(second)

    # Both child passes inherit the same master run id. Fail closed if not.
    if first.get("run_id") != second.get("run_id"):
        raise RuntimeError(
            "Daily FT combined status run-id mismatch: "
            f"{first.get('run_id')} != {second.get('run_id')}"
        )

    for field in COUNT_FIELDS:
        a = first.get(field)
        b = second.get(field)
        if isinstance(a, int) or isinstance(b, int):
            merged[field] = int(a or 0) + int(b or 0)

    first_health = str(first.get("health_status") or "").upper()
    second_health = str(second.get("health_status") or "").upper()
    if "FAILED" in {first_health, second_health}:
        merged["health_status"] = "FAILED"
    elif "PARTIAL_ERROR" in {first_health, second_health}:
        merged["health_status"] = "PARTIAL_ERROR"
    else:
        merged["health_status"] = "HEALTHY"

    statuses = {
        str(first.get("content_status") or ""),
        str(second.get("content_status") or ""),
    }
    if "NEW_ARTICLES_FOUND" in statuses:
        merged["content_status"] = "NEW_ARTICLES_FOUND"
    elif "CHANGED_ARTICLES_FOUND" in statuses:
        merged["content_status"] = "CHANGED_ARTICLES_FOUND"
    else:
        merged["content_status"] = second.get(
            "content_status",
            first.get("content_status", "NO_NEW_ARTICLES"),
        )

    merged["execution_status"] = "SUCCESS"
    merged["listing_scope"] = [
        "https://www.ft.lk/financial-services/42",
        FRONT_PAGE,
    ]
    merged["coverage_mode"] = "FINANCIAL_SERVICES_PLUS_FRONT_PAGE"

    path = current_status_path(SOURCE_KEY)
    path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return merged

def main() -> None:
    if not ORIGINAL.exists():
        raise FileNotFoundError(ORIGINAL)

    first = run_financial_services()
    second = run_front_page()
    merged = merge_status(first, second)

    print()
    print("=" * 80)
    print("DAILY FT COMBINED COVERAGE COMPLETE")
    print("=" * 80)
    print("Scope     : Financial Services + Front Page")
    print(f"Execution : {merged.get('execution_status')}")
    print(f"Health    : {merged.get('health_status')}")
    print(f"Content   : {merged.get('content_status')}")
    print(f"Candidates: {merged.get('candidate_urls', 0)}")
    print(f"New       : {merged.get('new_articles', 0)}")
    print("=" * 80)

if __name__ == "__main__":
    locked_main(main)
