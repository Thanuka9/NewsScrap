from __future__ import annotations

import csv
import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STAGED_ROOT = PROJECT_ROOT / "data" / "staged"
GOLD_ROOT = PROJECT_ROOT / "data" / "training" / "human_gold"
GOLD_FILE = GOLD_ROOT / "stakeholder_category_gold.csv"

SCHEMA_VERSION = "stakeholder-review-v1"

SECTIONS = (
    "KEY_BANKING_SUPERVISORY",
    "BANKING_PAYMENTS_INFRASTRUCTURE",
    "MONETARY_MARKETS_EXTERNAL",
    "MACRO_SYSTEMIC_CONTEXT",
    "OTHER_FINANCIAL_SECTOR",
    "EXCLUDE",
)

CATEGORIES = (
    "FINANCIAL_STABILITY_MACROPRUDENTIAL",
    "CAPITAL_LIQUIDITY",
    "CREDIT_ASSET_QUALITY",
    "SOVEREIGN_CONCENTRATION_RISK",
    "AML_CFT",
    "GOVERNANCE_CONDUCT",
    "OPERATIONAL_CYBER_FRAUD",
    "PAYMENTS_FINANCIAL_INFRASTRUCTURE",
    "FINANCIAL_INCLUSION_CONSUMER",
    "CLIMATE_FINANCIAL_RISK",
    "MONETARY_POLICY_LIQUIDITY",
    "SOVEREIGN_DEBT_RATES",
    "FX_RESERVES_EXTERNAL_SECTOR",
    "CAPITAL_MARKETS",
    "MACRO_GROWTH_INFLATION_FISCAL",
    "OTHER_FINANCIAL_SECTOR",
    "NOT_FINANCIAL",
)

LEGAL_POLITICAL = (
    r"\bremand(?:ed)?\b", r"\bcourt\b", r"\bbail\b", r"\btrial-at-bar\b",
    r"\bprison\b", r"\barrest(?:ed)?\b", r"\bindictment\b",
    r"\bformer first lady\b", r"\bopposition leader\b", r"\bpolitics\b",
    r"\bsjb\b", r"\bjvp\b", r"\bslpp\b", r"\bmurder\b",
)

PR_NOISE = (
    "celebrates", "anniversary", "award", "awards", "customer service week",
    "management trainee", "official accommodation partner", "campaign",
    "appointed", "appointment", "graduation", "merchant ecosystem",
)

def norm(value: object) -> str:
    return " ".join(str(value or "").lower().replace("’", "'").split())

def contains(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)

def staged_index() -> dict[str, dict]:
    result: dict[str, dict] = {}
    if not STAGED_ROOT.exists():
        return result
    for path in STAGED_ROOT.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        for key in {payload.get("source_url"), payload.get("canonical_url")}:
            key = str(key or "").strip()
            if key:
                result[key] = payload
    return result

def gold_index() -> dict[str, dict]:
    if not GOLD_FILE.exists():
        return {}
    with GOLD_FILE.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return {
        str(row.get("source_url") or "").strip(): row
        for row in rows
        if str(row.get("source_url") or "").strip()
    }

def category_from_text(headline: str, article_text: str, source_url: str) -> tuple[str, str, str]:
    text = norm(f"{headline} {article_text}")
    url = norm(source_url)

    if any(re.search(p, text, flags=re.I) for p in LEGAL_POLITICAL):
        if not contains(text, ("central bank", "cbsl", "fatf", "financial intelligence unit", "banking act")):
            return "EXCLUDE", "NOT_FINANCIAL", "LEGAL_POLITICAL_NO_DIRECT_FINANCIAL_IMPACT"

    if any(x in url for x in ("/sports/", "/politics/", "/lawnorder/", "/law-order/", "/entertainment/")):
        return "EXCLUDE", "NOT_FINANCIAL", "NON_FINANCIAL_SECTION"

    if contains(text, PR_NOISE) and not contains(
        text,
        ("central bank", "cbsl", "regulator", "regulatory", "capital adequacy",
         "liquidity", "npl", "non-performing", "aml", "fatf", "cyber")
    ):
        return "EXCLUDE", "NOT_FINANCIAL", "LOW_VALUE_PR"

    # Direct banking / supervisory.
    if contains(text, ("fatf", "grey list", "aml/cft", "anti-money laundering",
                       "money laundering", "financial intelligence unit")):
        return "KEY_BANKING_SUPERVISORY", "AML_CFT", "AML_CFT_SIGNAL"

    if contains(text, ("capital adequacy", "capital buffer", "liquidity coverage",
                       "liquidity buffer", "core capital", "tier 1")):
        return "KEY_BANKING_SUPERVISORY", "CAPITAL_LIQUIDITY", "CAPITAL_LIQUIDITY_SIGNAL"

    if contains(text, ("stage 3", "non-performing loan", "non performing loan", "npl",
                       "provisioning", "impaired loan", "asset quality",
                       "credit risk", "gold-backed", "gold backed", "vehicle loan")):
        return "KEY_BANKING_SUPERVISORY", "CREDIT_ASSET_QUALITY", "CREDIT_RISK_SIGNAL"

    if contains(text, ("sovereign exposure", "government securities exposure",
                       "sovereign concentration", "concentration risk")):
        return "KEY_BANKING_SUPERVISORY", "SOVEREIGN_CONCENTRATION_RISK", "SOVEREIGN_EXPOSURE_SIGNAL"

    if contains(text, ("financial stability review", "financial stability",
                       "macroprudential", "systemic risk")) and contains(
        text, ("central bank", "cbsl", "bank", "finance compan")
    ):
        return "KEY_BANKING_SUPERVISORY", "FINANCIAL_STABILITY_MACROPRUDENTIAL", "FINANCIAL_STABILITY_SIGNAL"

    if contains(text, ("governance", "board oversight", "conduct risk",
                       "fit and proper", "related party")) and contains(
        text, ("bank", "finance company", "insurer", "central bank", "cbsl")
    ):
        return "KEY_BANKING_SUPERVISORY", "GOVERNANCE_CONDUCT", "FINANCIAL_GOVERNANCE_SIGNAL"

    if contains(text, ("cyber", "fraud", "operational risk", "data breach")) and contains(
        text, ("bank", "finance company", "payment", "financial institution")
    ):
        return "KEY_BANKING_SUPERVISORY", "OPERATIONAL_CYBER_FRAUD", "OPERATIONAL_RISK_SIGNAL"

    # Banking / payments infrastructure.
    if contains(text, ("govpay", "lankaqr", "payment system", "digital payment",
                       "digital banking", "fintech", "instant payment", "payment infrastructure")):
        return "BANKING_PAYMENTS_INFRASTRUCTURE", "PAYMENTS_FINANCIAL_INFRASTRUCTURE", "PAYMENTS_SIGNAL"

    if contains(text, ("financial literacy", "financial inclusion", "consumer protection",
                       "access to finance", "savings account")):
        return "BANKING_PAYMENTS_INFRASTRUCTURE", "FINANCIAL_INCLUSION_CONSUMER", "INCLUSION_CONSUMER_SIGNAL"

    if contains(text, ("climate risk", "el niño", "el nino", "climate resilience")) and contains(
        text, ("bank", "credit", "borrower", "loan", "finance")
    ):
        return "BANKING_PAYMENTS_INFRASTRUCTURE", "CLIMATE_FINANCIAL_RISK", "CLIMATE_CREDIT_SIGNAL"

    # Monetary / markets / external.
    if contains(text, ("policy rate", "overnight policy rate", "opr",
                       "money market liquidity", "standing lending facility",
                       "standing deposit facility")):
        return "MONETARY_MARKETS_EXTERNAL", "MONETARY_POLICY_LIQUIDITY", "MONETARY_LIQUIDITY_SIGNAL"

    if contains(text, ("treasury bond", "t-bond", "bond auction", "bond market", "yield",
                       "sovereign debt", "debt restructuring", "debt sustainability", "g24", "g-24")):
        return "MONETARY_MARKETS_EXTERNAL", "SOVEREIGN_DEBT_RATES", "DEBT_RATE_SIGNAL"

    if contains(text, ("foreign reserve", "foreign reserves", "exchange rate", "forex",
                       "rupee", "remittance", "remittances", "current account",
                       "balance of payments")):
        return "MONETARY_MARKETS_EXTERNAL", "FX_RESERVES_EXTERNAL_SECTOR", "EXTERNAL_SECTOR_SIGNAL"

    if contains(text, ("colombo stock exchange", "cse", "aspi", "s&p sl20",
                       "stock market", "market turnover", "share market")):
        return "MONETARY_MARKETS_EXTERNAL", "CAPITAL_MARKETS", "CAPITAL_MARKET_SIGNAL"

    # Macro/systemic.
    if contains(text, ("imf", "international monetary fund", "world bank",
                       "asian development bank", "adb", "gdp", "economic growth",
                       "inflation", "fiscal", "unemployment", "productivity",
                       "ageing population", "aging population")):
        return "MACRO_SYSTEMIC_CONTEXT", "MACRO_GROWTH_INFLATION_FISCAL", "MACRO_SIGNAL"

    # Generic financial sector item.
    if contains(text, ("bank", "banking", "finance company", "financial institution",
                       "insurance", "insurer", "leasing", "credit", "deposit", "loan")):
        return "OTHER_FINANCIAL_SECTOR", "OTHER_FINANCIAL_SECTOR", "GENERIC_FINANCIAL_SIGNAL"

    return "EXCLUDE", "NOT_FINANCIAL", "NO_STRONG_FINANCIAL_SIGNAL"

def event_key(headline: str, article_text: str) -> str:
    text = norm(f"{headline} {article_text[:1200]}")
    anchors = []
    for token in (
        "financial stability review", "fatf", "grey list", "govpay", "lankaqr",
        "treasury bond", "t-bond", "bond auction", "imf", "world economic outlook",
        "g24", "g-24", "state of the economy", "remittance", "cse", "aspi",
        "gold loan", "vehicle loan", "sovereign exposure"
    ):
        if token in text:
            anchors.append(token)
    if not anchors:
        words = re.findall(r"[a-z0-9]+", norm(headline))
        stop = {"the","a","an","to","of","in","for","and","on","with","sri","lanka","sl"}
        anchors = [w for w in words if w not in stop][:6]
    base = "|".join(sorted(set(anchors))) or norm(headline)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]

def truthy(value: object) -> bool:
    return norm(value) in {"1", "true", "yes", "y", "approved", "approve"}

def enrich_review_csv(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(path)

    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
        original_fields = list(rows[0].keys()) if rows else []

    staged = staged_index()
    gold = gold_index()

    suggested_fields = [
        "suggested_section",
        "suggested_primary_category",
        "suggested_category_reason",
        "suggested_event_key",
        "prior_approved_section",
        "prior_approved_category",
    ]
    human_fields = [
        "human_section",
        "human_primary_category",
        "human_secondary_categories",
        "human_event_key",
        "human_duplicate_of_url",
        "human_importance",
    ]

    # Preserve existing human columns and insert the new taxonomy feedback fields.
    fieldnames = []
    for field in original_fields:
        if field == "human_relevance_label":
            fieldnames.extend(suggested_fields)
        fieldnames.append(field)
        if field == "human_topic_label":
            fieldnames.extend(human_fields)

    for field in suggested_fields + human_fields:
        if field not in fieldnames:
            fieldnames.append(field)

    for row in rows:
        url = str(row.get("source_url") or "").strip()
        payload = staged.get(url, {})
        headline = str(row.get("headline") or payload.get("headline") or "").strip()
        article_text = str(payload.get("article_text") or "")
        section, category, reason = category_from_text(headline, article_text, url)

        prior = gold.get(url, {})
        row["suggested_section"] = section
        row["suggested_primary_category"] = category
        row["suggested_category_reason"] = reason
        row["suggested_event_key"] = event_key(headline, article_text)
        row["prior_approved_section"] = str(prior.get("human_section") or "")
        row["prior_approved_category"] = str(prior.get("human_primary_category") or "")

        for field in human_fields:
            row.setdefault(field, "")

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return {
        "path": str(path),
        "rows": len(rows),
        "suggested_sections": {
            section: sum(1 for row in rows if row.get("suggested_section") == section)
            for section in SECTIONS
        },
    }

def ingest_approved_csv(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(path)

    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    GOLD_ROOT.mkdir(parents=True, exist_ok=True)
    existing = {}
    if GOLD_FILE.exists():
        with GOLD_FILE.open("r", newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                url = str(row.get("source_url") or "").strip()
                if url:
                    existing[url] = row

    ingested = 0
    skipped = 0
    now = datetime.now().astimezone().isoformat()

    for row in rows:
        if not truthy(row.get("approved_for_training")):
            continue

        url = str(row.get("source_url") or "").strip()
        reviewer = str(row.get("reviewer") or "").strip()
        relevance = str(row.get("human_relevance_label") or "").strip()
        human_section = str(row.get("human_section") or "").strip()
        human_category = str(row.get("human_primary_category") or "").strip()

        if not url or not reviewer or not relevance:
            skipped += 1
            continue

        if relevance.upper() not in {"NOISE", "EXCLUDE"} and (
            human_section not in SECTIONS or human_category not in CATEGORIES
        ):
            skipped += 1
            continue

        existing[url] = {
            "source_url": url,
            "headline": str(row.get("headline") or ""),
            "source_name": str(row.get("source_name") or ""),
            "human_relevance_label": relevance,
            "human_section": human_section or "EXCLUDE",
            "human_primary_category": human_category or "NOT_FINANCIAL",
            "human_secondary_categories": str(row.get("human_secondary_categories") or ""),
            "human_event_key": str(row.get("human_event_key") or row.get("suggested_event_key") or ""),
            "human_duplicate_of_url": str(row.get("human_duplicate_of_url") or ""),
            "human_importance": str(row.get("human_importance") or ""),
            "human_decision": str(row.get("human_decision") or ""),
            "reviewer": reviewer,
            "review_notes": str(row.get("review_notes") or ""),
            "approved_for_training": "TRUE",
            "approved_at": now,
            "schema_version": SCHEMA_VERSION,
        }
        ingested += 1

    fields = [
        "source_url", "headline", "source_name", "human_relevance_label",
        "human_section", "human_primary_category", "human_secondary_categories",
        "human_event_key", "human_duplicate_of_url", "human_importance",
        "human_decision", "reviewer", "review_notes", "approved_for_training",
        "approved_at", "schema_version",
    ]

    with GOLD_FILE.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for url in sorted(existing):
            writer.writerow(existing[url])

    return {
        "gold_file": str(GOLD_FILE),
        "ingested": ingested,
        "skipped_invalid": skipped,
        "gold_rows": len(existing),
    }

def render_review_guide(path: Path) -> Path:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    out = path.with_name(path.stem + " Categories.html")
    cards = []
    for row in rows:
        h = html.escape(str(row.get("headline") or ""))
        src = html.escape(str(row.get("source_name") or ""))
        url = html.escape(str(row.get("source_url") or ""), quote=True)
        sec = html.escape(str(row.get("suggested_section") or ""))
        cat = html.escape(str(row.get("suggested_primary_category") or ""))
        why = html.escape(str(row.get("suggested_category_reason") or ""))
        cards.append(
            f"""<article><div class="meta">{src}</div><h3>{h}</h3>
            <p><b>Suggested section:</b> {sec}<br>
            <b>Suggested category:</b> {cat}<br>
            <b>Why:</b> {why}</p>
            <a href="{url}" target="_blank" rel="noopener">Read source</a></article>"""
        )

    sections = "<br>".join(SECTIONS)
    cats = "<br>".join(CATEGORIES)
    body = f"""<!doctype html><html><head><meta charset="utf-8">
    <title>Category Review</title>
    <style>
    body{{font-family:Segoe UI,Arial;max-width:1150px;margin:28px auto;padding:0 18px;color:#1b2733}}
    article{{border:1px solid #dce3e9;border-radius:10px;padding:18px;margin:14px 0}}
    .meta{{color:#60727e;font-size:13px}} h1,h2{{color:#0b3154}}
    code{{background:#f3f6f8;padding:2px 5px}} .grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
    </style></head><body>
    <h1>Financial Brief — Category Review</h1>
    <p>Review both <b>relevance</b> and <b>where the story belongs</b>. Only rows explicitly approved for training enter the human-gold file.</p>
    <div class="grid"><div><h2>Sections</h2><code>{sections}</code></div>
    <div><h2>Primary categories</h2><code>{cats}</code></div></div>
    <h2>Stories</h2>{''.join(cards)}
    </body></html>"""
    out.write_text(body, encoding="utf-8")
    return out
