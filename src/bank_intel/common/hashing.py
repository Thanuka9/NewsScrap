import hashlib


def sha256_text(value: str) -> str:
    """
    Return SHA-256 hash for a UTF-8 string.
    """
    if not value:
        value = ""

    return hashlib.sha256(
        value.encode("utf-8", errors="ignore")
    ).hexdigest()


def content_hash(text: str) -> str:
    """
    Normalize article text before hashing so minor whitespace
    changes do not create a completely different content hash.
    """
    normalized = " ".join((text or "").split())
    return sha256_text(normalized)


def url_hash(url: str, length: int = 16) -> str:
    """
    Short stable identifier based on URL.
    """
    return sha256_text(url)[:length]