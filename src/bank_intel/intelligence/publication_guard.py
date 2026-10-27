from __future__ import annotations

import re
from typing import Iterable

from bank_intel.intelligence.reporting import (
    DigestArticle,
    all_entities,
    calculate_supervisory_importance,
    display_institution_type,
    display_topic,
    has_primary_regulated_entity,
    primary_entity_details,
    safe_text,
    source_summary,
    supervisory_lens,
)

PUBLICATION_POLICY_VERSION = "stakeholder-bank-focus-v1"

LEGAL_POLITICAL_PATTERNS = (
    r"\bremand(?:ed)?\b",
    r"\bcourt\b",
    r"\bbail\b",
    r"\btrial[- ]at[- ]bar\b",
    r"\bprison\b",
    r"\bmurder\b",
    r"\barrest(?:ed)?\b",
    r"\bindictment\b",
    r"\bbribery case\b",
    r"\bcorruption case\b",
    r"\bformer first lady\b",
    r"\bopposition leader\b",
    r"\bsjb\b",
    r"\bjvp\b",
    r"\bslpp\b",
    r"\bmp\b",
    r"\bminister\b",
    r"\bmonk\b",
    r"\bclergy\b",
    r"\bdeath sentence\b",
)

NON_FINANCIAL_URL_MARKERS = (
    "/sports/",
    "/politics/",
    "/lawnorder/",
    "/law-order/",
    "/entertainment/",
)

LOW_VALUE_PR_TERMS = (
    "celebrates",
    "anniversary",
    "award",
    "awards",
    "customer service week",
    "official accommodation partner",
    "management trainee",
    "graduation",
    "campaign",
    "partnership",
    "partners with",
    "merchant ecosystem",
    "appointed",
    "appointment",
    "conference",
    "forum",
    "brings stakeholders together",
)

REGULATORY_ACTOR_TERMS = (
    "central bank",
    "cbsl",
    "financial intelligence unit",
    "fatf",
    "bank supervision",
    "banking act",
    "finance business act",
    "deposit insurance",
    "regulator",
    "regulatory authority",
)

SUPERVISORY_TERMS = (
    "financial stability",
    "capital adequacy",
    "capital buffer",
    "liquidity",
    "non-performing loan",
    "non performing loan",
    "npl",
    "stage 3",
    "provisioning",
    "prudential",
    "aml/cft",
    "anti-money laundering",
    "money laundering",
    "counter-terrorist financing",
    "counter terrorist financing",
    "kyc",
    "sanctions",
    "cybersecurity",
    "cyber security",
    "operational risk",
    "payment system",
    "payments system",
    "regulatory",
    "supervision",
    "compliance",
    "governance",
    "conduct risk",
)

BANK_FINANCE_TERMS = (
    " bank ",
    " bank.",
    " bank,",
    "banking",
    "finance company",
    "financial institution",
    "financial sector",
    "financial system",
    "insurance company",
    "insurer",
    "leasing",
    "credit",
    "deposit",
    "loan",
    "fintech",
    "digital finance",
    "digital payment",
    "payment system",
    "lankaqr",
    "govpay",
    "financial inclusion",
    "financial literacy",
)

MACRO_TERMS = (
    "imf",
    "international monetary fund",
    "world bank",
    "asian development bank",
    "adb",
    "gdp",
    "economic growth",
    "inflation",
    "sovereign debt",
    "debt restructuring",
    "debt sustainability",
    "fiscal",
    "current account",
    "foreign reserve",
    "foreign reserves",
    "balance of payments",
    "remittance",
    "remittances",
)

MARKET_TERMS = (
    "treasury bond",
    "t-bond",
    "bond market",
    "bond auction",
    "yield",
    "interest rate",
    "policy rate",
    "exchange rate",
    "foreign exchange",
    "forex",
    "rupee",
    "stock market",
    "cse",
    "aspi",
    "s&p sl20",
)

PAYMENTS_TERMS = (
    "lankaqr",
    "govpay",
    "digital payment",
    "payment system",
    "payments system",
    "fintech",
)

SRI_LANKA_TERMS = (
    "sri lanka",
    "sri lankan",
    "cbsl",
    "lanka",
    "colombo",
)

OFFICIAL_MACRO_TERMS = (
    "imf",
    "international monetary fund",
    "world bank",
    "asian development bank",
    "adb",
    "central bank",
    "cbsl",
    "fatf",
)


def _norm(value: str) -> str:
    text = safe_text(value).lower()
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    return " ".join(text.split())


def _contains(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _matches(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _article_text(article: DigestArticle) -> str:
    # Intentionally exclude inferred entity names. This prevents a person name
    # such as "Sampath" from turning a non-financial story into a bank story.
    return _norm(
        f"{article.headline} {article.news_summary} {article.source_url}"
    )


def _headline_text(article: DigestArticle) -> str:
    return _norm(article.headline)


def publication_decision(article: DigestArticle) -> tuple[str, str]:
    text = _article_text(article)
    headline = _headline_text(article)
    url = _norm(article.source_url)

    regulatory_actor = _contains(text, REGULATORY_ACTOR_TERMS)
    supervisory_signal = _contains(text, SUPERVISORY_TERMS)
    finance_signal = _contains(text, BANK_FINANCE_TERMS)
    macro_signal = _contains(text, MACRO_TERMS)
    market_signal = _contains(text, MARKET_TERMS)
    legal_political = _matches(text, LEGAL_POLITICAL_PATTERNS)
    low_value_pr = _contains(text, LOW_VALUE_PR_TERMS)
    non_financial_section = _contains(url, NON_FINANCIAL_URL_MARKERS)

    # Legal/political/crime stories often mention money laundering, a bank
    # account, governance, Rs. values, or a bank employee. Those mentions alone
    # are not a banking-supervision development.
    if legal_political and not (regulatory_actor and supervisory_signal):
        return "SUPPRESS", "LEGAL_POLITICAL_WITHOUT_DIRECT_SUPERVISORY_IMPACT"

    if non_financial_section and not regulatory_actor and not market_signal:
        return "SUPPRESS", "NON_FINANCIAL_NEWS_SECTION"

    # Awards, celebrations, campaigns and commercial partnerships are not key
    # supervisory news unless there is an independent regulatory/risk event.
    if low_value_pr and not (regulatory_actor and supervisory_signal):
        return "SUPPRESS", "LOW_VALUE_PR_OR_PROMOTIONAL"

    # Opinion pieces are not key news unless the headline itself is anchored to
    # an official financial institution or authority.
    if "/general-opinion/" in url and not _contains(headline, OFFICIAL_MACRO_TERMS):
        return "SUPPRESS", "GENERAL_OPINION_WITHOUT_OFFICIAL_FINANCIAL_ANCHOR"

    # Direct supervisory section: bank/regulator + an actual supervisory/risk
    # signal. Topic labels alone are not enough.
    explicit_bank_context = finance_signal and (
        "bank" in text
        or "banking" in text
        or "finance company" in text
        or "financial institution" in text
    )
    if supervisory_signal and (regulatory_actor or explicit_bank_context):
        return "DIRECT", "DIRECT_BANK_REGULATOR_SUPERVISORY_SIGNAL"

    if market_signal or macro_signal:
        return "MACRO", "MACRO_OR_FINANCIAL_MARKET_SIGNAL"

    if finance_signal:
        return "OTHER", "FINANCIAL_SECTOR_SIGNAL"

    return "SUPPRESS", "NO_STRONG_FINANCIAL_SIGNAL"


def build_publication_sections(
    articles: list[DigestArticle],
    *,
    max_priority: int,
    max_macro: int,
    max_other: int,
) -> tuple[
    list[DigestArticle],
    list[DigestArticle],
    list[DigestArticle],
    list[DigestArticle],
    dict[str, tuple[str, str]],
]:
    decisions = {
        article.source_url: publication_decision(article)
        for article in articles
    }

    priority = [
        article for article in articles
        if decisions[article.source_url][0] == "DIRECT"
    ][:max_priority]

    macro = [
        article for article in articles
        if decisions[article.source_url][0] == "MACRO"
    ][:max_macro]

    other = [
        article for article in articles
        if decisions[article.source_url][0] == "OTHER"
    ][:max_other]

    suppressed = [
        article for article in articles
        if decisions[article.source_url][0] == "SUPPRESS"
    ]

    return priority, macro, other, suppressed, decisions


def _rescue_profile(
    *,
    headline: str,
    article_text: str,
    source_url: str,
    stage_b_label: str,
) -> tuple[str, str] | None:
    text = _norm(f"{headline} {article_text} {source_url}")
    headline_norm = _norm(headline)
    url = _norm(source_url)

    if _matches(text, LEGAL_POLITICAL_PATTERNS):
        return None
    if _contains(text, LOW_VALUE_PR_TERMS):
        return None
    if _contains(url, NON_FINANCIAL_URL_MARKERS):
        return None

    official = _contains(text, OFFICIAL_MACRO_TERMS)
    sri_lanka = _contains(text, SRI_LANKA_TERMS)
    macro = _contains(text, MACRO_TERMS)
    market = _contains(text, MARKET_TERMS)
    payments = _contains(text, PAYMENTS_TERMS)
    supervisory = _contains(text, SUPERVISORY_TERMS)
    stage_b = _norm(stage_b_label).upper()

    # Opinion rescue is intentionally stricter than ordinary macro monitoring.
    if "/general-opinion/" in url and not _contains(headline_norm, OFFICIAL_MACRO_TERMS):
        return None

    if market and (
        "cse" in text
        or "aspi" in text
        or "treasury bond" in text
        or "t-bond" in text
        or "bond auction" in text
        or "yield" in text
    ):
        return "MARKET_RATE_FX_RISK", "STRONG_MARKET_FALSE_NEGATIVE_RESCUE"

    if official and sri_lanka and payments:
        return "PAYMENTS_DIGITAL_FINANCE", "OFFICIAL_PAYMENTS_FALSE_NEGATIVE_RESCUE"

    if official and sri_lanka and (macro or supervisory):
        return "MACRO_FINANCIAL_SYSTEMIC", "OFFICIAL_MACRO_FALSE_NEGATIVE_RESCUE"

    if stage_b in {"WATCH", "RELEVANT"} and sri_lanka and macro:
        return "MACRO_FINANCIAL_SYSTEMIC", "AUXILIARY_MACRO_FALSE_NEGATIVE_RESCUE"

    return None


def rescue_high_value_financial_articles(
    *,
    analysis_by_url: dict[str, dict],
    staged_index: dict[str, dict],
    existing_urls: set[str],
    clusters: dict[str, int | None],
    related_counts: dict[str, int],
    event_by_url: dict[str, set[str]],
    config: dict,
    display: dict,
) -> list[DigestArticle]:
    rescued: list[DigestArticle] = []

    for url, analysis in analysis_by_url.items():
        if url in existing_urls:
            continue

        relevance = (
            ((analysis.get("ml", {}) or {}).get("supervisory_relevance", {}) or {})
        )
        stage_a = relevance.get("stage_a", {}) or {}
        stage_b = relevance.get("stage_b", {}) or {}

        # Only rescue items that are currently outside the operational KEEP
        # corpus. This does not alter raw model output or model weights.
        if _norm(stage_a.get("predicted_label")).upper() == "KEEP":
            continue

        canonical_url = safe_text(analysis.get("canonical_url"))
        staged_record = staged_index.get(url) or staged_index.get(canonical_url)
        if not staged_record:
            continue
        staged_payload = staged_record.get("payload", {}) or {}

        headline = safe_text(
            analysis.get("headline")
            or staged_payload.get("headline")
        )
        article_text = safe_text(staged_payload.get("article_text"))
        if not headline or not article_text:
            continue

        profile = _rescue_profile(
            headline=headline,
            article_text=article_text,
            source_url=url,
            stage_b_label=safe_text(stage_b.get("predicted_label")),
        )
        if profile is None:
            continue

        topic, rescue_reason = profile
        topics = [topic]

        entity_details = primary_entity_details(analysis)
        primary_names = tuple(
            safe_text(value.get("canonical_name"))
            for value in entity_details
            if safe_text(value.get("canonical_name"))
        )
        primary_types = tuple(
            display_institution_type(value.get("institution_type"), config)
            for value in entity_details
            if safe_text(value.get("canonical_name"))
        )
        primary_regulated = has_primary_regulated_entity(analysis)

        score, components = calculate_supervisory_importance(
            topics=topics,
            has_primary_regulated=primary_regulated,
            config=config,
        )
        components = tuple(components) + (
            f"PUBLICATION_RESCUE:{rescue_reason}",
        )

        published_at = safe_text(
            staged_payload.get("published_at")
            or analysis.get("publication_date")
            or analysis.get("published_at")
        )
        publication_date = published_at[:10]

        cluster = clusters.get(url)
        rescued.append(
            DigestArticle(
                publication_date=publication_date,
                source_name=safe_text(
                    analysis.get("source_name")
                    or staged_payload.get("source_name")
                ),
                headline=headline,
                source_url=url,
                news_summary=source_summary(
                    article_text,
                    max_characters=int(display.get("summary_characters", 950)),
                    max_sentences=int(display.get("summary_max_sentences", 5)),
                ),
                primary_topic=topic,
                topic_labels=tuple(topics),
                topic_display_names=tuple(
                    display_topic(value, config)
                    for value in topics
                ),
                supervisory_lens=supervisory_lens(topics, config),
                primary_entities=primary_names,
                primary_entity_types=primary_types,
                all_entities=tuple(all_entities(analysis)),
                has_primary_regulated_entity=primary_regulated,
                stage_b_label=safe_text(stage_b.get("predicted_label")),
                stage_b_confidence=(
                    float(stage_b.get("confidence"))
                    if stage_b.get("confidence") is not None
                    else None
                ),
                hdbscan_cluster=cluster,
                hdbscan_outlier=(cluster == -1),
                related_story_count=int(related_counts.get(url, 0)),
                event_group_ids=tuple(sorted(event_by_url.get(url, set()))),
                phase2_review_priority=safe_text(
                    (analysis.get("supervisory_comparison", {}) or {}).get(
                        "overall_review_priority"
                    )
                ),
                stage1_stage_a_status=safe_text(
                    (
                        (analysis.get("supervisory_comparison", {}) or {})
                        .get("stage_a", {})
                        or {}
                    ).get("comparison_status")
                ),
                supervisory_importance_score=float(score),
                supervisory_importance_components=components,
            )
        )

    return rescued


def filter_event_groups(
    event_groups: list[dict],
    *,
    published_urls: set[str],
) -> list[dict]:
    filtered = []
    for group in event_groups:
        members = [
            member
            for member in (group.get("members", []) or [])
            if safe_text(member.get("source_url")) in published_urls
        ]
        if len(members) < 2:
            continue
        copy = dict(group)
        copy["members"] = members
        filtered.append(copy)
    return filtered
