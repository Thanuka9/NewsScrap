import hashlib


def sha256_text(value: str) -> str:
    if not value:
        value = ""
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def content_hash(text: str) -> str:
    normalized = " ".join((text or "").split())
    return sha256_text(normalized)


def url_hash(url: str, length: int = 16) -> str:
    return sha256_text(url)[:length]
