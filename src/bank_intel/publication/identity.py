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
 """Deterministic keys only for statistic/market series whose identity is a
 reported period or trading session. Everything else is matched by
 `same_event` from entities, event type, figures and wording."""
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
 """Bound series keys to observed dates, never a release-date patch."""
 key=_event_key(article)
 if not key or key.startswith(('LK_EXPORTS_','GREEN_BOND_LUX_','CSE_DAILY_SESSION_')):return key
 published=str(article.get('publication_date') or '')[:10]
 try:dt.date.fromisoformat(published)
 except ValueError:return ''
 return key+'@'+published


def tokens(t: str) -> set[str]:
 ignored={'sri','lanka','bank','banks','the','for','and','with','from','this','that','more','than','its','new','have','into','over','under','will','are','were','also','first','after','today','says','said'}
 return {x for x in re.findall(r'[a-z0-9]+',(t or '').casefold()) if len(x)>2 and x not in ignored}


# Event types: two reports about one organisation are the same event only when
# they describe the same kind of development (a rating is not a debenture plan).
EVENT_TYPES=(
 ('RATING',r'\b(?:ratings?|rated|fitch|moody.s|icra|upgrades?|downgrades?)\b'),
 ('RESULTS',r'\b(?:pat|profits?|earnings|results|financials|[1-4]q|q[1-4]|quarter|interim)\b'),
 ('ISSUANCE',r'\b(?:debentures?|bonds? issue|green bonds?|subordinated|tier 2|ipo|rights issue|oversubscri\w*|listed debt|listing)\b'),
 ('AUCTION',r'\b(?:auctions?|bids?|t.bills?|treasury bills?|treasury bonds?)\b'),
 ('MARKET',r'\b(?:cse|aspi|bourse|stock market|share market|stocks?|secondary (?:bond )?market|yields?|indices|turnover)\b'),
 ('STATISTIC',r'\b(?:inflation|ncpi|ccpi|exports?|imports?|remittances?|touris[mt]s?|arrivals|reserves|gdp|growth|unemployment|private credit)\b'),
 ('APPOINTMENT',r'\b(?:ceo|chairman|chairperson|appoint\w*|board|director|leadership|elected|president)\b'),
 ('FRAUD_CYBER',r'\b(?:fraud\w*|scams?|cyber\w*|breach|attacks?|phishing|deepfake|money laundering|vulnerabilit\w*)\b'),
 ('PARTNERSHIP',r'\b(?:partner\w*|join forces|tie.up|mou|agreement|bancassurance|collaborat\w*|alliance)\b'),
 ('PRODUCT',r'\b(?:launch\w*|introduc\w*|unveil\w*|offers?|cards?|app|platform|leasing|loans?|scheme)\b'),
 ('POLICY',r'\b(?:budget|tax\w*|cabinet|bill|act|regulat\w*|directions?|circular|imf|policy|reform)\b'),
)
# Series whose consecutive releases share wording but differ in their figures.
SERIES=('MARKET','STATISTIC','AUCTION')
SERIES_TOPICS=(
 ('INFLATION',r'\b(?:inflation|ncpi|ccpi|consumer price)\b'),
 ('EXPORTS',r'\bexports?\b'),('IMPORTS',r'\bimports?\b'),('REMITTANCES',r'\bremittances?\b'),
 ('TOURISM',r'\b(?:touris[mt]s?|arrivals)\b'),('RESERVES',r'\breserves\b'),
 ('EQUITIES',r'\b(?:cse|aspi|bourse|stock market|share market|stocks?)\b'),
 ('GOVT_SECURITIES',r'\b(?:secondary (?:bond )?market|bond market|yields?|t.bills?|treasury (?:bills?|bonds?)|auctions?)\b'),
 ('GROWTH',r'\b(?:gdp|economic growth|economy grows)\b'),('LABOUR',r'\b(?:unemployment|labour force)\b'),
 ('CREDIT',r'\b(?:private credit|credit growth|lending)\b'),
)


def series_topics(article: dict) -> set[str]:
 h=str(article.get('headline') or '')
 return {name for name,pattern in SERIES_TOPICS if re.search(pattern,h,re.I)}
GENERIC_NAME={'sri','lanka','lankan','lankans','colombo','the','a','an','of','and','in','on','for','to','at','by','with','as','is',
 'plc','ltd','limited','ratings','group','company','co','holdings','bank','banks','banking','corporation','rs','lkr','us','usd',
 'mr','ms','dr','govt','government','president','minister','ministry','says','new','first','this','it','from',
 'monday','tuesday','wednesday','thursday','friday','saturday','sunday','january','february','march','april','may',
 'june','july','august','september','october','november','december','sept','oct','nov'}
# Words too common in business headlines to identify a specific story.
COMMON_WORDS={'sri','lanka','lankan','bank','banks','new','first','plc','says','said','rs','bn','mn','million','billion',
 'year','years','month','months','week','sector','market','markets','growth','amid','after','over','with','from',
 'into','for','and','the','its','their','this','that','will','are','was','were','has','have','more','than','also'}


def _stem(word: str) -> str:
 for suffix in ('ings','ing','ies','ied','es','ed','s'):
  if len(word)>len(suffix)+3 and word.endswith(suffix):return word[:-len(suffix)]
 return word


def content_tokens(text: str) -> set[str]:
 return {_stem(x) for x in re.findall(r'[a-z][a-z0-9]+',(text or '').casefold()) if len(x)>2 and x not in COMMON_WORDS}


def _registry():
 global _REGISTRY
 if _REGISTRY is None:
  try:
   from bank_intel.intelligence.entity_registry import EntityRegistry
   _REGISTRY=EntityRegistry.from_yaml()
  except Exception:  # Registry is a helper; matching must still work without it.
   _REGISTRY=False
 return _REGISTRY
_REGISTRY=None


def _lead(article: dict) -> str:
 return str(article.get('body') or article.get('summary') or '')[:450]


def entities(article: dict) -> set[str]:
 """Organisations the story is about: registry institutions plus named
 organisations that the headline shares with the article's opening text."""
 h=str(article.get('headline') or '');lead=_lead(article);found=set()
 registry=_registry()
 if registry:
  matches=registry.match(h+' . '+lead[:250])
  aliases=[a.casefold() for m in matches for a in m.matched_aliases]
  for m in matches:
   # 'HNB FINANCE' is a finance company, not Hatton National Bank.
   if any(re.search(re.escape(alias)+r'\s+finance\b',h+' '+lead[:250],re.I) for alias in m.matched_aliases):continue
   # 'Bank of Ceylon' inside 'Commercial Bank of Ceylon' is not a second bank.
   if all(any(a.casefold()!=b and a.casefold() in b for b in aliases) for a in m.matched_aliases):continue
   found.add(m.canonical_name.casefold())
 headline_words={_stem(w) for w in re.findall(r'[a-z0-9]+',h.casefold())}
 # Title-case headlines capitalise every word, so names come from the
 # sentence-case lead and must also appear in the headline.
 for m in re.finditer(r"[A-Z][A-Za-z0-9&'’-]*(?:\s+(?:[A-Z][A-Za-z0-9&'’-]*|of|and|&))*",lead):
  words=m.group().split()
  at_sentence_start=m.start()==0 or lead[:m.start()].rstrip()[-1:] in '.!?"“‘'
  if at_sentence_start and len(words)==1:continue
  for word in re.findall(r'[a-z0-9]+',m.group().casefold()):
   if word in GENERIC_NAME or len(word)<3 or word.isdigit():continue
   if _stem(word) in headline_words:found.add('name:'+_stem(word))
 # Short headline acronyms (ADB, IMF, NDB, PDMO) name organisations; longer
 # all-caps words are usually emphasis ('HNB FINANCE').
 for acronym in re.findall(r'\b[A-Z][A-Z0-9]{1,4}\b',h):
  word=acronym.casefold()
  if word not in GENERIC_NAME and len(word)>=3:found.add('name:'+_stem(word))
 return found


_MONEY=re.compile(r'(?i)(rs\.?|lkr|us\$|usd|\$)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(billion|bn|b\b|million|mn|m\b|trillion|tn)?')
_PERCENT=re.compile(r'(?i)([0-9]+(?:\.[0-9]+)?)\s*(?:%|per\s*cent|percent)')
_POINTS=re.compile(r'\b([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?)\b')
_MAGNITUDE=re.compile(r'(?i)(?<![$.,\d])(?<!usd\s)(?<!rs\.\s)(?<!rs\s)(?<!rs\.)(?<!lkr\s)(?<!us\$\s)\b([0-9]+(?:\.[0-9]+)?)\s*(million|mn|m|billion|bn)\b(?!\s*(?:rupees|dollars))')


def figures(text: str) -> set[tuple[str, float]]:
 """Reported amounts, percentages and index levels, normalised for comparison."""
 out=set();t=str(text or '')
 for cur,num,unit in _MONEY.findall(t):
  try:value=float(num.replace(',',''))
  except ValueError:continue
  scale={'billion':1e3,'bn':1e3,'b':1e3,'million':1,'mn':1,'m':1,'trillion':1e6,'tn':1e6}.get((unit or '').casefold(),1e-6)
  if value>0:out.add(('LKR' if cur.casefold().startswith(('rs','lkr')) else 'USD',round(value*scale,6)))
 for num in _PERCENT.findall(t):out.add(('PCT',float(num)))
 for num in _POINTS.findall(t):
  value=float(num.replace(',',''))
  if not 1900<=value<=2100:out.add(('NUM',value))
 # Counts without a currency ('1.8M tourists', '1.8 m arrivals') compare
 # with the same count written out in full ('1,813,484').
 for num,unit in _MAGNITUDE.findall(t):
  try:value=float(num)
  except ValueError:continue
  out.add(('APPROX',value*(1e9 if unit.casefold() in ('billion','bn','b') else 1e6)))
 return out


def _close(a: tuple, b: tuple) -> bool:
 kinds={a[0],b[0]}
 if kinds=={'PCT'}:return abs(a[1]-b[1])<0.051
 if kinds<={'NUM','APPROX'} and 'APPROX' in kinds or kinds<={'LKR'} or kinds<={'USD'}:
  # Publishers round (US$ 13.61 bn vs $ 13.6 b, 1.8 m vs 1,813,484).
  return abs(a[1]-b[1])<=max(abs(a[1]),abs(b[1]))*0.01
 # Exact counts and index levels (ASPI 19,717.92) must agree exactly.
 return kinds=={'NUM'} and abs(a[1]-b[1])<=max(abs(a[1]),abs(b[1]))*1e-5


def shared_figures(a: set, b: set) -> int:
 return sum(1 for x in a if any(_close(x,y) for y in b))


def event_types(article: dict) -> set[str]:
 h=str(article.get('headline') or '')
 return {name for name,pattern in EVENT_TYPES if re.search(pattern,h,re.I)}


# The headline's main development: a rating of a debenture is a rating event,
# a profit announcement with a growth remark is a results event.
TYPE_PRIORITY=('RATING','RESULTS','AUCTION','ISSUANCE','FRAUD_CYBER','APPOINTMENT','PARTNERSHIP','PRODUCT','POLICY','MARKET','STATISTIC')


def main_type(types: set[str]) -> str:
 found=next((t for t in TYPE_PRIORITY if t in types),'')
 # A tie-up and the product it launches are reported either way.
 return 'COMMERCIAL' if found in ('PARTNERSHIP','PRODUCT') else found


def organisations(names: set[str]) -> set[str]:
 """Distinct organisations: a name word already inside a registry name is an alias."""
 registered=[n for n in names if not n.startswith('name:')]
 return set(registered)|{n for n in names if n.startswith('name:') and not any(n[5:] in r for r in registered)}


def _date(article: dict):
 try:return dt.date.fromisoformat(str(article.get('publication_date') or '')[:10])
 except ValueError:return None


def headline_overlap(a: dict, b: dict) -> float:
 x,y=content_tokens(a.get('headline','')),content_tokens(b.get('headline',''))
 return len(x&y)/len(x|y) if x and y else 0.0


def same_event(a: dict, b: dict, max_gap_days: int=7) -> bool:
 """Same underlying development, across publishers and days.

 Evidence combines the article identity, the organisations involved, the
 kind of development, reported figures and headline wording. Recurring
 series (markets, statistics, auctions) match only on agreeing figures so a
 new release or trading session is never mistaken for the previous one.
 """
 if canonical(a.get('source_url',''))==canonical(b.get('source_url','')) and canonical(a.get('source_url','')): return True
 aa=publisher_article_id(a.get('source_url',''))
 if aa and aa==publisher_article_id(b.get('source_url','')):return True
 ka,kb=event_signature(a),event_signature(b)
 if ka and ka==kb: return True
 da,db=_date(a),_date(b)
 gap=abs((da-db).days) if da and db else None
 if gap is not None and gap>max_gap_days:return False
 ta,tb=event_types(a),event_types(b)
 if ta and tb and main_type(ta)!=main_type(tb):return False
 ea,eb=entities(a),entities(b)
 shared=ea&eb
 overlap=headline_overlap(a,b)
 fa=figures(a.get('headline','')+' '+_lead(a));fb=figures(b.get('headline','')+' '+_lead(b))
 common=shared_figures(fa,fb)
 # Headline figures are the reported fact; agreeing on one is strong evidence.
 headline_common=shared_figures(figures(a.get('headline','')),figures(b.get('headline','')))
 if (ta|tb)&set(SERIES) and not (ta|tb)-set(SERIES)-{'POLICY'}:
  # A new release or session repeats the wording but not the figures.
  needed=1 if gap==0 else 2
  related=bool(shared) or bool(series_topics(a)&series_topics(b)) or overlap>=0.2
  return (common>=needed or headline_common>=1) and related
 if shared:
  if headline_common or common>=2:return True
  # Commentary on a shared topic recurs across days; require closer wording.
  if overlap>=(0.3 if gap==0 else 0.4):return True
  # Two named counterparties together (bank + partner) identify a deal.
  orgs=organisations(shared)
  if len(orgs)>=2 and any(not o.startswith('name:') for o in orgs) and ta&tb:return True
  return False
 return overlap>=0.6 and (gap==0 or common>=1)
