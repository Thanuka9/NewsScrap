from __future__ import annotations

"""
Build a blind targeted Stage-B augmentation review pool.

Goal
----
Select ~100 NEW historical articles that are useful for learning the
WATCH vs RELEVANT supervisory boundary.

This script DOES NOT:
- train a model,
- change any labels,
- read/use the Stage-B test split as labels,
- include any row already present in the frozen v3 reviewed dataset,
- assume that a candidate stratum is its final label.

It scans staged article JSON, excludes every source_url/story_group_id/headline
already used in the frozen v3 reviewed dataset, assigns only a hidden
candidate-boundary stratum, and writes:
  * BLIND review packet
  * KEY with hidden stratum / scoring evidence
  * manifest / QA summary

The human/AI reviewer must assign WATCH or RELEVANT blind to the KEY.
"""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_STAGED_ROOT = PROJECT_ROOT / "data" / "staged"

DEFAULT_EXCLUDE_DATASET = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v3_reviewed"
)

DEFAULT_OUTPUT_BASE = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "stage_b_targeted_augmentation"
)

TZ = ZoneInfo("Asia/Colombo")

# Target quotas. These are candidate strata, NOT target labels.
QUOTAS = {
    "INSURANCE_ADJACENT": 18,
    "GENERIC_GOVERNANCE_CYBER": 12,
    "MACRO_CBSL_STATISTICS": 12,
    "ROUTINE_MARKET_BONDS": 12,
    "AML_CFT_FIU": 12,
    "CBSL_ENFORCEMENT_RESOLUTION": 14,
    "BANK_LFC_PRUDENTIAL_CONDITION": 12,
    "BANK_LFC_FINANCIAL_RESULTS": 10,
}

WATCH_SIDE = {
    "INSURANCE_ADJACENT",
    "GENERIC_GOVERNANCE_CYBER",
    "MACRO_CBSL_STATISTICS",
    "ROUTINE_MARKET_BONDS",
}

RELEVANT_SIDE = {
    "AML_CFT_FIU",
    "CBSL_ENFORCEMENT_RESOLUTION",
    "BANK_LFC_PRUDENTIAL_CONDITION",
    "BANK_LFC_FINANCIAL_RESULTS",
}


def norm(value: Any) -> str:
    return str(value or "").strip()


def normalize_headline(value: str) -> str:
    text = norm(value).lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    return text.strip()


def sha1_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def any_match(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, flags=re.I) for p in patterns)


def matched_names(text: str, patterns: dict[str, str]) -> list[str]:
    out = []
    for name, pattern in patterns.items():
        if re.search(pattern, text, flags=re.I):
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# Flexible article extraction
# ---------------------------------------------------------------------------

HEADLINE_KEYS = ("headline", "title", "article_title")
BODY_KEYS = (
    "article_text",
    "body",
    "content",
    "text",
    "full_text",
    "article_body",
)
URL_KEYS = ("source_url", "url", "canonical_url", "link")
DATE_KEYS = (
    "published_at",
    "published_date",
    "publication_date",
    "date",
    "published",
)
SOURCE_KEYS = (
    "source_name",
    "source",
    "publication",
    "site",
    "publisher",
)
STORY_GROUP_KEYS = (
    "story_group_id",
    "story_id",
    "cluster_id",
)


def first_value(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, (str, int, float)):
            text = norm(value)
            if text:
                return text
    return ""


def flatten_article_candidates(obj: Any):
    """
    Recursively yield dicts that look article-like.
    Handles files containing a single article, lists, or nested 'articles' arrays.
    """
    if isinstance(obj, dict):
        headline = first_value(obj, HEADLINE_KEYS)
        body = first_value(obj, BODY_KEYS)
        url = first_value(obj, URL_KEYS)

        if headline and (body or url):
            yield obj

        for value in obj.values():
            if isinstance(value, (dict, list)):
                yield from flatten_article_candidates(value)

    elif isinstance(obj, list):
        for item in obj:
            yield from flatten_article_candidates(item)


def parse_json_file(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []

    out = []
    seen = set()

    for row in flatten_article_candidates(payload):
        headline = first_value(row, HEADLINE_KEYS)
        url = first_value(row, URL_KEYS)
        body = first_value(row, BODY_KEYS)

        fingerprint = (
            url
            or sha1_text(
                normalize_headline(headline)
                + "\n"
                + body[:500]
            )
        )

        if fingerprint in seen:
            continue

        seen.add(fingerprint)
        out.append(row)

    return out


def canonical_article(
    row: dict[str, Any],
    source_file: Path,
) -> dict[str, str]:
    headline = first_value(row, HEADLINE_KEYS)
    body = first_value(row, BODY_KEYS)
    url = first_value(row, URL_KEYS)
    published = first_value(row, DATE_KEYS)
    source = first_value(row, SOURCE_KEYS)
    story_group = first_value(row, STORY_GROUP_KEYS)

    return {
        "headline": headline,
        "article_text": body,
        "source_url": url,
        "published_at": published,
        "source_name": source,
        "story_group_id": story_group,
        "source_file": str(source_file),
        "normalized_headline": normalize_headline(headline),
    }


# ---------------------------------------------------------------------------
# Frozen-dataset exclusion
# ---------------------------------------------------------------------------

def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_exclusions(dataset_root: Path) -> dict[str, set[str]]:
    """
    Exclude ALL rows in the frozen v3 dataset, including the inherited test split.
    We do not use their labels; we only prevent them from entering augmentation.
    """
    urls: set[str] = set()
    groups: set[str] = set()
    headlines: set[str] = set()

    paths = []

    dataset_csv = dataset_root / "dataset.csv"
    if dataset_csv.is_file():
        paths.append(dataset_csv)
    else:
        for name in ("train.csv", "validation.csv", "test.csv"):
            path = dataset_root / name
            if path.is_file():
                paths.append(path)

    if not paths:
        raise FileNotFoundError(
            f"No dataset.csv or split CSVs found under {dataset_root}"
        )

    for path in paths:
        for row in read_csv(path):
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


# ---------------------------------------------------------------------------
# Candidate taxonomy / scoring
# ---------------------------------------------------------------------------

INSURANCE = {
    "insurance": r"\binsurance\b",
    "assurance": r"\bassurance\b",
    "insurer": r"\binsurer\b",
    "life_insurance": r"\blife insurance\b",
    "general_insurance": r"\bgeneral insurance\b",
    "sanasa_life": r"\bsanasa life\b",
}

AML = {
    "AML": r"\baml\b",
    "CFT": r"\bcft\b",
    "money_laundering": r"money laundering",
    "terrorist_financing": r"terror(?:ist|ism)\s+financing",
    "FIU": r"financial intelligence unit|\bfiu\b",
    "FATF": r"\bfatf\b",
    "grey_list": r"grey[- ]list",
    "beneficial_ownership": r"beneficial ownership",
    "proceeds_of_crime": r"proceeds of crime",
}

GOV_CYBER = {
    "cyber": r"\bcyber(?:security)?\b",
    "PDPA": r"\bpdpa\b",
    "data_protection": r"data protection|personal data protection",
    "director_liability": r"director(?:s)?['’]?\s+(?:personal\s+)?liability",
    "corporate_governance": r"corporate governance|board governance",
    "operational_resilience": r"operational resilience|business continuity",
    "data_breach": r"data breach",
}

CBSL = {
    "CBSL": r"\bcbsl\b",
    "central_bank": r"central bank of sri lanka|\bcentral bank\b",
    "monetary_board": r"\bmonetary board\b",
}

MACRO_STATS = {
    "PMI": r"\bpmi\b|purchasing managers",
    "tourism": r"tourism earnings|tourist arrivals",
    "remittances": r"worker remittances|workers['’]? remittances",
    "trade": r"trade deficit|trade balance|exports|imports",
    "external_sector": r"external sector|current account|balance of payments",
    "inflation": r"\binflation\b|\bccpi\b|\bncpi\b",
    "policy_rate": r"policy rate|standing deposit facility|standing lending facility",
    "GDP": r"\bgdp\b|economic growth",
}

MARKET_BONDS = {
    "treasury_bill": r"treasury bills?|\bt[- ]?bills?\b",
    "treasury_bond": r"treasury bonds?|\bt[- ]?bonds?\b",
    "bond_yield": r"bond yields?|yield curve",
    "auction": r"treasury auction|bond auction|bill auction",
    "money_market": r"money market|overnight liquidity|call money",
    "secondary_market": r"secondary market",
}

ENFORCEMENT = {
    "administrator": r"\badministrator\b",
    "liquidator": r"\bliquidator\b",
    "liquidation": r"\bliquidation\b",
    "resolution": r"\bresolution\b",
    "revocation": r"\brevok(?:e|ed|es|ing)\b",
    "suspension": r"\bsuspend(?:ed|s|ing)?\b",
    "warning": r"\bwarn(?:s|ed|ing)?\b",
    "pyramid": r"pyramid scheme",
    "spot_exam": r"spot examination|on[- ]site examination|inspection",
    "sanction": r"\bsanction(?:s|ed)?\b",
    "penalty": r"\bpenalt(?:y|ies)\b|fine imposed",
    "direction": r"\bdirection(?:s)?\b",
    "failed_institution": r"failed bank|failed finance",
}

PRUDENTIAL = {
    "capital_adequacy": r"capital adequacy",
    "tier_capital": r"tier\s*[12]",
    "liquidity": r"\bliquidity\b|liquid assets",
    "asset_quality": r"asset quality",
    "NPL": r"non[- ]performing|\bnpl\b|stage\s*3",
    "provision": r"\bprovision(?:s|ing)?\b",
    "solvency": r"\bsolvency\b",
    "minimum_capital": r"minimum capital",
    "governance": r"corporate governance|board governance",
    "related_party": r"related[- ]party",
    "large_exposure": r"large exposure",
    "single_borrower": r"single borrower",
}

FIN_RESULTS = {
    "profit": r"\bprofit\b|profitability|profit after tax|profit before tax",
    "assets": r"total assets|\bassets\b",
    "deposits": r"\bdeposits\b|deposit base",
    "loans": r"loan book|gross loans|total loans|advances",
    "NPL": r"non[- ]performing|\bnpl\b|stage\s*3",
    "CAR": r"capital adequacy|\bcar\b",
    "ROE": r"\broe\b|return on equity",
    "results": r"financial results|annual report|quarterly results|interim results",
}

BANK_LFC_HEADLINE = [
    r"\bbank\b",
    r"\bbanking\b",
    r"\bfinance plc\b",
    r"\bfinance company\b",
    r"\blicensed finance\b",
    r"\bmicrofinance\b",
    r"\bleasing\b",
]

PR_WORDS = [
    r"\baward\b",
    r"\bawarded\b",
    r"\bwins?\b",
    r"\blaunch(?:es|ed)?\b",
    r"\bpartnership\b",
    r"\bcsr\b",
    r"\bsponsorship\b",
    r"\bcelebrates?\b",
]


def classify_candidates(article: dict[str, str]) -> list[dict[str, Any]]:
    """
    Return zero or more candidate-stratum scores.
    Strata are selection aids only; no target label is assigned.
    """
    headline = article["headline"]
    body = article["article_text"]
    lead = body[:1800]
    blob = f"{headline} {lead}"
    headline_blob = headline

    insurance_terms = matched_names(blob, INSURANCE)
    aml_terms = matched_names(blob, AML)
    gov_terms = matched_names(blob, GOV_CYBER)
    cbsl_terms = matched_names(blob, CBSL)
    macro_terms = matched_names(blob, MACRO_STATS)
    market_terms = matched_names(blob, MARKET_BONDS)
    enforcement_terms = matched_names(blob, ENFORCEMENT)
    prudential_terms = matched_names(blob, PRUDENTIAL)
    results_terms = matched_names(blob, FIN_RESULTS)

    bank_lfc_headline = any_match(headline_blob, BANK_LFC_HEADLINE)
    cbsl_present = bool(cbsl_terms)
    pr_like = any_match(headline_blob, PR_WORDS)

    candidates: list[dict[str, Any]] = []

    def add(category: str, score: float, terms: list[str], rationale: str):
        candidates.append(
            {
                "candidate_boundary_type": category,
                "selection_score": round(float(score), 4),
                "matched_terms": ";".join(sorted(set(terms))),
                "selection_rationale": rationale,
            }
        )

    # WATCH-side boundary candidates.
    if insurance_terms and not aml_terms:
        score = 40 + 4 * len(insurance_terms)
        if "rating" in blob.lower() or "capital" in blob.lower():
            score += 8
        if bank_lfc_headline:
            score -= 12
        add(
            "INSURANCE_ADJACENT",
            score,
            insurance_terms,
            "Insurance-sector article useful for testing adjacent-vs-direct supervisory scope.",
        )

    if gov_terms and not aml_terms and not enforcement_terms:
        score = 38 + 5 * len(gov_terms)
        if not bank_lfc_headline and not cbsl_present:
            score += 10
        add(
            "GENERIC_GOVERNANCE_CYBER",
            score,
            gov_terms,
            "Governance/cyber/PDPA article useful for testing generic-vs-direct supervisory relevance.",
        )

    if cbsl_present and macro_terms and not enforcement_terms and not prudential_terms:
        score = 36 + 4 * len(macro_terms)
        add(
            "MACRO_CBSL_STATISTICS",
            score,
            cbsl_terms + macro_terms,
            "CBSL/macro-statistics article useful for separating policy context from direct supervision.",
        )

    if market_terms and not enforcement_terms and not prudential_terms:
        score = 34 + 4 * len(market_terms)
        if cbsl_present:
            score += 4
        add(
            "ROUTINE_MARKET_BONDS",
            score,
            market_terms + cbsl_terms,
            "Routine bond/money-market article useful for testing WATCH market-transmission boundary.",
        )

    # RELEVANT-side boundary candidates.
    if aml_terms:
        score = 55 + 6 * len(aml_terms)
        if cbsl_present:
            score += 8
        add(
            "AML_CFT_FIU",
            score,
            aml_terms + cbsl_terms,
            "AML/CFT/FIU/FATF article useful for learning systemic financial-crime supervisory relevance.",
        )

    if enforcement_terms and (cbsl_present or bank_lfc_headline):
        score = 58 + 6 * len(enforcement_terms)
        if cbsl_present:
            score += 8
        add(
            "CBSL_ENFORCEMENT_RESOLUTION",
            score,
            enforcement_terms + cbsl_terms,
            "Direct enforcement/resolution/intervention candidate.",
        )

    if bank_lfc_headline and prudential_terms:
        score = 52 + 5 * len(prudential_terms)
        if cbsl_present:
            score += 5
        add(
            "BANK_LFC_PRUDENTIAL_CONDITION",
            score,
            prudential_terms + cbsl_terms,
            "Bank/LFC article with direct prudential-condition signals.",
        )

    if bank_lfc_headline and results_terms:
        score = 44 + 4 * len(results_terms)
        if pr_like:
            score -= 6
        add(
            "BANK_LFC_FINANCIAL_RESULTS",
            score,
            results_terms,
            "Bank/LFC financial-condition/results candidate for direct institution-level relevance.",
        )

    return candidates


# ---------------------------------------------------------------------------
# Diverse selection
# ---------------------------------------------------------------------------

def source_key(article: dict[str, str]) -> str:
    source = norm(article.get("source_name"))
    if source:
        return source.lower()

    url = norm(article.get("source_url"))
    match = re.search(r"https?://([^/]+)", url)
    if match:
        return match.group(1).lower()

    return "unknown"


def date_key(article: dict[str, str]) -> str:
    published = norm(article.get("published_at"))
    return published[:10] if len(published) >= 10 else published


def greedy_diverse_select(
    rows: list[dict[str, Any]],
    quota: int,
) -> list[dict[str, Any]]:
    """
    Prefer high-scoring candidates but avoid domination by one source/date.
    """
    if not rows or quota <= 0:
        return []

    ranked = sorted(
        rows,
        key=lambda r: (
            -float(r["selection_score"]),
            date_key(r),
            normalize_headline(r["headline"]),
        ),
    )

    selected: list[dict[str, Any]] = []
    source_counts = Counter()
    date_counts = Counter()

    # Soft caps; relaxed automatically if needed.
    source_cap = max(2, math.ceil(quota * 0.45))
    date_cap = 2

    for row in ranked:
        if len(selected) >= quota:
            break

        s = source_key(row)
        d = date_key(row)

        if source_counts[s] >= source_cap:
            continue
        if d and date_counts[d] >= date_cap:
            continue

        selected.append(row)
        source_counts[s] += 1
        if d:
            date_counts[d] += 1

    if len(selected) < quota:
        chosen_ids = {r["_candidate_id"] for r in selected}

        for row in ranked:
            if len(selected) >= quota:
                break
            if row["_candidate_id"] in chosen_ids:
                continue

            selected.append(row)
            chosen_ids.add(row["_candidate_id"])

    return selected


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a blind targeted Stage-B augmentation pool "
            "from staged historical articles."
        )
    )
    parser.add_argument(
        "--staged-root",
        type=Path,
        default=DEFAULT_STAGED_ROOT,
    )
    parser.add_argument(
        "--exclude-dataset",
        type=Path,
        default=DEFAULT_EXCLUDE_DATASET,
    )
    parser.add_argument(
        "--output-base",
        type=Path,
        default=DEFAULT_OUTPUT_BASE,
    )
    args = parser.parse_args()

    staged_root = args.staged_root
    exclude_dataset = args.exclude_dataset
    output_base = args.output_base

    if not staged_root.is_absolute():
        staged_root = PROJECT_ROOT / staged_root
    if not exclude_dataset.is_absolute():
        exclude_dataset = PROJECT_ROOT / exclude_dataset
    if not output_base.is_absolute():
        output_base = PROJECT_ROOT / output_base

    if not staged_root.is_dir():
        raise FileNotFoundError(
            f"Staged article root not found: {staged_root}"
        )

    exclusions = load_exclusions(exclude_dataset)

    json_files = sorted(staged_root.rglob("*.json"))

    scan_stats = Counter()
    canonical_rows: list[dict[str, str]] = []

    seen_urls: set[str] = set()
    seen_headlines: set[str] = set()

    for path in json_files:
        scan_stats["json_files_scanned"] += 1

        rows = parse_json_file(path)
        scan_stats["article_dicts_found"] += len(rows)

        for raw in rows:
            article = canonical_article(raw, path)

            headline = article["normalized_headline"]
            url = article["source_url"]
            group = article["story_group_id"]

            if not headline:
                scan_stats["excluded_missing_headline"] += 1
                continue

            if len(article["article_text"]) < 100:
                scan_stats["excluded_short_body"] += 1
                continue

            if (
                url
                and url in exclusions["source_urls"]
            ):
                scan_stats["excluded_frozen_dataset_url"] += 1
                continue

            if (
                group
                and group in exclusions["story_group_ids"]
            ):
                scan_stats["excluded_frozen_dataset_group"] += 1
                continue

            if headline in exclusions["normalized_headlines"]:
                scan_stats["excluded_frozen_dataset_headline"] += 1
                continue

            # Global deduplication.
            if url and url in seen_urls:
                scan_stats["excluded_duplicate_url"] += 1
                continue

            if headline in seen_headlines:
                scan_stats["excluded_duplicate_headline"] += 1
                continue

            if url:
                seen_urls.add(url)
            seen_headlines.add(headline)

            canonical_rows.append(article)

    # Build category-specific candidate rows.
    candidate_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for article in canonical_rows:
        candidate_specs = classify_candidates(article)

        for spec in candidate_specs:
            row = dict(article)
            row.update(spec)

            row["_candidate_id"] = sha1_text(
                row["candidate_boundary_type"]
                + "\n"
                + (row["source_url"] or row["normalized_headline"])
            )

            candidate_by_type[row["candidate_boundary_type"]].append(row)

    selected: list[dict[str, Any]] = []
    selected_article_keys: set[str] = set()
    selection_counts = Counter()
    shortage = {}

    # Primary pass: honor quotas while preventing the same article from
    # appearing twice in the packet.
    for category, quota in QUOTAS.items():
        available = candidate_by_type.get(category, [])

        filtered = [
            row
            for row in available
            if (
                row["source_url"]
                or row["normalized_headline"]
            )
            not in selected_article_keys
        ]

        picked = greedy_diverse_select(
            filtered,
            quota,
        )

        for row in picked:
            article_key = (
                row["source_url"]
                or row["normalized_headline"]
            )

            if article_key in selected_article_keys:
                continue

            selected_article_keys.add(article_key)
            selected.append(row)
            selection_counts[category] += 1

        if selection_counts[category] < quota:
            shortage[category] = {
                "quota": quota,
                "selected": selection_counts[category],
                "available_before_cross_category_dedupe": len(available),
            }

    # Stable blind-review order: interleave categories so reviewer does not
    # see long blocks of the same hidden boundary type.
    by_category = defaultdict(list)
    for row in selected:
        by_category[row["candidate_boundary_type"]].append(row)

    for rows in by_category.values():
        rows.sort(
            key=lambda r: (
                date_key(r),
                normalize_headline(r["headline"]),
            )
        )

    interleaved: list[dict[str, Any]] = []
    categories = list(QUOTAS)

    position = 0
    while True:
        added = False
        for category in categories:
            rows = by_category.get(category, [])
            if position < len(rows):
                interleaved.append(rows[position])
                added = True
        if not added:
            break
        position += 1

    timestamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    output_root = output_base / timestamp
    output_root.mkdir(parents=True, exist_ok=False)

    blind_rows = []
    key_rows = []

    for index, row in enumerate(interleaved, start=1):
        review_id = f"SB_AUG_{index:03d}"

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
                "watch_side_or_relevant_side": (
                    "WATCH_SIDE"
                    if row["candidate_boundary_type"] in WATCH_SIDE
                    else "RELEVANT_SIDE"
                ),
                "source_name": row["source_name"],
                "published_at": row["published_at"],
                "source_url": row["source_url"],
                "story_group_id": row["story_group_id"],
                "headline": row["headline"],
                "source_file": row["source_file"],
            }
        )

    blind_path = output_root / "stage_b_targeted_augmentation_BLIND.csv"
    key_path = output_root / "stage_b_targeted_augmentation_KEY.csv"

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
            "watch_side_or_relevant_side",
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
        "created_at": datetime.now(TZ).isoformat(),
        "purpose": (
            "Blind targeted Stage-B boundary augmentation candidate pool. "
            "Candidate strata are selection aids only and are not labels."
        ),
        "staged_root": str(staged_root),
        "exclude_dataset": str(exclude_dataset),
        "frozen_dataset_exclusion_counts": {
            "source_urls": len(exclusions["source_urls"]),
            "story_group_ids": len(exclusions["story_group_ids"]),
            "normalized_headlines": len(exclusions["normalized_headlines"]),
        },
        "test_policy": (
            "Frozen v3 test examples are excluded from augmentation using "
            "URL/story-group/headline identity. Their labels are not used."
        ),
        "test_rows_used_as_training_labels": 0,
        "scan_stats": dict(scan_stats),
        "canonical_new_articles_after_exclusions": len(canonical_rows),
        "quotas": QUOTAS,
        "candidate_counts_before_selection": candidate_counts,
        "selected_counts": dict(selection_counts),
        "selected_total": len(interleaved),
        "shortages": shortage,
        "blind_file": str(blind_path),
        "key_file": str(key_path),
        "review_labels_allowed": ["WATCH", "RELEVANT"],
        "instructions": [
            "Review the BLIND file without opening the KEY.",
            "Assign WATCH or RELEVANT using the agreed supervisory scope.",
            "Do not infer labels from the candidate stratum because the reviewer cannot see it.",
            "After adjudication, compare to KEY only for coverage/selection diagnostics.",
            "Do not add reviewed rows to training until provenance and split assignment are frozen.",
        ],
    }

    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    print("=" * 100)
    print("STAGE-B TARGETED AUGMENTATION POOL COMPLETE")
    print("=" * 100)
    print(f"JSON files scanned                    : {scan_stats['json_files_scanned']}")
    print(f"Article-like records found            : {scan_stats['article_dicts_found']}")
    print(f"New canonical articles after exclusions: {len(canonical_rows)}")
    print("Frozen v3 dataset examples are excluded from candidate selection.")
    print("Test labels used                      : 0")
    print()
    print("CANDIDATE / SELECTION COUNTS")
    for category, quota in QUOTAS.items():
        print(
            f"  {category:<34} "
            f"available={candidate_counts[category]:<4} "
            f"selected={selection_counts[category]:<3} "
            f"quota={quota}"
        )
    print()
    print(f"Selected total : {len(interleaved)}")
    if shortage:
        print("SHORTAGES:")
        for category, detail in shortage.items():
            print(f"  {category}: {detail}")
    else:
        print("All target quotas filled.")
    print()
    print(f"BLIND   : {blind_path}")
    print(f"KEY     : {key_path}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
