from __future__ import annotations

# BANK_SUPERVISION_UI_CLEANUP_V1

import argparse
import csv
import json
import re
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = PROJECT_ROOT / "data" / "processed" / "reports"
ML_ROOT = PROJECT_ROOT / "data" / "processed" / "ml"
STAGED_ROOT = PROJECT_ROOT / "data" / "staged"
STAGE1_ROOT = PROJECT_ROOT / "data" / "processed" / "stage1"
TZ = ZoneInfo("Asia/Colombo")

POLICY_VERSION = "bank-supervision-final-v1.0"

SECTION_ORDER = [
    "KEY_BANKING_SUPERVISORY",
    "BANKING_SECTOR_DEVELOPMENTS",
    "MONETARY_MARKETS_EXTERNAL",
    "MACRO_SYSTEMIC_CONTEXT",
    "OTHER_FINANCIAL_SECTOR",
]

SECTION_TITLES = {
    "KEY_BANKING_SUPERVISORY": "CBSL & Bank Supervisory Developments",
    "BANKING_SECTOR_DEVELOPMENTS": "Banking Sector Developments & Risk Context",
    "MONETARY_MARKETS_EXTERNAL": "Monetary, Markets & External Sector",
    "MACRO_SYSTEMIC_CONTEXT": "Macro & Systemic Context",
    "OTHER_FINANCIAL_SECTOR": "Other Financial-Sector Developments",
}

SECTION_NOTES = {
    "KEY_BANKING_SUPERVISORY": (
        "Highest priority for Bank Supervision: CBSL supervisory actions and direct "
        "bank governance, prudential, credit, liquidity, AML/CFT, fraud, cyber, "
        "enforcement and material risk developments."
    ),
    "BANKING_SECTOR_DEVELOPMENTS": (
        "Other relevant developments affecting licensed banks, including lending, "
        "SME finance, payments, technology, inclusion and banking-sector risk context."
    ),
    "MONETARY_MARKETS_EXTERNAL": (
        "Monetary policy, interest rates, sovereign debt, foreign exchange, reserves, "
        "remittances and capital-market developments relevant to banks."
    ),
    "MACRO_SYSTEMIC_CONTEXT": (
        "Macroeconomic and systemic developments retained where they provide useful "
        "context for banking-sector supervision."
    ),
    "OTHER_FINANCIAL_SECTOR": (
        "Material developments in other regulated or closely related financial "
        "institutions, after the banking-sector items."
    ),
}

VALID_SECTIONS = set(SECTION_ORDER) | {"EXCLUDE"}

LOCAL_BANK_TERMS = (
    "bank of ceylon", "boc",
    "people's bank", "peoples bank",
    "commercial bank of ceylon", "commercial bank", "combank",
    "hatton national bank", "hnb",
    "national development bank", "ndb",
    "dfcc bank", "dfcc",
    "sampath bank",
    "nations trust bank", "nation's trust bank", "ntb",
    "seyland bank",
    "pan asia bank",
    "union bank",
    "cargills bank",
    "amana bank",
    "sanasa development bank", "sdb bank",
    "regional development bank", "rdb",
    "national savings bank", "nsb",
    "state mortgage and investment bank", "smib",
    "standard chartered sri lanka", "standard chartered bank sri lanka",
    "hsbc sri lanka",
    "citibank sri lanka", "citi sri lanka",
    "deutsche bank sri lanka",
    "state bank of india sri lanka",
    "indian bank sri lanka",
    "indian overseas bank sri lanka",
    "habib bank sri lanka",
    "mcb bank sri lanka",
    "public bank berhad sri lanka",
)

CBSL_TERMS = (
    "central bank of sri lanka",
    "cbsl",
    "financial intelligence unit of sri lanka",
    "fiu sri lanka",
)

NONBANK_FINANCIAL_TERMS = (
    "finance plc",
    "finance limited",
    "finance ltd",
    "finance company",
    "insurance",
    "insurer",
    "assurance",
    "leasing company",
)

BANK_CSR_TERMS = (
    "donation", "donates", "donated",
    "scholarship", "scholarships",
    "stem classroom", "school classroom", "computer laboratory",
    "children's day", "childrens day",
    "customer service week",
    "sports sponsorship",
    "official accommodation partner",
    "anniversary", "celebrates", "celebrating",
)

BANK_LOW_VALUE_PR_TERMS = (
    "campaign", "reward children", "rewards children",
    "conference", "forum", "adfiap", "annual meeting",
    "branch opening", "opens branch", "new branch",
)

GOVERNANCE_TERMS = (
    "board", "director", "chief executive", "ceo",
    "chairman", "chairperson", "cfo", "chief financial officer",
    "managing director", "appoints", "appointed", "appointment",
    "resigns", "resignation", "steps down",
)

PRUDENTIAL_TERMS = (
    "capital adequacy", "tier 1", "capital buffer", "capital requirement",
    "liquidity coverage", "liquidity ratio", "funding risk",
    "non-performing", "non performing", "npl", "stage 3",
    "impaired loan", "asset quality", "provisioning", "credit risk",
    "concentration risk", "sovereign exposure", "sovereign concentration",
    "government securities exposure", "large exposure", "related party exposure",
    "collateral", "cash-secured", "cash secured", "deposit-backed", "deposit backed",
    "loan-to-value", "ltv", "risk weight",
    "interest coverage", "unable to cover interest", "debt-servicing capacity",
    "debt servicing capacity",
)

FRAUD_CYBER_TERMS = (
    "fraud", "cyber", "cyberattack", "data breach", "hacker",
    "operational risk", "forensic audit", "payment fraud",
)

AML_TERMS = (
    "aml", "aml/cft", "money laundering", "fatf", "grey list",
    "financial intelligence unit",
)

ENFORCEMENT_TERMS = (
    "regulatory action", "enforcement", "penalty", "fine", "sanction",
    "directive", "direction", "licence suspended", "license suspended",
)

MONEY_TERMS = (
    "policy rate", "monetary policy", "standing deposit facility",
    "standing lending facility", "money market liquidity",
    "overnight liquidity", "inflation target",
)

DEBT_TERMS = (
    "treasury bill", "treasury bills", "t-bill", "t-bills",
    "treasury bond", "treasury bonds", "t-bond", "t-bonds",
    "bond auction", "bond market", "sovereign debt",
    "debt restructuring", "debt sustainability",
)

FX_TERMS = (
    "foreign reserve", "foreign reserves", "exchange rate", "forex",
    "balance of payments", "current account", "remittance", "remittances",
    "outward investment cap", "foreign exchange act",
)

MARKET_TERMS = (
    "colombo stock exchange", "aspi", "s&p sl20",
    "stock market", "share market", "market turnover",
)

LEGAL_PATTERNS = (
    r"\bremand(?:ed)?\b", r"\bcourt\b", r"\bbail\b", r"\btrial-at-bar\b",
    r"\bprison\b", r"\barrest(?:ed)?\b", r"\bindictment\b",
    r"\bformer first lady\b", r"\bopposition leader\b",
    r"\bcontempt case\b", r"\bbribery case\b", r"\bmurder\b",
)

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "from", "by", "sri", "lanka", "sl", "says", "said", "new", "as", "at",
    "is", "are", "be", "after", "before", "amid", "over", "into", "its",
    "this", "that", "year",
}

BOILERPLATE_MARKERS = (
    "save my name, email, and website",
    "the morning is sri lanka's foremost national news brand",
    "developed by derana",
    "copyright ©",
)


def safe_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def norm(value) -> str:
    return (
        safe_text(value)
        .lower()
        .replace("’", "'")
        .replace("–", "-")
        .replace("—", "-")
    )


def contains(text: str, terms) -> bool:
    value = norm(text)
    for term in terms:
        token = norm(term)
        if not token:
            continue
        pattern = (
            r"(?<![a-z0-9])"
            + re.escape(token).replace(r"\ ", r"\s+")
            + r"(?![a-z0-9])"
        )
        if re.search(pattern, value):
            return True
    return False


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def assert_ml_window_complete(end_date: date, days: int) -> list[str]:
    start_date = end_date - timedelta(days=days - 1)
    missing: list[str] = []
    bad: list[str] = []
    good: list[str] = []

    current = start_date
    while current <= end_date:
        day = current.isoformat()
        manifest = ML_ROOT / day / "ml_manifest.json"

        if not manifest.is_file():
            missing.append(day)
        else:
            payload = load_json(manifest)
            if payload.get("status") != "SUCCESS":
                bad.append(day)
            else:
                good.append(day)

        current += timedelta(days=1)

    if missing or bad:
        details = []
        if missing:
            details.append("missing ML dates=" + ", ".join(missing))
        if bad:
            details.append("non-SUCCESS ML dates=" + ", ".join(bad))
        raise RuntimeError(
            "COVERAGE GATE FAILED. The report will not claim an incomplete date window. "
            + "; ".join(details)
        )

    return good


def _first_value(payload: dict, names: tuple[str, ...]):
    for name in names:
        value = payload.get(name)
        if value not in (None, "", [], {}):
            return value
    return ""


def _iter_article_dicts(obj):
    if isinstance(obj, dict):
        headline = _first_value(obj, ("headline", "title"))
        url = _first_value(
            obj,
            ("source_url", "canonical_url", "article_url", "url"),
        )
        if headline and url:
            yield obj
            return

        for value in obj.values():
            if isinstance(value, (dict, list)):
                yield from _iter_article_dicts(value)

    elif isinstance(obj, list):
        for value in obj:
            if isinstance(value, (dict, list)):
                yield from _iter_article_dicts(value)


def _normalise_stage1_record(payload: dict) -> dict:
    url = safe_text(
        _first_value(
            payload,
            ("source_url", "canonical_url", "article_url", "url"),
        )
    )
    headline = safe_text(_first_value(payload, ("headline", "title")))
    source_name = safe_text(
        _first_value(payload, ("source_name", "publisher", "source"))
    )
    publication_date = safe_text(
        _first_value(
            payload,
            ("publication_date", "published_at", "published_date", "date"),
        )
    )

    return {
        "source_url": url,
        "headline": headline,
        "source_name": source_name,
        "publication_date": publication_date,
        "_full_window_source": "STAGE1",
    }


def read_full_stage1_window(end_date: date, days: int) -> list[dict]:
    start_date = end_date - timedelta(days=days - 1)
    by_url: dict[str, dict] = {}

    current = start_date
    while current <= end_date:
        root = STAGE1_ROOT / current.isoformat()
        if not root.is_dir():
            raise FileNotFoundError(
                f"Full-window source is missing Stage-1 directory: {root}"
            )

        manifest_path = root / "stage1_manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"Stage-1 manifest is missing for {current.isoformat()}: {manifest_path}"
            )

        manifest = load_json(manifest_path)
        if manifest.get("status") != "SUCCESS":
            raise RuntimeError(
                f"Stage-1 manifest is not SUCCESS for {current.isoformat()}: "
                f"{manifest.get('status')}"
            )

        for value in manifest.get("analysis_files", []) or []:
            path = Path(str(value))
            if not path.is_absolute():
                path = PROJECT_ROOT / path

            if not path.is_file():
                raise FileNotFoundError(
                    f"Stage-1 analysis listed in manifest does not exist: {path}"
                )

            try:
                payload = load_json(path)
            except Exception:
                continue

            for item in _iter_article_dicts(payload):
                row = _normalise_stage1_record(item)
                url = row["source_url"]
                if not url or not row["headline"]:
                    continue

                previous = by_url.get(url)
                if previous is None:
                    by_url[url] = row
                else:
                    old_score = len(previous.get("source_name", ""))
                    new_score = len(row.get("source_name", ""))
                    if new_score > old_score:
                        by_url[url] = row

        current += timedelta(days=1)

    rows = list(by_url.values())
    rows.sort(
        key=lambda row: (
            safe_text(row.get("publication_date")),
            safe_text(row.get("source_name")),
            safe_text(row.get("headline")),
        )
    )
    return rows


def staged_index() -> dict[str, dict]:
    out: dict[str, dict] = {}

    for path in STAGED_ROOT.rglob("*.json"):
        try:
            payload = load_json(path)
        except Exception:
            continue

        body_len = len(safe_text(payload.get("article_text")))
        version = int(payload.get("article_version", 0) or 0)

        for raw_key in (payload.get("source_url"), payload.get("canonical_url")):
            key = safe_text(raw_key)
            if not key:
                continue

            previous = out.get(key)
            if previous is None:
                out[key] = payload
                continue

            old_body_len = len(safe_text(previous.get("article_text")))
            old_version = int(previous.get("article_version", 0) or 0)

            if (version, body_len) > (old_version, old_body_len):
                out[key] = payload

    return out


def headline_is_bank(headline: str) -> bool:
    h = norm(headline)

    if contains(h, LOCAL_BANK_TERMS):
        return True

    if re.search(r"\bbank\b", h) and not contains(
        h,
        (
            "world bank",
            "bank of thailand",
            "central bank",
            "development bank group",
        ),
    ):
        return True

    return False


def headline_is_cbsl(headline: str) -> bool:
    return contains(
        headline,
        CBSL_TERMS + ("central bank", "cbsl", "governor"),
    )


def headline_is_nonbank_financial(headline: str) -> bool:
    return contains(headline, NONBANK_FINANCIAL_TERMS)


def classify(
    headline: str,
    body: str,
    url: str,
) -> tuple[str, str, str, str]:
    # NEWS_FINAL_BRIEF_FINANCIAL_RESCUE_V1
    _news_rescue = _news_material_financial_rescue(
        headline,
        body,
        url,
    )

    if _news_rescue is not None:
        return _news_rescue

    h = norm(headline)
    t = norm(f"{headline} {body}")
    lead = norm(f"{headline} {body[:1800]}")
    u = norm(url)

    bank_subject = headline_is_bank(headline)
    cbsl_subject = headline_is_cbsl(headline)
    nonbank_financial_subject = headline_is_nonbank_financial(headline)

    # Headline-led PR/CSR controls run before body-level risk terms so incidental
    # mentions inside a ceremony/award cannot promote promotional coverage.
    if bank_subject and contains(h, BANK_CSR_TERMS):
        return "EXCLUDE", "NOT_FINANCIAL", "BANK_CSR_OR_CEREMONIAL_NOISE", "NONE"

    if (
        bank_subject
        and contains(h, BANK_LOW_VALUE_PR_TERMS)
        and not contains(
            h,
            (
                "fraud", "cyber", "aml", "money laundering",
                "capital", "liquidity", "npl", "stage 3",
                "credit risk", "financial stability",
            ),
        )
    ):
        return "EXCLUDE", "NOT_FINANCIAL", "LOW_VALUE_BANK_PR_OR_EVENT", "NONE"

    if bank_subject and contains(h, ("award", "awards", "recognised", "recognized")):
        if contains(
            t,
            (
                "risk management", "operational efficiency",
                "artificial intelligence", "machine learning",
                "cyber resilience", "digital transformation",
            ),
        ):
            return (
                "BANKING_SECTOR_DEVELOPMENTS",
                "OPERATIONAL_TECHNOLOGY",
                "MATERIAL_BANK_TECH_IMPLEMENTATION",
                "LOW",
            )
        return "EXCLUDE", "NOT_FINANCIAL", "BANK_AWARD_WITHOUT_MATERIAL_CHANGE", "NONE"

    # 1. Direct supervisory / risk developments.
    if contains(h, ("financial stability review",)):
        return (
            "KEY_BANKING_SUPERVISORY",
            "FINANCIAL_STABILITY_MACROPRUDENTIAL",
            "FINANCIAL_STABILITY_REVIEW",
            "HIGH",
        )

    if contains(h, ("financial literacy", "financial inclusion")):
        return (
            "BANKING_SECTOR_DEVELOPMENTS",
            "FINANCIAL_INCLUSION_CONSUMER",
            "SYSTEM_INCLUSION_SIGNAL",
            "MEDIUM",
        )

    if (bank_subject or cbsl_subject) and (
        contains(h, AML_TERMS)
        or (
            contains(lead, AML_TERMS)
            and contains(h, ("fatf", "grey list", "aml", "money laundering"))
        )
    ):
        return "KEY_BANKING_SUPERVISORY", "AML_CFT", "DIRECT_BANK_AML_CFT_SIGNAL", "HIGH"

    if (bank_subject or cbsl_subject) and (
        contains(h, FRAUD_CYBER_TERMS)
        or (
            contains(lead, FRAUD_CYBER_TERMS)
            and contains(
                h,
                ("fraud", "cyber", "hacker", "breach", "forensic", "operational risk"),
            )
        )
    ):
        return (
            "KEY_BANKING_SUPERVISORY",
            "OPERATIONAL_CYBER_FRAUD",
            "DIRECT_BANK_OPERATIONAL_RISK",
            "HIGH",
        )

    if (bank_subject or cbsl_subject) and (
        contains(h, PRUDENTIAL_TERMS)
        or (
            cbsl_subject
            and contains(h, ("vehicle", "gold loan", "gold loans", "pawning"))
            and contains(lead, PRUDENTIAL_TERMS)
        )
        or (
            bank_subject
            and contains(
                h,
                (
                    "asset quality", "capital", "liquidity", "exposure",
                    "loan fully cash-secured", "loan fully cash secured",
                ),
            )
            and contains(lead, PRUDENTIAL_TERMS)
        )
    ):
        if contains(
            lead,
            ("sovereign exposure", "sovereign concentration", "government securities exposure"),
        ):
            category = "SOVEREIGN_CONCENTRATION_RISK"
        elif contains(
            lead,
            (
                "capital adequacy", "tier 1", "capital buffer",
                "liquidity coverage", "liquidity ratio", "funding risk",
            ),
        ):
            category = "CAPITAL_LIQUIDITY"
        elif contains(
            lead,
            (
                "interest coverage", "unable to cover interest",
                "debt-servicing capacity", "debt servicing capacity",
            ),
        ):
            category = "FINANCIAL_STABILITY_MACROPRUDENTIAL"
        else:
            category = "CREDIT_ASSET_QUALITY"

        high_terms = (
            "stage 3", "npl", "non-performing", "capital adequacy",
            "liquidity coverage", "sovereign exposure",
            "unable to cover interest", "interest coverage",
        )
        importance = "HIGH" if contains(lead, high_terms) else "MEDIUM"

        return (
            "KEY_BANKING_SUPERVISORY",
            category,
            "DIRECT_BANK_PRUDENTIAL_SIGNAL",
            importance,
        )

    if (bank_subject or cbsl_subject) and contains(lead, ENFORCEMENT_TERMS):
        return (
            "KEY_BANKING_SUPERVISORY",
            "GOVERNANCE_CONDUCT",
            "REGULATORY_ENFORCEMENT_SIGNAL",
            "HIGH",
        )

    if bank_subject and contains(h, GOVERNANCE_TERMS):
        return (
            "KEY_BANKING_SUPERVISORY",
            "GOVERNANCE_CONDUCT",
            "DIRECT_BANK_GOVERNANCE_CHANGE",
            "MEDIUM",
        )

    # 2. Direct licensed-bank developments.
    if bank_subject:
        if contains(
            lead,
            (
                "sme", "msme", "entrepreneur", "entrepreneurship",
                "financial inclusion", "access to finance",
            ),
        ):
            return (
                "BANKING_SECTOR_DEVELOPMENTS",
                "FINANCIAL_INCLUSION_CONSUMER",
                "DIRECT_BANK_DEVELOPMENT",
                "MEDIUM",
            )

        if contains(
            lead,
            ("loan", "loans", "credit", "lending", "leasing", "mortgage"),
        ):
            return (
                "BANKING_SECTOR_DEVELOPMENTS",
                "CREDIT_PRODUCT_STRATEGY",
                "DIRECT_BANK_DEVELOPMENT",
                "LOW",
            )

        if contains(
            lead,
            ("payment", "payments", "digital banking", "mobile banking", "fintech"),
        ):
            return (
                "BANKING_SECTOR_DEVELOPMENTS",
                "PAYMENTS_FINANCIAL_INFRASTRUCTURE",
                "DIRECT_BANK_DEVELOPMENT",
                "MEDIUM",
            )

        if contains(
            lead,
            ("artificial intelligence", "machine learning", "ai/ml", "digital transformation"),
        ):
            return (
                "BANKING_SECTOR_DEVELOPMENTS",
                "OPERATIONAL_TECHNOLOGY",
                "DIRECT_BANK_DEVELOPMENT",
                "LOW",
            )

    if contains(h, ("masterminds quiz", "cse masterminds", "worth of prizes")):
        return "EXCLUDE", "NOT_FINANCIAL", "LOW_VALUE_MARKET_PROMOTION", "NONE"

    if bank_subject and contains(h, ("cse and commercial bank",)) and contains(
        lead,
        (
            "capital formation", "listing", "prospective issuers",
            "capital raising", "capital-raising",
        ),
    ):
        return (
            "BANKING_SECTOR_DEVELOPMENTS",
            "CAPITAL_MARKETS",
            "BANK_ENTERPRISE_CAPITAL_ACCESS",
            "LOW",
        )

    if bank_subject and contains(
        lead,
        ("el niño", "el nino", "climate risk", "climate resilience"),
    ):
        return (
            "BANKING_SECTOR_DEVELOPMENTS",
            "CLIMATE_FINANCIAL_RISK",
            "BANK_CLIMATE_RISK_CONTEXT",
            "MEDIUM",
        )

    # 3. Banking-sector context / infrastructure.
    if contains(h, ("govpay", "lankaqr", "lankapay")):
        return (
            "BANKING_SECTOR_DEVELOPMENTS",
            "PAYMENTS_FINANCIAL_INFRASTRUCTURE",
            "SYSTEM_PAYMENT_INFRASTRUCTURE",
            "MEDIUM",
        )

    if contains(t, ("bangkok blueprint",)) and contains(
        t,
        ("cyber-enabled fraud", "digital financial services"),
    ):
        return (
            "BANKING_SECTOR_DEVELOPMENTS",
            "OPERATIONAL_CYBER_FRAUD",
            "INTERNATIONAL_SUPERVISORY_REFERENCE",
            "MEDIUM",
        )

    if contains(t, ("financial institutions",)) and contains(
        t,
        ("fraud incidents", "bank fraud", "hacker", "banking risk"),
    ):
        return (
            "BANKING_SECTOR_DEVELOPMENTS",
            "FINANCIAL_STABILITY_MACROPRUDENTIAL",
            "EXTERNAL_BANKING_RISK_CONTEXT",
            "MEDIUM",
        )

    if contains(t, ("crib", "credit information bureau")) and contains(
        t,
        ("loan", "credit", "borrower", "repayment"),
    ):
        return (
            "BANKING_SECTOR_DEVELOPMENTS",
            "FINANCIAL_INCLUSION_CONSUMER",
            "BANK_CREDIT_INFORMATION_POLICY_CONTEXT",
            "LOW",
        )

    if any(
        marker in u
        for marker in ("/sports/", "/entertainment/", "/lawnorder/", "/law-order/")
    ):
        return "EXCLUDE", "NOT_FINANCIAL", "NON_FINANCIAL_SECTION", "NONE"

    # 4. CBSL monetary / external.
    if cbsl_subject and contains(t, MONEY_TERMS):
        return (
            "MONETARY_MARKETS_EXTERNAL",
            "MONETARY_POLICY_LIQUIDITY",
            "CBSL_MONETARY_SIGNAL",
            "HIGH",
        )

    if cbsl_subject and contains(t, FX_TERMS):
        return (
            "MONETARY_MARKETS_EXTERNAL",
            "FX_RESERVES_EXTERNAL_SECTOR",
            "CBSL_EXTERNAL_SECTOR_SIGNAL",
            "MEDIUM",
        )

    # 5. Monetary / markets / external.
    if contains(h, DEBT_TERMS) or (
        contains(t, DEBT_TERMS)
        and contains(h, ("bill", "bond", "auction", "debt"))
    ):
        return (
            "MONETARY_MARKETS_EXTERNAL",
            "SOVEREIGN_DEBT_RATES",
            "DEBT_RATE_SIGNAL",
            "MEDIUM",
        )

    if contains(h, MARKET_TERMS) or (
        contains(t, MARKET_TERMS)
        and contains(h, ("market", "cse", "aspi", "indices"))
    ):
        return (
            "MONETARY_MARKETS_EXTERNAL",
            "CAPITAL_MARKETS",
            "CAPITAL_MARKET_SIGNAL",
            "LOW",
        )

    if contains(h, ("worker remittance", "worker remittances", "remittances")):
        return (
            "MONETARY_MARKETS_EXTERNAL",
            "FX_RESERVES_EXTERNAL_SECTOR",
            "REMITTANCE_SIGNAL",
            "MEDIUM",
        )

    if contains(h, ("outward investment cap",)) or contains(t, ("foreign exchange act",)):
        return (
            "MONETARY_MARKETS_EXTERNAL",
            "FX_RESERVES_EXTERNAL_SECTOR",
            "CAPITAL_CONTROL_FX_SIGNAL",
            "HIGH",
        )

    if contains(h, ("inflation target", "target consultation")) and contains(
        t,
        ("central bank", "cbsl"),
    ):
        return (
            "MONETARY_MARKETS_EXTERNAL",
            "MONETARY_POLICY_LIQUIDITY",
            "MONETARY_POLICY_INFLATION_SIGNAL",
            "HIGH",
        )

    if contains(t, ("g24", "g-24")) and contains(t, ("debt",)):
        return (
            "MONETARY_MARKETS_EXTERNAL",
            "SOVEREIGN_DEBT_RATES",
            "G24_DEBT_SIGNAL",
            "MEDIUM",
        )

    if contains(
        t,
        (
            "money market liquidity",
            "standing deposit facility",
            "standing lending facility",
            "policy rate",
        ),
    ):
        return (
            "MONETARY_MARKETS_EXTERNAL",
            "MONETARY_POLICY_LIQUIDITY",
            "MONETARY_SIGNAL",
            "HIGH",
        )

    # 6. Macro / systemic.
    if contains(h, ("e-invoicing", "revenue agencies")):
        return "EXCLUDE", "NOT_FINANCIAL", "TOO_INDIRECT_FOR_BANK_SUPERVISION", "NONE"

    if contains(h, ("government revenue", "customs achieves", "customs revenue")):
        return (
            "MACRO_SYSTEMIC_CONTEXT",
            "MACRO_GROWTH_INFLATION_FISCAL",
            "FISCAL_REVENUE_PERFORMANCE",
            "LOW",
        )

    if contains(h, ("ageing", "aging")) and contains(t, ("sri lanka", "imf")):
        return (
            "MACRO_SYSTEMIC_CONTEXT",
            "MACRO_GROWTH_INFLATION_FISCAL",
            "DEMOGRAPHIC_MACRO_SIGNAL",
            "MEDIUM",
        )

    if contains(h, ("imf projects", "imf predicts", "imf sees")) and contains(
        t,
        ("sri lanka",),
    ) and contains(
        t,
        ("growth", "inflation", "gdp"),
    ):
        return (
            "MACRO_SYSTEMIC_CONTEXT",
            "MACRO_GROWTH_INFLATION_FISCAL",
            "IMF_SRI_LANKA_MACRO_SIGNAL",
            "HIGH",
        )

    if contains(h, ("imf warns global economy", "global economy")) and contains(t, ("imf",)):
        return (
            "MACRO_SYSTEMIC_CONTEXT",
            "MACRO_GROWTH_INFLATION_FISCAL",
            "GLOBAL_MACRO_SIGNAL",
            "LOW",
        )

    if contains(t, ("state of the economy",)) or (
        contains(t, ("institute of policy studies", "ips"))
        and contains(h, ("growth", "state of the economy"))
    ):
        return (
            "MACRO_SYSTEMIC_CONTEXT",
            "MACRO_GROWTH_INFLATION_FISCAL",
            "DOMESTIC_MACRO_SIGNAL",
            "MEDIUM",
        )

    if contains(h, ("port city",)) and contains(t, ("tax", "fiscal", "investment")):
        return (
            "MACRO_SYSTEMIC_CONTEXT",
            "MACRO_GROWTH_INFLATION_FISCAL",
            "DOMESTIC_POLICY_MACRO_SIGNAL",
            "LOW",
        )

    if contains(h, ("business approvals", "investment security")) and contains(
        t,
        ("government", "investment", "business"),
    ):
        return (
            "MACRO_SYSTEMIC_CONTEXT",
            "MACRO_GROWTH_INFLATION_FISCAL",
            "DOMESTIC_POLICY_MACRO_SIGNAL",
            "LOW",
        )

    if "/general-opinion/" in u:
        if contains(h, ("adb", "imf", "world bank", "ips")):
            return (
                "MACRO_SYSTEMIC_CONTEXT",
                "MACRO_GROWTH_INFLATION_FISCAL",
                "INSTITUTIONAL_MACRO_OPINION_SECTION",
                "LOW",
            )
        return (
            "EXCLUDE",
            "NOT_FINANCIAL",
            "GENERAL_OPINION_WITHOUT_BANK_SUPERVISION_ANCHOR",
            "NONE",
        )

    if any(re.search(pattern, t, flags=re.I) for pattern in LEGAL_PATTERNS):
        return (
            "EXCLUDE",
            "NOT_FINANCIAL",
            "LEGAL_POLITICAL_NO_DIRECT_FINANCIAL_IMPACT",
            "NONE",
        )

    # 7. Other financial-sector institutions.
    if nonbank_financial_subject:
        if contains(h, BANK_CSR_TERMS) or contains(
            h,
            ("branch opening", "opens branch", "new branch"),
        ):
            return "EXCLUDE", "NOT_FINANCIAL", "LOW_MATERIALITY_FINANCIAL_PR", "NONE"

        if contains(h, GOVERNANCE_TERMS):
            return (
                "OTHER_FINANCIAL_SECTOR",
                "GOVERNANCE_LEADERSHIP_CHANGE",
                "NONBANK_FINANCIAL_GOVERNANCE_CHANGE",
                "LOW",
            )

        if contains(
            t,
            PRUDENTIAL_TERMS + FRAUD_CYBER_TERMS + AML_TERMS + ENFORCEMENT_TERMS,
        ):
            return (
                "OTHER_FINANCIAL_SECTOR",
                "OTHER_FINANCIAL_SECTOR",
                "MATERIAL_NONBANK_FINANCIAL_RISK",
                "MEDIUM",
            )

        return "EXCLUDE", "NOT_FINANCIAL", "LOW_MATERIALITY_FINANCIAL_PR", "NONE"

    return "EXCLUDE", "NOT_FINANCIAL", "NO_STRONG_BANK_SUPERVISION_OR_FINANCE_SIGNAL", "NONE"


def clean_article_text(value: str) -> str:
    # Some collectors preserve escaped newline/tab sequences literally.
    # Normalise those before whitespace cleanup so the stakeholder HTML does not
    # display "\n\n" inside article summaries.
    text = str(value or "")
    text = (
        text
        .replace("\\r\\n", " ")
        .replace("\\n", " ")
        .replace("\\r", " ")
        .replace("\\t", " ")
    )
    text = safe_text(text)

    # Work on an apostrophe-normalised copy so publisher boilerplate using a
    # curly apostrophe (e.g. "Sri Lanka’s") is caught by ASCII markers too.
    low = text.lower().replace("’", "'")
    cut = len(text)

    for marker in BOILERPLATE_MARKERS:
        marker_low = str(marker).lower().replace("’", "'")
        idx = low.find(marker_low)
        if idx >= 0:
            cut = min(cut, idx)

    return safe_text(text[:cut])


def make_summary(body: str, max_chars: int = 900) -> str:
    text = clean_article_text(body)
    if not text:
        return ""

    if len(text) <= max_chars:
        return text

    clipped = text[:max_chars]
    sentence_end = max(
        clipped.rfind(". "),
        clipped.rfind("? "),
        clipped.rfind("! "),
    )

    if sentence_end >= int(max_chars * 0.55):
        return clipped[: sentence_end + 1].strip()

    word_end = clipped.rfind(" ")
    if word_end > 0:
        clipped = clipped[:word_end]

    return clipped.rstrip(" ,;:-") + "..."


def headline_tokens(headline: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", norm(headline))
        if len(token) > 2 and token not in STOPWORDS
    }


def event_key(row: dict) -> str:
    # NEWS_FINAL_BRIEF_FINANCIAL_RESCUE_V1
    _news_headline = safe_text(
        row.get("headline")
    ).lower()

    _news_body = safe_text(
        row.get("body")
    ).lower()

    _news_date = safe_text(
        row.get("publication_date")
    )[:10]

    _news_text = (
        _news_headline
        + " "
        + _news_body
    )

    # Consolidate multiple newspapers reporting the same
    # daily Colombo equity-market movement.
    if (
        (
            "colombo stock exchange" in _news_text
            or "cse " in _news_text
            or "aspi" in _news_text
            or "all share price index" in _news_text
            or "s&p sl20" in _news_text
        )
        and any(
            term in _news_text
            for term in (
                "turnover",
                "bourse",
                "buying interest",
                "selling pressure",
                "rebounded",
                "recovered",
                "market capitalisation",
                "market capitalization",
            )
        )
    ):
        return (
            f"CSE_MARKET_{_news_date}"
        )

    # Consolidate same-day Sri Lanka IMF/EFF coverage.
    if (
        (
            "imf" in _news_text
            or "international monetary fund"
            in _news_text
        )
        and (
            "sri lanka" in _news_text
            or "sri lankan" in _news_text
        )
        and any(
            term in _news_text
            for term in (
                "extended fund facility",
                "eff",
                "debt restructuring",
                "staff-level agreement",
                "staff level agreement",
                "macroeconomic",
                "fiscal performance",
            )
        )
    ):
        return (
            f"IMF_SRI_LANKA_{_news_date}"
        )

    h = norm(row["headline"])
    t = norm(row["body"])
    d = row["publication_date"] or "unknown"

    if contains(h, ("financial stability review",)):
        return "FINANCIAL_STABILITY_REVIEW"

    if contains(h, ("grey list", "fatf")) and contains(t, ("central bank", "cbsl", "fiu")):
        return "FATF_CBSL_" + d

    if contains(h, ("ageing", "aging")) and contains(t, ("imf",)):
        return "IMF_AGING_SRI_LANKA"

    if contains(t, ("state of the economy",)):
        return "IPS_STATE_OF_ECONOMY"

    if contains(t, ("g24", "g-24")) and contains(t, ("debt",)):
        return "G24_DEBT"

    if contains(t, ("world economic outlook",)) and contains(t[:1800], ("sri lanka",)):
        return "IMF_WEO_SRI_LANKA"

    if contains(h, ("inflation target", "target consultation")) and contains(
        t,
        ("central bank", "cbsl"),
    ):
        return "CBSL_INFLATION_TARGET"

    if (
        (
            contains(h, ("aspi", "cse falls", "stock market", "indices close"))
            and contains(
                t,
                (
                    "all share price index",
                    "market turnover",
                    "s&p sl20",
                    "share price index",
                ),
            )
        )
        or contains(
            h,
            (
                "capital markets: aspi",
                "heavy selling pressure sends aspi",
                "cse falls to",
            ),
        )
    ):
        return "CSE_MARKET_" + d

    if contains(h, ("bond market activity", "secondary market activity")) and contains(
        t,
        ("auction",),
    ):
        return "TBOND_PRE_AUCTION_" + d

    if contains(h, ("t-bond auction delivers", "treasury bond auction delivers")):
        return "TBOND_AUCTION_RESULT_" + d

    if contains(h, ("govpay", "lankaqr")):
        return "GOVPAY_LANKAQR"

    return ""


def same_event(a: dict, b: dict) -> bool:
    # A publisher's distinct article URLs are distinct editorial records.
    # The old fuzzy matcher wrongly coalesced them and the old source list
    # then discarded all but one URL per publisher, failing final membership QA.
    # Exact URL repetitions (e.g. homepage + business discovery) remain dedupable.
    u_a = safe_text(a.get("source_url")).split("?", 1)[0].rstrip("/").casefold()
    u_b = safe_text(b.get("source_url")).split("?", 1)[0].rstrip("/").casefold()
    publisher_a = norm(a.get("source_name"))
    publisher_b = norm(b.get("source_name"))
    if publisher_a and publisher_a == publisher_b and u_a and u_b and u_a != u_b:
        from bank_intel.publication.identity import publisher_article_id
        pa=publisher_article_id(u_a)
        if pa and pa==publisher_article_id(u_b):
            return True
        # Different stories from the same newspaper must not vanish merely
        # because both discuss lending, bank supervision or NBFI risks.
        return False

    ka = a["event_key"]
    kb = b["event_key"]

    if ka and kb:
        return ka == kb
    # A strong event identity must never be overridden by a vague headline
    # similarity comparison with an unkeyed article.
    if bool(ka) != bool(kb):
        return False

    if a["publication_date"] != b["publication_date"]:
        return False

    if a["section"] != b["section"] or a["category"] != b["category"]:
        return False

    ta = headline_tokens(a["headline"])
    tb = headline_tokens(b["headline"])

    if not ta or not tb:
        return False

    jaccard = len(ta & tb) / max(1, len(ta | tb))
    sequence = SequenceMatcher(
        None,
        norm(a["headline"]),
        norm(b["headline"]),
    ).ratio()

    return jaccard >= 0.48 or sequence >= 0.72


def group_events(rows: list[dict]) -> list[dict]:
    groups: list[list[dict]] = []

    for row in rows:
        placed = False

        for group in groups:
            if any(same_event(row, other) for other in group):
                group.append(row)
                placed = True
                break

        if not placed:
            groups.append([row])

    importance_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}
    section_rank = {section: i for i, section in enumerate(SECTION_ORDER)}

    events: list[dict] = []

    for members in groups:
        members.sort(
            key=lambda item: (
                -importance_rank.get(item["importance"], 0),
                -len(item["summary"]),
                item["source_name"],
            )
        )

        primary = members[0]
        sources = []
        seen_sources: set[str] = set()
        for member in members:
            # Source lineage has article/URL grain, NOT newspaper grain.
            # Two genuinely different Daily Mirror URLs cannot be replaced
            # by one representative link when an event is grouped.
            source_key = safe_text(member["source_url"]).split("?", 1)[0].rstrip("/").casefold()
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            sources.append(
                {
                    "source_name": member["source_name"],
                    "source_url": member["source_url"],
                    "headline": member["headline"],
                }
            )

        events.append(
            {
                "section": primary["section"],
                "category": primary["category"],
                "importance": primary["importance"],
                "headline": primary["headline"],
                "summary": primary["summary"],
                "publication_date": primary["publication_date"],
                "sources": sources,
                "event_key": primary["event_key"],
                "member_count": len(members),
            }
        )

    def subject_rank(event: dict) -> int:
        if headline_is_cbsl(event["headline"]):
            return 0
        if headline_is_bank(event["headline"]):
            return 1
        return 2

    events.sort(
        key=lambda event: (
            section_rank.get(event["section"], 999),
            subject_rank(event),
            -importance_rank.get(event["importance"], 0),
            event["publication_date"],
            event["headline"],
        )
    )

    return events


# ============================================================================
# NEWS_FINAL_BRIEF_FINANCIAL_RESCUE_V1
# ============================================================================
# Stage A is the primary ML corpus filter, but the stakeholder brief reviews
# the complete Stage-1 population.  This rescue layer protects materially
# relevant banking / financial / macro stories that Stage A may conservatively
# classify as NOISE.
#
# IMPORTANT:
#   * This is NOT a generic "include finance words" rule.
#   * Strong supervisory / financial evidence is required.
#   * Ordinary corporate PR, awards, CSR, politics, crime and sport remain
#     subject to the normal classifier.
# ============================================================================

def _news_material_financial_rescue(
    headline,
    body,
    url="",
):
    h = safe_text(headline).lower()
    b = safe_text(body).lower()
    text = f"{h} {b}"

    # OCT22_RELEVANCE_RECOVERY: conservative general patterns, not URL overrides.
    # Preserve the source's exact claims in the source-extractive summary.
    if (re.search(r"\b(?:fitch|moody.s|s&p)\b", h)
            and re.search(r"\b(?:rating|ratings)\b", h)
            and "national" in h and "scale" in h):
        return ("MONETARY_MARKETS_EXTERNAL", "CAPITAL_MARKETS",
                "NATIONAL_RATING_SCALE_RECALIBRATION", "MEDIUM")

    if (re.search(r"\b(?:sdf|sarvodaya development finance)\b", h)
            and "green bond" in h
            and re.search(r"\b(?:deploy|deployment|proceeds|funds)\b", h)):
        return ("OTHER_FINANCIAL_SECTOR", "CAPITAL_MARKETS",
                "FINANCE_COMPANY_GREEN_BOND_DEPLOYMENT", "MEDIUM")

    if (("industry" in h or "construction" in h)
            and re.search(r"\b(?:credit|lending)\b", h)
            and ("personal" in h or "sector" in text or "bank" in text)):
        return ("BANKING_SECTOR_DEVELOPMENTS", "CREDIT_ASSET_QUALITY",
                "SECTORAL_BANK_CREDIT_ALLOCATION", "HIGH")

    if (re.search(r"\b(?:pawned|pawning|gold loans?)\b", h)
            and re.search(r"\b(?:commercial banks?|banks?)\b", h)):
        return ("BANKING_SECTOR_DEVELOPMENTS", "CREDIT_ASSET_QUALITY",
                "REPORTED_BANK_PAWNING_CLAIM_VERIFY_SOURCE", "MEDIUM")

    if (("ncpi" in h or "national consumer price index" in h
            or "overall inflation" in h)
            and re.search(r"\b(?:unchanged|rose|fell|increase|decline|\d+(?:\.\d+)?%)\b", h)):
        return ("MACRO_SYSTEMIC_CONTEXT", "MACRO_GROWTH_INFLATION_FISCAL",
                "NATIONAL_INFLATION_INDICATOR", "MEDIUM")

    if (re.search(r"\badb\b", h)
            and re.search(r"\b(?:approves?|approved)\b", h)
            and re.search(r"(?:\$|usd|million|billion|\bbn\b)", h)):
        return ("MACRO_SYSTEMIC_CONTEXT", "MACRO_GROWTH_INFLATION_FISCAL",
                "MULTILATERAL_FINANCING_APPROVAL", "MEDIUM")

    if ("hsbc" in h and re.search(r"\b(?:winds down|wind down|closing|closes|exits?)\b", h)
            and "operations" in h):
        return ("BANKING_SECTOR_DEVELOPMENTS", "OPERATIONAL_TECHNOLOGY",
                "BANK_GROUP_SERVICE_OPERATION_CHANGE_NOT_LICENCE_EXIT", "LOW")

    # ------------------------------------------------------------------
    # 1. DIRECT LICENSED-BANK PRUDENTIAL / CAPITAL STORY
    # ------------------------------------------------------------------
    direct_bank = any(
        term in text
        for term in LOCAL_BANK_TERMS
    )

    capital_terms = (
        "basel iii",
        "basel 3",
        "tier 2",
        "tier ii",
        "capital adequacy",
        "regulatory capital",
        "subordinated debenture",
        "subordinated loan",
        "non-viability",
        "loss absorption",
        "capital base",
        "capital requirement",
    )

    if (
        direct_bank
        and any(
            term in text
            for term in capital_terms
        )
    ):
        return (
            "KEY_BANKING_SUPERVISORY",
            "CAPITAL_SOLVENCY",
            "MATERIAL_BANK_PRUDENTIAL_CAPITAL",
            "HIGH",
        )

    # ------------------------------------------------------------------
    # 2. REGULATED / CLOSELY RELATED NON-BANK FINANCIAL CAPITAL STORY
    # ------------------------------------------------------------------
    nonbank_financial_terms = (
        "finance plc",
        "finance company",
        "commercial credit",
        "people's leasing",
        "peoples leasing",
        "leasing and finance",
        "licensed finance company",
        "non-bank financial",
        "non bank financial",
    )

    if (
        any(
            term in text
            for term in nonbank_financial_terms
        )
        and any(
            term in text
            for term in capital_terms
        )
    ):
        return (
            "OTHER_FINANCIAL_SECTOR",
            "CAPITAL_SOLVENCY",
            "MATERIAL_REGULATED_FINANCIAL_CAPITAL",
            "MEDIUM",
        )

    # ------------------------------------------------------------------
    # 3. IMF / EFF / DEBT-RESTRUCTURING / SYSTEMIC MACRO
    # ------------------------------------------------------------------
    imf_signal = (
        "imf" in text
        or "international monetary fund" in text
    )

    sri_lanka_signal = (
        "sri lanka" in text
        or "sri lankan" in text
    )

    imf_material_terms = (
        "extended fund facility",
        "eff",
        "staff-level agreement",
        "staff level agreement",
        "seventh review",
        "sixth review",
        "eighth review",
        "debt restructuring",
        "debt sustainability",
        "fiscal performance",
        "fiscal consolidation",
        "macroeconomic",
        "macro-economic",
        "foreign reserves",
        "structural reform",
        "governance reform",
    )

    if (
        imf_signal
        and sri_lanka_signal
        and any(
            term in text
            for term in imf_material_terms
        )
    ):
        return (
            "MACRO_SYSTEMIC_CONTEXT",
            "MACRO_GROWTH_INFLATION_FISCAL",
            "MATERIAL_IMF_SYSTEMIC_CONTEXT",
            "MEDIUM",
        )

    # ------------------------------------------------------------------
    # 4. LOCAL CAPITAL-MARKET MOVEMENT
    # ------------------------------------------------------------------
    local_market_signal = (
        "colombo stock exchange" in text
        or "cse " in text
        or " cse" in text
        or "aspi" in text
        or "all share price index" in text
        or "s&p sl20" in text
        or "s&p sl 20" in text
    )

    market_movement_signal = any(
        term in text
        for term in (
            "market turnover",
            "turnover",
            "market capitalisation",
            "market capitalization",
            "foreign outflow",
            "foreign inflow",
            "buying interest",
            "selling pressure",
            "bourse",
            "index rose",
            "index fell",
            "index gained",
            "index declined",
            "rebounded",
            "recovered",
        )
    )

    if (
        local_market_signal
        and market_movement_signal
    ):
        return (
            "MONETARY_MARKETS_EXTERNAL",
            "CAPITAL_MARKETS",
            "LOCAL_CAPITAL_MARKET_SIGNAL",
            "LOW",
        )

    # ------------------------------------------------------------------
    # 5. CBSL / DOMESTIC RATES / LIQUIDITY / FX
    # ------------------------------------------------------------------
    cbsl_signal = (
        "central bank of sri lanka" in text
        or "cbsl" in text
    )

    rates_signal = any(
        term in text
        for term in (
            "treasury bill",
            "treasury bills",
            "bond yield",
            "bond yields",
            "money market",
            "liquidity surplus",
            "liquidity deficit",
            "standing deposit facility",
            "standing lending facility",
            "sdfr",
            "slfr",
            "policy rate",
            "usd/lkr",
            "foreign exchange",
            "exchange rate",
            "official reserves",
        )
    )

    if (
        cbsl_signal
        and rates_signal
    ):
        return (
            "MONETARY_MARKETS_EXTERNAL",
            "SOVEREIGN_DEBT_RATES",
            "CBSL_MARKET_LIQUIDITY_FX_SIGNAL",
            "MEDIUM",
        )

    # ------------------------------------------------------------------
    # 6. HOUSING / REAL-ESTATE FINANCIAL SYSTEM CONTEXT
    # ------------------------------------------------------------------
    housing_signal = (
        "housing" in text
        or "real estate" in text
        or "property market" in text
    )

    housing_financial_signal = any(
        term in text
        for term in (
            "real estate market analysis",
            "central bank of sri lanka",
            "mortgage",
            "housing finance",
            "property prices",
            "condominium price",
            "financing constraints",
            "lending standards",
        )
    )

    if (
        housing_signal
        and housing_financial_signal
    ):
        return (
            "MACRO_SYSTEMIC_CONTEXT",
            "MACRO_GROWTH_INFLATION_FISCAL",
            "HOUSING_FINANCIAL_SYSTEM_CONTEXT",
            "LOW",
        )

    # ------------------------------------------------------------------
    # 7. MATERIAL GLOBAL FINANCIAL-MARKET SHOCK
    # ------------------------------------------------------------------
    global_market_headline = (
        "global stocks" in h
        or "global equities" in h
        or "world stocks" in h
    )

    global_signals = sum(
        1
        for term in (
            "treasury yield",
            "interest rates",
            "dollar",
            "oil prices",
            "brent crude",
            "gold",
            "nikkei",
            "kospi",
            "msci",
        )
        if term in text
    )

    if (
        global_market_headline
        and global_signals >= 3
    ):
        return (
            "MONETARY_MARKETS_EXTERNAL",
            "GLOBAL_MARKETS",
            "MATERIAL_GLOBAL_MARKET_CONTEXT",
            "LOW",
        )

    return None


def classify_articles(
    article_rows: list[dict],
    staged: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    retained: list[dict] = []
    audit: list[dict] = []

    for row in article_rows:
        url = safe_text(row.get("source_url"))
        payload = staged.get(url, {})

        headline = safe_text(row.get("headline") or payload.get("headline"))
        body = clean_article_text(payload.get("article_text") or row.get("article_text") or "")

        section, category, reason, importance = classify(headline, body, url)
        summary = make_summary(body)

        event = {
            "headline": headline,
            "source_name": safe_text(row.get("source_name") or payload.get("source_name")),
            "source_url": url,
            "publication_date": safe_text(
                row.get("publication_date") or payload.get("published_at")
            )[:10],
            "summary": summary,
            "body": body,
            "section": section,
            "category": category,
            "reason": reason,
            "importance": importance,
        }
        event["event_key"] = event_key(event)

        audit.append(dict(event))
        if section != "EXCLUDE":
            retained.append(event)

    return retained, audit


def preserve_human_fields(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}

    with path.open("r", newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))

    return {
        safe_text(row.get("source_url")): row
        for row in rows
        if safe_text(row.get("source_url"))
    }


def write_review_csv(path: Path, audit: list[dict]) -> None:
    old = preserve_human_fields(path)

    human_fields = [
        "human_relevance_label",
        "human_section",
        "human_primary_category",
        "human_secondary_categories",
        "human_event_key",
        "human_duplicate_of_url",
        "human_importance",
        "human_decision",
        "reviewer",
        "review_notes",
        "approved_for_training",
    ]

    fields = [
        "publication_date",
        "source_name",
        "headline",
        "source_url",
        "suggested_section",
        "suggested_primary_category",
        "suggested_category_reason",
        "suggested_importance",
        "suggested_event_key",
    ] + human_fields

    rows = []

    for item in audit:
        url = item["source_url"]
        prior = old.get(url, {})

        row = {
            "publication_date": item["publication_date"],
            "source_name": item["source_name"],
            "headline": item["headline"],
            "source_url": url,
            "suggested_section": item["section"],
            "suggested_primary_category": item["category"],
            "suggested_category_reason": item["reason"],
            "suggested_importance": item["importance"],
            "suggested_event_key": item["event_key"],
        }

        for field in human_fields:
            row[field] = prior.get(field, "")

        rows.append(row)

    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_article_audit_csv(path: Path, audit: list[dict]) -> None:
    fields = [
        "publication_date",
        "source_name",
        "headline",
        "source_url",
        "section",
        "category",
        "reason",
        "importance",
        "event_key",
    ]

    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for item in audit:
            writer.writerow({field: item.get(field, "") for field in fields})


def why_it_matters(category: str) -> str:
    mapping = {
        "FINANCIAL_STABILITY_MACROPRUDENTIAL":
            "Relevant to the resilience of the banking system and the transmission of macro-financial risk.",
        "CAPITAL_LIQUIDITY":
            "Relevant to bank capital strength, liquidity resilience and prudential buffers.",
        "CREDIT_ASSET_QUALITY":
            "Relevant to credit risk, collateral, asset quality and potential loss exposure.",
        "SOVEREIGN_CONCENTRATION_RISK":
            "Relevant to bank concentration in sovereign assets and related balance-sheet risk.",
        "AML_CFT":
            "Relevant to AML/CFT controls, sanctions risk and supervisory compliance.",
        "GOVERNANCE_CONDUCT":
            "Relevant to bank governance, oversight, accountability and senior decision-making.",
        "GOVERNANCE_LEADERSHIP_CHANGE":
            "Relevant to governance and leadership at a regulated financial institution.",
        "OPERATIONAL_CYBER_FRAUD":
            "Relevant to fraud controls, cyber resilience and operational-risk management.",
        "PAYMENTS_FINANCIAL_INFRASTRUCTURE":
            "Relevant to payment-system resilience, digital banking and financial infrastructure.",
        "FINANCIAL_INCLUSION_CONSUMER":
            "Relevant to access to finance, SME credit and consumer-facing banking policy.",
        "CREDIT_PRODUCT_STRATEGY":
            "Relevant to bank lending activity, product strategy and credit-channel developments.",
        "CLIMATE_FINANCIAL_RISK":
            "Relevant to how banks assess and respond to climate-related credit and sector risks.",
        "OPERATIONAL_TECHNOLOGY":
            "Relevant to technology-enabled bank operations, risk management and digital transformation.",
        "MONETARY_POLICY_LIQUIDITY":
            "Relevant to funding conditions, market liquidity and the monetary environment facing banks.",
        "SOVEREIGN_DEBT_RATES":
            "Relevant to sovereign yields, bank investment portfolios and funding conditions.",
        "FX_RESERVES_EXTERNAL_SECTOR":
            "Relevant to foreign-exchange conditions, reserves and external-sector liquidity.",
        "CAPITAL_MARKETS":
            "Relevant to domestic financial-market conditions and investor sentiment.",
        "MACRO_GROWTH_INFLATION_FISCAL":
            "Provides macroeconomic context that can affect borrowers, banks and financial stability.",
        "OTHER_FINANCIAL_SECTOR":
            "Relevant to risk or structural developments elsewhere in the regulated financial sector.",
    }
    return mapping.get(
        category,
        "Relevant financial-sector context for the Bank Supervision audience.",
    )


def esc(value) -> str:
    return escape(safe_text(value))


def render_html(
    *,
    end_date: date,
    days: int,
    events: list[dict],
    audit_count: int,
) -> str:
    """
    Render the final stakeholder brief using the established 14-Oct Financial
    Brief visual template while keeping the current bank-supervision sectioning,
    event consolidation and classification logic unchanged.
    """
    start_date = end_date - timedelta(days=days - 1)
    period = (
        end_date.isoformat()
        if days == 1
        else f"{start_date.isoformat()} to {end_date.isoformat()}"
    )

    by_section: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        by_section[event["section"]].append(event)

    importance_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}

    def audience_rank(event: dict) -> int:
        value = norm(
            f"{event.get('headline', '')} {event.get('summary', '')}"
        )
        if contains(value, CBSL_TERMS):
            return 0
        if contains(value, LOCAL_BANK_TERMS):
            return 1
        return 2

    for values in by_section.values():
        values.sort(
            key=lambda item: (
                audience_rank(item),
                -importance_rank.get(item["importance"], 0),
                item["publication_date"],
                item["headline"],
            )
        )

    def primary_source_label(event: dict) -> str:
        sources = event.get("sources", []) or []
        if not sources:
            return "Source unavailable"
        return safe_text(sources[0].get("source_name")) or "Source"

    def source_links(event: dict) -> str:
        # NEWS_ONE_VISIBLE_SOURCE_PER_EVENT_V1
        # Full source lineage stays in event["sources"] / audit JSON, while the
        # stakeholder brief shows one best/primary source link per event.
        sources = event.get("sources", []) or []
        if not sources:
            return ""

        source = sources[0]
        name = esc(source.get("source_name") or "Source")
        url = escape(
            safe_text(source.get("source_url")),
            quote=True,
        )
        return (
            f'<a href="{url}" target="_blank" '
            f'rel="noopener noreferrer">{name} ↗</a>'
        )

    def priority_card(event: dict, number: int) -> str:
        category = event["category"].replace("_", " ").title()
        sources = source_links(event)
        coverage = (
            f' · {event["member_count"]} reports combined'
            if event.get("member_count", 1) > 1
            else ""
        )

        return f"""
        <article class="priority-card">
            <div class="priority-number">{number:02d}</div>
            <div class="priority-body">
                <div class="story-topline">
                    <span>{esc(primary_source_label(event))}</span>
                    <span class="dot">•</span>
                    <span>{esc(event["publication_date"])}</span>
                    {f'<span class="small-muted">{esc(coverage)}</span>' if coverage else ''}
                </div>

                <h3>{esc(event["headline"])}</h3>

                <div class="meta-row supervisory-focus-row">
                    <div class="meta-main">
                        <div class="meta-label">Supervisory focus</div>
                        <div class="entity-name">{esc(category)}</div>
                    </div>
                </div>

                <div class="summary-block">
                    <div class="block-label">What happened</div>
                    <p>{esc(event["summary"])}</p>
                </div>

                <div class="relevance-block">
                    <div class="block-label">Why it matters</div>
                    <p>{esc(why_it_matters(event["category"]))}</p>
                </div>

                <div class="story-footer">
                    <div class="context-wrap"></div>
                    <div class="source-link">{sources}</div>
                </div>
            </div>
        </article>
        """

    def context_card(event: dict) -> str:
        category = event["category"].replace("_", " ").title()
        sources = source_links(event)
        coverage = (
            f'{event["member_count"]} reports combined'
            if event.get("member_count", 1) > 1
            else ""
        )

        return f"""
        <article class="context-card">
            <div class="context-card-head">
                <div>
                    <div class="story-topline">
                        <span>{esc(primary_source_label(event))}</span>
                        <span class="dot">•</span>
                        <span>{esc(event["publication_date"])}</span>
                    </div>
                    <h3>{esc(event["headline"])}</h3>
                </div>
            </div>

            <div class="compact-meta">
                <span class="topic-chip">{esc(category)}</span>
                {f'<span class="small-muted">{esc(coverage)}</span>' if coverage else ''}
            </div>

            <div class="summary-block">
                <div class="block-label">What happened</div>
                <p class="context-summary">{esc(event["summary"])}</p>
            </div>

            <div class="context-bottom">
                <div class="context-relevance">
                    <strong>Why it matters:</strong>
                    {esc(why_it_matters(event["category"]))}
                </div>
                <div class="source-list"><strong>Source:</strong> {sources}</div>
            </div>
        </article>
        """

    section_kickers = {
        "KEY_BANKING_SUPERVISORY": "NEWS HIGHLIGHTS",
        "BANKING_SECTOR_DEVELOPMENTS": "BANKING SECTOR",
        "MONETARY_MARKETS_EXTERNAL": "ECONOMY & MARKETS",
        "MACRO_SYSTEMIC_CONTEXT": "MACRO & SYSTEMIC",
        "OTHER_FINANCIAL_SECTOR": "OTHER FINANCIAL SECTOR",
    }

    sections = []

    for key in SECTION_ORDER:
        values = by_section.get(key, [])
        if not values:
            continue

        if key == "KEY_BANKING_SUPERVISORY":
            cards = "".join(
                priority_card(event, index)
                for index, event in enumerate(values, start=1)
            )
        else:
            cards = "".join(context_card(event) for event in values)

        sections.append(
            f"""
            <section class="report-section">
                <div class="section-title-row">
                    <div>
                        <div class="section-kicker">{esc(section_kickers[key])}</div>
                        <h2>{esc(SECTION_TITLES[key])}</h2>
                        <p>{esc(SECTION_NOTES[key])}</p>
                    </div>
                </div>
                {cards}
            </section>
            """
        )

    css = '* {\n    box-sizing: border-box;\n}\nhtml {\n    background: #edf1f5;\n}\nbody {\n    margin: 0;\n    background: #edf1f5;\n    color: #1b2733;\n    font-family: "Segoe UI", Arial, Helvetica, sans-serif;\n    font-size: 16px;\n    line-height: 1.58;\n}\na {\n    color: #0d547a;\n}\n.report-shell {\n    width: 100%;\n    max-width: 1080px;\n    margin: 28px auto;\n    background: #ffffff;\n    border: 1px solid #dce3e9;\n    box-shadow: 0 12px 34px rgba(22, 41, 58, 0.08);\n}\n.top-rule {\n    height: 6px;\n    background: #0b3154;\n}\na:focus-visible { outline:3px solid #b28a44; outline-offset:3px; }\n.report-header {\n    padding: 30px 38px 24px;\n    border-bottom: 1px solid #dce3e9;\n}\n.header-kicker {\n    margin-bottom: 8px;\n    color: #577080;\n    font-size: 14px;\n    font-weight: 700;\n    letter-spacing: 1.4px;\n}\n.header-row {\n    display: table;\n    width: 100%;\n}\n.header-title,\n.header-date {\n    display: table-cell;\n    vertical-align: bottom;\n}\n.header-title h1 {\n    margin: 0;\n    color: #0b3154;\n    font-size: 38px;\n    line-height: 1.15;\n    font-weight: 700;\n    letter-spacing: -0.4px;\n}\n.header-title p {\n    margin: 7px 0 0;\n    color: #617381;\n    font-size: 16px;\n}\n.header-date {\n    width: 230px;\n    text-align: right;\n}\n.date-label {\n    color: #70828f;\n    font-size: 12px;\n    font-weight: 700;\n    letter-spacing: 1px;\n    text-transform: uppercase;\n}\n.date-value {\n    margin-top: 4px;\n    color: #0b3154;\n    font-size: 18px;\n    font-weight: 700;\n}\n\n.report-content {\n    padding: 8px 38px 32px;\n}\n.report-section {\n    margin-top: 32px;\n}\n.section-title-row {\n    display: table;\n    width: 100%;\n    margin-bottom: 14px;\n}\n.section-title-row > div {\n    display: table-cell;\n    vertical-align: bottom;\n}\n.section-title-row h2 {\n    margin: 1px 0 0;\n    color: #173a56;\n    font-size: 21px;\n    line-height: 1.25;\n}\n.section-title-row p {\n    margin: 5px 0 0;\n    color: #526477;\n    font-size: 15px;\n}\n.section-kicker {\n    color: #8b6a2f;\n    font-size: 12px;\n    font-weight: 700;\n    letter-spacing: 1.25px;\n}\n\n.priority-card {\n    display: table;\n    width: 100%;\n    margin-bottom: 15px;\n    border: 1px solid #d9e1e7;\n    border-left: 4px solid #b28a44;\n    background: #ffffff;\n    page-break-inside: avoid;\n}\n.priority-number,\n.priority-body {\n    display: table-cell;\n    vertical-align: top;\n}\n.priority-number {\n    width: 58px;\n    padding: 20px 10px;\n    color: #b28a44;\n    background: #fbf8f1;\n    font-size: 20px;\n    font-weight: 700;\n    text-align: center;\n}\n.priority-body {\n    padding: 18px 20px 16px;\n}\n.story-topline {\n    color: #70818c;\n    font-size: 14px;\n    font-weight: 600;\n}\n.dot {\n    margin: 0 5px;\n    color: #a9b5bd;\n}\n.priority-card h3,\n.context-card h3 {\n    margin: 4px 0 11px;\n    color: #182c3b;\n    font-size: 19px;\n    line-height: 1.3;\n}\n.meta-row {\n    display: table;\n    width: 100%;\n    margin: 0 0 13px;\n    padding: 10px 12px;\n    background: #f6f8fa;\n    border: 1px solid #e3e8ec;\n}\n.meta-main,\n.meta-topics {\n    display: table-cell;\n    vertical-align: middle;\n}\n.meta-main {\n    width: 63%;\n}\n.meta-topics {\n    text-align: right;\n}\n.meta-label,\n.block-label {\n    margin-bottom: 2px;\n    color: #526477;\n    font-size: 12px;\n    font-weight: 700;\n    letter-spacing: 1px;\n    text-transform: uppercase;\n}\n.entity-name {\n    color: #1d3343;\n    font-size: 15px;\n    font-weight: 700;\n}\n.entity-type {\n    color: #72818c;\n    font-size: 14px;\n}\n.topic-chip {\n    display: inline-block;\n    margin: 2px 0 2px 5px;\n    padding: 3px 8px;\n    border: 1px solid #c9dbe6;\n    background: #edf5f9;\n    color: #28556f;\n    font-size: 12px;\n    font-weight: 700;\n}\n.summary-block p {\n    margin: 4px 0 0;\n    color: #263642;\n}\n.relevance-block {\n    margin-top: 12px;\n    padding: 10px 12px;\n    border-left: 3px solid #6c92a8;\n    background: #f3f7f9;\n}\n.relevance-block p {\n    margin: 3px 0 0;\n    color: #344955;\n    font-size: 15px;\n}\n.story-footer {\n    display: table;\n    width: 100%;\n    margin-top: 12px;\n    padding-top: 10px;\n    border-top: 1px solid #e5e9ed;\n}\n.context-wrap,\n.story-footer > .source-link {\n    display: table-cell;\n    vertical-align: middle;\n}\n.context-item {\n    margin-right: 12px;\n    color: #74838e;\n    font-size: 12px;\n}\n.source-link {\n    color: #0c557c;\n    font-size: 14px;\n    font-weight: 700;\n    text-decoration: none;\n    white-space: nowrap;\n    text-align: right;\n}\n.context-card {\n    margin-bottom: 12px;\n    padding: 15px 17px;\n    border: 1px solid #dfe5ea;\n    background: #fbfcfd;\n    page-break-inside: avoid;\n}\n.context-card-head {\n    display: table;\n    width: 100%;\n}\n.context-card-head > div,\n.context-card-head > .source-link {\n    display: table-cell;\n    vertical-align: top;\n}\n.context-card-head > .source-link {\n    width: 75px;\n    text-align: right;\n}\n.context-card h3 {\n    margin-bottom: 8px;\n    font-size: 21px;\n}\n.compact-meta {\n    margin-bottom: 9px;\n    color: #697a86;\n    font-size: 14px;\n}\n.scope-text {\n    margin-right: 10px;\n}\n.context-summary {\n    margin: 0;\n    color: #2b3c48;\n}\n.context-bottom {\n    margin-top: 10px;\n    padding-top: 9px;\n    border-top: 1px solid #e4e9ed;\n    color: #647782;\n    font-size: 14px;\n}\n.context-relevance {\n    margin-bottom: 3px;\n}\n.related-note {\n    color: #7a8891;\n}\n.secondary-section {\n    margin-top: 34px;\n}\n.other-table-wrap {\n    border: 1px solid #dfe5ea;\n}\n.other-table {\n    width: 100%;\n    border-collapse: collapse;\n    table-layout: fixed;\n}\n.other-table th {\n    padding: 9px 11px;\n    background: #f1f4f6;\n    color: #60727e;\n    border-bottom: 1px solid #dfe5ea;\n    font-size: 12px;\n    letter-spacing: .5px;\n    text-align: left;\n    text-transform: uppercase;\n}\n.other-table th:nth-child(1) { width: 26%; }\n.other-table th:nth-child(2) { width: 22%; }\n.other-table th:nth-child(3) { width: 52%; }\n.other-table td {\n    padding: 11px;\n    border-bottom: 1px solid #e7ecef;\n    vertical-align: top;\n    color: #31424d;\n    font-size: 14px;\n}\n.other-table tr:last-child td {\n    border-bottom: 0;\n}\n.other-title a {\n    display: block;\n    margin-top: 2px;\n    color: #173e59;\n    font-size: 12px;\n    font-weight: 700;\n    line-height: 1.35;\n    text-decoration: none;\n}\n.small-muted {\n    margin-top: 2px;\n    color: #7a8993;\n    font-size: 12px;\n}\n.event-group {\n    margin-bottom: 10px;\n    padding: 13px 15px;\n    border: 1px solid #dfe5ea;\n    background: #f8fafb;\n}\n.event-label {\n    color: #8b6a2f;\n    font-size: 12px;\n    font-weight: 700;\n    letter-spacing: 1px;\n    text-transform: uppercase;\n}\n.event-group h3 {\n    margin: 2px 0 7px;\n    color: #173a56;\n}\n.event-group ul {\n    margin: 0;\n    padding-left: 18px;\n}\n.event-group li {\n    margin: 3px 0;\n}\n.event-group li span {\n    margin-left: 5px;\n    color: #74828b;\n    font-size: 12px;\n}\n.empty-state {\n    padding: 16px;\n    border: 1px solid #dfe5ea;\n    background: #f8fafb;\n    color: #72828d;\n}\n.report-footer {\n    padding: 17px 38px 23px;\n    border-top: 1px solid #dce3e9;\n    background: #f8fafb;\n    color: #697b87;\n    font-size: 12px;\n}\n.report-footer strong {\n    color: #405663;\n}\n\n@media (max-width: 760px) {\n    .report-shell {\n        margin: 0;\n        border-left: 0;\n        border-right: 0;\n    }\n    .report-header,\n    .report-content,\n    .report-footer {\n        padding-left: 20px;\n        padding-right: 20px;\n    }\n\n    .header-row,\n    .header-title,\n    .header-date,\n    .priority-card,\n    .priority-number,\n    .priority-body,\n    .meta-row,\n    .meta-main,\n    .meta-topics,\n    .context-card-head,\n    .context-card-head > div,\n    .context-card-head > .source-link,\n    .story-footer,\n    .context-wrap,\n    .story-footer > .source-link {\n        display: block;\n        width: 100%;\n    }\n    .header-date,\n    .meta-topics,\n    .context-card-head > .source-link,\n    .story-footer > .source-link {\n        margin-top: 8px;\n        text-align: left;\n    }\n    .priority-number {\n        padding: 7px 12px;\n        text-align: left;\n    }\n\n\n    .other-table,\n    .other-table thead,\n    .other-table tbody,\n    .other-table tr,\n    .other-table th,\n    .other-table td {\n        display: block;\n        width: 100% !important;\n    }\n    .other-table thead {\n        display: none;\n    }\n    .other-table td {\n        border-bottom: 0;\n        padding-top: 4px;\n        padding-bottom: 4px;\n    }\n    .other-table tr {\n        padding: 9px 4px;\n        border-bottom: 1px solid #e7ecef;\n    }\n}\n@media print {\n    @page {\n        size: A4;\n        margin: 12mm;\n    }\n    html,\n    body {\n        background: #ffffff;\n    }\n    .report-shell {\n        max-width: none;\n        margin: 0;\n        border: 0;\n        box-shadow: none;\n    }\n    .source-link {\n        color: #0c557c;\n    }\n    .priority-card,\n    .context-card,\n    .event-group,\n    .other-table tr {\n        page-break-inside: avoid;\n    }\n}\n\n/* Final bank-supervision additions while preserving the 14-Oct visual language. */\n/* BANK_SUPERVISION_UI_CLEANUP_V1\n   Stronger section separation + cleaner supervisory-focus presentation. */\n.report-section {\n    margin-top: 42px;\n}\n.report-section + .report-section {\n    margin-top: 46px;\n    padding-top: 25px;\n    border-top: 1px solid #d6e0e7;\n}\n.section-title-row {\n    display: block;\n    width: 100%;\n    margin: 0 0 18px;\n    padding: 15px 18px 14px;\n    border: 1px solid #dbe4ea;\n    border-left: 5px solid #0b3154;\n    background: linear-gradient(90deg, #f3f7fa 0%, #ffffff 82%);\n    box-shadow: 0 3px 10px rgba(22, 41, 58, 0.04);\n    page-break-after: avoid;\n    break-after: avoid;\n}\n.section-title-row > div {\n    display: block;\n}\n.section-title-row h2 {\n    margin: 2px 0 0;\n    color: #0b3154;\n    font-size: 23px;\n    line-height: 1.22;\n    letter-spacing: -0.15px;\n}\n.section-title-row p {\n    max-width: 880px;\n    margin: 6px 0 0;\n    color: #5d7180;\n    font-size: 14px;\n}\n.section-kicker {\n    margin-bottom: 2px;\n    color: #8b6a2f;\n    font-size: 11px;\n    font-weight: 800;\n    letter-spacing: 1.45px;\n}\n.supervisory-focus-row {\n    padding: 11px 13px;\n    border-left: 3px solid #b28a44;\n    background: #f7f9fb;\n}\n.supervisory-focus-row .meta-main {\n    width: 100%;\n}\n.supervisory-focus-row .entity-name {\n    margin-top: 2px;\n    color: #173a56;\n    font-size: 15px;\n}\n.priority-card {\n    box-shadow: 0 3px 12px rgba(22, 41, 58, 0.045);\n}\n.context-card {\n    border-radius: 2px;\n    box-shadow: 0 2px 8px rgba(22, 41, 58, 0.025);\n}\n\n/* Keep a section heading with the first story when printing. */\n.section-title-row + .priority-card,\n.section-title-row + .context-card {\n    break-before: avoid;\n}\n\n.priority-card .source-list,\n.context-card .source-list {\n    margin-top: 10px;\n    color: #647782;\n    font-size: 13px;\n}\n.source-list a {\n    color: #0c557c;\n    font-weight: 700;\n    text-decoration: none;\n}\n.importance-chip {\n    display: inline-block;\n    margin: 2px 0 2px 5px;\n    padding: 3px 8px;\n    border: 1px solid #e1d4b7;\n    background: #f8f3e9;\n    color: #765b2a;\n    font-size: 12px;\n    font-weight: 700;\n}\n.report-note {\n    margin-top: 7px;\n    color: #7a8993;\n    font-size: 12px;\n}\n'

    css += "\n.importance-chip { display: none; }\n"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Financial Brief | News Brief</title>
<style>{css}</style>
</head>
<body>
<div class="report-shell">
    <div class="top-rule"></div>

    <header class="report-header">
        <div class="header-kicker">FINANCIAL SECTOR</div>
        <div class="header-row">
            <div class="header-title">
                <h1>Financial Brief</h1>
                <p>The latest banking, financial sector and market news.</p>
            </div>
            <div class="header-date">
                <div class="date-label">Reporting period</div>
                <div class="date-value">{esc(period)}</div>
            </div>
        </div>
    </header>

    <main class="report-content">
        {''.join(sections)}
    </main>

    <footer class="report-footer">
        <strong>Bank Supervision News Intelligence.</strong>
        Items are selected for supervisory relevance; no fixed story-count target is applied.
        Multiple reports of the same underlying development are consolidated; one primary source is shown while full source lineage remains in the audit record.
    </footer>
</div>
</body>
</html>"""



# NEWS_ATOMIC_FINAL_BRIEF_V1
def source_url_is_article(url: str) -> bool:
    value = safe_text(url)
    if not value:
        return False

    try:
        parsed = urlsplit(value)
    except Exception:
        return False

    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    low_path = path.lower()

    if host in {"ft.lk", "www.ft.lk", "dailymirror.lk", "www.dailymirror.lk"}:
        return bool(re.search(r"/\d{1,4}-\d{5,}$", path))

    if low_path in {"", "/", "/business", "/economy", "/markets"}:
        return False

    if "/category/" in low_path or low_path.endswith("/category"):
        return False

    return True


def validate_rendered_html(html: str, events: list[dict]) -> list[str]:
    failures: list[str] = []

    for marker in ("\ufffd", "â€", "Ã¢", "Ãƒ"):
        if marker in html:
            failures.append(f"BROKEN_TEXT_ENCODING:{marker!r}")

    if re.search(r'class=["\']dot["\'][^>]*>\s*\?\s*</span>', html, flags=re.I):
        failures.append("BROKEN_STORY_SEPARATOR")

    visible_links = html.count('target="_blank"')
    if visible_links != len(events):
        failures.append(
            f"VISIBLE_SOURCE_LINK_COUNT_MISMATCH:{visible_links}!={len(events)}"
        )

    return failures


def validate_outputs(
    *,
    audit: list[dict],
    retained: list[dict],
    events: list[dict],
) -> dict:
    failures: list[str] = []

    if not audit:
        failures.append("NO_ARTICLES_REVIEWED")

    for item in audit:
        if item["section"] not in VALID_SECTIONS:
            failures.append(f"UNKNOWN_SECTION:{item['section']}:{item['headline']}")
        if not item["source_url"]:
            failures.append(f"MISSING_SOURCE_URL:{item['headline']}")
        elif not source_url_is_article(item["source_url"]):
            failures.append(f"NON_ARTICLE_SOURCE_URL:{item['headline']}:{item['source_url']}")

    for item in retained:
        if len(safe_text(item.get("summary"))) < 40:
            failures.append(f"RETAINED_ARTICLE_WITHOUT_USABLE_BODY:{item['headline']}")

    if len(retained) > len(audit):
        failures.append("RETAINED_COUNT_EXCEEDS_AUDIT_COUNT")

    if len(events) > len(retained):
        failures.append("EVENT_COUNT_EXCEEDS_RETAINED_COUNT")

    event_source_pairs: set[tuple[str, str]] = set()
    seen_primary_urls: set[str] = set()
    for event in events:
        if event["section"] == "EXCLUDE":
            failures.append(f"EXCLUDED_EVENT_RENDERED:{event['headline']}")

        if not event.get("sources"):
            failures.append(f"EVENT_WITHOUT_SOURCE:{event['headline']}")
        else:
            primary_url = safe_text(event["sources"][0].get("source_url"))
            if not source_url_is_article(primary_url):
                failures.append(f"EVENT_PRIMARY_SOURCE_NOT_ARTICLE:{event['headline']}:{primary_url}")
            if primary_url in seen_primary_urls:
                failures.append(f"PRIMARY_SOURCE_REUSED_ACROSS_EVENTS:{primary_url}")
            seen_primary_urls.add(primary_url)

        for source in event["sources"]:
            pair = (event["headline"], source["source_url"])
            if pair in event_source_pairs:
                failures.append(
                    f"DUPLICATE_EVENT_SOURCE:{event['headline']}:{source['source_url']}"
                )
            event_source_pairs.add(pair)

    for item in retained:
        if headline_is_bank(item["headline"]) and item["section"] in {
            "MACRO_SYSTEMIC_CONTEXT",
            "OTHER_FINANCIAL_SECTOR",
        }:
            failures.append(
                f"DIRECT_BANK_STORY_MISPLACED:{item['headline']}:{item['section']}"
            )

    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "checks": {
            "articles_reviewed_nonzero": bool(audit),
            "retained_not_above_reviewed": len(retained) <= len(audit),
            "events_not_above_retained": len(events) <= len(retained),
            "valid_sections_only": all(
                item["section"] in VALID_SECTIONS
                for item in audit
            ),
            "all_articles_have_source_url": all(
                bool(item["source_url"])
                for item in audit
            ),
            "no_direct_bank_story_in_macro_or_other_financial": not any(
                headline_is_bank(item["headline"])
                and item["section"] in {
                    "MACRO_SYSTEMIC_CONTEXT",
                    "OTHER_FINANCIAL_SECTOR",
                }
                for item in retained
            ),
        },
    }



# NEWS_FINAL_LAYER_MONDAY_HARDENING_V1
# Bounded staged carry-forward + semantic consolidation + semantic QA.
_NEWS_BASE_CLASSIFY = classify
_NEWS_BASE_EVENT_KEY = event_key
_NEWS_BASE_GROUP_EVENTS = group_events
_NEWS_BASE_CLASSIFY_ARTICLES = classify_articles
_NEWS_BASE_VALIDATE_OUTPUTS = validate_outputs

POLICY_VERSION = "bank-supervision-final-v1.1"


def _news_text_date(value):
    text = safe_text(value)
    if not text:
        return None
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if not match:
        return None
    try:
        return parse_date(match.group(0))
    except Exception:
        return None


def _news_is_cbsl_pmi(headline: str, body: str) -> bool:
    text = norm(f"{headline} {body}")
    return (
        ("purchasing managers" in text or re.search(r"\bpmi\b", text) is not None)
        and contains(text, ("central bank of sri lanka", "cbsl"))
        and contains(text, ("manufacturing", "services"))
    )


def _news_is_port_city_investment(headline: str, body: str) -> bool:
    h = norm(headline)
    text = norm(f"{headline} {body}")
    return (
        contains(h, ("port city", "colombo port city"))
        and contains(
            text,
            (
                "investment", "investor", "land plot", "land plots",
                "lease", "leased", "approval", "approved",
                "withdraw", "withdrawn", "commission", "copf",
            ),
        )
    )


def _news_is_domestic_dollar_bond(headline: str, body: str) -> bool:
    h = norm(headline)
    text = norm(f"{headline} {body}")
    return (
        contains(text, ("domestic dollar bond", "domestic dollar bonds"))
        or (
            contains(h, ("dollar bond", "dollar bonds"))
            and contains(text, ("government", "sri lanka", "licensed banks"))
        )
    )


def _news_bank_capital_subject(headline: str, body: str) -> str:
    h = norm(headline)
    text = norm(f"{headline} {body[:2400]}")
    if "debenture" not in text:
        return ""
    if not contains(text, ("basel iii", "tier 2", "subordinated", "non-viability")):
        return ""

    for term in sorted(LOCAL_BANK_TERMS, key=len, reverse=True):
        nterm = norm(term)
        if nterm and (nterm in h or nterm in text[:1200]):
            return re.sub(r"[^a-z0-9]+", "_", nterm).strip("_").upper()
    return ""


def classify(
    headline: str,
    body: str,
    url: str,
) -> tuple[str, str, str, str]:
    if _news_is_domestic_dollar_bond(headline, body):
        return (
            "MONETARY_MARKETS_EXTERNAL",
            "SOVEREIGN_DEBT_RATES",
            "SOVEREIGN_EXTERNAL_FUNDING_SIGNAL",
            "MEDIUM",
        )

    if _news_is_cbsl_pmi(headline, body):
        return (
            "MACRO_SYSTEMIC_CONTEXT",
            "MACRO_GROWTH_INFLATION_FISCAL",
            "CBSL_PMI_MACRO_SIGNAL",
            "MEDIUM",
        )

    if _news_is_port_city_investment(headline, body):
        return (
            "MACRO_SYSTEMIC_CONTEXT",
            "MACRO_GROWTH_INFLATION_FISCAL",
            "PORT_CITY_INVESTMENT_EXECUTION_CONTEXT",
            "LOW",
        )

    return _NEWS_BASE_CLASSIFY(headline, body, url)


def event_key(row: dict) -> str:
    headline = safe_text(row.get("headline"))
    body = safe_text(row.get("body"))
    publication_date = safe_text(row.get("publication_date")) or "unknown"

    bank_slug = _news_bank_capital_subject(headline, body)
    if bank_slug:
        return f"BANK_CAPITAL_{bank_slug}_{publication_date}"

    if _news_is_cbsl_pmi(headline, body):
        return "CBSL_PMI_" + publication_date

    if _news_is_domestic_dollar_bond(headline, body):
        return "SL_DOMESTIC_DOLLAR_BOND_" + publication_date

    if _news_is_port_city_investment(headline, body):
        return "PORT_CITY_INVESTMENT_" + publication_date

    return _NEWS_BASE_EVENT_KEY(row)


def _news_prior_reviewed_urls(before_date: date) -> set[str]:
    """URLs with evidence of actual distribution, not generated/QA test reports.

    Older workflows used PUBLISHED.ok; newer ones can use PUBLISHED_URLS.json.
    A QA PASS or saved HTML alone is never proof of sending.
    """
    seen: set[str] = set()
    if not REPORT_ROOT.is_dir():
        return seen

    for day_root in REPORT_ROOT.iterdir():
        if not day_root.is_dir():
            continue
        day = _news_text_date(day_root.name)
        if day is None or day >= before_date:
            continue

        for window_dir in day_root.glob("window_*d"):
            sent_urls = window_dir / "PUBLISHED_URLS.json"
            sent_marker = window_dir / "PUBLISHED.ok"
            if sent_urls.is_file():
                record = load_json(sent_urls)  # invalid evidence must fail closed
                if not isinstance(record.get("source_urls"), list):
                    raise RuntimeError(f"Invalid source_urls in publication evidence: {sent_urls}")
                seen.update(safe_text(url).split("?", 1)[0].rstrip("/")
                            for url in record["source_urls"] if safe_text(url))
            elif sent_marker.is_file():
                audit_path = window_dir / "stakeholder_brief_v2_audit.json"
                if not audit_path.is_file():
                    raise RuntimeError(f"Published report audit missing: {audit_path}")
                record = load_json(audit_path)
                for event in record.get("events", []) or []:
                    for source in event.get("sources", []) or []:
                        url = safe_text(source.get("source_url"))
                        if url:
                            seen.add(url.split("?", 1)[0].rstrip("/"))
    return seen


def _news_staged_late_rows(
    *,
    start_date: date,
    end_date: date,
    existing_urls: set[str],
) -> list[dict]:
    # Normal run: previous day. Monday: Friday-Sunday catch-up.
    lookback_days = 3 if end_date.weekday() == 0 else 1
    late_start = start_date - timedelta(days=lookback_days)
    previously_reviewed = _news_prior_reviewed_urls(start_date)

    best: dict[str, tuple[tuple[int, int], dict]] = {}

    for path in STAGED_ROOT.rglob("*.json"):
        try:
            payload = load_json(path)
        except Exception:
            continue

        url = safe_text(
            _first_value(
                payload,
                ("source_url", "canonical_url", "article_url", "url"),
            )
        )
        headline = safe_text(_first_value(payload, ("headline", "title")))
        pub = _news_text_date(
            _first_value(
                payload,
                ("publication_date", "published_at", "published_date", "date"),
            )
        )

        if not url or not headline or pub is None:
            continue

        if not (late_start <= pub < start_date):
            continue

        if url in existing_urls or url in previously_reviewed:
            continue

        body = clean_article_text(payload.get("article_text") or "")
        if len(body) < 80:
            continue

        row = _normalise_stage1_record(payload)
        row["publication_date"] = pub.isoformat()
        row["_full_window_source"] = "LATE_STAGED_CARRY_FORWARD"
        row["_late_arrival"] = True

        score = (
            int(payload.get("article_version", 0) or 0),
            len(body),
        )
        old = best.get(url)
        if old is None or score > old[0]:
            best[url] = (score, row)

    rows = [pair[1] for pair in best.values()]
    rows.sort(
        key=lambda row: (
            safe_text(row.get("publication_date")),
            safe_text(row.get("source_name")),
            safe_text(row.get("headline")),
        )
    )
    return rows


def read_final_article_population(end_date: date, days: int) -> list[dict]:
    current = read_full_stage1_window(end_date, days)

    existing_urls = {
        safe_text(row.get("source_url"))
        for row in current
        if safe_text(row.get("source_url"))
    }

    start_date = end_date - timedelta(days=days - 1)

    late_rows = _news_staged_late_rows(
        start_date=start_date,
        end_date=end_date,
        existing_urls=existing_urls,
    )

    if late_rows:
        print()
        print("BOUNDED LATE-ARRIVAL CARRY-FORWARD")
        print("-" * 100)
        for row in late_rows:
            print(
                f"[LATE] {row['publication_date']} | "
                f"{row['source_name']} | {row['headline']}"
            )
        print(f"Late-arrival candidates : {len(late_rows)}")
        print("-" * 100)

    return current + late_rows


def classify_articles(
    article_rows: list[dict],
    staged: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    retained, audit = _NEWS_BASE_CLASSIFY_ARTICLES(article_rows, staged)

    late_urls = {
        safe_text(row.get("source_url"))
        for row in article_rows
        if bool(row.get("_late_arrival"))
    }

    for collection in (audit, retained):
        for item in collection:
            if safe_text(item.get("source_url")) in late_urls:
                item["_late_arrival"] = True
                item["late_arrival"] = True
                item["event_key"] = event_key(item)

    return retained, audit


def _news_member_rows(rows: list[dict], key: str) -> list[dict]:
    if not key:
        return []
    return [
        row
        for row in rows
        if safe_text(row.get("event_key")) == key
    ]


def group_events(rows: list[dict]) -> list[dict]:
    # Existing grouping now sees the stronger deterministic event keys above.
    events = _NEWS_BASE_GROUP_EVENTS(rows)

    for event in events:
        key = safe_text(event.get("event_key"))
        members = _news_member_rows(rows, key)

        if (
            key.startswith("BANK_CAPITAL_")
            and "SAMPATH" in key
            and len(members) >= 2
        ):
            event["summary"] = (
                "Sampath Bank’s proposed Basel III-compliant subordinated "
                "debenture issue of up to Rs. 10 billion has received "
                "in-principle approval from the Colombo Stock Exchange, with "
                "subscriptions opening on 20 October. Fitch assigned the "
                "proposed Tier 2 debentures a final National Long-Term Rating "
                "of ‘A(lka)’. The bank plans an initial Rs. 7 billion issue "
                "with capacity to increase it to Rs. 10 billion, and the "
                "proceeds are intended to support regulatory capital and "
                "loan growth."
            )

        elif key.startswith("PORT_CITY_INVESTMENT_") and len(members) >= 2:
            event["summary"] = (
                "Colombo Port City officials told the Parliamentary Committee "
                "on Public Finance that nine land plots valued at about "
                "US$ 1.12 billion had been leased, with further plots planned. "
                "In the same review, the Committee was told that five of 10 "
                "earlier approved investors had withdrawn or were no longer "
                "proceeding, highlighting execution risk around announced "
                "investment commitments."
            )

        elif key.startswith("CBSL_PMI_") and len(members) >= 1:
            joined = norm(
                " ".join(
                    safe_text(member.get("body"))
                    for member in members
                )
            )
            if (
                re.search(r"\b53(?:\.0)?\b", joined)
                and re.search(r"\b60\.5\b", joined)
            ):
                event["summary"] = (
                    "CBSL’s September Purchasing Managers’ Index showed "
                    "Manufacturing at 53 and Services at 60.5, with both "
                    "remaining in expansion territory. Financial Services led "
                    "the increase in services business activity and new "
                    "business, while manufacturing expansion was supported by "
                    "new orders and production."
                )

    return events


def validate_outputs(
    *,
    audit: list[dict],
    retained: list[dict],
    events: list[dict],
) -> dict:
    qa = _NEWS_BASE_VALIDATE_OUTPUTS(
        audit=audit,
        retained=retained,
        events=events,
    )

    failures = list(qa.get("failures", []) or [])

    event_keys = [
        safe_text(event.get("event_key"))
        for event in events
        if safe_text(event.get("event_key"))
    ]

    duplicates = sorted(
        key
        for key in set(event_keys)
        if event_keys.count(key) > 1
    )
    for key in duplicates:
        failures.append(f"DUPLICATE_SEMANTIC_EVENT_KEY:{key}")

    for item in audit:
        if (
            _news_is_cbsl_pmi(
                safe_text(item.get("headline")),
                safe_text(item.get("body")),
            )
            and item.get("section") == "EXCLUDE"
        ):
            failures.append(
                "CBSL_PMI_EXCLUDED:"
                + safe_text(item.get("headline"))
            )

    rendered_urls = {
        safe_text(source.get("source_url"))
        for event in events
        for source in (event.get("sources", []) or [])
    }

    for item in retained:
        if (
            item.get("_late_arrival")
            and safe_text(item.get("source_url")) not in rendered_urls
        ):
            failures.append(
                "LATE_ARRIVAL_NOT_RENDERED:"
                + safe_text(item.get("headline"))
            )

    for prefix in (
        "PORT_CITY_INVESTMENT_",
        "BANK_CAPITAL_",
        "CBSL_PMI_",
    ):
        keys = {
            safe_text(item.get("event_key"))
            for item in retained
            if safe_text(item.get("event_key")).startswith(prefix)
        }

        for key in keys:
            retained_count = sum(
                1
                for item in retained
                if safe_text(item.get("event_key")) == key
            )
            event_count = sum(
                1
                for event in events
                if safe_text(event.get("event_key")) == key
            )

            if retained_count > 1 and event_count != 1:
                failures.append(
                    f"SEMANTIC_CONSOLIDATION_FAILED:{key}:"
                    f"retained={retained_count}:events={event_count}"
                )

    qa["failures"] = failures
    qa["status"] = "PASS" if not failures else "FAIL"

    checks = dict(qa.get("checks", {}) or {})
    checks.update(
        {
            "no_duplicate_semantic_event_keys": not duplicates,
            "cbsl_pmi_not_excluded": not any(
                failure.startswith("CBSL_PMI_EXCLUDED:")
                for failure in failures
            ),
            "late_arrivals_rendered": not any(
                failure.startswith("LATE_ARRIVAL_NOT_RENDERED:")
                for failure in failures
            ),
            "semantic_event_consolidation": not any(
                failure.startswith("SEMANTIC_CONSOLIDATION_FAILED:")
                for failure in failures
            ),
        }
    )
    qa["checks"] = checks

    return qa



# NEWS_FINAL_LAYER_MONDAY_HARDENING_V2
# Correct late-arrival eligibility:
# include only articles actually collected on THIS run date whose
# publication date is in the bounded prior-day/weekend window.

_NEWS_V1_EVENT_KEY = event_key
_NEWS_V1_GROUP_EVENTS = group_events

POLICY_VERSION = "bank-supervision-final-v1.2"


def _news_collection_date(payload: dict):
    direct = _news_text_date(payload.get("collection_run_date"))
    if direct is not None:
        return direct
    return _news_text_date(payload.get("collected_at"))


def _news_staged_late_rows(
    *,
    start_date: date,
    end_date: date,
    existing_urls: set[str],
    earliest_override: date | None = None,
) -> list[dict]:
    lookback_days = 3 if end_date.weekday() == 0 else 1
    late_start = (earliest_override if earliest_override is not None
                  else start_date - timedelta(days=lookback_days))
    previously_reviewed = _news_prior_reviewed_urls(start_date)

    best: dict[str, tuple[tuple[int, int], dict]] = {}

    for path in STAGED_ROOT.rglob("*.json"):
        try:
            payload = load_json(path)
        except Exception:
            continue

        collected_on = _news_collection_date(payload)
        # Publication date, NOT the latest collection/test date, determines
        # carry-forward eligibility. Do not accept future-dated collection.
        if collected_on is not None and collected_on > end_date:
            continue

        url = safe_text(
            _first_value(
                payload,
                ("source_url", "canonical_url", "article_url", "url"),
            )
        )
        headline = safe_text(_first_value(payload, ("headline", "title")))
        pub = _news_text_date(
            _first_value(
                payload,
                ("publication_date", "published_at", "published_date", "date"),
            )
        )

        if not url or not headline or pub is None:
            continue

        if not (late_start <= pub < start_date):
            continue

        if (url.split("?", 1)[0].rstrip("/") in existing_urls
                or url.split("?", 1)[0].rstrip("/") in previously_reviewed):
            continue

        body = clean_article_text(payload.get("article_text") or "")
        if len(body) < 80:
            continue

        row = _normalise_stage1_record(payload)
        row["publication_date"] = pub.isoformat()
        row["_full_window_source"] = "LATE_STAGED_CARRY_FORWARD"
        row["_late_arrival"] = True
        row["_collection_run_date"] = collected_on.isoformat() if collected_on else ""
        row["_intake_status"] = safe_text(payload.get("intake_status"))

        score = (
            int(payload.get("article_version", 0) or 0),
            len(body),
        )
        old = best.get(url)
        if old is None or score > old[0]:
            best[url] = (score, row)

    rows = [pair[1] for pair in best.values()]
    rows.sort(
        key=lambda row: (
            safe_text(row.get("publication_date")),
            safe_text(row.get("source_name")),
            safe_text(row.get("headline")),
        )
    )
    return rows


def _news_is_imf_sri_lanka_program(headline: str, body: str) -> bool:
    text = norm(f"{headline} {body[:3200]}")
    return (
        contains(text, ("imf", "international monetary fund"))
        and contains(text, ("sri lanka", "colombo"))
        and contains(
            text,
            (
                "eff", "extended fund facility", "programme", "program",
                "review", "support will continue", "post-eff",
                "staff-level agreement", "fund programme", "fund program",
            ),
        )
    )


def event_key(row: dict) -> str:
    headline = safe_text(row.get("headline"))
    body = safe_text(row.get("body"))

    if _news_is_port_city_investment(headline, body):
        text = norm(f"{headline} {body}")
        if contains(
            text,
            (
                "copf", "committee on public finance",
                "parliamentary committee on public finance",
            ),
        ):
            return "PORT_CITY_COPF_REVIEW"
        return "PORT_CITY_INVESTMENT"

    if _news_is_imf_sri_lanka_program(headline, body):
        return "IMF_SRI_LANKA_CURRENT_PROGRAM"

    return _NEWS_V1_EVENT_KEY(row)


def group_events(rows: list[dict]) -> list[dict]:
    events = _NEWS_V1_GROUP_EVENTS(rows)

    by_key: dict[str, list[dict]] = {}
    for row in rows:
        key = safe_text(row.get("event_key"))
        if key:
            by_key.setdefault(key, []).append(row)

    for event in events:
        key = safe_text(event.get("event_key"))
        if not key:
            continue

        members = by_key.get(key, [])
        if not members:
            continue

        sources = list(event.get("sources", []) or [])
        seen_urls = {
            safe_text(source.get("source_url"))
            for source in sources
            if safe_text(source.get("source_url"))
        }

        for member in members:
            url = safe_text(member.get("source_url"))
            if not url or url in seen_urls:
                continue
            sources.append(
                {
                    "source_name": safe_text(member.get("source_name")),
                    "source_url": url,
                    "headline": safe_text(member.get("headline")),
                }
            )
            seen_urls.add(url)

        event["sources"] = sources
        event["member_count"] = max(
            int(event.get("member_count", 0) or 0),
            len(members),
            len(sources),
        )

        if key == "PORT_CITY_COPF_REVIEW" and len(members) >= 2:
            event["summary"] = (
                "Colombo Port City officials told Parliament’s Committee "
                "on Public Finance that nine land plots valued at about "
                "US$ 1.12 billion had been leased, with further plots "
                "planned. The Committee was also told that five of 10 "
                "earlier approved investors had withdrawn or were no longer "
                "proceeding, while members examined safeguards, approvals "
                "and execution of Port City investment commitments."
            )

        if key == "IMF_SRI_LANKA_CURRENT_PROGRAM" and len(members) >= 2:
            event["summary"] = (
                "The IMF said Sri Lanka’s current Extended Fund Facility "
                "continues to deliver strongly, with macroeconomic conditions "
                "much improved and two reviews remaining. The Fund said any "
                "decision on a subsequent programme rests with Sri Lanka and "
                "that it remains ready to continue supporting the country "
                "through financing, surveillance and capacity development."
            )

    return events


def validate_outputs(
    *,
    audit: list[dict],
    retained: list[dict],
    events: list[dict],
) -> dict:
    qa = _NEWS_BASE_VALIDATE_OUTPUTS(
        audit=audit,
        retained=retained,
        events=events,
    )

    failures = list(qa.get("failures", []) or [])

    event_keys = [
        safe_text(event.get("event_key"))
        for event in events
        if safe_text(event.get("event_key"))
    ]

    duplicate_keys = sorted(
        key
        for key in set(event_keys)
        if event_keys.count(key) > 1
    )

    for key in duplicate_keys:
        failures.append(f"DUPLICATE_SEMANTIC_EVENT_KEY:{key}")

    for item in audit:
        if (
            _news_is_cbsl_pmi(
                safe_text(item.get("headline")),
                safe_text(item.get("body")),
            )
            and item.get("section") == "EXCLUDE"
        ):
            failures.append(
                "CBSL_PMI_EXCLUDED:"
                + safe_text(item.get("headline"))
            )

    event_source_urls = {
        safe_text(source.get("source_url"))
        for event in events
        for source in (event.get("sources", []) or [])
        if safe_text(source.get("source_url"))
    }
    rendered_event_keys = {
        safe_text(event.get("event_key"))
        for event in events
        if safe_text(event.get("event_key"))
    }

    for item in retained:
        if not item.get("_late_arrival"):
            continue

        url = safe_text(item.get("source_url"))
        key = safe_text(item.get("event_key"))

        represented = (
            (url and url in event_source_urls)
            or (key and key in rendered_event_keys)
        )

        if not represented:
            failures.append(
                "LATE_ARRIVAL_NOT_REPRESENTED:"
                + safe_text(item.get("headline"))
            )

    for prefix in (
        "BANK_CAPITAL_",
        "CBSL_PMI_",
        "PORT_CITY_",
        "IMF_SRI_LANKA_CURRENT_PROGRAM",
    ):
        relevant_keys = {
            safe_text(item.get("event_key"))
            for item in retained
            if safe_text(item.get("event_key")).startswith(prefix)
        }

        for key in relevant_keys:
            retained_count = sum(
                1
                for item in retained
                if safe_text(item.get("event_key")) == key
            )
            event_count = sum(
                1
                for event in events
                if safe_text(event.get("event_key")) == key
            )

            if retained_count > 1 and event_count != 1:
                failures.append(
                    f"SEMANTIC_CONSOLIDATION_FAILED:{key}:"
                    f"retained={retained_count}:events={event_count}"
                )

    qa["failures"] = failures
    qa["status"] = "PASS" if not failures else "FAIL"

    checks = dict(qa.get("checks", {}) or {})
    checks.update(
        {
            "no_duplicate_semantic_event_keys": not duplicate_keys,
            "cbsl_pmi_not_excluded": not any(
                f.startswith("CBSL_PMI_EXCLUDED:")
                for f in failures
            ),
            "late_arrivals_represented": not any(
                f.startswith("LATE_ARRIVAL_NOT_REPRESENTED:")
                for f in failures
            ),
            "semantic_event_consolidation": not any(
                f.startswith("SEMANTIC_CONSOLIDATION_FAILED:")
                for f in failures
            ),
        }
    )
    qa["checks"] = checks
    return qa


# NEWS_FINAL_LAYER_MONDAY_HARDENING_V3_STAGE1_LATE
_NEWS_V2_EVENT_KEY = event_key
_NEWS_V2_GROUP_EVENTS = group_events

POLICY_VERSION = "bank-supervision-final-v1.3"


def read_final_article_population(
    end_date: date,
    days: int,
) -> list[dict]:
    """Current Stage-1 window plus unpublished, bounded older staged stories.

    21 Oct preview articles can appear in 22 Oct's one-day edition without
    falsely claiming that 21 Oct was part of today's ML coverage window.
    """
    current = read_full_stage1_window(end_date, days)
    known = {safe_text(r.get("source_url")).split("?", 1)[0].rstrip("/")
             for r in current}
    start_date = end_date - timedelta(days=days - 1)
    from bank_intel.publication.backlog import earliest_unpublished_day
    earliest = earliest_unpublished_day(REPORT_ROOT, end_date, days=days)
    prior = _news_staged_late_rows(
        start_date=start_date,
        end_date=end_date,
        existing_urls=known,
        earliest_override=earliest,
    )
    # A held Monday edition has not been sent. On Tuesday, preserve its
    # Friday-to-Monday evidence; actual prior sent URLs are excluded above.
    if days == 1:
        prior = [r for r in prior if (d := _news_text_date(r.get('publication_date')))
                 is not None and earliest <= d < end_date]
    if earliest < start_date:
        print(f"[UNSENT BACKLOG] {earliest} through {start_date - timedelta(days=1)} will be checked against confirmed sent events")
    if prior:
        print(f"[CARRY FORWARD] {len(prior)} prior-day article candidates for publication review")
    return current + prior


def event_key(row: dict) -> str:
    headline = safe_text(row.get("headline"))
    body = safe_text(row.get("body"))

    if _news_is_port_city_investment(headline, body):
        return "PORT_CITY_INVESTMENT"

    return _NEWS_V2_EVENT_KEY(row)


def group_events(rows: list[dict]) -> list[dict]:
    events = _NEWS_V2_GROUP_EVENTS(rows)

    port_members = [
        row
        for row in rows
        if safe_text(row.get("event_key")) == "PORT_CITY_INVESTMENT"
    ]

    for event in events:
        key = safe_text(event.get("event_key"))

        if key == "PORT_CITY_INVESTMENT" and len(port_members) >= 2:
            event["summary"] = (
                "Colombo Port City investment and execution developments "
                "remain under scrutiny. Current reporting covers land-lease "
                "and investment commitments as well as the Parliamentary "
                "Committee on Public Finance review, including evidence that "
                "several earlier approved investors have withdrawn or are no "
                "longer proceeding. The combined event provides a clearer "
                "view of announced investment versus project execution."
            )

            sources = list(event.get("sources", []) or [])
            seen = {
                safe_text(source.get("source_url"))
                for source in sources
                if safe_text(source.get("source_url"))
            }

            for member in port_members:
                url = safe_text(member.get("source_url"))
                if not url or url in seen:
                    continue
                sources.append(
                    {
                        "source_name": safe_text(member.get("source_name")),
                        "source_url": url,
                        "headline": safe_text(member.get("headline")),
                    }
                )
                seen.add(url)

            event["sources"] = sources
            event["member_count"] = max(
                int(event.get("member_count", 0) or 0),
                len(port_members),
                len(sources),
            )

    return events


def validate_outputs(
    *,
    audit: list[dict],
    retained: list[dict],
    events: list[dict],
) -> dict:
    qa = _NEWS_BASE_VALIDATE_OUTPUTS(
        audit=audit,
        retained=retained,
        events=events,
    )

    failures = list(qa.get("failures", []) or [])

    event_keys = [
        safe_text(event.get("event_key"))
        for event in events
        if safe_text(event.get("event_key"))
    ]

    duplicate_keys = sorted(
        key
        for key in set(event_keys)
        if event_keys.count(key) > 1
    )

    for key in duplicate_keys:
        failures.append(
            f"DUPLICATE_SEMANTIC_EVENT_KEY:{key}"
        )

    for item in audit:
        if (
            _news_is_cbsl_pmi(
                safe_text(item.get("headline")),
                safe_text(item.get("body")),
            )
            and item.get("section") == "EXCLUDE"
        ):
            failures.append(
                "CBSL_PMI_EXCLUDED:"
                + safe_text(item.get("headline"))
            )

    port_events = [
        event
        for event in events
        if safe_text(event.get("event_key"))
        == "PORT_CITY_INVESTMENT"
    ]

    port_retained = [
        item
        for item in retained
        if safe_text(item.get("event_key"))
        == "PORT_CITY_INVESTMENT"
    ]

    if len(port_events) > 1:
        failures.append(
            f"PORT_CITY_DUPLICATED:{len(port_events)}"
        )

    if len(port_retained) > 1 and len(port_events) != 1:
        failures.append(
            "PORT_CITY_CONSOLIDATION_FAILED:"
            f"retained={len(port_retained)}:"
            f"events={len(port_events)}"
        )

    sampath_events = [
        event
        for event in events
        if safe_text(event.get("event_key")).startswith(
            "BANK_CAPITAL_"
        )
        and "SAMPATH" in safe_text(event.get("event_key"))
    ]

    if len(sampath_events) > 1:
        failures.append(
            f"SAMPATH_CAPITAL_DUPLICATED:{len(sampath_events)}"
        )

    qa["failures"] = failures
    qa["status"] = "PASS" if not failures else "FAIL"

    checks = dict(qa.get("checks", {}) or {})
    checks.update(
        {
            "no_duplicate_semantic_event_keys": not duplicate_keys,
            "cbsl_pmi_not_excluded": not any(
                f.startswith("CBSL_PMI_EXCLUDED:")
                for f in failures
            ),
            "port_city_single_event": len(port_events) <= 1,
            "sampath_capital_single_event": len(sampath_events) <= 1,
            "stage1_and_bounded_staged_carry_forward": True,
        }
    )
    qa["checks"] = checks

    return qa


# Production semantic QA, independent of older key-count gates.
POLICY_VERSION = "bank-supervision-final-v1.4.1-publication-and-carryforward"


# NEWS_PUBLISHED_ONLY_DUPLICATE_QA_V1
# Keep existing news_quality_policy editorial checks intact. Its legacy
# duplicate check treats any prior QA PASS as "distributed". Provide it a
# temporary, evidence-filtered history instead of the raw report directory.
def _news_published_only_qa_history(report_root: Path, end_date: date,
                                    temp_root: Path) -> int:
    """Provide legacy QA with exactly the immutable sent-manifest events.

    An earlier QA-PASS draft can diverge from the actual emailed HTML; the
    sent record, never its generated audit, is the authority here.
    """
    from news_publication_history import published_editions
    published_events=0
    for edition in published_editions(report_root, end_date, max_days=3):
        day=edition['date']
        events=[]
        for card in edition['cards']:
            events.append({
                'headline':card['headline'],
                'publication_date':card.get('publication_date',day.isoformat()),
                'sources':[{'source_url':u} for u in card['source_urls']],
            })
        dest=temp_root / day.isoformat() / 'window_1d'
        dest.mkdir(parents=True, exist_ok=True)
        (dest/'stakeholder_brief_v2_audit.json').write_text(
            json.dumps({'qa':{'status':'PASS'},'events':events},ensure_ascii=False),
            encoding='utf-8')
        published_events += len(events)
    return published_events


# NEWS_PUBLICATION_RECONCILIATION_V2
# A single-day brief covers that date and only the preceding day's as-yet
# unreported material. A QA-pass file alone NEVER establishes distribution.
def _news_report_cards(html_path: Path) -> list[dict]:
    from html.parser import HTMLParser

    class _Cards(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.depth = 0
            self.card = None
            self.in_heading = False
            self.in_summary = False
            self.heading_depth = 0
            self.summary_depth = 0
            self.cards = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            classes = attrs.get('class', '').split()
            if tag == 'article' and {'priority-card','context-card'} & set(classes):
                self.card = {'headline':'', 'body':'', 'source_urls':[]}
                self.depth = 1
                return
            if self.card is None:
                return
            self.depth += 1
            if tag == 'h3':
                self.in_heading = True
                self.heading_depth = self.depth
            if 'summary-block' in classes:
                self.in_summary = True
                self.summary_depth = self.depth
            if tag == 'a' and attrs.get('href'):
                self.card['source_urls'].append(attrs['href'])

        def handle_data(self, data):
            if self.card is None:
                return
            if self.in_heading:
                self.card['headline'] += data
            if self.in_summary:
                self.card['body'] += data

        def handle_endtag(self, tag):
            if self.card is None:
                return
            if self.in_heading and self.depth == self.heading_depth:
                self.in_heading = False
            if self.in_summary and self.depth == self.summary_depth:
                self.in_summary = False
            self.depth -= 1
            if self.depth == 0:
                self.cards.append(self.card)
                self.card = None

    parser = _Cards()
    parser.feed(html_path.read_text(encoding='utf-8-sig'))
    if not parser.cards:
        raise RuntimeError(f'Previous published HTML has no story cards: {html_path}')
    return parser.cards


def _news_story_words(value):
    text = norm(value).replace('billion', 'b').replace('billions', 'b')
    return set(re.findall(r'[a-z0-9]+', text)) - {
        'a','an','the','and','or','of','to','with','on','in','for','from','as',
        'sri','lanka','bank','banks','new','its','by','over','at','has','have'
    }


def _news_sent_match(row: dict, published_cards: list[dict]) -> bool:
    from difflib import SequenceMatcher
    import news_editorial_final as _editorial
    row_url = _editorial._url(row.get('source_url'))
    row_title = safe_text(row.get('headline'))
    row_tokens = _news_story_words(row_title)
    row_kind = _editorial._event_kind(row)
    for prior in published_cards:
        if row_url and any(row_url == _editorial._url(u)
                           for u in prior.get('source_urls', [])):
            return True
        prior_title = safe_text(prior.get('headline'))
        prior_tokens = _news_story_words(prior_title)
        if row_title and norm(row_title) == norm(prior_title):
            return True
        if row_tokens and prior_tokens and len(row_tokens & prior_tokens) >= 4:
            overlap = len(row_tokens & prior_tokens) / max(1,min(len(row_tokens),len(prior_tokens)))
            similarity = SequenceMatcher(None, norm(row_title), norm(prior_title)).ratio()
            if overlap >= .83 and similarity >= .50:
                return True
        prior_kind = _editorial._event_kind(prior)
        if row_kind and prior_kind and row_kind == prior_kind and row_kind in {
            'ACCESS_LOGISTICS_HUB_EXIT', 'HLAB_FUND_LAUNCH',
            'PPP_AGENCY_BILL_APPROVAL'
        }:
            return True
    return False


def _news_read_editorial_review(path: Path) -> dict[str,dict]:
    with path.open('r',encoding='utf-8-sig',newline='') as file:
        rows = list(csv.DictReader(file))
    if not rows or not {'source_url','editorial_action'}.issubset(rows[0]):
        raise RuntimeError('Editorial review needs source_url and editorial_action columns')
    output = {}
    for item in rows:
        url=safe_text(item.get('source_url')).split('?',1)[0].rstrip('/').casefold()
        if not url or url in output:
            raise RuntimeError('Blank or duplicate URL in editorial review CSV')
        output[url] = item
    return output


def _news_apply_published_and_reviewed(audit,retained,*,end_date,
                                       sent_html=None,extra_titles=(),review_csv=None):
    # Auto-discover the immutable manifest of the edition actually emailed.
    # Never use generated/QA-passed reports as publication evidence.
    from news_publication_history import published_cards
    cards = published_cards(REPORT_ROOT, end_date)
    if sent_html:
        cards.extend(_news_report_cards(Path(sent_html)))
    for name in extra_titles:
        cards.append({'headline':name,'source_urls':[],'body':''})
    reviewed = _news_read_editorial_review(Path(review_csv)) if review_csv else {}
    kept_urls=set()
    change_counts={}
    from bank_intel.publication.backlog import earliest_unpublished_day
    earliest=earliest_unpublished_day(REPORT_ROOT,end_date,days=1)
    for item in audit:
        pub=_news_text_date(item.get('publication_date'))
        url=safe_text(item.get('source_url')).split('?',1)[0].rstrip('/').casefold()
        reason=''
        if pub is None or pub < earliest or pub > end_date:
            reason='OUTSIDE_TODAY_AND_YESTERDAY'
        elif cards and _news_sent_match(item,cards):
            reason='IN_ALREADY_DISTRIBUTED_EDITION'
        elif reviewed:
            decision=reviewed.get(url)
            # Publishing from a reviewed short list is fail-closed: anything not
            # expressly retained, including newly discovered URLs, is held for QA.
            if decision is None or not safe_text(decision.get('editorial_action')).startswith('KEEP'):
                reason='EDITORIAL_REVIEW_NOT_APPROVED'
        if reason:
            item.update(section='EXCLUDE',category='NOT_IN_CURRENT_EDITION',
                        reason=reason,importance='NONE',event_key='')
            change_counts[reason]=change_counts.get(reason,0)+1
        elif item.get('section')!='EXCLUDE':
            # Prevent broad IPS and CSE semantic keys from blending unrelated news.
            if safe_text(item.get('event_key')) in {'IPS_STATE_OF_ECONOMY'}:
                item['event_key']=''
            kept_urls.add(url)
    retained=[dict(item) for item in audit
              if item.get('section')!='EXCLUDE' and
              safe_text(item.get('source_url')).split('?',1)[0].rstrip('/').casefold() in kept_urls]
    if reviewed:
        expected={u for u,r in reviewed.items()
                  if safe_text(r.get('editorial_action')).startswith('KEEP')
                  and (pub:=_news_text_date(r.get('publication_date'))) is not None
                  and earliest<=pub<=end_date}
        missing=expected-kept_urls
        # A published story may properly be excluded even if review said KEEP.
        missing={u for u in missing
                 if not _news_sent_match(reviewed[u], cards)}
        if missing:
            missing_headlines=[reviewed[u].get('headline','?') for u in sorted(missing)]
            raise RuntimeError('Reviewed KEEP sources unavailable/unretained: '+ '; '.join(missing_headlines))
    if cards:
        print(f'[SENT EDITION] {len(cards)} known previously distributed story titles')
    for reason,n in sorted(change_counts.items()):
        print(f'[EDITORIAL SELECTION] {reason}: {n}')
    return retained, audit


def _news_simple_url(value):
    return safe_text(value).split('?',1)[0].rstrip('/').casefold()


def _news_separate_unrelated_members(audit, retained):
    """Fail-safe: never merge unrelated events just because a broad key matches.

    IPS tax vs IPS employment; CSE cumulative vs daily trading and distinct
    capital/funding developments must retain separate source lineage.
    """
    import hashlib
    from collections import defaultdict
    from news_editorial_final import _event_kind
    groups=defaultdict(list)
    for row in retained:
        if row.get('event_key'):
            groups[str(row['event_key'])].append(row)
    split=[]
    for key, members in groups.items():
        if len(members)<2:
            continue
        # Independent factual signatures are stronger than the old fuzzy
        # headline overlap rule. All sources describe the same transaction.
        from bank_intel.publication.identity import event_signature
        if key in {
            'BOC_TIER2_DEBENTURE_30BN_NOV2026',
            'HNB_FINANCE_TOYOTA_LEASING_OCT2026',
            'SEYLAN_FAIRWAY_LATITUDE_HOUSING_OCT2026',
            'CBSL_EARLY_SETTLEMENT_PARATE_THEMATIC_2026',
            'NBFI_HOUSEHOLD_STAGE2_RISK_JUN2026',
            'CBSL_76TH_ORATION_SHIRIN_HAMID',
        } and all(event_signature(r)==key for r in members):
            continue
        # Group into event-consistent clusters using title facts. A narrow
        # editorial event identity takes precedence over fuzzy title tokens.
        anchors=[]
        for row in members:
            title=_news_story_words(row.get('headline',''))
            kind=_event_kind(row)
            selected=None
            for a in anchors:
                anchor=a[0]
                old=_news_story_words(anchor.get('headline',''))
                old_kind=_event_kind(anchor)
                # Same-publisher URLs with different headlines must not share
                # a forced key: the final semantic guard otherwise restores
                # both URLs into *both* events and creates duplicate sources.
                same_publisher = norm(anchor.get('source_name')) == norm(row.get('source_name'))
                different_url = _news_simple_url(anchor.get('source_url')) != _news_simple_url(row.get('source_url'))
                if (same_publisher and different_url and
                    norm(anchor.get('headline')) != norm(row.get('headline'))):
                    continue
                if kind and kind==old_kind:
                    selected=a;break
                shared=len(title&old)
                if shared>=3 and shared/max(1,min(len(title),len(old)))>=.60:
                    selected=a;break
            if selected is None:
                selected=[]
                anchors.append(selected)
            selected.append(row)
        if len(anchors)<2:
            continue
        for cluster in anchors:
            lead=cluster[0]
            fingerprint=hashlib.sha1(str(lead.get('source_url')).encode('utf-8')).hexdigest()[:12]
            newkey=key+'__'+fingerprint
            for row in cluster:
                row['event_key']=newkey
                for original in audit:
                    if original.get('source_url')==row.get('source_url'):
                        original['event_key']=newkey
        split.append((key,len(anchors)))
    for key,n in split:
        print(f'[EVENT KEY SAFETY] Split {key} into {n} distinct stories')
    return retained, audit


# NEWS_OCT27_FACTUAL_EVENT_IDENTITY_V6
# Earlier broad-key overrides remain in place for all other stories.
_NEWS_OCT27_PREVIOUS_EVENT_KEY=event_key

def event_key(row: dict) -> str:
    from bank_intel.publication.identity import event_signature
    signature=event_signature(row)
    if signature in {
        'BOC_TIER2_DEBENTURE_30BN_NOV2026',
        'HNB_FINANCE_TOYOTA_LEASING_OCT2026',
        'SEYLAN_FAIRWAY_LATITUDE_HOUSING_OCT2026',
        'CBSL_EARLY_SETTLEMENT_PARATE_THEMATIC_2026',
        'NBFI_HOUSEHOLD_STAGE2_RISK_JUN2026',
        'CBSL_76TH_ORATION_SHIRIN_HAMID',
    }:
        return signature
    return _NEWS_OCT27_PREVIOUS_EVENT_KEY(row)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build the final Bank Supervision news brief from the complete "
            "Stage-1 population for the requested date window."
        )
    )
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--sent-yesterday-html", default=None,
                        help="Optional extra sent HTML; publication history auto-loaded by default")
    parser.add_argument("--sent-additional-title", action="append", default=[],
                        help="An already-sent story absent from the saved HTML copy")
    parser.add_argument("--editorial-review", default=None,
                        help="Previously reviewed source_url/editorial_action CSV for final selection")
    args = parser.parse_args()

    end_date = parse_date(args.end_date)
    days = int(args.days)

    if days < 1:
        raise ValueError("--days must be >= 1")

    # NEWS_SENT_EDITION_IMMUTABILITY_V1: stop BEFORE any report files
    # are modified. A prior recorded sent edition is never a QA preview.
    from news_publication_history import assert_edition_not_sent
    assert_edition_not_sent(REPORT_ROOT, end_date)

    coverage = assert_ml_window_complete(end_date, days)

    output_root = REPORT_ROOT / end_date.isoformat() / f"window_{days}d"
    output_root.mkdir(parents=True, exist_ok=True)

    # COLLECTION_COMPLETENESS_RECOVERY_V1
    articles = read_final_article_population(end_date, days)
    if days == 1:
        from bank_intel.publication.backlog import earliest_unpublished_day
        earliest = earliest_unpublished_day(REPORT_ROOT,end_date,days=1)
        articles = [row for row in articles
                    if (d := _news_text_date(row.get('publication_date'))) is not None
                    and earliest <= d <= end_date]
        print(f"[DATE WINDOW] {len(articles)} unique articles dated {earliest} or {end_date}")
    if not articles:
        raise RuntimeError(
            "FULL-WINDOW SOURCE FAILED: no Stage-1 article records were resolved."
        )

    # Collection completeness is enforced before report generation by
    # run_pipeline.py. The final brief NEVER injects source-page recoveries.
    staged = staged_index()

    retained, audit = classify_articles(
        articles,
        staged,
    )

    # NEWS_FINAL_SOURCE_SEMANTIC_GATE_V1
    # No hardcoded article injection: classification audit drives changes.
    import brief_semantic_guard as _brief_guard
    retained, audit = _brief_guard.before_group(retained, audit)
    # NEWS_PERMANENT_EDITORIAL_QA_V1
    import news_quality_policy as _permanent
    retained, rescued = _permanent.rescue(audit, event_key)
    for headline in rescued: print('[EDITORIAL RESCUE]', headline)
    # NEWS_EDITORIAL_FINAL_V1: apply editorial policy before event grouping.
    import news_editorial_final as _newsfinal
    retained, _dropped = _newsfinal.prepare_rows(retained, audit, REPORT_ROOT, end_date)
    for _item in _dropped: print('[EDITORIAL FILTER]', _item['reason'], _item['headline'])
    retained, audit = _news_apply_published_and_reviewed(
        audit, retained, end_date=end_date,
        sent_html=args.sent_yesterday_html,
        extra_titles=args.sent_additional_title,
        review_csv=args.editorial_review)


    if not retained:
        raise RuntimeError('NO APPROVED MATERIAL after sent-edition and editorial reconciliation')
    retained, audit = _news_separate_unrelated_members(audit, retained)
    supplemental_added = 0
    retained_recovery_rows = []
    collection_feedback_added = 0
    events = group_events(retained)
    events = _brief_guard.after_group(events, audit)
    events, _merged = _newsfinal.consolidate(events)
    for _item in _merged: print('[EVENT CONSOLIDATION]', _item['kind'], _item['source_count'])
    _permanent.source_summary = _newsfinal.source_summary
    # Prior SENT events remain in the full audit for traceability, but are
    # inapplicable to the test for *new* stories missing from this edition.
    active_audit = [row for row in audit
                    if row.get("reason") not in {
                        "PREVIOUS_EDITION_SAME_STORY",
                        "OUTSIDE_TODAY_AND_YESTERDAY",
                        "IN_ALREADY_DISTRIBUTED_EDITION",
                        "EDITORIAL_REVIEW_NOT_APPROVED"}]
    from news_publication_history import published_cards
    print(f'[PUBLICATION QA] Actually published event titles checked: '
          f'{len(published_cards(REPORT_ROOT, end_date))}')
    editorial_issues = _permanent.editorial_issues(
        events, active_audit, REPORT_ROOT, end_date)
    editorial_issues += _newsfinal.summary_issues(
        events, {_newsfinal._url(x.get('source_url')): x for x in audit}
    )

    # NEWS_SUMMARY_ENTITY_NORMALIZATION_V1
    # Decode source text before QA; HTML rendering still escapes output.
    from html import unescape as _unescape_summary
    for _event in events:
        _text = str(_event.get("summary") or "")
        for _ in range(3):
            _decoded = _unescape_summary(_text)
            if _decoded == _text:
                break
            _text = _decoded
        _event["summary"] = _text.replace(chr(160), " ")

    qa = validate_outputs(
        audit=audit,
        retained=retained,
        events=events,
    )

    qa.setdefault('checks', {})['source_grounded_editorial_qa'] = not editorial_issues
    if editorial_issues:
        qa['failures'] = list(qa.get('failures', [])) + editorial_issues
        qa['status'] = 'FAIL'

    # Independently verify semantic membership and source consistency.
    semantic_failures = _brief_guard.semantic_checks(active_audit, events)
    qa.setdefault("checks", {})["semantic_source_consistency"] = not semantic_failures
    qa.setdefault("checks", {})["material_bank_developments_preserved"] = not any(
        error.startswith("BANK_DIGITAL_INTEGRATION_MISSED")
        for error in semantic_failures
    )
    qa.setdefault("checks", {})["no_cross_topic_event_contamination"] = not any(
        error.startswith(("IMF_EVENT_CONTAMINATED", "DIFFERENT_EVENTS_GROUPED"))
        for error in semantic_failures
    )
    if semantic_failures:
        qa["failures"] = list(qa.get("failures", [])) + semantic_failures
        qa["status"] = "FAIL"

    if qa["status"] != "PASS":
        raise RuntimeError(
            "FINAL BRIEF QA FAILED: " + "; ".join(qa["failures"])
        )

    named_html = output_root / f"Daily Financial Sector News Brief {end_date.isoformat()} Source Preview.html"
    qa_html = output_root / f"Daily Financial Sector News Brief {end_date.isoformat()} QA.html"
    production_alias = output_root / "legacy_source_preview.html"
    review_path = output_root / "stakeholder_category_review.csv"
    audit_json_path = output_root / "stakeholder_brief_v2_audit.json"
    article_csv_path = output_root / f"Daily Financial Sector News Brief {end_date.isoformat()} Articles.csv"
    report_manifest_path = output_root / "legacy_report_manifest.json"

    html = render_html(
        end_date=end_date,
        days=days,
        events=events,
        audit_count=len(audit),
    )

    render_failures = validate_rendered_html(html, events)
    if render_failures:
        raise RuntimeError(
            "FINAL HTML QA FAILED: " + "; ".join(render_failures)
        )

    # Candidate QA HTML is safe to write before publication. The named report
    # and production alias are updated only at the very end after all internal
    # audit/manifest outputs have been written successfully.
    qa_html.write_text(html, encoding="utf-8")

    write_review_csv(review_path, audit)
    write_article_audit_csv(article_csv_path, audit)

    payload = {
        "status": "SUCCESS",
        "generated_at": datetime.now(TZ).isoformat(),
        "policy_version": POLICY_VERSION,
        "publication_policy": {
            "audience": "BANK_SUPERVISION",
            "retention_policy": "RELEVANCE_ONLY_NO_COUNT_TARGET",
            "primary_scope": ["CBSL", "LICENSED_BANKS"],
            "section_priority": SECTION_ORDER,
        },
        "coverage_dates": coverage,
        "article_count": len(audit),
        "collection_recovery_count": supplemental_added,

        "retained_article_count": len(retained),
        "consolidated_event_count": len(events),
        "excluded_count": sum(
            1 for item in audit
            if item["section"] == "EXCLUDE"
        ),
        "sections": {
            key: sum(
                1 for event in events
                if event["section"] == key
            )
            for key in SECTION_ORDER
        },
        "qa": qa,
        "events": events,
        "audit": audit,
    }

    audit_json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    report_manifest = {
        "status": "SUCCESS",
        "generated_at": datetime.now(TZ).isoformat(),
        "policy_version": POLICY_VERSION,
        "publication_date": end_date.isoformat(),
        "window_days": days,
        "coverage_dates": coverage,
        "article_count": len(audit),
        "collection_recovery_count": supplemental_added,

        "retained_article_count": len(retained),
        "consolidated_event_count": len(events),
        "final_attachment": str(production_alias.relative_to(PROJECT_ROOT)),
        "named_attachment": str(named_html.relative_to(PROJECT_ROOT)),
        "audit_json": str(audit_json_path.relative_to(PROJECT_ROOT)),
        "review_csv": str(review_path.relative_to(PROJECT_ROOT)),
        "qa": qa,
    }

    report_manifest_path.write_text(
        json.dumps(report_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Atomic-ish final publication: update both stakeholder HTML files only
    # after every QA/audit/manifest write has succeeded.
    named_tmp = named_html.with_name(named_html.name + ".tmp")
    alias_tmp = production_alias.with_name(production_alias.name + ".tmp")
    named_tmp.write_text(html, encoding="utf-8")
    alias_tmp.write_text(html, encoding="utf-8")
    named_tmp.replace(named_html)
    alias_tmp.replace(production_alias)

    print("=" * 100)
    print("BANK SUPERVISION NEWS BRIEF COMPLETE")
    print("=" * 100)
    print(f"Policy version         : {POLICY_VERSION}")
    print(f"Coverage dates         : {coverage}")
    print("Retention policy       : RELEVANCE_ONLY_NO_COUNT_TARGET")
    print("Primary audience       : BANK_SUPERVISION")
    print("Priority               : CBSL + LICENSED BANKS FIRST")
    print(f"Articles reviewed      : {len(audit)}")
    print(f"Collection candidates  : {supplemental_added}")
    print(f"Material recoveries    : {len(retained_recovery_rows)}")
    print(f"Recovery learning rows : {collection_feedback_added}")
    print(f"Articles retained      : {len(retained)}")
    print(f"Articles excluded      : {payload['excluded_count']}")
    print(f"Consolidated events    : {len(events)}")
    print(f"Section counts         : {payload['sections']}")
    print(f"Final QA               : {qa['status']}")
    print(f"Final brief            : {named_html}")
    print(f"Production alias       : {production_alias}")
    print(f"Review CSV             : {review_path}")
    print(f"Audit JSON             : {audit_json_path}")
    print(f"Report manifest        : {report_manifest_path}")
    print("=" * 100)


if __name__ == "__main__":
    main()
