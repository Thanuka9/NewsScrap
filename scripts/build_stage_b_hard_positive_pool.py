from __future__ import annotations

"""
Build a second blind Stage-B candidate pool focused on the hard positive concepts
that the fresh boundary diagnostic exposed.

This script:
- scans staged historical articles,
- excludes the frozen v3 reviewed dataset,
- excludes all 102 articles from the first targeted-augmentation run,
- does not read model predictions or test labels,
- selects NEW candidate articles only,
- writes BLIND + KEY packets for adjudication.

The hidden candidate category is NOT a target label.
"""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_stage_b_targeted_augmentation_pool import (
    canonical_article,
    date_key,
    greedy_diverse_select,
    load_exclusions,
    normalize_headline,
    norm,
    parse_json_file,
    sha1_text,
    source_key,
    write_csv,
)

TZ = ZoneInfo("Asia/Colombo")

DEFAULT_STAGED_ROOT = PROJECT_ROOT / "data" / "staged"

DEFAULT_EXCLUDE_DATASET = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v3_reviewed"
)

DEFAULT_PRIOR_KEY = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "stage_b_targeted_augmentation"
    / "20261009_133941"
    / "stage_b_targeted_augmentation_KEY.csv"
)

DEFAULT_OUTPUT_BASE = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "stage_b_hard_positive_pool"
)

QUOTAS = {
    "BANK_LFC_FINANCIAL_RESULTS": 20,
    "BANK_LFC_PRUDENTIAL_CAPITAL": 20,
    "AML_CFT_FIU_DIRECT": 15,
    "BANKING_SECTOR_SYSTEMIC_RISK": 15,
    "INSURANCE_RESULTS_CAPITAL": 8,
    "MACRO_CBSL_CONTEXT": 6,
    "GENERIC_GOVERNANCE_CYBER": 6,
}

POSITIVE_SIDE = {
    "BANK_LFC_FINANCIAL_RESULTS",
    "BANK_LFC_PRUDENTIAL_CAPITAL",
    "AML_CFT_FIU_DIRECT",
    "BANKING_SECTOR_SYSTEMIC_RISK",
}

NEGATIVE_SIDE = {
    "INSURANCE_RESULTS_CAPITAL",
    "MACRO_CBSL_CONTEXT",
    "GENERIC_GOVERNANCE_CYBER",
}

BANK_LFC_HEADLINE = [
    r"\bbank\b",
    r"\bbanking\b",
    r"\bfinance plc\b",
    r"\bfinance company\b",
    r"\blicensed finance\b",
    r"\bleasing\b",
    r"\bmicrofinance\b",
]

CBSL = [
    r"\bcbsl\b",
    r"central bank of sri lanka",
    r"\bcentral bank\b",
    r"\bmonetary board\b",
]

FIN_RESULTS = {
    "profit": r"\bprofit\b|profitability|profit after tax|profit before tax|\bPAT\b|\bPBT\b",
    "assets": r"total assets|\bassets\b",
    "deposits": r"\bdeposits\b|deposit base",
    "loans": r"loan book|gross loans|total loans|advances|credit growth",
    "results": r"financial results|annual report|quarterly results|interim results",
    "ROE": r"\bROE\b|return on equity",
}

PRUDENTIAL = {
    "capital_adequacy": r"capital adequacy|\bCAR\b",
    "tier_capital": r"tier\s*[12]",
    "subordinated": r"subordinated debt|subordinated debenture",
    "liquidity": r"\bliquidity\b|liquid assets",
    "asset_quality": r"asset quality",
    "NPL": r"non[- ]performing|\bNPL\b|stage\s*3|impaired loans",
    "provisions": r"\bprovision(?:s|ing)?\b",
    "minimum_capital": r"minimum capital",
    "solvency": r"\bsolvency\b",
    "large_exposure": r"large exposure",
    "single_borrower": r"single borrower",
    "governance": r"corporate governance|board governance",
}

AML = {
    "AML": r"\bAML\b",
    "CFT": r"\bCFT\b",
    "FIU": r"\bFIU\b|financial intelligence unit",
    "FATF": r"\bFATF\b",
    "money_laundering": r"money laundering",
    "terrorist_financing": r"terror(?:ist|ism)\s+financing",
    "beneficial_ownership": r"beneficial ownership",
    "proceeds_of_crime": r"proceeds of crime",
    "grey_list": r"grey[- ]list",
}

SYSTEMIC = {
    "banking_sector": r"banking sector|banking industry|licensed commercial banks|licensed specialised banks",
    "credit_growth": r"credit growth|credit boom|private sector credit",
    "systemic_risk": r"systemic risk|financial stability|macroprudential",
    "sector_asset_quality": r"sector.*asset quality|banking.*asset quality",
    "sector_capital": r"banking.*capital|sector.*capital adequacy",
    "sector_liquidity": r"banking.*liquidity|sector.*liquidity",
}

INSURANCE = {
    "insurance": r"\binsurance\b|\bassurance\b|\binsurer\b",
    "capital": r"capital|tier\s*[12]|solvency",
    "results": r"profit|financial results|assets|premium|claims",
    "rating": r"rating|investment[- ]grade",
}

MACRO = {
    "PMI": r"\bPMI\b|purchasing managers",
    "inflation": r"\binflation\b|\bCCPI\b|\bNCPI\b",
    "external": r"external sector|current account|balance of payments|official reserves",
    "tourism": r"tourism earnings|tourist arrivals",
    "remittances": r"worker remittances|workers['’]? remittances",
    "policy_rate": r"policy rate|standing deposit facility|standing lending facility",
    "trade": r"trade deficit|trade balance|\bexports\b|\bimports\b",
}

GOV_CYBER = {
    "cyber": r"\bcyber(?:security)?\b",
    "PDPA": r"\bPDPA\b",
    "data_protection": r"data protection|personal data protection",
    "director_liability": r"director(?:s)?['’]?\s+(?:personal\s+)?liability",
    "corporate_governance": r"corporate governance",
    "operational_resilience": r"operational resilience|business continuity",
}

NOISE_PR = [
    r"\baward\b",
    r"\bawarded\b",
    r"\bcelebrates?\b",
    r"\banniversary\b",
    r"\bbranch opening\b",
    r"\blaunch(?:es|ed)?\b",
    r"\bsponsorship\b",
    r"\bCSR\b",
]


def any_match(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def matched(text: str, patterns: dict[str, str]) -> list[str]:
    return [
        name
        for name, pattern in patterns.items()
        if re.search(pattern, text, flags=re.I)
    ]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_prior_exclusions(path: Path) -> dict[str, set[str]]:
    if not path.is_file():
        raise FileNotFoundError(path)

    urls = set()
    groups = set()
    headlines = set()

    for row in read_rows(path):
        url = norm(row.get("source_url"))
        group = norm(row.get("story_group_id"))
        headline = normalize_headline(norm(row.get("headline")))

        if url:
            urls.add(url)
        if group:
            groups.add(group)
        if headline:
            headlines.add(headline)

    return {
        "source_urls": urls,
        "story_group_ids": groups,
        "normalized_headlines": headlines,
    }


def excluded(article: dict[str, str], *exclusions: dict[str, set[str]]) -> bool:
    url = norm(article.get("source_url"))
    group = norm(article.get("story_group_id"))
    headline = normalize_headline(article.get("headline", ""))

    for ex in exclusions:
        if url and url in ex["source_urls"]:
            return True
        if group and group in ex["story_group_ids"]:
            return True
        if headline and headline in ex["normalized_headlines"]:
            return True

    return False


def classify(article: dict[str, str]) -> list[dict[str, Any]]:
    headline = article["headline"]
    body = article["article_text"]
    lead = body[:2200]
    blob = f"{headline} {lead}"

    bank_lfc = any_match(headline, BANK_LFC_HEADLINE)
    cbsl = any_match(blob, CBSL)
    pr_like = any_match(headline, NOISE_PR)

    results_terms = matched(blob, FIN_RESULTS)
    prudential_terms = matched(blob, PRUDENTIAL)
    aml_terms = matched(blob, AML)
    systemic_terms = matched(blob, SYSTEMIC)
    insurance_terms = matched(blob, INSURANCE)
    macro_terms = matched(blob, MACRO)
    gov_terms = matched(blob, GOV_CYBER)

    out = []

    def add(category: str, score: float, terms: list[str], rationale: str):
        out.append(
            {
                "candidate_boundary_type": category,
                "selection_score": round(float(score), 4),
                "matched_terms": ";".join(sorted(set(terms))),
                "selection_rationale": rationale,
            }
        )

    if bank_lfc and results_terms:
        score = 55 + 5 * len(results_terms)
        if prudential_terms:
            score += 8
        if pr_like:
            score -= 10
        add(
            "BANK_LFC_FINANCIAL_RESULTS",
            score,
            results_terms + prudential_terms,
            "Direct bank/LFC financial-condition/results hard-positive candidate.",
        )

    if bank_lfc and prudential_terms:
        score = 58 + 6 * len(prudential_terms)
        if cbsl:
            score += 5
        add(
            "BANK_LFC_PRUDENTIAL_CAPITAL",
            score,
            prudential_terms,
            "Direct bank/LFC prudential, capital, asset-quality or liquidity hard-positive candidate.",
        )

    if aml_terms:
        score = 60 + 6 * len(aml_terms)
        if cbsl:
            score += 8
        if re.search(r"\bseminar\b|\bworkshop\b|\bawareness\b", headline, flags=re.I):
            score -= 8
        add(
            "AML_CFT_FIU_DIRECT",
            score,
            aml_terms,
            "AML/CFT/FIU candidate focused on the direct supervisory-policy boundary.",
        )

    if systemic_terms:
        score = 56 + 6 * len(systemic_terms)
        if cbsl:
            score += 6
        if prudential_terms:
            score += 6
        add(
            "BANKING_SECTOR_SYSTEMIC_RISK",
            score,
            systemic_terms + prudential_terms,
            "Banking-sector systemic/prudential-risk hard-positive candidate.",
        )

    # Paired hard negatives remain in the blind pool so the next dataset
    # does not become a one-sided positive-only sample.
    if insurance_terms and not bank_lfc and not aml_terms:
        score = 42 + 4 * len(insurance_terms)
        if pr_like:
            score -= 8
        add(
            "INSURANCE_RESULTS_CAPITAL",
            score,
            insurance_terms,
            "Insurance financial-condition/capital boundary candidate.",
        )

    if cbsl and macro_terms and not prudential_terms and not aml_terms:
        score = 40 + 4 * len(macro_terms)
        add(
            "MACRO_CBSL_CONTEXT",
            score,
            macro_terms,
            "CBSL macro/statistical context hard-negative candidate.",
        )

    if gov_terms and not bank_lfc and not aml_terms:
        score = 40 + 5 * len(gov_terms)
        add(
            "GENERIC_GOVERNANCE_CYBER",
            score,
            gov_terms,
            "Generic governance/cyber boundary hard-negative candidate.",
        )

    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build second blind Stage-B hard-positive candidate pool."
    )
    parser.add_argument("--staged-root", type=Path, default=DEFAULT_STAGED_ROOT)
    parser.add_argument("--exclude-dataset", type=Path, default=DEFAULT_EXCLUDE_DATASET)
    parser.add_argument("--prior-key", type=Path, default=DEFAULT_PRIOR_KEY)
    parser.add_argument("--output-base", type=Path, default=DEFAULT_OUTPUT_BASE)
    args = parser.parse_args()

    staged_root = args.staged_root if args.staged_root.is_absolute() else PROJECT_ROOT / args.staged_root
    exclude_dataset = args.exclude_dataset if args.exclude_dataset.is_absolute() else PROJECT_ROOT / args.exclude_dataset
    prior_key = args.prior_key if args.prior_key.is_absolute() else PROJECT_ROOT / args.prior_key
    output_base = args.output_base if args.output_base.is_absolute() else PROJECT_ROOT / args.output_base

    frozen = load_exclusions(exclude_dataset)
    prior = load_prior_exclusions(prior_key)

    candidate_by_type = defaultdict(list)
    seen_url = set()
    seen_headline = set()
    scan = Counter()

    for path in sorted(staged_root.rglob("*.json")):
        scan["json_files"] += 1

        for raw in parse_json_file(path):
            scan["article_records"] += 1
            article = canonical_article(raw, path)

            if not article["headline"] or len(article["article_text"]) < 100:
                continue

            if excluded(article, frozen, prior):
                scan["excluded_existing"] += 1
                continue

            url = norm(article["source_url"])
            headline_norm = normalize_headline(article["headline"])

            if url and url in seen_url:
                continue
            if headline_norm in seen_headline:
                continue

            if url:
                seen_url.add(url)
            seen_headline.add(headline_norm)

            scan["new_unique_articles"] += 1

            for spec in classify(article):
                row = dict(article)
                row.update(spec)
                row["_candidate_id"] = sha1_text(
                    row["candidate_boundary_type"]
                    + "\n"
                    + (row["source_url"] or row["normalized_headline"])
                )
                candidate_by_type[row["candidate_boundary_type"]].append(row)

    selected = []
    selected_article_keys = set()
    selected_counts = Counter()

    for category, quota in QUOTAS.items():
        available = [
            row
            for row in candidate_by_type.get(category, [])
            if (row["source_url"] or row["normalized_headline"])
            not in selected_article_keys
        ]

        picked = greedy_diverse_select(available, quota)

        for row in picked:
            key = row["source_url"] or row["normalized_headline"]
            if key in selected_article_keys:
                continue
            selected_article_keys.add(key)
            selected.append(row)
            selected_counts[category] += 1

    # Interleave categories to keep the BLIND file genuinely blind.
    grouped = defaultdict(list)
    for row in selected:
        grouped[row["candidate_boundary_type"]].append(row)

    for rows in grouped.values():
        rows.sort(
            key=lambda r: (
                date_key(r),
                normalize_headline(r["headline"]),
            )
        )

    interleaved = []
    categories = list(QUOTAS)
    index = 0

    while True:
        added = False
        for category in categories:
            rows = grouped.get(category, [])
            if index < len(rows):
                interleaved.append(rows[index])
                added = True
        if not added:
            break
        index += 1

    timestamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    output_root = output_base / timestamp
    output_root.mkdir(parents=True, exist_ok=False)

    blind_rows = []
    key_rows = []

    for i, row in enumerate(interleaved, start=1):
        review_id = f"SB_HP_{i:03d}"

        blind_rows.append(
            {
                "review_id": review_id,
                "source_name": row["source_name"],
                "headline": row["headline"],
                "article_text": row["article_text"],
                "published_at": row["published_at"],
                "source_url": row["source_url"],
                "reviewer": "",
                "review_label": "",
                "review_confidence": "",
                "review_notes": "",
            }
        )

        key_rows.append(
            {
                "review_id": review_id,
                "candidate_boundary_type": row["candidate_boundary_type"],
                "selection_score": row["selection_score"],
                "matched_terms": row["matched_terms"],
                "selection_rationale": row["selection_rationale"],
                "hidden_candidate_side": (
                    "POSITIVE_SIDE"
                    if row["candidate_boundary_type"] in POSITIVE_SIDE
                    else "NEGATIVE_SIDE"
                ),
                "source_name": row["source_name"],
                "published_at": row["published_at"],
                "source_url": row["source_url"],
                "story_group_id": row["story_group_id"],
                "headline": row["headline"],
                "source_file": row["source_file"],
            }
        )

    blind_path = output_root / "stage_b_hard_positive_BLIND.csv"
    key_path = output_root / "stage_b_hard_positive_KEY.csv"

    write_csv(
        blind_path,
        blind_rows,
        [
            "review_id",
            "source_name",
            "headline",
            "article_text",
            "published_at",
            "source_url",
            "reviewer",
            "review_label",
            "review_confidence",
            "review_notes",
        ],
    )

    write_csv(
        key_path,
        key_rows,
        [
            "review_id",
            "candidate_boundary_type",
            "selection_score",
            "matched_terms",
            "selection_rationale",
            "hidden_candidate_side",
            "source_name",
            "published_at",
            "source_url",
            "story_group_id",
            "headline",
            "source_file",
        ],
    )

    candidate_counts = {
        category: len(candidate_by_type.get(category, []))
        for category in QUOTAS
    }

    manifest = {
        "status": "PASS",
        "purpose": "Second blind Stage-B pool focused on hard-positive concepts.",
        "created_at": datetime.now(TZ).isoformat(),
        "previous_102_articles_excluded": True,
        "frozen_v3_examples_excluded": True,
        "model_predictions_read": 0,
        "test_labels_read": 0,
        "quotas": QUOTAS,
        "candidate_counts": candidate_counts,
        "selected_counts": dict(selected_counts),
        "selected_total": len(interleaved),
        "instructions": [
            "Review BLIND without opening KEY.",
            "Allowed decisions: WATCH, RELEVANT, EXCLUDE_STAGE_B.",
            "After adjudication, reserve a NEW untouched evaluation set before retraining.",
            "Do not reuse the previously inspected 16-row boundary set as fresh promotion evidence.",
        ],
    }

    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    print("=" * 100)
    print("STAGE-B HARD-POSITIVE POOL COMPLETE")
    print("=" * 100)
    print(f"JSON files scanned        : {scan['json_files']}")
    print(f"Article records scanned   : {scan['article_records']}")
    print(f"Existing articles excluded: {scan['excluded_existing']}")
    print(f"New unique articles       : {scan['new_unique_articles']}")
    print("Model predictions read    : 0")
    print("Test labels read          : 0")
    print()
    print("CANDIDATE / SELECTION COUNTS")
    for category, quota in QUOTAS.items():
        print(
            f"  {category:<34} "
            f"available={candidate_counts[category]:<4} "
            f"selected={selected_counts[category]:<3} "
            f"quota={quota}"
        )
    print()
    print(f"Selected total: {len(interleaved)}")
    print(f"BLIND   : {blind_path}")
    print(f"KEY     : {key_path}")
    print(f"Manifest: {output_root / 'manifest.json'}")


if __name__ == "__main__":
    main()
