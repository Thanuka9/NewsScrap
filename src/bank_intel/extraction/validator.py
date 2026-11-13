from dataclasses import dataclass
import re

from bank_intel.extraction.article import (
    ExtractedArticle,
)


# ============================================================
# VALIDATION RESULT
# ============================================================


SHORT_FINANCIAL_ITEM = re.compile(
    r"(?i)\b(?:bank|banking|NDB|BOC|HNB|DFCC|NSB|HDFC|CBSL|central bank|monetary board|"
    r"finance (?:plc|company|companies)|leasing|insurance|assurance|debenture|"
    r"Fitch|ICRA|Lanka Rating|SEC|CSE|treasury bills?|bonds?|rupee|IMF)\b"
)


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]


# ============================================================
# ARTICLE VALIDATOR
# ============================================================


def validate_article(
    article: ExtractedArticle,
) -> ValidationResult:
    """
    Validate an extracted newspaper article.

    Hard errors:
        - prevent article from being accepted

    Warnings:
        - article remains usable
        - metadata may require improvement
    """

    errors: list[str] = []
    warnings: list[str] = []

    # ========================================================
    # HARD REQUIREMENTS
    # ========================================================

    if not article.source_name:

        errors.append(
            "SOURCE_NAME_MISSING"
        )

    if not article.section_name:

        warnings.append(
            "SECTION_NAME_MISSING"
        )

    # --------------------------------------------------------
    # ORIGINAL SOURCE LINK
    #
    # This is mandatory because every intelligence item
    # must remain traceable to the original newspaper page.
    # --------------------------------------------------------

    if not article.source_url:

        errors.append(
            "SOURCE_URL_MISSING"
        )

    elif not article.source_url.startswith(
        ("http://", "https://")
    ):

        errors.append(
            "SOURCE_URL_INVALID"
        )

    # --------------------------------------------------------
    # CANONICAL URL
    # --------------------------------------------------------

    if not article.canonical_url:

        errors.append(
            "CANONICAL_URL_MISSING"
        )

    elif not article.canonical_url.startswith(
        ("http://", "https://")
    ):

        errors.append(
            "CANONICAL_URL_INVALID"
        )

    # --------------------------------------------------------
    # HEADLINE
    # --------------------------------------------------------

    if not article.headline:

        errors.append(
            "HEADLINE_MISSING"
        )

    elif len(
        article.headline.strip()
    ) < 10:

        warnings.append(
            "HEADLINE_UNUSUALLY_SHORT"
        )

    # --------------------------------------------------------
    # ARTICLE BODY
    # --------------------------------------------------------

    if not article.article_text:

        errors.append(
            "ARTICLE_TEXT_MISSING"
        )

    # ========================================================
    # CONTENT QUALITY
    # ========================================================

    if article.article_text:

        # Extremely short output usually means:
        #
        # - popup text
        # - cookie notice
        # - extraction failure
        # - teaser instead of article body

        if 40 <= article.word_count < 80 and SHORT_FINANCIAL_ITEM.search(article.headline or ""):

            # A short front-page item about a bank or regulator (a board
            # resignation, an allotment) is still news; it was being dropped.
            warnings.append(
                f"SHORT_FINANCIAL_ITEM:{article.word_count}"
            )

        elif article.word_count < 80:

            errors.append(
                f"ARTICLE_TOO_SHORT:"
                f"{article.word_count}"
            )

        elif article.word_count < 150:

            warnings.append(
                f"LOW_WORD_COUNT:"
                f"{article.word_count}"
            )

        # ----------------------------------------------------
        # POPUP / PAGE-UI SANITY CHECKS
        # ----------------------------------------------------

        normalized_text = (
            article.article_text
            .lower()
            .strip()
        )

        suspicious_exact_texts = {
            "subscribe now",
            "accept cookies",
            "enable javascript",
            "sign in",
            "log in",
        }

        if normalized_text in (
            suspicious_exact_texts
        ):

            errors.append(
                "PAGE_UI_EXTRACTED_INSTEAD_OF_ARTICLE"
            )

    # ========================================================
    # METADATA WARNINGS
    # ========================================================

    if not article.published_at:

        warnings.append(
            "PUBLISHED_DATE_MISSING"
        )

    # Author is optional.
    #
    # Many legitimate newspaper stories have no named byline.
    # Therefore this must never invalidate an article.

    if not article.author:

        warnings.append(
            "AUTHOR_NOT_AVAILABLE"
        )

    # ========================================================
    # CONTENT HASH
    # ========================================================

    if not article.content_hash:

        warnings.append(
            "CONTENT_HASH_MISSING"
        )

    # ========================================================
    # FINAL RESULT
    # ========================================================

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )