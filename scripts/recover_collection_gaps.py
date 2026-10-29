
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import (
    urljoin,
    urlsplit,
    urlunsplit,
)

import requests
from bs4 import BeautifulSoup


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# NEWS_COLLECTION_AUDIT_ONLY_V1
# This module detects collection gaps only. It must never inject rows directly
# into the stakeholder brief. run_pipeline.py treats any recovered material row
# as a collection-completeness failure and blocks publication.
REPORT_INJECTION_ALLOWED = False

OUTPUT_ROOT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "collection_completeness"
)

FEEDBACK_LEDGER = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "relevance_feedback"
    / "relevance_feedback_ledger.jsonl"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/155 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

DISCOVERY_SOURCES = [
    {
        "source_name": "Daily FT",
        "domains": {
            "ft.lk",
            "www.ft.lk",
        },
        "listing_urls": [
            "https://www.ft.lk/Front-Page/44/0",
            "https://www.ft.lk/financial-services/42",
            "https://www.ft.lk/top-story/26/0",
            "https://www.ft.lk/",
        ],
    },
    {
        "source_name": "Daily Mirror",
        "domains": {
            "dailymirror.lk",
            "www.dailymirror.lk",
        },
        "listing_urls": [
            "https://www.dailymirror.lk/business",
            "https://www.dailymirror.lk/",
            "https://www.dailymirror.lk/breaking-news/108",
            "https://www.dailymirror.lk/top-storys/155",
            "https://www.dailymirror.lk/latest-news/342",
        ],
    },
]


def safe_text(value):
    return str(value or "").strip()


def canonical_url(url):
    url = safe_text(url)

    if not url:
        return ""

    try:
        p = urlsplit(url)

        return urlunsplit(
            (
                (p.scheme or "https").lower(),
                p.netloc.lower(),
                p.path.rstrip("/"),
                "",
                "",
            )
        )

    except Exception:
        return (
            url
            .split("#", 1)[0]
            .split("?", 1)[0]
            .rstrip("/")
        )


def is_real_article_url(
    url,
    allowed_domains,
):
    """
    FT and Daily Mirror actual story URLs end with:

        /44-798713
        /42-798733
        /273-352550
        /245-352552

    Category/listing pages such as:

        /business
        /business/215/60
        /Markets/557
        /Economy/126
        /Weekend_Online/.../520

    are rejected.
    """

    try:
        p = urlsplit(url)
    except Exception:
        return False

    if p.netloc.lower() not in allowed_domains:
        return False

    path = p.path.rstrip("/")

    if not re.search(
        r"/\d{1,4}-\d{5,}$",
        path,
    ):
        return False

    return True


def fetch(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=25,
    )

    response.raise_for_status()

    return response.text


def listing_article_urls(
    html,
    listing_url,
    allowed_domains,
):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    urls = []

    for node in soup.select("a[href]"):
        href = safe_text(
            node.get("href")
        )

        if not href:
            continue

        url = canonical_url(
            urljoin(
                listing_url,
                href,
            )
        )

        if not is_real_article_url(
            url,
            allowed_domains,
        ):
            continue

        urls.append(url)

    return list(
        dict.fromkeys(urls)
    )


def json_ld(soup):
    items = []

    for node in soup.find_all(
        "script",
        attrs={
            "type":
            "application/ld+json"
        },
    ):
        raw = safe_text(
            node.string
            or node.get_text()
        )

        if not raw:
            continue

        try:
            obj = json.loads(raw)
        except Exception:
            continue

        if isinstance(obj, dict):

            graph = obj.get("@graph")

            if isinstance(graph, list):
                items.extend(
                    x
                    for x in graph
                    if isinstance(x, dict)
                )
            else:
                items.append(obj)

        elif isinstance(obj, list):
            items.extend(
                x
                for x in obj
                if isinstance(x, dict)
            )

    return items


def clean_title(title):
    title = safe_text(title)

    suffixes = (
        " - Business News | Daily Mirror",
        " - Business Main | Daily Mirror",
        " | Daily Mirror",
        " | Daily FT",
    )

    for suffix in suffixes:
        if title.endswith(suffix):
            title = title[
                :-len(suffix)
            ].strip()

    return title


def extract_title(soup):
    for selector in (
        'meta[property="og:title"]',
        'meta[name="twitter:title"]',
    ):
        node = soup.select_one(selector)

        if node:
            title = clean_title(
                node.get("content")
            )

            if title:
                return title

    h1 = soup.find("h1")

    if h1:
        title = clean_title(
            h1.get_text(
                " ",
                strip=True,
            )
        )

        if title:
            return title

    return ""


def invalid_title(title):
    low = " ".join(
        safe_text(title)
        .lower()
        .split()
    )

    if not low:
        return True

    bad = (
        "sri lanka 24 hours online breaking news",
        "sri lanka latest breaking news and headlines",
        "stay informed with the latest world news",
    )

    return any(
        phrase in low
        for phrase in bad
    )


def extract_date(soup):
    values = []

    for selector in (
        'meta[property="article:published_time"]',
        'meta[name="date"]',
        'meta[name="pubdate"]',
        'meta[itemprop="datePublished"]',
    ):
        node = soup.select_one(selector)

        if node:
            values.append(
                node.get("content")
            )

    for item in json_ld(soup):
        values.append(
            item.get("datePublished")
        )

    for value in values:
        text = safe_text(value)

        match = re.search(
            r"\b20\d{2}-\d{2}-\d{2}\b",
            text,
        )

        if match:
            return match.group(0)

    return ""


def extract_body(soup):
    for node in soup(
        [
            "script",
            "style",
            "nav",
            "footer",
            "header",
            "aside",
        ]
    ):
        node.decompose()

    selectors = (
        "article",
        '[itemprop="articleBody"]',
        ".article-body",
        ".article-content",
        ".story-body",
        ".entry-content",
        ".post-content",
        ".news-content",
    )

    best = ""

    for selector in selectors:

        for container in soup.select(
            selector
        ):

            paragraphs = [
                safe_text(
                    p.get_text(
                        " ",
                        strip=True,
                    )
                )
                for p in container.find_all(
                    "p"
                )
            ]

            value = " ".join(
                p
                for p in paragraphs
                if len(p) >= 25
            )

            if len(value) > len(best):
                best = value

    return best


def important_candidate(
    headline,
    body,
):
    """
    High recall, but NOT:
      bank name alone
      Port City name alone
      AI award alone
      generic business expansion
    """

    text = (
        safe_text(headline)
        + " "
        + safe_text(body)
    ).lower()

    # --------------------------------------------------------------
    # Obvious PR / awards are not collection-rescue targets unless
    # they also contain a hard supervisory development.
    # --------------------------------------------------------------

    pr_terms = (
        "award",
        "awards",
        "recognised",
        "recognized",
        "anniversary",
        "celebrates",
        "csr",
        "sponsorship",
        "branch opening",
        "opens new branch",
    )

    hard_terms = (
        "capital adequacy",
        "tier 1",
        "tier i",
        "tier 2",
        "tier ii",
        "basel iii",
        "basel 3",
        "liquidity",
        "non-performing",
        "npl",
        "fraud",
        "cyber",
        "aml",
        "money laundering",
        "regulatory action",
        "enforcement",
        "governance",
        "credit rating",
        "fitch",
        "moody",
        "s&p global ratings",
    )

    if (
        any(x in text for x in pr_terms)
        and not any(
            x in text
            for x in hard_terms
        )
    ):
        return False

    # --------------------------------------------------------------
    # Hard supervisory / banking topics
    # --------------------------------------------------------------

    if any(
        term in text
        for term in (
            "basel iii",
            "basel 3",
            "capital adequacy",
            "regulatory capital",
            "tier 1 capital",
            "tier i capital",
            "tier 2 capital",
            "tier ii capital",
            "subordinated debenture",
            "subordinated loan",
            "non-performing loan",
            "non-performing loans",
            "npl ratio",
            "liquidity coverage",
            "liquidity ratio",
            "bank fraud",
            "cyber fraud",
            "cyberattack",
            "cyber attack",
            "money laundering",
            "aml/cft",
            "regulatory action",
            "central bank directive",
            "central bank approval",
        )
    ):
        return True

    # --------------------------------------------------------------
    # CBSL / monetary / liquidity / FX
    # --------------------------------------------------------------

    cbsl = (
        "central bank of sri lanka"
        in text
        or "cbsl" in text
    )

    if (
        cbsl
        and any(
            term in text
            for term in (
                "policy rate",
                "interest rate",
                "liquidity",
                "money market",
                "foreign exchange",
                "exchange rate",
                "reserves",
                "credit growth",
                "inflation",
                "pmi",
                "purchasing managers",
            )
        )
    ):
        return True

    # --------------------------------------------------------------
    # IMF / EFF / sovereign / fiscal
    # --------------------------------------------------------------

    if (
        (
            "imf" in text
            or "international monetary fund"
            in text
        )
        and any(
            term in text
            for term in (
                "sri lanka",
                "eff",
                "extended fund facility",
                "debt",
                "fiscal",
                "reform",
                "reserves",
            )
        )
    ):
        return True

    if any(
        term in text
        for term in (
            "domestic dollar bond",
            "sovereign bond",
            "treasury bill",
            "treasury bills",
            "t-bill",
            "t-bills",
            "bond yields",
            "bond yield",
            "government borrowing",
            "public debt",
            "debt restructuring",
            "debt sustainability",
        )
    ):
        return True

    # --------------------------------------------------------------
    # Local capital markets
    # --------------------------------------------------------------

    if (
        any(
            term in text
            for term in (
                "colombo stock exchange",
                "aspi",
                "s&p sl20",
                "s&p sl 20",
            )
        )
        and any(
            term in text
            for term in (
                "turnover",
                "foreign selling",
                "foreign buying",
                "foreign inflow",
                "foreign outflow",
                "market capitalisation",
                "market capitalization",
                "index",
                "bourse",
            )
        )
    ):
        return True

    # --------------------------------------------------------------
    # Bank payment / infrastructure change
    # --------------------------------------------------------------

    bank_signal = any(
        term in text
        for term in (
            " bank ",
            "bank plc",
            "people's bank",
            "peoples bank",
            "commercial bank",
            "sampath bank",
            "hatton national bank",
            "hnb",
            "ndb",
            "dfcc",
            "nations trust bank",
            "pan asia bank",
            "union bank",
            "amana bank",
        )
    )

    if (
        bank_signal
        and any(
            term in text
            for term in (
                "payment platform",
                "digital payment",
                "digital payments",
                "qr payment",
                "qr payments",
                "merchant payment",
                "merchant payments",
                "share trading platform",
                "banking platform",
                "payment infrastructure",
                "soundbox",
            )
        )
    ):
        return True

    # --------------------------------------------------------------
    # Material fiscal / activity indicators
    # --------------------------------------------------------------

    if any(
        term in text
        for term in (
            "manufacturing pmi",
            "services pmi",
            "purchasing managers' index",
            "purchasing managers index",
        )
    ):
        return True

    if (
        "government revenue" in text
        or "customs revenue" in text
    ):
        return True

    return False


def late_arrival_is_material(
    headline,
    body,
):
    """
    Previous-day rescue is deliberately narrower.
    """

    text = (
        safe_text(headline)
        + " "
        + safe_text(body)
    ).lower()

    return any(
        term in text
        for term in (
            "domestic dollar bond",
            "sovereign bond",
            "treasury bill",
            "t-bill",
            "bond yield",
            "imf",
            "extended fund facility",
            "eff",
            "capital adequacy",
            "tier 2",
            "tier ii",
            "basel iii",
            "liquidity",
            "central bank",
            "cbsl",
            "foreign exchange",
            "official reserves",
            "credit rating",
            "debt restructuring",
        )
    )


def existing_ml_urls():
    urls = set()

    root = (
        PROJECT_ROOT
        / "data"
        / "processed"
        / "ml"
    )

    if not root.exists():
        return urls

    for path in root.rglob("*.json"):

        if path.name == "ml_manifest.json":
            continue

        try:
            payload = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )
        except Exception:
            continue

        url = canonical_url(
            payload.get("source_url")
        )

        if url:
            urls.add(url)

    return urls


def existing_feedback_keys():
    keys = set()

    if not FEEDBACK_LEDGER.exists():
        return keys

    for line in FEEDBACK_LEDGER.read_text(
        encoding="utf-8"
    ).splitlines():

        if not line.strip():
            continue

        try:
            row = json.loads(line)
        except Exception:
            continue

        keys.add(
            (
                safe_text(
                    row.get(
                        "publication_date"
                    )
                ),
                canonical_url(
                    row.get(
                        "source_url"
                    )
                ),
                safe_text(
                    row.get(
                        "feedback_type"
                    )
                ),
            )
        )

    return keys


def append_material_feedback(
    recovered_articles,
    retained_articles,
):
    """
    ONLY recovered articles that survived the final brief classifier
    are written to the learning ledger.
    """

    recovered_by_url = {
        canonical_url(
            row.get("source_url")
        ): row
        for row in recovered_articles
        if canonical_url(
            row.get("source_url")
        )
    }

    retained_by_url = {
        canonical_url(
            row.get("source_url")
        ): row
        for row in retained_articles
        if canonical_url(
            row.get("source_url")
        )
    }

    relevant_urls = (
        set(recovered_by_url)
        & set(retained_by_url)
    )

    if not relevant_urls:
        return 0

    FEEDBACK_LEDGER.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    seen = existing_feedback_keys()

    added = 0

    with FEEDBACK_LEDGER.open(
        "a",
        encoding="utf-8",
    ) as file:

        for url in sorted(relevant_urls):

            recovered = (
                recovered_by_url[url]
            )

            retained = (
                retained_by_url[url]
            )

            key = (
                safe_text(
                    recovered.get(
                        "publication_date"
                    )
                ),
                url,
                "COLLECTION_MISS_MATERIAL",
            )

            if key in seen:
                continue

            row = {
                "publication_date":
                    recovered.get(
                        "publication_date"
                    ),

                "source_name":
                    recovered.get(
                        "source_name"
                    ),

                "headline":
                    recovered.get(
                        "headline"
                    ),

                "source_url":
                    url,

                "stage_a_label": "",
                "stage_a_confidence": None,
                "operational_keep": None,

                "stage_b_executed": None,
                "stage_b_label": "",
                "stage_b_confidence": None,

                "final_section":
                    retained.get(
                        "section",
                        "",
                    ),

                "final_category":
                    retained.get(
                        "category",
                        "",
                    ),

                "final_reason":
                    retained.get(
                        "reason",
                        "",
                    ),

                "final_importance":
                    retained.get(
                        "importance",
                        "",
                    ),

                "feedback_type":
                    "COLLECTION_MISS_MATERIAL",

                "provisional_target_label":
                    "KEEP",

                "label_status":
                    "FINAL_CLASSIFIER_CONFIRMED_COLLECTION_GAP",

                # Collector miss != model miss.
                "auto_training_candidate":
                    False,

                "approved_for_training":
                    False,

                "reviewer": "",

                "review_notes":
                    (
                        "Article was absent from the main "
                        "collection/ML population and recovered "
                        "by the strict source-completeness pass."
                    ),

                "ml_file": "",

                "created_at":
                    datetime.now()
                    .astimezone()
                    .isoformat(),
            }

            file.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                )
                + "\n"
            )

            seen.add(key)

            added += 1

    return added


def recover_for_window(
    end_date,
    days=1,
    late_arrival_days=1,
):
    if isinstance(
        end_date,
        datetime,
    ):
        end = end_date.date()

    elif isinstance(
        end_date,
        date,
    ):
        end = end_date

    else:
        end = date.fromisoformat(
            str(end_date)[:10]
        )

    start = (
        end
        - timedelta(
            days=max(days - 1, 0)
        )
    )

    earliest = (
        start
        - timedelta(
            days=max(
                late_arrival_days,
                0,
            )
        )
    )

    current_dates = {
        (
            start
            + timedelta(days=i)
        ).isoformat()
        for i in range(
            (end - start).days + 1
        )
    }

    allowed_dates = {
        (
            earliest
            + timedelta(days=i)
        ).isoformat()
        for i in range(
            (end - earliest).days + 1
        )
    }

    existing = existing_ml_urls()

    recovered = []

    checks = []

    fetched_urls = set()

    for source in DISCOVERY_SOURCES:

        source_name = (
            source["source_name"]
        )

        allowed_domains = {
            domain.lower()
            for domain
            in source["domains"]
        }

        candidates = []

        for listing_url in source[
            "listing_urls"
        ]:

            try:
                html = fetch(
                    listing_url
                )

                links = (
                    listing_article_urls(
                        html,
                        listing_url,
                        allowed_domains,
                    )
                )

                candidates.extend(
                    links
                )

                checks.append({
                    "source_name":
                        source_name,

                    "listing_url":
                        listing_url,

                    "status":
                        "SUCCESS",

                    "real_article_links":
                        len(links),
                })

            except Exception as exc:

                checks.append({
                    "source_name":
                        source_name,

                    "listing_url":
                        listing_url,

                    "status":
                        "ERROR",

                    "error":
                        (
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        ),
                })

        candidates = list(
            dict.fromkeys(candidates)
        )

        for url in candidates:

            url = canonical_url(url)

            if (
                not url
                or url in existing
                or url in fetched_urls
            ):
                continue

            fetched_urls.add(url)

            try:
                html = fetch(url)

                soup = BeautifulSoup(
                    html,
                    "html.parser",
                )

                headline = extract_title(
                    soup
                )

                if (
                    not headline
                    or invalid_title(
                        headline
                    )
                ):
                    continue

                published = extract_date(
                    soup
                )

                if (
                    not published
                    or published
                    not in allowed_dates
                ):
                    continue

                body = extract_body(
                    soup
                )

                if len(body) < 250:
                    continue

                if not important_candidate(
                    headline,
                    body,
                ):
                    continue

                late = (
                    published
                    not in current_dates
                )

                if (
                    late
                    and not late_arrival_is_material(
                        headline,
                        body,
                    )
                ):
                    continue

                article_id = (
                    hashlib.sha256(
                        url.encode("utf-8")
                    )
                    .hexdigest()[:16]
                )

                recovered.append({
                    "article_id":
                        article_id,

                    "source_name":
                        source_name,

                    "source_url":
                        url,

                    "headline":
                        headline,

                    "publication_date":
                        published,

                    "article_text":
                        body,

                    "collection_recovery":
                        True,

                    "collection_recovery_reason":
                        "STRICT_MATERIAL_ARTICLE_MISSING_FROM_MAIN_COLLECTION",

                    "late_arrival":
                        late,

                    "recovered_at":
                        datetime.now()
                        .astimezone()
                        .isoformat(),
                })

                existing.add(url)

            except Exception:
                continue

    recovered.sort(
        key=lambda row: (
            row["publication_date"],
            row["source_name"],
            row["headline"],
        )
    )

    output_dir = (
        OUTPUT_ROOT
        / end.isoformat()
        / f"window_{days}d"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    articles_path = (
        output_dir
        / "recovered_articles.json"
    )

    manifest_path = (
        output_dir
        / "collection_completeness_manifest.json"
    )

    articles_path.write_text(
        json.dumps(
            recovered,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manifest = {
        "status":
            "PASS"
            if checks and all(
                x.get("status") == "SUCCESS"
                for x in checks
            )
            else "FAIL",

        "mode": "AUDIT_ONLY_NO_REPORT_INJECTION",

        # This independent re-check lists only the publishers below; its PASS
        # says nothing about the others. Seven-publisher coverage is decided by
        # the signed coverage worksheet (news_coverage_verified.json).
        "scope": "INDEPENDENT_LISTING_RECHECK",
        "checked_publishers": [source["source_name"] for source in DISCOVERY_SOURCES],
        "not_checked_publishers": sorted(
            {"Daily FT", "Daily Mirror", "Daily News", "The Morning", "The Island", "NewsFirst", "Ceylon Today"}
            - {source["source_name"] for source in DISCOVERY_SOURCES}
        ),
        "seven_publisher_completeness": "NOT_ESTABLISHED_BY_THIS_CHECK",

        "generated_at":
            datetime.now()
            .astimezone()
            .isoformat(),

        "end_date":
            end.isoformat(),

        "window_days":
            days,

        "late_arrival_days":
            late_arrival_days,

        "listing_checks":
            checks,

        "recovered_article_count":
            len(recovered),

        "late_arrival_count":
            sum(
                bool(
                    row.get(
                        "late_arrival"
                    )
                )
                for row in recovered
            ),

        "feedback_policy":
            "ONLY_FINAL_RETAINED_RECOVERIES_ENTER_LEDGER",

        "recovered_articles_file":
            str(
                articles_path.relative_to(
                    PROJECT_ROOT
                )
            ),
    }

    manifest_path.write_text(
        json.dumps(
            manifest,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("=" * 100)
    print(
        "STRICT COLLECTION COMPLETENESS AUDIT (NO REPORT INJECTION)"
    )
    print("=" * 100)

    print(
        f"End date              : "
        f"{end.isoformat()}"
    )

    print(
        f"Recovered candidates  : "
        f"{len(recovered)}"
    )

    print(
        f"Late arrivals         : "
        f"{manifest['late_arrival_count']}"
    )

    for row in recovered:
        late = (
            " [LATE]"
            if row["late_arrival"]
            else ""
        )

        print(
            f"  + [{row['source_name']}]"
            f"{late} "
            f"{row['headline']}"
        )

    print(
        f"Manifest              : "
        f"{manifest_path}"
    )

    return recovered


def main():
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--end-date",
        required=True,
    )

    parser.add_argument(
        "--days",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--late-arrival-days",
        type=int,
        default=1,
    )

    args = parser.parse_args()

    recover_for_window(
        args.end_date,
        args.days,
        args.late_arrival_days,
    )


if __name__ == "__main__":
    main()
