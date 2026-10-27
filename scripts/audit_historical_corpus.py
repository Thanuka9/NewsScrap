
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

DEFAULT_SOURCES = ["daily_ft", "ceylon_today", "daily_mirror", "island"]


def parse_args():
    p = argparse.ArgumentParser(
        description="Audit staged historical news corpus before ML processing."
    )
    p.add_argument("--root", default="data/staged")
    p.add_argument("--sources", nargs="*", default=DEFAULT_SOURCES)
    p.add_argument("--start-date", default="2026-01-01")
    p.add_argument("--end-date", default="2026-10-07")
    p.add_argument("--short-words", type=int, default=80)
    p.add_argument("--output-dir", default="data/backfill_runs")
    return p.parse_args()


def nested_candidates(obj: dict[str, Any]):
    yield obj
    for key in ("article", "record", "payload", "data", "content"):
        value = obj.get(key)
        if isinstance(value, dict):
            yield value


def get_any(obj: dict[str, Any], *keys: str):
    for candidate in nested_candidates(obj):
        for key in keys:
            value = candidate.get(key)
            if value not in (None, ""):
                return value
    return None


def parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        pass
    for fmt in (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            continue
    return None


def canonical_url(value: Any) -> str | None:
    if not value:
        return None
    url = str(value).strip()
    return url.rstrip("/") if url else None


def word_count(text: Any) -> int:
    if not text:
        return 0
    return len(str(text).split())


def main():
    args = parse_args()
    root = Path(args.root)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    detail_rows = []
    source_stats = {}
    monthly_counts = {}
    global_urls = defaultdict(list)

    for source in args.sources:
        source_dir = root / source
        stats = {
            "source": source,
            "folder_exists": source_dir.exists(),
            "json_files": 0,
            "valid_json": 0,
            "invalid_json": 0,
            "missing_url": 0,
            "missing_headline": 0,
            "missing_date": 0,
            "unparseable_date": 0,
            "missing_body": 0,
            "short_body": 0,
            "outside_window": 0,
            "duplicate_url_records": 0,
            "unique_urls": 0,
            "earliest_date": None,
            "latest_date": None,
        }

        monthly = Counter()
        source_urls = defaultdict(list)
        source_dates = []

        files = sorted(source_dir.rglob("*.json")) if source_dir.exists() else []

        for path in files:
            stats["json_files"] += 1
            row = {
                "source": source,
                "file": str(path),
                "status": "OK",
                "published_date": "",
                "url": "",
                "headline": "",
                "word_count": 0,
                "issues": "",
            }

            try:
                obj = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(obj, dict):
                    raise ValueError("JSON root is not an object")
                stats["valid_json"] += 1
            except Exception as exc:
                stats["invalid_json"] += 1
                row["status"] = "INVALID_JSON"
                row["issues"] = f"{type(exc).__name__}: {exc}"
                detail_rows.append(row)
                continue

            url = canonical_url(
                get_any(obj, "source_url", "url", "article_url", "canonical_url")
            )
            headline = get_any(obj, "headline", "title", "article_title")
            published_raw = get_any(
                obj, "published_at", "published_date", "publication_date", "date"
            )
            body = get_any(
                obj, "article_text", "body", "text", "content_text", "article_body"
            )

            issues = []

            if not url:
                stats["missing_url"] += 1
                issues.append("MISSING_URL")
            else:
                source_urls[url].append(str(path))
                global_urls[url].append(f"{source}:{path}")

            if not headline:
                stats["missing_headline"] += 1
                issues.append("MISSING_HEADLINE")

            published_date = None
            if published_raw in (None, ""):
                stats["missing_date"] += 1
                issues.append("MISSING_DATE")
            else:
                published_date = parse_date(published_raw)
                if published_date is None:
                    stats["unparseable_date"] += 1
                    issues.append("UNPARSEABLE_DATE")
                else:
                    source_dates.append(published_date)
                    monthly[published_date.strftime("%Y-%m")] += 1
                    if not (start_date <= published_date <= end_date):
                        stats["outside_window"] += 1
                        issues.append("OUTSIDE_WINDOW")

            wc = word_count(body)
            if wc == 0:
                stats["missing_body"] += 1
                issues.append("MISSING_BODY")
            elif wc < args.short_words:
                stats["short_body"] += 1
                issues.append("SHORT_BODY")

            row.update(
                {
                    "status": "FLAGGED" if issues else "OK",
                    "published_date": (
                        published_date.isoformat() if published_date else ""
                    ),
                    "url": url or "",
                    "headline": str(headline or ""),
                    "word_count": wc,
                    "issues": ";".join(issues),
                }
            )
            detail_rows.append(row)

        stats["unique_urls"] = len(source_urls)
        stats["duplicate_url_records"] = sum(
            max(0, len(paths) - 1) for paths in source_urls.values()
        )

        if source_dates:
            stats["earliest_date"] = min(source_dates).isoformat()
            stats["latest_date"] = max(source_dates).isoformat()

        source_stats[source] = stats
        monthly_counts[source] = monthly

    global_duplicate_urls = {
        url: refs for url, refs in global_urls.items() if len(refs) > 1
    }

    summary = {
        "generated_at": datetime.now().isoformat(),
        "window": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "short_body_threshold_words": args.short_words,
        "sources": source_stats,
        "global": {
            "total_json_files": sum(s["json_files"] for s in source_stats.values()),
            "total_valid_json": sum(s["valid_json"] for s in source_stats.values()),
            "total_invalid_json": sum(
                s["invalid_json"] for s in source_stats.values()
            ),
            "missing_dates": sum(s["missing_date"] for s in source_stats.values()),
            "unparseable_dates": sum(
                s["unparseable_date"] for s in source_stats.values()
            ),
            "missing_bodies": sum(s["missing_body"] for s in source_stats.values()),
            "short_bodies": sum(s["short_body"] for s in source_stats.values()),
            "outside_window": sum(s["outside_window"] for s in source_stats.values()),
            "duplicate_urls_across_all_sources": len(global_duplicate_urls),
        },
        "global_duplicate_urls": global_duplicate_urls,
    }

    summary_path = out_dir / f"corpus_audit_{timestamp}.json"
    detail_path = out_dir / f"corpus_audit_details_{timestamp}.csv"
    monthly_path = out_dir / f"corpus_audit_monthly_{timestamp}.csv"

    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    with detail_path.open("w", newline="", encoding="utf-8-sig") as f:
        fields = [
            "source",
            "file",
            "status",
            "published_date",
            "url",
            "headline",
            "word_count",
            "issues",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(detail_rows)

    all_months = sorted(
        {month for counter in monthly_counts.values() for month in counter}
    )

    with monthly_path.open("w", newline="", encoding="utf-8-sig") as f:
        fields = ["month", *args.sources]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for month in all_months:
            writer.writerow(
                {
                    "month": month,
                    **{
                        source: monthly_counts[source].get(month, 0)
                        for source in args.sources
                    },
                }
            )

    print("=" * 88)
    print("HISTORICAL NEWS CORPUS AUDIT")
    print("=" * 88)
    print(f"Expected window : {start_date} -> {end_date}")
    print()

    header = (
        f"{'SOURCE':<16}"
        f"{'FILES':>8}"
        f"{'UNIQUE':>9}"
        f"{'BADJSON':>9}"
        f"{'MISSDT':>8}"
        f"{'SHORT':>8}"
        f"{'OUTWIN':>8}"
        f"  {'EARLIEST':<10}"
        f"  {'LATEST':<10}"
    )
    print(header)
    print("-" * len(header))

    for source in args.sources:
        s = source_stats[source]
        print(
            f"{source:<16}"
            f"{s['json_files']:>8}"
            f"{s['unique_urls']:>9}"
            f"{s['invalid_json']:>9}"
            f"{s['missing_date']:>8}"
            f"{s['short_body']:>8}"
            f"{s['outside_window']:>8}"
            f"  {str(s['earliest_date'] or '-'):<10}"
            f"  {str(s['latest_date'] or '-'):<10}"
        )

    print()
    print("GLOBAL")
    print(f"  JSON files       : {summary['global']['total_json_files']}")
    print(f"  Valid JSON       : {summary['global']['total_valid_json']}")
    print(f"  Invalid JSON     : {summary['global']['total_invalid_json']}")
    print(f"  Missing dates    : {summary['global']['missing_dates']}")
    print(f"  Unparseable dates: {summary['global']['unparseable_dates']}")
    print(f"  Missing bodies   : {summary['global']['missing_bodies']}")
    print(f"  Short bodies     : {summary['global']['short_bodies']}")
    print(f"  Outside window   : {summary['global']['outside_window']}")
    print(
        "  Cross-source duplicate URLs: "
        f"{summary['global']['duplicate_urls_across_all_sources']}"
    )

    print()
    print(f"Summary JSON : {summary_path}")
    print(f"Detail CSV   : {detail_path}")
    print(f"Monthly CSV  : {monthly_path}")


if __name__ == "__main__":
    main()
