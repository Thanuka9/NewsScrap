from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path

from bank_intel.collectors.http_client import HttpClient
from bank_intel.collectors.browser_client import BrowserClient
from bank_intel.collectors.diagnostic import SOURCE_SPECS, diagnose_source

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "diagnostics"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def print_source_result(result):
    print()
    print("=" * 78)
    print(f"{result.source_name}")
    print("=" * 78)
    print(f"Section       : {result.section_name}")
    print(f"URL           : {result.section_url}")
    print(f"Section fetch : {result.section_fetch_success}")
    print(f"Fetch method  : {result.section_fetch_method}")
    print(f"HTTP status   : {result.section_status_code}")
    print(f"Links found   : {result.candidate_links_found}")
    print(f"Articles test : {result.tested_articles}")
    print(f"Valid articles: {result.successful_articles}")
    print(f"Browser used  : {result.browser_fallback_used}")
    if result.notes:
        print(f"Notes         : {result.notes}")
    for index, article in enumerate(result.article_results, start=1):
        print()
        print(f"  ARTICLE {index}")
        print(f"  Listing title : {article.headline}")
        print(f"  URL           : {article.url}")
        print(f"  Fetch method  : {article.fetch_method}")
        print(f"  Status        : {article.status_code}")
        print(f"  Extracted     : {article.extracted_headline}")
        print(f"  Published     : {article.published_at}")
        print(f"  Words         : {article.word_count}")
        print(f"  Valid         : {article.valid}")
        if article.errors:
            print(f"  Errors        : {article.errors}")
        if article.warnings:
            print(f"  Warnings      : {article.warnings}")


def main():
    print()
    print("=" * 78)
    print("SRI LANKAN FINANCIAL NEWS SOURCE DIAGNOSTIC")
    print("=" * 78)
    print()
    print("Testing:")
    for spec in SOURCE_SPECS:
        print(f"  - {spec.name}")
    results = []
    with HttpClient() as http_client:
        with BrowserClient(headless=True) as browser_client:
            for spec in SOURCE_SPECS:
                print()
                print(f"Testing {spec.name}...")
                result = diagnose_source(spec=spec, http_client=http_client, browser_client=browser_client, max_article_tests=3)
                results.append(result)
                print_source_result(result)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"source_diagnostic_{timestamp}.json"
    payload = {"generated_at": datetime.now().astimezone().isoformat(), "sources": [asdict(result) for result in results]}
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    print()
    print("=" * 78)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 78)
    for result in results:
        if result.section_fetch_success and result.successful_articles > 0:
            status = "PASS"
        elif result.section_fetch_success:
            status = "SECTION OK / ARTICLE EXTRACTION NEEDS WORK"
        else:
            status = "FAILED"
        print(f"{result.source_name:15} {status}")
    print()
    print(f"Diagnostic saved:\n{output_path}")


if __name__ == "__main__":
    main()