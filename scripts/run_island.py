from __future__ import annotations

import argparse

from bank_intel.collectors.sources._public_business_base import (
    default_window,
    parse_cli_date,
    run_collection,
    write_source_status,
)
from bank_intel.collectors.sources.island import IslandBusinessAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect The Island Business articles."
    )
    parser.add_argument(
        "--from-date",
        dest="from_date",
        help="YYYY-MM-DD; defaults to yesterday",
    )
    parser.add_argument(
        "--to-date",
        dest="to_date",
        help="YYYY-MM-DD; defaults to today",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=6,
        help=(
            "Maximum listing pages to inspect. "
            "Use a larger value for historical backfill."
        ),
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.20,
        help="Polite delay between article requests.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    default_from, default_to = default_window()

    start_date = (
        parse_cli_date(args.from_date)
        if args.from_date
        else default_from
    )
    end_date = (
        parse_cli_date(args.to_date)
        if args.to_date
        else default_to
    )

    adapter = IslandBusinessAdapter()

    status = run_collection(
        adapter,
        start_date=start_date,
        end_date=end_date,
        max_pages=args.max_pages,
        sleep_seconds=args.sleep_seconds,
        extra_surfaces=[
            ("Homepage", lambda page: "https://island.lk/", 1),
            ("News", lambda page: (
                "https://island.lk/category/news/" if page == 1
                else f"https://island.lk/category/news/page/{page}/"
            ), min(4, args.max_pages)),
        ],
    )

    rss = adapter.rss_diagnostics()
    status["official_rss_fallback"] = {
        "used": rss["used"],
        "feed_pages": {k: {"url": v["url"], "count": v["count"]}
                       for k, v in rss["feed_pages"].items()},
        "full_articles_recovered": rss["full_articles_recovered"],
        "errors": rss["errors"],
    }
    if rss["used"] and rss["errors"]:
        # Page/feed gaps must not silently pass as complete coverage.
        if status.get("health_status") == "HEALTHY":
            status["health_status"] = "PARTIAL_ERROR"
    write_source_status(adapter.source_key, status)
    print("Island official RSS fallback:", status["official_rss_fallback"])

    if status.get("execution_status") != "SUCCESS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
