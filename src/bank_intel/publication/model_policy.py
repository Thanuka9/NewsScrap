"""Conservative FinBERT/rule reconciliation.

The model is an observed signal, not a license to publish unverified text.
A confident direct-bank rule may recover Stage-A NOISE. A KEEP veto by later
rules is surfaced for human review, not silently changed or hidden.
"""
from __future__ import annotations
import re

# Headline terms that make a rule exclusion worth an editor's second look.
FINANCIAL_SIGNAL = re.compile(r'(?i)\b(?:banks?|banking|finance|financial|financing|credit|loans?|lending|lenders?|borrowers?|debt|bonds?|'
                              r'debentures?|treasury|t.bills?|central bank|cbsl|monetary|insur\w*|capital market|stocks?|shares?|equit\w*|cse|aspi|'
                              r'indices|securities|sec|imf|fiscal|tax\w*|budget|inflation|interest rates?|forex|fx|exchange rate|rupee|'
                              r'remittances?|deposits?|fintech|payments?|exports?|imports?|reserves|rating|aml|money laundering|fraud|scam|'
                              # Macro-economy context the edition carries alongside financial news.
                              r'econom\w*|growth|gdp|pmi|manufacturing|unemployment|employment|jobs|workforce|labour|productivity|investments?|investors?|'
                              r'funds?|ppps?|public.private|markets?|bourse|blackout|power crisis|logistics hub|port city|'
                              r'social.protection|poverty|households?|welfare|housing|wages?|prices?|tariffs?|revenue|soes?|state.owned)\b')
OPINION_URL = re.compile(r'(?i)/(?:columns?|opinion|general-opinion|features?|ft_view__editorial|editorial|letters?)/')
OPINION_HEADLINE = re.compile(r'(?i)^(?:review:|editorial|opinion|comment)\b|\bwhy\b.*\?$|^how can\b')


def financial_signal(article: dict) -> str:
    """'' when the article warrants review; else the reason it does not.

    Opinion and commentary pieces are excluded unless a regulator or bank is
    the headline subject; otherwise the headline must carry a financial term
    or name a registered financial institution.
    """
    h = str(article.get('headline') or '')
    url = str(article.get('source_url') or '')
    from .decisions import licensed_bank_in, nonbank_institution_in, FINANCIAL_REGULATOR
    named = licensed_bank_in(h) or nonbank_institution_in(h) or bool(FINANCIAL_REGULATOR.search(h))
    if named or FINANCIAL_SIGNAL.search(h):
        return ''
    # Commentary with no financial or economic subject in its headline.
    if OPINION_URL.search(url) or OPINION_HEADLINE.search(h):
        return 'OPINION_OR_COMMENTARY'
    return 'NO_FINANCIAL_SECTOR_SIGNAL'


def reconcile(assessment: dict, trace: dict | None, article: dict | None = None) -> dict:
    result = dict(assessment)
    result['ml_trace_found'] = bool(trace)
    result['ml_action'] = 'NOT_AVAILABLE'
    if not trace:
        return result
    raw = trace.get('raw_stage_a')
    operational = trace.get('operational_keep')
    if raw not in ('KEEP', 'NOISE'):
        result['ml_action'] = 'UNRECOGNIZED_RAW_LABEL'
        return result
    if raw == 'KEEP' and operational is False:
        result['ml_action'] = 'POST_MODEL_SUPPRESSION'
    elif raw == 'NOISE' and operational is True:
        result['ml_action'] = 'POST_MODEL_PROMOTION'
    else:
        result['ml_action'] = 'AGREEMENT_OR_UNSPECIFIED'
    # Do not send generic political/PR articles to review merely because
    # the model matched a finance term in their body.
    blocked = {'POLITICAL_NONFINANCIAL', 'LOW_MATERIALITY_PROMOTIONAL', 'FOREIGN_DOMESTIC_NO_LOCAL_TRANSMISSION',
               'NONTRANSACTIONAL_BANK_COURTESY_MEETING'}
    if raw == 'KEEP' and result['decision'] == 'EXCLUDE' and result['reason'] not in blocked:
        # The model's KEEP earns a review only when the headline shows a
        # financial-sector subject; the exclusion reason stays on record.
        why_not = financial_signal(article) if article is not None else ''
        if why_not:
            result.update(reason='MODEL_KEEP_OVERRULED_' + why_not)
        else:
            result.update(decision='REVIEW', reason='FINBERT_KEEP_RULE_EXCLUSION_REVIEW')
    # Stage-B WATCH is not a production veto: v5 test performance was poor.
    return result
