"""Shared, evidence-based recall rules for Stage A and publication selection."""
import re


def material_context(article: dict) -> tuple[str, str, str] | None:
    h = str(article.get('headline') or article.get('title') or '')
    body = str(article.get('body') or article.get('content') or article.get('summary') or '')
    text = h + ' ' + body[:1800]
    local = re.search(r'(?i)\b(?:sri lanka|sri lankan|lankan|SL|SLPA|Colombo|Hambantota)\b', text)
    # Require corroborating source context for abbreviations and market flows.
    # These are development types, not a list of approved historical headlines.
    if re.search(r'(?i)\b(?:SEC|securities and exchange commission)\b', h) and re.search(r'(?i)\b(?:market manipulation|enforcement|regulatory action|sanctions?|insider trading)\b', h) and re.search(r'(?i)securities and exchange commission', body[:1800]) and local:
        return 'MONETARY_MARKETS_EXTERNAL', 'CAPITAL_MARKETS_CONDUCT', 'SEC_MARKET_CONDUCT_ACTION'
    if local and re.search(r'(?i)\bforeign\b', h) and re.search(r'(?i)\b(?:buying|selling|inflows?|outflows?|holdings?)\b', h) and re.search(r'(?i)\b(?:rupee bonds?|treasury bonds?|government securities)\b', text) and re.search(r'\d', body[:1800]):
        return 'MONETARY_MARKETS_EXTERNAL', 'SOVEREIGN_PORTFOLIO_FLOWS', 'OBSERVED_FOREIGN_SECURITIES_FLOW'
    if local and re.search(r'(?i)\b(?:central bank|cenbank|cbsl)\b', h) and re.search(r'(?i)\breserve(?:s| building| accumulation)\b', h) and re.search(r'(?i)\b(?:foreign exchange|forex|rupee|dollars?|USD)\b', body[:1800]):
        return 'MONETARY_MARKETS_EXTERNAL', 'RESERVES_FX_POLICY', 'CENTRAL_BANK_RESERVE_POLICY'
    if re.search(r'(?i)\b(?:cse|colombo stock exchange|bourse)\b', h) and re.search(r'(?i)\b(?:capital raising|new investors|branch rollout)\b', h) and re.search(r'(?i)colombo stock exchange', body[:1800]) and re.search(r'\d', body[:1800]):
        return 'MONETARY_MARKETS_EXTERNAL', 'CAPITAL_MARKETS_DEVELOPMENT', 'CSE_MARKET_ACCESS_OR_FUNDING'
    if local and re.search(r'(?i)\b(?:apg|aml|cft|financial watchlists?|money laundering)\b', text) and re.search(r'(?i)mutual evaluation|assessment|evaluation begins|evaluation opens', text):
        return 'KEY_BANKING_SUPERVISORY', 'REGULATORY_RISK', 'AML_MUTUAL_EVALUATION'
    if re.search(r'(?i)\b(?:crib|credit information bureau)\b', h) and re.search(r'(?i)onboard|expan|credit information|reporting', text):
        return 'OTHER_FINANCIAL_SECTOR', 'FINANCIAL_INCLUSION_CONSUMER', 'CREDIT_INFORMATION_COVERAGE'
    # A Cabinet decision is domestic even when the lead never says 'Sri Lanka'.
    sovereign = local or re.search(r'(?i)\bcabinet\b', text)
    if sovereign and re.search(r'(?i)\b(?:adb|asian development bank|opec fund|ofid|world bank|jica|aiib)\b', text) and re.search(r'(?i)\b(?:loan|financing|facility)\b',h) and re.search(r'(?i)approv|clear|sign|disburs',h) and re.search(r'\d',text):
        return 'MACRO_SYSTEMIC_CONTEXT', 'SOVEREIGN_EXTERNAL_FINANCING', 'MULTILATERAL_FINANCING_DEVELOPMENT'
    if re.search(r'(?i)capital.market',h) and re.search(r'(?i)budget|government|govt|sec|securities and exchange',text) and re.search(r'(?i)minister|treasury|finance ministry',text):
        return 'MONETARY_MARKETS_EXTERNAL', 'CAPITAL_MARKETS', 'OFFICIAL_CAPITAL_MARKET_POLICY'
    if local and re.search(r'(?i)tax (?:burden|relief)|indirect tax|value.added tax',text) and re.search(r'(?i)finance.*minister|minister.*finance',text) and re.search(r'(?i)revenue|fiscal consolidation',text):
        return 'MACRO_SYSTEMIC_CONTEXT', 'FISCAL_SYSTEMIC', 'OFFICIAL_FISCAL_POLICY_SIGNAL'
    if re.search(r'(?i)\b(?:cse|bourse|aspi|stock market)\b',h) and re.search(r'(?i)all share price index|\baspi\b',body) and re.search(r'(?i)\d[\d,.]*\s+points',body):
        return 'MONETARY_MARKETS_EXTERNAL', 'MARKET_EXTERNAL', 'OBSERVED_CSE_TRADING_SESSION'
    if local and re.search(r'(?i)\b(?:apparel|tea|rubber|coconut|services|merchandise) exports?\b',h) and re.search(r'(?i)\$|usd|million|billion',body) and re.search(r'(?i)september|october|january|months|quarter|year',body):
        return 'MONETARY_MARKETS_EXTERNAL', 'SECTOR_EXPORT_EARNINGS', 'MATERIAL_SECTOR_EXPORT_STATISTICS'
    if local and re.search(r'(?i)\bexports?\b', h) and re.search(r'(?i)earn(?:s|ings)?|revenue|hit|surpass|top|reach|record',h) and re.search(r'(?i)earnings|revenue|US\$|\$|billion|million', text):
        return 'MONETARY_MARKETS_EXTERNAL', 'EXPORT_EARNINGS', 'MATERIAL_EXTERNAL_SECTOR_STATISTICS'
    if re.search(r'(?i)\b(?:green|sustainability|corporate) bonds?\b', h) and re.search(r'(?i)list(?:s|ing|ed)?|issu(?:e|ance)|stock exchange|LuxSE', text):
        return 'MONETARY_MARKETS_EXTERNAL', 'CAPITAL_MARKETS_FUNDING', 'MATERIAL_BOND_MARKET_DEVELOPMENT'
    if local and re.search(r'(?i)rolling forecasts?|three.month.*forecast', h) and re.search(r'(?i)government|economic|fiscal|monetary', body):
        return 'MACRO_SYSTEMIC_CONTEXT', 'ECONOMIC_FORECAST', 'ECONOMIC_FORECAST_REVIEW'
    # Port/terminal news is material when it reports money (revenue, investment,
    # financing); speeches about digitalisation or capability are not.
    money = re.search(r'(?i)(?:rs\.?|lkr|us\$|usd|\$)\s*[\d.,]+|\b[\d.,]+\s*(?:million|billion|bn|mn)\b', text)
    if local and money and re.search(r'(?i)\b(?:port|terminal)\b', h) and re.search(r'(?i)revenue|commission|investment|financ', text):
        return 'MACRO_SYSTEMIC_CONTEXT', 'INFRASTRUCTURE_EXTERNAL', 'MATERIAL_INFRASTRUCTURE_REVIEW'
    return None
