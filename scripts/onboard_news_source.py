from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "sources" / "generic_registry.yaml"
RUNNER = ROOT / "scripts" / "run_generic_sources.py"


def slug(value: str) -> str:
    value = re.sub(r"^www\.", "", value.lower().strip())
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value or "new_source"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Add a new website to the generic TESTING registry and immediately "
            "run a structural diagnostic."
        )
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--name")
    parser.add_argument("--source-key")
    parser.add_argument("--prefer-browser", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    parsed = urlparse(args.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit("A valid http/https URL is required.")

    key = args.source_key or slug(parsed.netloc)
    name = args.name or re.sub(r"[_-]+", " ", key).title()

    payload = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}
    sources = payload.setdefault("sources", {})

    if key in sources:
        print(f"Source already exists: {key}")
    else:
        sources[key] = {
            "name": name,
            "section_name": "Business",
            "listing_url": args.url,
            "enabled": True,
            "status": "TESTING",
            "listing_prefer_browser": bool(args.prefer_browser),
            "article_prefer_browser": False,
            "article_url_patterns": [],
            "exclude_url_patterns": [],
            "minimum_article_words": 80,
            "date_regexes": [],
        }

        REGISTRY.write_text(
            yaml.safe_dump(
                payload,
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        print(f"Added TESTING profile: {key}")
        print(f"Registry: {REGISTRY}")

    print()
    print("Running diagnostic...")
    command = [
        sys.executable,
        str(RUNNER),
        "--source",
        key,
        "--diagnose",
        "--limit",
        str(args.limit),
    ]
    raise SystemExit(subprocess.call(command, cwd=ROOT))


if __name__ == "__main__":
    main()
