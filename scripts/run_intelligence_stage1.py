from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from bank_intel.intelligence.stage1_gate import analyse_stage1, load_stage1_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAGED_ROOT = PROJECT_ROOT / "data" / "staged"
PROCESSED_ROOT = PROJECT_ROOT / "data" / "processed" / "stage1"
REVIEW_ROOT = PROJECT_ROOT / "data" / "review"
SRI_LANKA_TZ = ZoneInfo("Asia/Colombo")
ACTIVE_SOURCE_FOLDERS = ["daily_ft", "ceylon_today"]
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VERSION_PATTERN = re.compile(r"v(\d+)\.json$", re.IGNORECASE)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def version_number(path: Path, payload: dict) -> int:
    value = payload.get("article_version")
    try:
        if value is not None:
            return int(value)
    except (TypeError, ValueError):
        pass
    match = VERSION_PATTERN.search(path.name)
    return int(match.group(1)) if match else 0


def published_date(payload: dict) -> str | None:
    value = payload.get("published_at")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    parsed = parsed.replace(tzinfo=SRI_LANKA_TZ) if parsed.tzinfo is None else parsed.astimezone(SRI_LANKA_TZ)
    return parsed.date().isoformat()


def path_date(path: Path) -> str | None:
    for part in path.parts:
        if DATE_PATTERN.match(part):
            return part
    return None


def matches_target_date(*, path: Path, payload: dict, target_date: str) -> tuple[bool, str | None]:
    """Match Stage-1 input by the article's real publication date.

    ``published_at`` is authoritative. A later collection/storage date must
    never override a valid publication date. ``collection_run_date`` and the
    staged path date are retained only as legacy fallbacks when publication
    time is unavailable or invalid.
    """
    article_pub_date = published_date(payload)
    if article_pub_date is not None:
        if article_pub_date == target_date:
            return True, "published_at"
        return False, None

    collection_date = payload.get("collection_run_date")
    if collection_date and str(collection_date) == target_date:
        return True, "collection_run_date_fallback"

    file_date = path_date(path)
    if file_date == target_date:
        return True, "path_date_fallback"

    return False, None


def article_identity(*, path: Path, payload: dict, source_folder: str) -> str:
    if payload.get("canonical_url"):
        return f"{source_folder}|{payload['canonical_url']}"
    if payload.get("source_url"):
        return f"{source_folder}|{payload['source_url']}"
    if path.parent.name:
        return f"{source_folder}|{path.parent.name}"
    return f"{source_folder}|{path}"


def find_latest_article_files(*, target_date: str) -> tuple[list[Path], dict]:
    latest_by_article = {}
    scanned_files = valid_json_files = matched_files = load_failures = 0
    source_counts = {}
    match_methods = {
        "published_at": 0,
        "collection_run_date_fallback": 0,
        "path_date_fallback": 0,
    }

    for source_folder in ACTIVE_SOURCE_FOLDERS:
        source_root = STAGED_ROOT / source_folder
        source_counts[source_folder] = {"scanned": 0, "matched": 0}
        if not source_root.exists():
            continue

        for path in source_root.rglob("*.json"):
            scanned_files += 1
            source_counts[source_folder]["scanned"] += 1
            try:
                payload = load_json(path)
            except Exception:
                load_failures += 1
                continue
            if not isinstance(payload, dict):
                continue
            valid_json_files += 1

            matched, method = matches_target_date(path=path, payload=payload, target_date=target_date)
            if not matched:
                continue

            matched_files += 1
            source_counts[source_folder]["matched"] += 1
            if method:
                match_methods[method] += 1

            identity = article_identity(path=path, payload=payload, source_folder=source_folder)
            candidate_version = version_number(path, payload)
            mtime = path.stat().st_mtime
            existing = latest_by_article.get(identity)

            if (
                existing is None
                or candidate_version > existing["version"]
                or (candidate_version == existing["version"] and mtime > existing["mtime"])
            ):
                latest_by_article[identity] = {
                    "path": path,
                    "version": candidate_version,
                    "mtime": mtime,
                }

    selected = sorted((item["path"] for item in latest_by_article.values()), key=str)
    diagnostics = {
        "scanned_json_files": scanned_files,
        "valid_json_files": valid_json_files,
        "matched_json_files": matched_files,
        "unique_articles_selected": len(selected),
        "load_failures": load_failures,
        "match_methods": match_methods,
        "source_counts": source_counts,
    }
    return selected, diagnostics


def find_available_dates() -> dict:
    """Report effective publication dates, not a union of folder/run dates."""
    dates = {}
    for source_folder in ACTIVE_SOURCE_FOLDERS:
        source_root = STAGED_ROOT / source_folder
        source_dates = {}
        if not source_root.exists():
            dates[source_folder] = {}
            continue

        for path in source_root.rglob("*.json"):
            try:
                payload = load_json(path)
            except Exception:
                continue

            value = published_date(payload)
            if value is None:
                collection_date = payload.get("collection_run_date")
                if collection_date and DATE_PATTERN.match(str(collection_date)):
                    value = str(collection_date)
            if value is None:
                value = path_date(path)
            if value:
                source_dates[value] = source_dates.get(value, 0) + 1

        dates[source_folder] = dict(sorted(source_dates.items()))
    return dates


def source_slug(source_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", source_name.lower().strip()).strip("_")


def article_id_from_path(path: Path, article: dict) -> str:
    if (
        path.parent
        and path.parent.name
        and path.parent.name not in {"daily_ft", "ceylon_today"}
        and not DATE_PATTERN.match(path.parent.name)
    ):
        return path.parent.name
    if article.get("content_hash"):
        return str(article["content_hash"])[:16]
    return re.sub(r"[^a-zA-Z0-9]+", "_", path.stem)


def save_analysis(*, run_date: str, input_path: Path, article: dict, analysis) -> Path:
    source_name = str(article.get("source_name", "unknown") or "unknown")
    article_id = article_id_from_path(input_path, article)
    folder = PROCESSED_ROOT / run_date / source_slug(source_name)
    folder.mkdir(parents=True, exist_ok=True)
    output_path = folder / f"{article_id}.json"
    try:
        input_file_value = str(input_path.relative_to(PROJECT_ROOT))
    except ValueError:
        input_file_value = str(input_path)

    payload = {
        "processed_at": datetime.now(SRI_LANKA_TZ).isoformat(),
        "analysis_date": run_date,
        "input_file": input_file_value,
        "source_name": article.get("source_name"),
        "section_name": article.get("section_name"),
        "headline": article.get("headline"),
        "published_at": article.get("published_at"),
        "source_url": article.get("source_url"),
        "canonical_url": article.get("canonical_url"),
        "content_hash": article.get("content_hash"),
        "article_version": article.get("article_version"),
        "collection_status": article.get("collection_status"),
        "intake_status": article.get("intake_status"),
        "collection_run_date": article.get("collection_run_date"),
        "stage1": analysis.to_dict(),
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def load_existing_review_annotations(review_path: Path) -> dict[str, dict[str, str]]:
    """Preserve analyst labels when scheduled Stage-1 re-runs a date."""
    if not review_path.exists():
        return {}

    annotations: dict[str, dict[str, str]] = {}
    try:
        with review_path.open("r", newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            for row in reader:
                source_url = str(row.get("source_url", "") or "").strip()
                if not source_url:
                    continue
                annotations[source_url] = {
                    "human_label": str(row.get("human_label", "") or ""),
                    "review_notes": str(row.get("review_notes", "") or ""),
                }
    except Exception:
        return {}
    return annotations


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage-1 financial relevance and PR/noise analysis.")
    parser.add_argument(
        "--date",
        help=(
            "Target publication date YYYY-MM-DD. Uses published_at as the "
            "authoritative date; collection/path dates are legacy fallbacks only."
        ),
    )
    args = parser.parse_args()
    run_date = args.date or datetime.now(SRI_LANKA_TZ).date().isoformat()
    try:
        datetime.strptime(run_date, "%Y-%m-%d")
    except ValueError:
        raise ValueError("--date must use YYYY-MM-DD format.")

    config = load_stage1_config()
    article_files, diagnostics = find_latest_article_files(target_date=run_date)
    print(
        "BANK SUPERVISION NEWS INTELLIGENCE — STAGE 1\n"
        f"Target publication date: {run_date}\n"
        f"Rules version: {config.get('version')}\n"
        f"Unique articles: {diagnostics['unique_articles_selected']}\n"
        f"Match methods: {diagnostics['match_methods']}"
    )

    if not article_files:
        print("No staged articles matched the requested publication date.")
        for source, date_counts in find_available_dates().items():
            print(source, date_counts)
        return

    REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    review_path = REVIEW_ROOT / f"stage1_review_{run_date}.csv"
    existing_annotations = load_existing_review_annotations(review_path)
    review_rows = []
    include_count = watch_count = exclude_count = failed_count = 0

    for path in article_files:
        try:
            article = load_json(path)
            headline = str(article.get("headline", "") or "").strip()
            article_text = str(article.get("article_text", "") or "").strip()
            source_url = str(article.get("source_url", "") or "").strip()
            if not headline:
                raise ValueError("HEADLINE_MISSING")
            if not article_text:
                raise ValueError("ARTICLE_TEXT_MISSING")
            if not source_url:
                raise ValueError("SOURCE_URL_MISSING")

            analysis = analyse_stage1(headline=headline, article_text=article_text, config=config)
            output_path = save_analysis(run_date=run_date, input_path=path, article=article, analysis=analysis)

            if analysis.gate_decision == "INCLUDE_CANDIDATE":
                include_count += 1
            elif analysis.gate_decision == "WATCH":
                watch_count += 1
            else:
                exclude_count += 1

            previous = existing_annotations.get(source_url, {})
            review_rows.append({
                "source_name": article.get("source_name"),
                "headline": headline,
                "published_at": article.get("published_at"),
                "source_url": source_url,
                "financial_score": analysis.financial_relevance_score,
                "financial_label": analysis.financial_relevance_label,
                "pr_score": analysis.pr_noise_score,
                "pr_label": analysis.pr_noise_label,
                "article_type_hint": analysis.article_type_hint,
                "gate_decision": analysis.gate_decision,
                "institutions": "; ".join(analysis.institutions),
                "matched_financial_terms": "; ".join(f"{k}:" + "|".join(v) for k, v in analysis.matched_financial_terms.items()),
                "matched_pr_terms": "; ".join(f"{k}:" + "|".join(v) for k, v in analysis.matched_pr_terms.items()),
                "pr_counter_signals": "; ".join(analysis.pr_counter_signals),
                "human_label": previous.get("human_label", ""),
                "review_notes": previous.get("review_notes", ""),
                "analysis_file": str(output_path),
            })
        except Exception as exc:
            failed_count += 1
            print(f"FAILED: {type(exc).__name__}: {exc}")

    fieldnames = [
        "source_name", "headline", "published_at", "source_url",
        "financial_score", "financial_label", "pr_score", "pr_label",
        "article_type_hint", "gate_decision", "institutions",
        "matched_financial_terms", "matched_pr_terms", "pr_counter_signals",
        "human_label", "review_notes", "analysis_file",
    ]
    with review_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(review_rows)

    print(
        "STAGE-1 INTELLIGENCE COMPLETE\n"
        f"Articles analysed: {len(review_rows)}\n"
        f"Include candidates: {include_count}\n"
        f"Watch: {watch_count}\n"
        f"Excluded as noise: {exclude_count}\n"
        f"Failures: {failed_count}\n"
        f"Human review file: {review_path}"
    )


if __name__ == "__main__":
    main()
