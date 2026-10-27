from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "run_historical_backfill.py"
FRONT_PAGE = "https://www.ft.lk/front-page/44"

def front_page_url(page_number: int) -> str:
    if int(page_number) <= 1:
        return FRONT_PAGE
    return f"{FRONT_PAGE}/{int(page_number)}"

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill Daily FT Front Page into the existing daily_ft staged corpus."
    )
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()

    if not TARGET.exists():
        raise FileNotFoundError(TARGET)

    spec = importlib.util.spec_from_file_location(
        "_daily_ft_front_page_backfill_runtime",
        TARGET,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load run_historical_backfill.py")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    original = module.SOURCES["daily_ft"]
    module.SOURCES["daily_ft"] = module.BackfillSource(
        source_key="daily_ft",
        source_name=original.source_name,
        section_name="Front Page",
        build_page_url=front_page_url,
        discover_articles=original.discover_articles,
    )

    old_argv = sys.argv[:]
    try:
        sys.argv = [
            str(TARGET),
            "--source", "daily_ft",
            "--start-date", args.start_date,
            "--end-date", args.end_date,
            "--max-pages", "1",
        ]
        module.main()
    finally:
        sys.argv = old_argv

if __name__ == "__main__":
    main()
