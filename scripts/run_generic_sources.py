from __future__ import annotations

from _runtime import locked_main
import argparse
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bank_intel.collection.generic_source_engine import (
    collect_and_stage,
    diagnose_source,
)
from bank_intel.common.source_status import current_status_path

TZ = ZoneInfo("Asia/Colombo")
REGISTRY = ROOT / "config" / "sources" / "generic_registry.yaml"
DIAG_DIR = ROOT / "data" / "source_diagnostics"
DIAG_DIR.mkdir(parents=True, exist_ok=True)


def load_registry() -> dict:
    payload = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}
    sources = payload.get("sources") or {}
    if not isinstance(sources, dict):
        raise ValueError("generic_registry.yaml must contain a sources mapping")
    return sources


def default_window(to_date: date) -> tuple[date, date]:
    from bank_intel.common.collection_window import get_collection_window
    window = get_collection_window(datetime.combine(to_date, datetime.min.time(), tzinfo=TZ))
    return window.start_date, window.end_date


def write_production_status(
    *,
    source_key: str,
    profile: dict,
    item: dict,
    run_id: str,
    started_at: datetime,
    from_date: date,
    to_date: date,
) -> dict:
    listing = item.get("listing") or {}
    candidates = int(item.get("candidate_urls", 0) or 0)
    valid = int(item.get("valid_extractions", 0) or 0)
    invalid = int(item.get("invalid_extractions", 0) or 0)
    blocked = int(item.get("blocked_fetches", 0) or 0)
    listing_partial = bool(listing.get("listing_partial_error"))
    truncated = int(item.get("truncated_discovery_urls", 0) or 0)

    listing_failed = bool(
        listing.get("listing_blocked")
        or listing.get("listing_error")
        or listing.get("listing_status") is None
    )
    discovery_failed = candidates <= 0
    extraction_failed = candidates > 0 and valid <= 0

    if listing_failed or discovery_failed or extraction_failed:
        execution_status = "FAILED"
        health_status = "FAILED"
    elif invalid or blocked or listing_partial or truncated:
        execution_status = "SUCCESS"
        health_status = "PARTIAL_ERROR"
    else:
        execution_status = "SUCCESS"
        health_status = "HEALTHY"

    new_articles = int(item.get("new_articles", 0) or 0)
    changed_articles = int(item.get("changed_articles", 0) or 0)

    if execution_status == "FAILED":
        content_status = "COLLECTION_FAILED"
    elif new_articles or changed_articles:
        content_status = "NEW_OR_CHANGED_ARTICLES"
    else:
        content_status = "NO_NEW_ARTICLES"

    completed_at = datetime.now(TZ)
    status = {
        "run_id": run_id,
        "source_key": source_key,
        "source_name": profile.get("name", source_key),
        "execution_status": execution_status,
        "health_status": health_status,
        "content_status": content_status,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "collection_mode": "GENERIC_DATE_WINDOW",
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "candidate_urls": candidates,
        "all_discovered_urls": int(item.get("all_discovered_urls", candidates)),
        "truncated_discovery_urls": truncated,
        "staged_count": len(item.get("staged") or []),
        "discovered_urls": item.get("discovered_urls") or [],
        "discovery_surfaces": [
            page.get("listing_name")
            for page in listing.get("listing_pages", [])
        ] if listing.get("listing_pages") else ["Default"],
        "failed_discovery_surfaces": [
            page.get("listing_name")
            for page in listing.get("listing_pages", [])
            if page.get("listing_blocked") or page.get("listing_error") or page.get("pagination_complete") is False
            or not (200 <= int(page.get("listing_status") or 0) < 400)
        ],
        "new_articles": new_articles,
        "late_discoveries": int(item.get("late_discoveries", 0) or 0),
        "changed_articles": changed_articles,
        "unchanged_articles": int(item.get("unchanged_articles", 0) or 0),
        "wrong_date_skipped": int(item.get("wrong_date_skipped", 0) or 0),
        "missing_date": int(item.get("missing_date", 0) or 0),
        "invalid_extraction": invalid,
        "fetch_failures": int(item.get("fetch_failures", blocked) or 0),
        "recent_duplicates": int(item.get("recent_duplicates", 0) or 0),
        "extraction_failures": item.get("extraction_failures", []),
        "unresolved_fetch_urls": item.get("unresolved_fetch_urls", []),
        "recovered_urls": item.get("recovered_urls", []),
        "listing_pages": listing.get("listing_pages", []),
        "listing_fetch_method": listing.get("listing_fetch_method"),
        "listing_status": listing.get("listing_status"),
    }

    path = current_status_path(source_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(status, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print()
    print("-" * 100)
    print("PRODUCTION SOURCE STATUS")
    print("-" * 100)
    print(f"Execution : {execution_status}")
    print(f"Health    : {health_status}")
    print(f"Content   : {content_status}")
    print(f"Status    : {path}")

    return status


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generic news-source diagnostics, test staging and production collection."
    )
    parser.add_argument("--source", default="all")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    args = parser.parse_args()

    if args.production:
        args.stage = True
        if args.source == "all":
            raise SystemExit("--production requires one explicit --source key")

    if not args.diagnose and not args.stage:
        args.diagnose = True

    sources = load_registry()
    if args.source == "all":
        selected = [
            (k, v)
            for k, v in sources.items()
            if bool(v.get("enabled", True))
        ]
    else:
        if args.source not in sources:
            raise SystemExit(f"Unknown generic source: {args.source}")
        selected = [(args.source, sources[args.source])]

    stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    report = {
        "version": "generic-source-v2-production",
        "generated_at": datetime.now(TZ).isoformat(),
        "mode": (
            "production"
            if args.production
            else ("stage" if args.stage else "diagnose")
        ),
        "sources": [],
    }

    print("=" * 100)
    print("GENERIC NEWS SOURCES")
    print("=" * 100)
    print(f"Mode   : {report['mode'].upper()}")
    print(f"Sources: {len(selected)}")

    if args.stage:
        today = datetime.now(TZ).date()
        to_date = date.fromisoformat(args.to_date) if args.to_date else today
        if args.from_date:
            from_date = date.fromisoformat(args.from_date)
        else:
            from_date, _ = default_window(to_date)

    production_failed = False

    for source_key, profile in selected:
        print()
        print("#" * 100)
        print(f"SOURCE: {profile.get('name', source_key)} [{source_key}]")
        print("#" * 100)

        if args.diagnose and not args.stage:
            item = diagnose_source(
                source_key,
                profile,
                limit=args.limit,
            )
            report["sources"].append(item)
            print()
            print(f"RESULT: {item['status']}")
            print(
                f"Candidates={item['candidate_article_links']} | "
                f"tested={item['articles_tested']} | "
                f"valid={item['articles_valid']} | "
                f"blocked={item['articles_blocked']}"
            )
            continue

        started_at = datetime.now(TZ)
        item = collect_and_stage(
            project_root=ROOT,
            source_key=source_key,
            profile=profile,
            from_date=from_date,
            to_date=to_date,
            limit=args.limit,
            production=args.production,
        )

        if args.production:
            run_id = (
                os.environ.get("BANK_INTEL_RUN_ID")
                or started_at.strftime("%Y%m%d_%H%M%S") + "_standalone"
            )
            source_status = write_production_status(
                source_key=source_key,
                profile=profile,
                item=item,
                run_id=run_id,
                started_at=started_at,
                from_date=from_date,
                to_date=to_date,
            )
            item["production_status"] = source_status
            if source_status.get("execution_status") != "SUCCESS":
                production_failed = True

        report["sources"].append(item)
        print(json.dumps(item, indent=2, ensure_ascii=False))

    output = DIAG_DIR / f"generic_source_{report['mode']}_{stamp}.json"
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print()
    print("=" * 100)
    print(f"Diagnostic/output: {output}")
    print("=" * 100)

    if production_failed:
        raise SystemExit(1)


if __name__ == "__main__":
    locked_main(main)
