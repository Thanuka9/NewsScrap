"""Source identity, calendar and factual event matching. stdlib only."""
from __future__ import annotations
import datetime as dt
import re
from urllib.parse import urlsplit, urlunsplit

SOURCES = {
 'Daily FT': ('ft.lk',),
 'Daily Mirror': ('dailymirror.lk',),
 'Daily News': ('dailynews.lk',),
 'The Morning': ('themorning.lk',),
 'The Island': ('island.lk',),
 'NewsFirst': ('newsfirst.lk',),
 'Ceylon Today': ('ceylontoday.lk',),
}


def canonical(url: str) -> str:
 p=urlsplit((url or '').strip())
 if p.scheme.lower() not in ('https','http') or not p.hostname: return ''
 host=p.hostname.casefold().removeprefix('www.')
 path=re.sub('/+', '/',p.path).rstrip('/') or '/'
 return urlunsplit(('https', host, path, '', ''))



def publisher_article_id(url: str) -> str:
 """Stable publisher-level ID only where the URL carries an unambiguous ID.

 Daily Mirror serves the SAME article on both /business/<category>-<id>
 and /business-news/<category>-<id> paths. Do not use fuzzy URLs from
 other publishers as asserted equivalents.
 """
 p=urlsplit((url or '').strip())
 host=(p.hostname or '').casefold().removeprefix('www.')
 if host=='dailymirror.lk':
  m=re.search(r'/(?:[0-9]{2,4})-(\d{5,9})/?$',p.path)
  if m:return 'DAILY_MIRROR_ARTICLE_'+m.group(1)
 return ''

def approved(source: str, url: str) -> bool:
 p=urlsplit(url)
 h=(p.hostname or '').casefold()
 return (p.scheme in ('http','https') and
         any(h==x or h.endswith('.'+x) for x in SOURCES.get(source, ())))


def first_window_day(target: dt.date) -> dt.date:
 # Include the period following the last normal business-day edition.
 return target-dt.timedelta(days=(3 if target.weekday()==0 else 1))


def market_session(article: dict) -> str:
 h=str(article.get('headline') or '').casefold()
 t=str(article.get('body') or article.get('summary') or '')
 if not re.search(r'\b(aspi|cse|stocks?|shares?|bourse|indices|stock market)\b',h): return ''
 m=re.search(r'\b(1[5-9]|2[0-9]),\d{3}\.\d{1,2}\b',t)
 if m: return 'CSE_ASPI_'+m.group().replace(',','')
 return ''


def _event_key(article: dict) -> str:
 """Only high-confidence events get signatures. No generic IMF or CSE keys."""
 h=str(article.get('headline') or '').casefold()
 body=str(article.get('body') or article.get('summary') or '').casefold()
 t=h+' '+body[:1800]
 # Recurring statistics are bounded by their reported period and amount.
 year=re.search(r'\b20\d{2}\b',t)
 if ('export' in h and 'sri lanka' in t and year
     and re.search(r'first nine months|9m\b|nine.month',t)):
  amount=re.search(r'(?:us\$|\$)\s*(\d+(?:\.\d+)?)\s*(?:billion|bn|b\b)',body)
  period_amount=re.search(r'(?:ytd|year.to.date) (?:earnings|exports).{0,65}?(?:us\$|\$)\s*(\d+(?:\.\d+)?)\s*(?:billion|bn|b\b)',body)
  if period_amount:amount=period_amount
  if amount:return 'LK_EXPORTS_9M_'+year.group()+'_'+amount.group(1)
 if 'green bond' in h and re.search(r'luxembourg|luxse',t):
  issuer=re.match(r'([a-z][a-z ]+?)\s+(?:plc\s+)?(?:becomes|lists|has|announced)',h)
  amount=re.search(r'(?:rs\.?|lkr)\s*(\d+(?:\.\d+)?)\s*(billion|million)',t)
  pub=str(article.get('publication_date') or '')[:7]
  if issuer and amount and pub:
   return 'GREEN_BOND_LUX_'+issuer.group(1).strip().replace(' ','_')+'_'+amount.group(1)+'_'+amount.group(2)+'_'+pub
 if ('adb' in t or 'asian development bank' in t) and re.search(r'(?:100\s*(?:million|mn|m\b)|\$\s*100\s*(?:million|m\b))',t) and ('social protection' in t or 'eldercare' in t or 'inclusive recovery' in t):
  return 'ADB_SOCIAL_PROTECTION_100M'
 if 'fitch' in h and 'national' in h and 'rating' in h and re.search(r'recalibrat', h):
  return 'FITCH_SRI_LANKA_NATIONAL_RECALIBRATION'
 if ('fake bank' in h or 'bank reward scam' in h or 'bank giveaway' in h) and ('vap poya' in t or 'reward' in t or 'giveaway' in t):
  return 'BANK_IMPERSONATION_VAP_POYA'
 if ('slic life' in h or 'sri lanka insurance corporation life' in t or 'sri lanka insurance life' in t) and '247' in t and ('life fund' in t or 'bonus' in t):
  return 'SLIC_LIFE_247BN_FUND'
 if re.search(r'(?i)\b(?:touris[tm]|tourist arrivals|tourists)\b',h) and (('1,813,484' in t) or ('119,805' in t) or ('1.8m' in h) or ('1.8 m' in h)) and ('october' in t or 'october' in h):
  return 'SRI_LANKA_TOURISM_ARRIVALS'
 if 'shirin hamid' in t and ('oration' in t or 'intelligent central bank' in t or 'imf chief information officer' in t):
  return 'CBSL_ORATION_SHIRIN_HAMID'
 if 'sampath bank' in t and 'debenture' in h and re.search(r'oversubscri|subscriptions?',t):
  return 'SAMPATH_DEBENTURES_OVERSUB'
 if ('people’s bank' in h or "people's bank" in h or 'peoples bank' in h) and 'lanka securities' in t and ('trading' in h or 'share' in h):
  return 'PEOPLES_BANK_LANKA_SECURITIES_APP'
 if 'asia asset finance' in h and ('debenture' in h or 'raise rs' in h):
  return 'ASIA_ASSET_FINANCE_DEBENTURE'
 if 'asia asset finance' in h and ('mcnair' in h or 'board' in h):
  return 'ASIA_ASSET_FINANCE_MCNAIR_BOARD'
 # Narrow event identities require BOTH the institution and its counterpart or
 # transaction facts, not generic banking/financing vocabulary.
 if ('boc' in h.split() or 'bank of ceylon' in h or 'bank of ceylon' in t[:250]) and ('debenture' in t or 'listed debt issue' in h) and re.search(r'(?:30\s*(?:billion|bn|b\b)|30,000\s*million)',t) and ('tier 2' in t or 'subordinated' in t or 'largest' in h):
  return 'BOC_TIER2_DEBENTURE_30BN'
 if 'hnb finance' in t and 'toyota lanka' in t and ('leas' in t or 'vehicle financ' in t):
  return 'HNB_FINANCE_TOYOTA_LEASING'
 if 'seylan bank' in t and 'fairway latitude' in t and ('home' in t or 'hous' in t or 'mortgage' in t):
  return 'SEYLAN_FAIRWAY_LATITUDE_HOUSING'
 if ('cbsl' in t or 'central bank' in t) and ('early settlement' in t or 'early-settlement' in t) and ('parate' in t) and ('review' in t or 'complaint' in t):
  return 'CBSL_EARLY_SETTLEMENT_PARATE_THEMATIC'
 if ('nbfi' in t or 'finance compan' in t) and 'stage 2' in t and ('52.4' in t or 'household borrowers' in t):
  return 'NBFI_HOUSEHOLD_STAGE2_RISK'

 if ('pdmo' in t or 'public debt management office' in t) and 'domestic dollar bond' in t and ('reject' in t or 'no bids' in t):
  return 'PDMO_DOMESTIC_DOLLAR_BOND_BIDS_REJECTED'
 if ('union assurance' in t and ('nations trust bank' in t or re.search(r'\bntb\b',t)) and 'bancassurance' in t):
  return 'NTB_UNION_ASSURANCE_BANCASSURANCE_RENEWAL'
 if 'pine labs' in h and 'webxpay' in h: return 'PINE_LABS_WEBXPAY_PAYMENTS'
 if re.search(r'\bndb\b|national development bank', h) and re.search(r'earnings|profit|\bpat\b|records|financials',h) and re.search(r'3q|q3|third quarter|september quarter',h):
  return 'NDB_Q3_EARNINGS'
 # A Tuesday–Friday publisher's "yesterday" ASPI trading report refers to
 # the preceding trading day. This groups same-session headlines WITHOUT
 # claiming their reported numbers agree; conflicting figures are reviewed
 # independently before publication. Monday/weekend recaps are excluded from
 # this shortcut because Friday is not yesterday on a Monday.
 if re.search(r'\b(?:cse|stock market|aspi|bourse|indices)\b',h) and 'yesterday' in body and re.search(r'\b(?:aspi|all share price index)\b',body):
  try:published=dt.date.fromisoformat(str(article.get('publication_date') or '')[:10])
  except ValueError:published=None
  if published and 1<=published.weekday()<=4:
   return 'CSE_DAILY_SESSION_'+(published-dt.timedelta(days=1)).isoformat()
 # Only exact observed market close is merged, never broad market descriptions.
 mk=market_session(article)
 if mk and re.search(r'\b(close|closed|ended|ends|slips?|falls?|down|slide|plummet|red|declin)',h): return mk
 return ''


def event_signature(article: dict) -> str:
 """Bound recurring entity/action keys to observed dates, never a release date patch.

 Explicit period/market-session keys already carry a date or accounting period.
 Other keys need a source publication month; without a date do not assert an identity.
 """
 key=_event_key(article)
 if not key or key.startswith(('LK_EXPORTS_','GREEN_BOND_LUX_','CSE_DAILY_SESSION_')):return key
 published=str(article.get('publication_date') or '')[:10]
 try:day=dt.date.fromisoformat(published)
 except ValueError:return ''
 if key.startswith('CSE_ASPI_'):
  return key+'@'+published
 return key+'@'+day.strftime('%Y-%m')


def tokens(t: str) -> set[str]:
 ignored={'sri','lanka','bank','banks','the','for','and','with','from','this','that','more','than','its','new','have','into','over','under','will','are','were','also','first','after','today','says','said'}
 return {x for x in re.findall(r'[a-z0-9]+',(t or '').casefold()) if len(x)>2 and x not in ignored}


def same_event(a: dict, b: dict) -> bool:
 if canonical(a.get('source_url',''))==canonical(b.get('source_url','')) and canonical(a.get('source_url','')): return True
 aa=publisher_article_id(a.get('source_url',''))
 if aa and aa==publisher_article_id(b.get('source_url','')):return True
 ka,kb=event_signature(a),event_signature(b)
 if ka and ka==kb: return True
 # Same exact normalized issuer/headline only if same date, preserving daily updates.
 if str(a.get('publication_date'))[:10]!=str(b.get('publication_date'))[:10]: return False
 x,y=tokens(a.get('headline','')),tokens(b.get('headline',''))
 return bool(len(x)>=5 and len(y)>=5 and x==y)
