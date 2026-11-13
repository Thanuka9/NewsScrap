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
 'EconomyNext': ('economynext.com',),
 'EconomyNews': ('economynews.lk',),
}

SOURCE_KEYS = {
 'Daily FT': 'daily_ft', 'Daily Mirror': 'daily_mirror', 'Daily News': 'daily_news',
 'The Morning': 'the_morning', 'The Island': 'island', 'NewsFirst': 'newsfirst',
 'Ceylon Today': 'ceylon_today', 'EconomyNext': 'economynext', 'EconomyNews': 'economynews',
}
SOURCE_START_DATES = {'EconomyNext': '2026-10-29', 'EconomyNews': '2026-10-29'}


def sources_for_date(report_date) -> dict:
 """Coverage obligations at the edition date; preserve historical replays."""
 return {name: domains for name, domains in SOURCES.items()
         if str(report_date)[:10] >= SOURCE_START_DATES.get(name, '0001-01-01')}


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
 # Daily FT keeps the number and varies the slug (Govt-to-, Govt--to-).
 if host=='ft.lk':
  m=re.search(r'/(?:[0-9]{2})-(\d{6,9})/?$',p.path)
  if m:return 'DAILY_FT_ARTICLE_'+m.group(1)
 return ''

def approved(source: str, url: str) -> bool:
 p=urlsplit(url)
 h=(p.hostname or '').casefold()
 return (p.scheme in ('http','https') and
         any(h==x or h.endswith('.'+x) for x in SOURCES.get(source, ())))


def first_window_day(target: dt.date) -> dt.date:
 # Include the period following the last normal business-day edition.
 return target-dt.timedelta(days=(3 if target.weekday()==0 else 1))


MONTHS=('january','february','march','april','may','june','july','august','september','october','november','december')
WEEKDAYS=('monday','tuesday','wednesday','thursday','friday','saturday','sunday')
FITCH_NATIONAL_RATINGS='FITCH_NATIONAL_RATINGS'
MARKET_HEADLINE=re.compile(r'\b(?:aspi|cse|stocks|bourse|indices|stock market)\b')


def closing_levels(article: dict) -> list[str]:
 # Index levels in the opening text, the close first; two sessions never close alike.
 return re.findall(r'\b(?:1[5-9]|2[0-9]),\d{3}\.\d{1,2}\b',str(article.get('body') or article.get('summary') or '')[:600])


def reported_aspi_fall(article: dict) -> str:
 # Points the ASPI fell by; the S&P SL20 sentence that follows is a different index.
 m=re.search(r'(?i)(?:\bASPI\b|All Share Price Index)(?:(?!S&P|SL20).){0,130}?(?:down|fell|declined)(?:(?!S&P|SL20).){0,35}?([0-9]{1,3}\.[0-9]{1,2})\s+points\b',
             str(article.get('body') or ''))
 return m.group(1) if m else ''


def opening_market_update(article: dict) -> bool:
 h=str(article.get('headline') or '').casefold()
 lead=str(article.get('body') or article.get('summary') or '')[:220].casefold()
 if not MARKET_HEADLINE.search(h) or re.search(r'\b(?:close[sd]?|ended|ends)\b',h+' '+lead):return False
 return bool(re.search(r'\b(?:opened (?:up|down|flat|higher|lower)|mid.?day|trend(?:s|ing|ed)? (?:up|down))\b',h+' '+lead))


def rupee_quote(article: dict) -> str:
 # The day's dealer quote, '332.75/80'; the next day's quote is close but not equal.
 m=re.search(r'(?i)\brupee\b.{0,40}?\b(\d{3}\.\d{2}/\d{2})\b',str(article.get('headline') or ''))
 return m.group(1) if m else ''


def session_date(article: dict) -> dt.date | None:
 # The trading day a report describes. Publishers report the same close on
 # the day or the morning after, and a sent story can carry the edition date.
 try:published=dt.date.fromisoformat(str(article.get('publication_date') or '')[:10])
 except ValueError:return None
 body=str(article.get('body') or article.get('summary') or '').casefold()
 lead=body[:500]
 stated=re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+('+'|'.join(MONTHS)+r')\b',lead)
 if stated:
  try:
   day=dt.date(published.year,MONTHS.index(stated.group(2))+1,int(stated.group(1)))
   if 0<=(published-day).days<=6:return day
  except ValueError:pass
 # 'Closed sharply higher on Thursday' names the session; a later 'began yesterday' does not.
 closed_on=re.search(r'\b(?:closed|ended|close[sd]?)\b[^.]{0,80}?\bon ('+'|'.join(WEEKDAYS)+r')\b',lead)
 if closed_on:return published-dt.timedelta(days=(published.weekday()-WEEKDAYS.index(closed_on.group(1)))%7)
 if 'yesterday' in body:
  # Friday is not yesterday on a Monday; a weekend paper's Saturday is.
  return published-dt.timedelta(days=1) if 1<=published.weekday()<=5 else None
 for index,name in enumerate(WEEKDAYS):
  if re.search(r'\b'+name+r'\b',lead):return published-dt.timedelta(days=(published.weekday()-index)%7)
 return published


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
 # One month's current account is one release, whoever reports it and when.
 if 'current account' in h:
  period=re.search(r'\b('+'|'.join(m[:3] for m in MONTHS)+r')[a-z]*\b',h+' '+body[:300])
  published=_date(article)
  if period and published:
   # Reports quote last year's figure too, so the year comes from the date.
   month=[m[:3] for m in MONTHS].index(period.group(1))+1
   year=published.year-1 if month>published.month else published.year
   return f'LK_CURRENT_ACCOUNT_{year}_{period.group(1)}'
 # One agency review of national ratings is one event across the banks it names.
 if 'fitch' in h and re.search(r'\b(?:upgrades?|downgrades?|revises?|takes? action|affirms?|assigns?|tops?|rates)\b|\bnotch',h) and re.search(r'\(lka\)|national (?:long.term )?ratings?',t):
  # Finance companies follow their parent banks in a separate rating action.
  return FITCH_NATIONAL_RATINGS+('_NBFI' if re.search(r'\bnbfis?\b|financ(?:e|ing) compan|leasing',h) else '')
 # One closing session is one event, however a publisher words or dates it.
 # This groups same-session reports WITHOUT claiming their numbers agree;
 # conflicting figures are reviewed independently before publication.
 # Only an observed close is merged, never a broad market description.
 if (MARKET_HEADLINE.search(h) or re.search(r'\b(?:aspi|all share price index)\b',body[:120])) and not opening_market_update(article):
  reported=('yesterday' in body and re.search(r'\b(?:aspi|all share price index)\b',body)) or (
   re.search(r'\b(close|closed|ended|ends|slips?|falls?|down|slide|plummet|red|declin)',h) and (closing_levels(article) or 'closed' in body[:200]))
  session=session_date(article) if reported else None
  if session:return 'CSE_DAILY_SESSION_'+session.isoformat()
 return ''


def event_signature(article: dict) -> str:
 """Bound series keys to observed dates, never a release-date patch."""
 key=_event_key(article)
 if not key or key.startswith(('LK_EXPORTS_','LK_CURRENT_ACCOUNT_','GREEN_BOND_LUX_','CSE_DAILY_SESSION_')):return key
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
 # Financial-reporting acronyms, not organisations.
 'pat','pbt','eps','npl','npls','ytd','yoy','gdp','ceo','cfo','coo','ipo','sme','smes','fdi','ppp',
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
 # Market moves differ by hundredths (0.62% vs 0.65%); only rounding to one
 # decimal place (8.1 vs 8.10) is the same figure.
 if kinds=={'PCT'}:return abs(a[1]-b[1])<0.006
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
 # Older sent records keep no publication date; the edition still bounds them.
 for value in (article.get('publication_date'),article.get('edition_date')):
  try:return dt.date.fromisoformat(str(value or '')[:10])
  except ValueError:continue
 return None


def headline_overlap(a: dict, b: dict) -> float:
 x,y=content_tokens(a.get('headline','')),content_tokens(b.get('headline',''))
 return len(x&y)/len(x|y) if x and y else 0.0


def url_headline(url: str) -> str:
 '''The publisher's own headline, kept in a link's address ('NDB-and-IFC-expand-...').'''
 path=urlsplit(url or '').path.rstrip('/')
 slug=path.rsplit('/',2)[-2] if re.search(r'/\d{2,4}-\d{5,9}$',path) else path.rsplit('/',1)[-1]
 words=[w for w in re.split(r'[-_]+',slug) if w]
 return ' '.join(words) if len(words)>=4 and not slug.isdigit() else ''


RESULTS=re.compile(r'(?i)\b(?:profit|pat|pbt|results|earnings|net income)\b')


def _specific(figure: tuple) -> bool:
 # Round amounts (Rs. 10 b, 8%) recur across unrelated stories; specific ones do not.
 kind,value=figure
 if kind=='PCT':return value!=int(value) or value in (49,51) or value>20
 if kind in ('LKR','USD'):return value>=1 and (value%1000 if value>=1000 else value%10)!=0
 return kind=='NUM' and value>=1000 and value%1000!=0


def shares_reported_figure(a: dict, b: dict, max_gap_days: int=4) -> bool:
 '''Another paper's report of a story already sent: the same specific figure
 and at least two subject words within a few days, however it is worded.'''
 da,db=_date(a),_date(b)
 if not da or not db or abs((da-db).days)>max_gap_days:return False
 if rupee_quote(a) and rupee_quote(b) and rupee_quote(a)!=rupee_quote(b):return False
 def facts(x,words=120):return str(x.get('headline') or '')+' '+' '.join(str(x.get('body') or x.get('summary') or '').split()[:words])
 mine={f for f in figures(facts(a)) if _specific(f)}
 theirs={f for f in figures(facts(b)) if _specific(f)}
 if not any(_close(f,t) for f in mine for t in theirs):return False
 return len(content_tokens(str(a.get('headline') or ''))&content_tokens(facts(b,60)))>=2


def words_in_facts(a: dict, b: dict) -> float:
 # Share of one headline's words found in the other report's opening text.
 # An editor rewrites a sent headline, but the facts keep the report's words.
 best=0.0
 for one,other in ((a,b),(b,a)):
  words=content_tokens(one.get('headline',''))
  facts=content_tokens(_lead(other))
  if words:best=max(best,len(words&facts)/len(words))
 return best


GENERIC_CAPS={'budget','sri','lanka','lankan','lankans','sl','govt','government','board','bank','banks','central','economy','economic',
 'cabinet','parliament','president','minister','ministry','new','from','beyond','what','how','why','will','the','and','for','with',
 'national','state','public','global','independent','director','directors','chairman','ceo','md','plc','group','holdings'}


MONTH_STEMS={m[:3] for m in MONTHS}


def headline_names(article: dict) -> set[str]:
 '''Proper names written in a headline ('Ravi', 'HSBC'), not generic capitalised words.'''
 words=re.findall(r"\b[A-Z][A-Za-z&'’-]+",str(article.get('headline') or ''))
 # Months ('Sept') and units ('Rs', 'Bn') are in every results headline; they name nothing.
 return {w.casefold() for w in words if len(w)>1 and w.casefold() not in GENERIC_CAPS
         and w.casefold()[:3] not in MONTH_STEMS and w.casefold() not in ('rs','bn','mn','tn','us','usd','lkr','yoy','pct','q1','q2','q3','q4')}


DAILY_SERIES=re.compile(r'(?i)\brupee\b|\bcse\b|\baspi\b|\bindices\b|\bstock market\b|\bbourse\b|\bshares? (?:jump|fall|rise|close)|\bstocks? close|'
                        r'\bt.?bills?\b|\btreasury (?:bills?|bonds?)\b|\bbond (?:market|yields?)\b|\byields?\b|\bsecondary (?:bond )?market\b|'
                        r'\bweek ends\b|\bforeign (?:investors?|selling|buying|holdings)\b')
RESULT_WORDS=re.compile(r'(?i)\b(?:profit|pat|pbt|earnings|results|revenue|total assets|deposits|tn|trillion|nine.month|quarter)\b')


def possible_repeat(a: dict, b: dict, max_gap_days: int=14) -> bool:
 '''A reworded follow-up of a sent story ('Seylan crosses Rs 1 tn in assets' after
 'Seylan nine-month profit'). It goes to an editor; it is never dropped on this alone.'''
 da,db=_date(a),_date(b)
 if not da or not db or abs((da-db).days)>max_gap_days:return False
 # Daily series read alike every day and are new every day.
 if rupee_quote(a) or rupee_quote(b) or event_signature(a).startswith('CSE_DAILY_SESSION_') or DAILY_SERIES.search(str(a.get('headline') or '')):return False
 # Two loans for stated, different purposes ('macro recovery', 'social protection') are two loans.
 fa,fb=financing_identity(a),financing_identity(b)
 if fa.get('purposes') and fb.get('purposes') and not fa['purposes']&fb['purposes']:return False
 names=headline_names(a)&headline_names(b)
 if len(names)>=2:return True
 if names and RESULT_WORDS.search(str(a.get('headline') or '')) and RESULT_WORDS.search(str(b.get('headline') or '')) and abs((da-db).days)<=10:return True
 if wording_overlap(a,b)>=0.40 and named_in_common(a,b,entities(a)&entities(b)):return True
 # Different wording, but the same names, phrase, acronym or speaker within a few days.
 return abs((da-db).days)<=7 and same_subject_signals(a,b)


def named_in_common(a: dict, b: dict, shared: set) -> bool:
 '''A real organisation both name (not Fitch, not a generic word such as 'Board'), or a name in both headlines.'''
 return bool({o for o in organisations(shared) if not o.startswith('name:') and 'fitch' not in o} or headline_names(a)&headline_names(b))


TITLE_PERSON=re.compile(r"\b(?:Dr|Mr|Mrs|Ms|Prof|Eng)\.?\s+((?:[A-Z][a-z]+\s+){1,3}[A-Z][a-z]+)")
PHRASE_STOP={'sri','lanka','lankan','lankas','govt','government','new','says','said','will','over','with','from','after','amid','into','for','and','the',
             'its','to','of','in','on','at','by','as','up','down','more','than','first','year','years','day','week','month','rs','bn','mn','b','m'}


def headline_phrases(article: dict) -> set[str]:
 '''Two-word phrases in a headline ('revenue strategy', 'capital market', 'pyramid scheme').'''
 words=[w[:-1] if len(w)>4 and w.endswith('s') and not w.endswith('ss') else w
        for w in re.findall(r"[a-z]+",str(article.get('headline') or '').casefold())]
 return {a+' '+b for a,b in zip(words,words[1:]) if a not in PHRASE_STOP and b not in PHRASE_STOP and len(a)>2 and len(b)>2}


def acronyms(article: dict) -> set[str]:
 '''Initials of capitalised names in a headline: 'Asia Frontier Capital' -> 'afc'.'''
 found=set()
 for run in re.findall(r"(?:[A-Z][a-z]+(?:\s+|$)){2,5}",str(article.get('headline') or '')):
  found.add(''.join(w[0] for w in run.split()).casefold())
 return {x for x in found if len(x)>=3}


def people(article: dict) -> set[str]:
 '''Titled people named in the headline or lead ('Dr Anil Jayantha Fernando').'''
 text=str(article.get('headline') or '')+' '+_lead(article)[:700]
 return {m.group(1).casefold() for m in TITLE_PERSON.finditer(text)}


def same_subject_signals(a: dict, b: dict) -> bool:
 '''Different wording, but the same names, phrase or speaker: an editor should look.'''
 ov=wording_overlap(a,b)
 if named_in_common(a,b,entities(a)&entities(b)) and ov>=0.25:return True
 if headline_phrases(a)&headline_phrases(b) and ov>=0.18:return True
 caps=lambda x:{w.casefold() for w in re.findall(r'\b[A-Z]{3,5}\b',str(x.get('headline') or ''))}
 if caps(a)&acronyms(b) or caps(b)&acronyms(a):return True
 return bool(people(a)&people(b)) and ov>=0.15


def possible_duplicate(a: dict, b: dict) -> bool:
 '''Two stories the same day that may be one development written very differently.'''
 da,db=_date(a),_date(b)
 if not da or not db or abs((da-db).days)>1:return False
 if DAILY_SERIES.search(str(a.get('headline') or '')) or DAILY_SERIES.search(str(b.get('headline') or '')):return False
 fa,fb=financing_identity(a),financing_identity(b)
 if fa.get('purposes') and fb.get('purposes') and not fa['purposes']&fb['purposes']:return False
 # Two companies' results share the template, not the story.
 if RESULT_WORDS.search(str(a.get('headline') or '')) and RESULT_WORDS.search(str(b.get('headline') or '')) and not headline_names(a)&headline_names(b):return False
 return same_subject_signals(a,b)


def wording_overlap(a: dict, b: dict) -> float:
 '''Share of the shorter report's headline-and-lead words that the other uses too.'''
 def toks(x):return content_tokens(str(x.get('headline') or '')+' '+' '.join(_lead(x).split()[:70]))
 ta,tb=toks(a),toks(b)
 return len(ta&tb)/min(len(ta),len(tb)) if ta and tb else 0.0


BOND_SESSION=re.compile(r'(?i)\b(?:secondary (?:bond )?market|bond (?:market )?yields?|long.end yields?|yields? (?:edge|drop|fall|rise|decline|consolidate|continue|see)|yield curve)\b')
BUDGET_WORD=re.compile(r'(?i)\bbudget\b')
# Presented in Parliament on 12 Nov 2026. Before it, Budget stories are previews.
BUDGET_DAY=dt.date(2026,11,13)


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
 # Shared lenders and amounts recur across unrelated programmes. Resolve
 # purpose and transaction stage before broad entity/figure similarity.
 financing_a, financing_b = financing_identity(a), financing_identity(b)
 if financing_a or financing_b:
  if not (financing_a and financing_b):return False
  if financing_a['purposes'] and financing_b['purposes'] and not (financing_a['purposes'] & financing_b['purposes']):return False
  if financing_a['stage'] and financing_b['stage'] and financing_a['stage'] != financing_b['stage']:return False
  if not _date(a) or not _date(b):return False
  # With no stated purpose on either side, wording and figures decide below.
  if (financing_a['purposes'] or financing_b['purposes']) and not financing_a['purposes'] & financing_b['purposes']:return False
  # Same lender, purpose, stage and amount is the same transaction, whatever
  # kind of development the wording of each report suggests.
  if (financing_a['stage'] and financing_a['stage']==financing_b['stage'] and entities(a)&entities(b)
      and shared_figures(figures(a.get('headline','')+' '+_lead(a)),figures(b.get('headline','')+' '+_lead(b)))):return True
 # Aggregate exports and a sector's export release are distinct observations.
 if 'export' in str(a.get('headline','')).lower() and 'export' in str(b.get('headline','')).lower():
  if export_scope(a) != export_scope(b):return False
 # Two different rupee quotes are two different trading days.
 qa,qb=rupee_quote(a),rupee_quote(b)
 if qa and qb and qa!=qb:return False
 # Dealers' daily bond-market reports quote the same maturities every day; the
 # shared yields (11.10%) do not make Wednesday's session Tuesday's (13 Nov 2026).
 if BOND_SESSION.search(str(a.get('headline') or '')) and BOND_SESSION.search(str(b.get('headline') or '')) \
    and _date(a) and _date(b) and _date(a)!=_date(b):return False
 # A report of the Budget as presented is not a preview of it ("Budget aims at
 # economic transformation", 13 Nov, against a 9 Nov "how will it spend" preview).
 if BUDGET_WORD.search(str(a.get('headline') or '')+str(b.get('headline') or '')) and _date(a) and _date(b) \
    and (_date(a)>=BUDGET_DAY)!=(_date(b)>=BUDGET_DAY):return False
 ka,kb=event_signature(a),event_signature(b)
 # Reports of one ratings review follow for days; a later review is a new event.
 if ka.startswith(FITCH_NATIONAL_RATINGS) and kb.startswith(FITCH_NATIONAL_RATINGS) and _date(a) and _date(b):
  if ('_NBFI' in ka)!=('_NBFI' in kb):return False
  # Fitch acts on several institutions in one week. Reports belong together when
  # they name the same institution (Fitch itself does not count) or are both
  # part of one sweep: the scale recalibration, or a several-bank action.
  def sweep(x):
   h=str(x.get('headline') or '')
   return bool(re.search(r'(?i)recalibrat|rating scale',h+' '+_lead(x)) or re.search(
    r'(?i)\b(?:\d+|two|three|four|five|six|seven|eight|nine|ten)\b[^.]{0,30}\b(?:banks|nbfis|lenders|institutions|finance companies)\b',h))
  same_institution={o for o in organisations(entities(a)&entities(b)) if 'fitch' not in o}
  if not same_institution and not (sweep(a) and sweep(b)):return False
  gap=abs((_date(a)-_date(b)).days)
  if gap<=3:return True
  # A bank's own release about the same upgrade follows a week or more later:
  # the same institution and the same new rating is the same action.
  def grades(x):return {g.replace('−','-').upper() for g in re.findall(r'\b[ABC]{1,3}[+\-−]?\(lka\)',str(x.get('headline',''))+' '+_lead(x),re.I)}
  # One sweep reported again later ("Fitch upgrades three ... NBFIs", 13 Nov, after
  # "Fitch upgrades 3 ... NBFIs", sent 5 Nov): the same action on the same number
  # of institutions in the same direction.
  def sweep_action(x):
   h=str(x.get('headline') or '').lower()
   words={'two':'2','three':'3','four':'4','five':'5','six':'6','seven':'7','eight':'8','nine':'9','ten':'10'}
   n=re.search(r'\b(\d+|two|three|four|five|six|seven|eight|nine|ten)\b[^.]{0,30}\b(?:banks|nbfis|lenders|institutions|finance companies)\b',h)
   verb=re.search(r'\b(upgrade|downgrade|affirm|revise|takes? action)',h)
   return (words.get(n.group(1),n.group(1)) if n else '', verb.group(1) if verb else '')
  if gap<=14 and sweep(a) and sweep(b) and sweep_action(a)==sweep_action(b) and all(sweep_action(a)):return True
  return gap<=14 and bool(grades(a)&grades(b))
 if ka and ka==kb:
  # A sent story may carry its edition date, so the levels confirm the session.
  la,lb=closing_levels(a),closing_levels(b)
  return not (ka.startswith('CSE_DAILY_SESSION_') and la and lb and not set(la)&set(lb))
 # Dated apart by the wording, but the same reported close is the same session.
 if ka.startswith('CSE_DAILY_SESSION_') and kb.startswith('CSE_DAILY_SESSION_'):
  la,lb=closing_levels(a),closing_levels(b)
  if la and lb and la[0]==lb[0]:return True
 da,db=_date(a),_date(b)
 gap=abs((da-db).days) if da and db else None
 if gap is not None and gap>max_gap_days:return False
 ta,tb=event_types(a),event_types(b)
 if ta and tb and main_type(ta)!=main_type(tb):return False
 ea,eb=entities(a),entities(b)
 shared=ea&eb
 # Older sent records keep only the headline; look for the other report's
 # named organisations in that headline instead.
 for one,other,names in ((a,b,ea),(b,a,eb)):
  if not _lead(other).strip():
   words={_stem(w) for w in re.findall(r'[a-z0-9]+',str(other.get('headline') or '').casefold())}
   shared|={n for n in names if n.startswith('name:') and n[5:] in words}
 overlap=headline_overlap(a,b)
 fa=figures(a.get('headline','')+' '+_lead(a));fb=figures(b.get('headline','')+' '+_lead(b))
 common=shared_figures(fa,fb)
 # Headline figures are the reported fact; agreeing on one is strong evidence.
 headline_common=shared_figures(figures(a.get('headline','')),figures(b.get('headline','')))
 if (ta|tb)&set(SERIES) and not (ta|tb)-set(SERIES)-{'POLICY'}:
  # A new release or session repeats the wording but not the figures.
  needed=1 if gap==0 else 2
  related=bool(shared) or bool(series_topics(a)&series_topics(b)) or overlap>=0.2
  # A shared headline figure across days can be a standing target (the 5%
  # inflation target), not the same release; require similar wording too.
  goal=re.compile(r'(?i)\b(?:targets?|goals?|aims?|forecasts?|projections?|projected|expects?|achievable|ready for)\b')
  standing=bool(goal.search(str(a.get('headline') or '')) or goal.search(str(b.get('headline') or '')))
  headline_match=headline_common>=1 and (gap==0 or not standing or overlap>=0.3)
  return (common>=needed or headline_match) and related
 # A piece reprinted under (almost) the same headline is the same piece.
 if overlap>=0.85 and min(len(content_tokens(str(a.get('headline') or ''))),len(content_tokens(str(b.get('headline') or ''))))>=5:return True
 # Another paper's report of the same development within a day: alike in wording
 # and naming the same organisation ('CIC' and 'Kelani Cables' results stay apart).
 # A merge needs a real organisation in common or two shared headline names: one
 # shared name ('HNB') joins HNB Finance's leasing with HNB PLC's motor deal.
 if gap is not None and (gap<=1 and wording_overlap(a,b)>=0.45 or gap==0 and wording_overlap(a,b)>=0.40) \
    and ({o for o in organisations(shared) if not o.startswith('name:') and 'fitch' not in o} or len(headline_names(a)&headline_names(b))>=2):return True
 if shared:
  if headline_common or common>=2:return True
  # Same day, both headlines naming the same person and organisation ('Ravi ... HSBC').
  if gap==0 and organisations(shared) and len(headline_names(a)&headline_names(b))>=2:return True
  # Commentary on a shared topic recurs across days; require closer wording.
  if overlap>=(0.3 if gap==0 else 0.4):return True
  if gap and words_in_facts(a,b)>=0.7:return True
  # One institution's results release, reported as a quarter or nine months.
  if gap is not None and gap<=3 and RESULTS.search(str(a.get('headline') or '')) and RESULTS.search(str(b.get('headline') or '')) \
     and any(not o.startswith('name:') for o in organisations(shared)):return True
  # Two named counterparties together (bank + partner) identify a deal.
  orgs=organisations(shared)
  if len(orgs)>=2 and any(not o.startswith('name:') for o in orgs) and (ta&tb or overlap>=0.3):return True
  return False
 return overlap>=0.6 and (gap==0 or common>=1)


def export_scope(article: dict) -> str:
 text=str(article.get('headline','')).casefold()
 return next((sector for sector in ('apparel','tea','rubber','coconut','services','merchandise') if sector in text),'aggregate')


def financing_identity(article: dict) -> dict:
 text=(str(article.get('headline',''))+' '+str(article.get('body') or article.get('summary') or '')[:1800]).casefold()
 head=str(article.get('headline','')).casefold()
 # Only a story *about* a lender's financing: a body that merely mentions
 # the IMF and a dollar figure must not change how other stories match.
 if not re.search(r'\b(?:adb|asian development bank|opec fund|ofid|world bank|imf|jica|aiib)\b',head) or not re.search(r'loan|financ|facility|credit|grant|\$|million|billion|approv',head):return {}
 purposes={key for key,pattern in {
  'social_protection':r'social protection|elderly care|economic opportunities',
  'macro_recovery':r'macro.recovery|economic recovery|policy.based',
  'water_security':r'water security|water sector|water supply|water services|water reforms?|mahaweli|irrigation',
  'skills':r'skills development|vocational|technical education|tvet',
  'energy':r'energy|electricity|power grid',
  'transport':r'transport|railway|highway',
 }.items() if re.search(pattern,text)}
 h=str(article.get('headline','')).casefold()
 stage=('cabinet_approval' if 'cabinet' in h and re.search(r'approv|clear',h) else
        'disbursement' if re.search(r'disburs|releases? funds',h) else
        'signing' if re.search(r'\bsign',h) else
        'lender_approval' if re.search(r'approv',h) else '')
 return {'purposes':purposes,'stage':stage}


def explain_match(a: dict, b: dict) -> str:
 """Human-readable evidence for why two reports are the same development."""
 if canonical(a.get('source_url',''))==canonical(b.get('source_url','')):evidence='same article URL'
 elif publisher_article_id(a.get('source_url','')) and publisher_article_id(a.get('source_url',''))==publisher_article_id(b.get('source_url','')):
  evidence='same publisher article under another URL'
 else:
  shared=sorted(organisations(entities(a)&entities(b)))
  common=shared_figures(figures(a.get('headline','')+' '+_lead(a)),figures(b.get('headline','')+' '+_lead(b)))
  parts=[]
  if shared:parts.append('organisations: '+', '.join(x.replace('name:','') for x in shared[:4]))
  if common:parts.append(f'{common} shared figure(s)')
  parts.append(f'headline overlap {headline_overlap(a,b):.2f}')
  types=event_types(a)&event_types(b)
  if types:parts.append('development: '+'/'.join(sorted(types)))
  evidence='; '.join(parts)
 edition=b.get('edition_date') or str(b.get('publication_date') or '')[:10] or 'earlier edition'
 return f"{edition}: \"{str(b.get('headline') or '')[:90]}\" ({evidence})"
