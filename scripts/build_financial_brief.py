from __future__ import annotations

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
    ka = a["event_key"]
    kb = b["event_key"]

    if ka and kb:
        return ka == kb

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
        events.append(
            {
                "section": primary["section"],
                "category": primary["category"],
                "importance": primary["importance"],
                "headline": primary["headline"],
                "summary": primary["summary"],
                "publication_date": primary["publication_date"],
                "sources": [
                    {
                        "source_name": member["source_name"],
                        "source_url": member["source_url"],
                        "headline": member["headline"],
                    }
                    for member in members
                ],
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
        body = clean_article_text(payload.get("article_text") or "")

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
        first = safe_text(sources[0].get("source_name")) or "Source"
        extra = len(sources) - 1
        return first if extra <= 0 else f"{first} + {extra} more"

    def source_links(event: dict) -> str:
        links = []
        for source in event.get("sources", []) or []:
            name = esc(source.get("source_name") or "Source")
            url = escape(
                safe_text(source.get("source_url")),
                quote=True,
            )
            links.append(
                f'<a href="{url}" target="_blank" '
                f'rel="noopener noreferrer">{name} ↗</a>'
            )
        return " · ".join(links)

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

                <div class="meta-row">
                    <div class="meta-main">
                        <div class="meta-label">Supervisory focus</div>
                        <div class="entity-name">{esc(category)}</div>
                    </div>
                    <div class="meta-topics">
                        <span class="topic-chip">{esc(category)}</span>
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

    css = '* {\n    box-sizing: border-box;\n}\nhtml {\n    background: #edf1f5;\n}\nbody {\n    margin: 0;\n    background: #edf1f5;\n    color: #1b2733;\n    font-family: "Segoe UI", Arial, Helvetica, sans-serif;\n    font-size: 16px;\n    line-height: 1.58;\n}\na {\n    color: #0d547a;\n}\n.report-shell {\n    width: 100%;\n    max-width: 1080px;\n    margin: 28px auto;\n    background: #ffffff;\n    border: 1px solid #dce3e9;\n    box-shadow: 0 12px 34px rgba(22, 41, 58, 0.08);\n}\n.top-rule {\n    height: 6px;\n    background: #0b3154;\n}\na:focus-visible { outline:3px solid #b28a44; outline-offset:3px; }\n.report-header {\n    padding: 30px 38px 24px;\n    border-bottom: 1px solid #dce3e9;\n}\n.header-kicker {\n    margin-bottom: 8px;\n    color: #577080;\n    font-size: 14px;\n    font-weight: 700;\n    letter-spacing: 1.4px;\n}\n.header-row {\n    display: table;\n    width: 100%;\n}\n.header-title,\n.header-date {\n    display: table-cell;\n    vertical-align: bottom;\n}\n.header-title h1 {\n    margin: 0;\n    color: #0b3154;\n    font-size: 38px;\n    line-height: 1.15;\n    font-weight: 700;\n    letter-spacing: -0.4px;\n}\n.header-title p {\n    margin: 7px 0 0;\n    color: #617381;\n    font-size: 16px;\n}\n.header-date {\n    width: 230px;\n    text-align: right;\n}\n.date-label {\n    color: #70828f;\n    font-size: 12px;\n    font-weight: 700;\n    letter-spacing: 1px;\n    text-transform: uppercase;\n}\n.date-value {\n    margin-top: 4px;\n    color: #0b3154;\n    font-size: 18px;\n    font-weight: 700;\n}\n\n.report-content {\n    padding: 8px 38px 32px;\n}\n.report-section {\n    margin-top: 32px;\n}\n.section-title-row {\n    display: table;\n    width: 100%;\n    margin-bottom: 14px;\n}\n.section-title-row > div {\n    display: table-cell;\n    vertical-align: bottom;\n}\n.section-title-row h2 {\n    margin: 1px 0 0;\n    color: #173a56;\n    font-size: 21px;\n    line-height: 1.25;\n}\n.section-title-row p {\n    margin: 5px 0 0;\n    color: #526477;\n    font-size: 15px;\n}\n.section-kicker {\n    color: #8b6a2f;\n    font-size: 12px;\n    font-weight: 700;\n    letter-spacing: 1.25px;\n}\n\n.priority-card {\n    display: table;\n    width: 100%;\n    margin-bottom: 15px;\n    border: 1px solid #d9e1e7;\n    border-left: 4px solid #b28a44;\n    background: #ffffff;\n    page-break-inside: avoid;\n}\n.priority-number,\n.priority-body {\n    display: table-cell;\n    vertical-align: top;\n}\n.priority-number {\n    width: 58px;\n    padding: 20px 10px;\n    color: #b28a44;\n    background: #fbf8f1;\n    font-size: 20px;\n    font-weight: 700;\n    text-align: center;\n}\n.priority-body {\n    padding: 18px 20px 16px;\n}\n.story-topline {\n    color: #70818c;\n    font-size: 14px;\n    font-weight: 600;\n}\n.dot {\n    margin: 0 5px;\n    color: #a9b5bd;\n}\n.priority-card h3,\n.context-card h3 {\n    margin: 4px 0 11px;\n    color: #182c3b;\n    font-size: 19px;\n    line-height: 1.3;\n}\n.meta-row {\n    display: table;\n    width: 100%;\n    margin: 0 0 13px;\n    padding: 10px 12px;\n    background: #f6f8fa;\n    border: 1px solid #e3e8ec;\n}\n.meta-main,\n.meta-topics {\n    display: table-cell;\n    vertical-align: middle;\n}\n.meta-main {\n    width: 63%;\n}\n.meta-topics {\n    text-align: right;\n}\n.meta-label,\n.block-label {\n    margin-bottom: 2px;\n    color: #526477;\n    font-size: 12px;\n    font-weight: 700;\n    letter-spacing: 1px;\n    text-transform: uppercase;\n}\n.entity-name {\n    color: #1d3343;\n    font-size: 15px;\n    font-weight: 700;\n}\n.entity-type {\n    color: #72818c;\n    font-size: 14px;\n}\n.topic-chip {\n    display: inline-block;\n    margin: 2px 0 2px 5px;\n    padding: 3px 8px;\n    border: 1px solid #c9dbe6;\n    background: #edf5f9;\n    color: #28556f;\n    font-size: 12px;\n    font-weight: 700;\n}\n.summary-block p {\n    margin: 4px 0 0;\n    color: #263642;\n}\n.relevance-block {\n    margin-top: 12px;\n    padding: 10px 12px;\n    border-left: 3px solid #6c92a8;\n    background: #f3f7f9;\n}\n.relevance-block p {\n    margin: 3px 0 0;\n    color: #344955;\n    font-size: 15px;\n}\n.story-footer {\n    display: table;\n    width: 100%;\n    margin-top: 12px;\n    padding-top: 10px;\n    border-top: 1px solid #e5e9ed;\n}\n.context-wrap,\n.story-footer > .source-link {\n    display: table-cell;\n    vertical-align: middle;\n}\n.context-item {\n    margin-right: 12px;\n    color: #74838e;\n    font-size: 12px;\n}\n.source-link {\n    color: #0c557c;\n    font-size: 14px;\n    font-weight: 700;\n    text-decoration: none;\n    white-space: nowrap;\n    text-align: right;\n}\n.context-card {\n    margin-bottom: 12px;\n    padding: 15px 17px;\n    border: 1px solid #dfe5ea;\n    background: #fbfcfd;\n    page-break-inside: avoid;\n}\n.context-card-head {\n    display: table;\n    width: 100%;\n}\n.context-card-head > div,\n.context-card-head > .source-link {\n    display: table-cell;\n    vertical-align: top;\n}\n.context-card-head > .source-link {\n    width: 75px;\n    text-align: right;\n}\n.context-card h3 {\n    margin-bottom: 8px;\n    font-size: 21px;\n}\n.compact-meta {\n    margin-bottom: 9px;\n    color: #697a86;\n    font-size: 14px;\n}\n.scope-text {\n    margin-right: 10px;\n}\n.context-summary {\n    margin: 0;\n    color: #2b3c48;\n}\n.context-bottom {\n    margin-top: 10px;\n    padding-top: 9px;\n    border-top: 1px solid #e4e9ed;\n    color: #647782;\n    font-size: 14px;\n}\n.context-relevance {\n    margin-bottom: 3px;\n}\n.related-note {\n    color: #7a8891;\n}\n.secondary-section {\n    margin-top: 34px;\n}\n.other-table-wrap {\n    border: 1px solid #dfe5ea;\n}\n.other-table {\n    width: 100%;\n    border-collapse: collapse;\n    table-layout: fixed;\n}\n.other-table th {\n    padding: 9px 11px;\n    background: #f1f4f6;\n    color: #60727e;\n    border-bottom: 1px solid #dfe5ea;\n    font-size: 12px;\n    letter-spacing: .5px;\n    text-align: left;\n    text-transform: uppercase;\n}\n.other-table th:nth-child(1) { width: 26%; }\n.other-table th:nth-child(2) { width: 22%; }\n.other-table th:nth-child(3) { width: 52%; }\n.other-table td {\n    padding: 11px;\n    border-bottom: 1px solid #e7ecef;\n    vertical-align: top;\n    color: #31424d;\n    font-size: 14px;\n}\n.other-table tr:last-child td {\n    border-bottom: 0;\n}\n.other-title a {\n    display: block;\n    margin-top: 2px;\n    color: #173e59;\n    font-size: 12px;\n    font-weight: 700;\n    line-height: 1.35;\n    text-decoration: none;\n}\n.small-muted {\n    margin-top: 2px;\n    color: #7a8993;\n    font-size: 12px;\n}\n.event-group {\n    margin-bottom: 10px;\n    padding: 13px 15px;\n    border: 1px solid #dfe5ea;\n    background: #f8fafb;\n}\n.event-label {\n    color: #8b6a2f;\n    font-size: 12px;\n    font-weight: 700;\n    letter-spacing: 1px;\n    text-transform: uppercase;\n}\n.event-group h3 {\n    margin: 2px 0 7px;\n    color: #173a56;\n}\n.event-group ul {\n    margin: 0;\n    padding-left: 18px;\n}\n.event-group li {\n    margin: 3px 0;\n}\n.event-group li span {\n    margin-left: 5px;\n    color: #74828b;\n    font-size: 12px;\n}\n.empty-state {\n    padding: 16px;\n    border: 1px solid #dfe5ea;\n    background: #f8fafb;\n    color: #72828d;\n}\n.report-footer {\n    padding: 17px 38px 23px;\n    border-top: 1px solid #dce3e9;\n    background: #f8fafb;\n    color: #697b87;\n    font-size: 12px;\n}\n.report-footer strong {\n    color: #405663;\n}\n\n@media (max-width: 760px) {\n    .report-shell {\n        margin: 0;\n        border-left: 0;\n        border-right: 0;\n    }\n    .report-header,\n    .report-content,\n    .report-footer {\n        padding-left: 20px;\n        padding-right: 20px;\n    }\n\n    .header-row,\n    .header-title,\n    .header-date,\n    .priority-card,\n    .priority-number,\n    .priority-body,\n    .meta-row,\n    .meta-main,\n    .meta-topics,\n    .context-card-head,\n    .context-card-head > div,\n    .context-card-head > .source-link,\n    .story-footer,\n    .context-wrap,\n    .story-footer > .source-link {\n        display: block;\n        width: 100%;\n    }\n    .header-date,\n    .meta-topics,\n    .context-card-head > .source-link,\n    .story-footer > .source-link {\n        margin-top: 8px;\n        text-align: left;\n    }\n    .priority-number {\n        padding: 7px 12px;\n        text-align: left;\n    }\n\n\n    .other-table,\n    .other-table thead,\n    .other-table tbody,\n    .other-table tr,\n    .other-table th,\n    .other-table td {\n        display: block;\n        width: 100% !important;\n    }\n    .other-table thead {\n        display: none;\n    }\n    .other-table td {\n        border-bottom: 0;\n        padding-top: 4px;\n        padding-bottom: 4px;\n    }\n    .other-table tr {\n        padding: 9px 4px;\n        border-bottom: 1px solid #e7ecef;\n    }\n}\n@media print {\n    @page {\n        size: A4;\n        margin: 12mm;\n    }\n    html,\n    body {\n        background: #ffffff;\n    }\n    .report-shell {\n        max-width: none;\n        margin: 0;\n        border: 0;\n        box-shadow: none;\n    }\n    .source-link {\n        color: #0c557c;\n    }\n    .priority-card,\n    .context-card,\n    .event-group,\n    .other-table tr {\n        page-break-inside: avoid;\n    }\n}\n\n/* Final bank-supervision additions while preserving the 14-Oct visual language. */\n.priority-card .source-list,\n.context-card .source-list {\n    margin-top: 10px;\n    color: #647782;\n    font-size: 13px;\n}\n.source-list a {\n    color: #0c557c;\n    font-weight: 700;\n    text-decoration: none;\n}\n.importance-chip {\n    display: inline-block;\n    margin: 2px 0 2px 5px;\n    padding: 3px 8px;\n    border: 1px solid #e1d4b7;\n    background: #f8f3e9;\n    color: #765b2a;\n    font-size: 12px;\n    font-weight: 700;\n}\n.report-note {\n    margin-top: 7px;\n    color: #7a8993;\n    font-size: 12px;\n}\n'

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
        Multiple reports of the same underlying development are consolidated and original source links are retained.
    </footer>
</div>
</body>
</html>"""


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

    if len(retained) > len(audit):
        failures.append("RETAINED_COUNT_EXCEEDS_AUDIT_COUNT")

    if len(events) > len(retained):
        failures.append("EVENT_COUNT_EXCEEDS_RETAINED_COUNT")

    event_source_pairs: set[tuple[str, str]] = set()
    for event in events:
        if event["section"] == "EXCLUDE":
            failures.append(f"EXCLUDED_EVENT_RENDERED:{event['headline']}")

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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build the final Bank Supervision news brief from the complete "
            "Stage-1 population for the requested date window."
        )
    )
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--days", type=int, default=1)
    args = parser.parse_args()

    end_date = parse_date(args.end_date)
    days = int(args.days)

    if days < 1:
        raise ValueError("--days must be >= 1")

    coverage = assert_ml_window_complete(end_date, days)

    output_root = REPORT_ROOT / end_date.isoformat() / f"window_{days}d"
    output_root.mkdir(parents=True, exist_ok=True)

    articles = read_full_stage1_window(end_date, days)
    if not articles:
        raise RuntimeError(
            "FULL-WINDOW SOURCE FAILED: no Stage-1 article records were resolved."
        )

    staged = staged_index()
    retained, audit = classify_articles(articles, staged)
    events = group_events(retained)

    qa = validate_outputs(
        audit=audit,
        retained=retained,
        events=events,
    )

    if qa["status"] != "PASS":
        raise RuntimeError(
            "FINAL BRIEF QA FAILED: " + "; ".join(qa["failures"])
        )

    named_html = output_root / f"Daily Financial Sector News Brief {end_date.isoformat()}.html"
    qa_html = output_root / f"Daily Financial Sector News Brief {end_date.isoformat()} QA.html"
    production_alias = output_root / "supervisory_digest.html"
    review_path = output_root / "stakeholder_category_review.csv"
    audit_json_path = output_root / "stakeholder_brief_v2_audit.json"
    article_csv_path = output_root / f"Daily Financial Sector News Brief {end_date.isoformat()} Articles.csv"
    report_manifest_path = output_root / "report_manifest.json"

    html = render_html(
        end_date=end_date,
        days=days,
        events=events,
        audit_count=len(audit),
    )

    for path in (named_html, qa_html, production_alias):
        path.write_text(html, encoding="utf-8")

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

    print("=" * 100)
    print("BANK SUPERVISION NEWS BRIEF COMPLETE")
    print("=" * 100)
    print(f"Policy version         : {POLICY_VERSION}")
    print(f"Coverage dates         : {coverage}")
    print("Retention policy       : RELEVANCE_ONLY_NO_COUNT_TARGET")
    print("Primary audience       : BANK_SUPERVISION")
    print("Priority               : CBSL + LICENSED BANKS FIRST")
    print(f"Articles reviewed      : {len(audit)}")
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
