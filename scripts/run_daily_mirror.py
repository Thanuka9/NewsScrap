from __future__ import annotations

# NEWS_DAILY_MIRROR_LATE_REPLAY_V1

import argparse
from datetime import timedelta

from bank_intel.collectors.sources._public_business_base import (
    default_window,
    parse_cli_date,
    run_collection,
)
from bank_intel.collectors.sources.daily_mirror import DailyMirrorBusinessAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect Daily Mirror Business articles with bounded late-arrival replay."
    )
    parser.add_argument(
        "--from-date",
        dest="from_date",
        help="YYYY-MM-DD; default automatically includes the late-arrival/Monday replay window",
    )
    parser.add_argument(
        "--to-date",
        dest="to_date",
        help="YYYY-MM-DD; defaults to today",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=12,
        help="Maximum listing pages to inspect. Default is 12 for robust late/weekend replay.",
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

    end_date = (
        parse_cli_date(args.to_date)
        if args.to_date
        else default_to
    )

    if args.from_date:
        start_date = parse_cli_date(args.from_date)
    else:
        # Normal day: replay two prior publication dates so a late article is
        # collected automatically. Monday: explicitly cover Friday -> Monday.
        required_start = (
            end_date - timedelta(days=3)
            if end_date.weekday() == 0
            else end_date - timedelta(days=1)
        )
        start_date = min(default_from, required_start)

    print("=" * 80)
    print("DAILY MIRROR PRODUCTION WINDOW")
    print("=" * 80)
    print(f"From : {start_date}")
    print(f"To   : {end_date}")
    print("Mode : bounded late-arrival replay / Monday weekend catch-up")

    adapter = DailyMirrorBusinessAdapter()
    status = run_collection(
        adapter,
        start_date=start_date,
        end_date=end_date,
        max_pages=args.max_pages,
        sleep_seconds=args.sleep_seconds,
        extra_surfaces=[
            ("Homepage", lambda page: "https://www.dailymirror.lk/", 1),
            ("Breaking News", lambda page: "https://www.dailymirror.lk/breaking-news/108", 1),
            ("Top Stories", lambda page: "https://www.dailymirror.lk/top-storys/155", 1),
            ("Latest News", lambda page: "https://www.dailymirror.lk/latest-news/342", 1),
        ],
    )

    if status.get("execution_status") != "SUCCESS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
