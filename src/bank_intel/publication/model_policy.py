"""Conservative FinBERT/rule reconciliation.

The model is an observed signal, not a license to publish unverified text.
A confident direct-bank rule may recover Stage-A NOISE. A KEEP veto by later
rules is surfaced for human review, not silently changed or hidden.
"""
from __future__ import annotations


def reconcile(assessment: dict, trace: dict | None) -> dict:
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
    blocked = {'POLITICAL_NONFINANCIAL', 'LOW_MATERIALITY_PROMOTIONAL', 'FOREIGN_DOMESTIC_NO_LOCAL_TRANSMISSION'}
    if raw == 'KEEP' and result['decision'] == 'EXCLUDE' and result['reason'] not in blocked:
        result.update(decision='REVIEW', reason='FINBERT_KEEP_RULE_EXCLUSION_REVIEW')
    # Stage-B WATCH is not a production veto: v5 test performance was poor.
    return result
