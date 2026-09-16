from dataclasses import dataclass

from bank_intel.extraction.article import ExtractedArticle

@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]

def validate_article(article: ExtractedArticle) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    if not article.source_name:
        errors.append("SOURCE_NAME_MISSING")
    if not article.section_name:
        warnings.append("SECTION_NAME_MISSING")
    if not article.source_url:
        errors.append("SOURCE_URL_MISSING")
    elif not article.source_url.startswith(("http://", "https://")):
        errors.append("SOURCE_URL_INVALID")
    if not article.canonical_url:
        errors.append("CANONICAL_URL_MISSING")
    elif not article.canonical_url.startswith(("http://", "https://")):
        errors.append("CANONICAL_URL_INVALID")
    if not article.headline:
        errors.append("HEADLINE_MISSING")
    elif len(article.headline.strip()) < 10:
        warnings.append("HEADLINE_UNUSUALLY_SHORT")
    if not article.article_text:
        errors.append("ARTICLE_TEXT_MISSING")
    if article.article_text:
        if article.word_count < 80:
            errors.append(f"ARTICLE_TOO_SHORT:{article.word_count}")
        elif article.word_count < 150:
            warnings.append(f"LOW_WORD_COUNT:{article.word_count}")
        normalized_text = article.article_text.lower().strip()
        suspicious_exact_texts = {"subscribe now", "accept cookies", "enable javascript", "sign in", "log in"}
        if normalized_text in suspicious_exact_texts:
            errors.append("PAGE_UI_EXTRACTED_INSTEAD_OF_ARTICLE")
    if not article.published_at:
        warnings.append("PUBLISHED_DATE_MISSING")
    if not article.author:
        warnings.append("AUTHOR_NOT_AVAILABLE")
    if not article.content_hash:
        warnings.append("CONTENT_HASH_MISSING")
    return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)
