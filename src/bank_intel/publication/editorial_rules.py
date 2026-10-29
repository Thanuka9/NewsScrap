"""Shared materiality rules for the legacy builder and publication authority."""
from __future__ import annotations
import re


def nontransactional_bank_visit(row: dict) -> bool:
    from .decisions import licensed_bank_in, RISK_ACTION
    headline = str(row.get('headline') or '')
    if not licensed_bank_in(headline):
        return False
    if not re.search(r'(?i)\b(?:welcomes?|hosts?|hosted|courtesy|visits?|bilateral (?:financial )?talks)\b', headline):
        return False
    # Customer service, appointments and completed financial outcomes are not
    # ceremonial visits merely because the headline also says "welcomes".
    if RISK_ACTION.search(headline) or re.search(
        r'(?i)\b(?:sign\w*|launch\w*|approv\w*|issu\w*|raises?|secur\w*|disburs\w*|acquir\w*|'
        r'agreement|facility|debt|remittance|clearing|director|appoint\w*|customers?)\b', headline):
        return False
    if not re.search(r'(?i)\b(?:delegation|president|chairman|chairperson|ambassador|courtesy|bilateral|visit\w*)\b', headline):
        return False
    body = str(row.get('body') or row.get('summary') or '')
    outcome = r'\b(?:signed|executed|approved|disbursed|launched)\b'
    subject = r'\b(?:financing|loan|facility|agreement|memorandum|mou|clearing|capital|bond|debt|product)\b'
    # A possible outcome anywhere in the source deserves ordinary assessment;
    # this conservative exclusion must not hide a real transaction.
    if re.search('(?i)'+outcome+r'.{0,90}'+subject+'|'+subject+r'.{0,90}'+outcome, body):
        return False
    return True
