from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

TRACKING_PARAMETERS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}

def absolute_url(base_url: str, href: str) -> str:
    return urljoin(base_url, href)

def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    query_params = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() not in TRACKING_PARAMETERS]
    clean_path = parsed.path
    if clean_path != "/":
        clean_path = clean_path.rstrip("/")
    normalized = parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(), path=clean_path, query=urlencode(query_params), fragment="")
    return urlunparse(normalized)

def domain_matches(url: str, expected_domain: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    expected_domain = expected_domain.lower()
    return hostname == expected_domain or hostname.endswith("." + expected_domain)
