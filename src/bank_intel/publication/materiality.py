"""Shared, evidence-based recall rules for Stage A and publication selection."""
import re


def material_context(article: dict) -> tuple[str, str, str] | None:
    h = str(article.get('headline') or article.get('title') or '')
    body = str(article.get('body') or article.get('content') or article.get('summary') or '')
    text = h + ' ' + body[:1800]
    local = re.search(r'(?i)\b(?:sri lanka|sri lankan|lankan|SL|SLPA|Colombo|Hambantota)\b', text)
    if local and re.search(r'(?i)\bexports?\b', h) and re.search(r'(?i)earn(?:s|ings)?|revenue|hit|surpass|top|reach|record',h) and re.search(r'(?i)earnings|revenue|US\$|\$|billion|million', text):
        return 'MONETARY_MARKETS_EXTERNAL', 'EXPORT_EARNINGS', 'MATERIAL_EXTERNAL_SECTOR_STATISTICS'
    if re.search(r'(?i)\b(?:green|sustainability|corporate) bonds?\b', h) and re.search(r'(?i)list(?:s|ing|ed)?|issu(?:e|ance)|stock exchange|LuxSE', text):
        return 'MONETARY_MARKETS_EXTERNAL', 'CAPITAL_MARKETS_FUNDING', 'MATERIAL_BOND_MARKET_DEVELOPMENT'
    if local and re.search(r'(?i)rolling forecasts?|three.month.*forecast', h) and re.search(r'(?i)government|economic|fiscal|monetary', body):
        return 'MACRO_SYSTEMIC_CONTEXT', 'ECONOMIC_FORECAST', 'ECONOMIC_FORECAST_REVIEW'
    if local and re.search(r'(?i)\b(?:port|terminal)\b', h) and re.search(r'(?i)revenue|commission|investment|financ', text):
        return 'MACRO_SYSTEMIC_CONTEXT', 'INFRASTRUCTURE_EXTERNAL', 'MATERIAL_INFRASTRUCTURE_REVIEW'
    return None
