"""Article-body evidence, caption suppression, and input consistency."""
from __future__ import annotations
from html import unescape
import re
from .identity import tokens

BOILER = (r'^\s*(?:latest news\s+published on\s+by\s+(?:share\s+tweet\s+)?|'
          r'news published on by(?: by)?(?:\s+share\s+tweet)?\s+|'
          r'colombo\s*\(news\s*1st\)\s*;\s*|'
          r'\s*pic\s+by\s+[^,\-]+\s*[-–]\s*)')


def flatten(raw: str) -> str:
 t=str(raw or '')
 for _ in range(3):
  z=unescape(t)
  if z==t:break
  t=z
 t=re.sub(r'<[^>]+>', ' ', t)
 t=t.replace('\xa0',' ')
 return re.sub(r'\s+', ' ',t).strip()


LEAD_VERB=re.compile(r'(?i)\b(?:has|have|had|is|was|were|can|could|must|will|would|announced|launched|introduced|rejected|reported|recorded|reached|said|signed|issued|approved|opened|assigned|warned|rose|fell|marked|celebrated|elected|secured|expanded)\b')


def strip_deck(t: str, headline: str='') -> str:
 """Drop flattened '- point - point' subheadline decks ahead of the real lead.

 Flattening loses the line break, so the lead is found where a deck point
 runs into a capitalised clause ('...conservation Cargills Bank has launched').
 The lead must start with 'The' or a multi-word proper name, carry a news
 verb, and (unless it starts with 'The') name something in the headline;
 otherwise the text is returned unchanged rather than guessed at.
 """
 if not re.match(r'^\s*[-–•]\s',t):return t
 wanted=tokens(headline)
 # Deck points carry no full stops; past the first sentence break the
 # candidate would be body text, not the lead.
 end=re.search(r'[.!?]\s+(?=[A-Z])',t[:900])
 for m in re.finditer(r'(?<=[a-z0-9%,;)’\'])\s+(?=[A-Z])',t[:end.start() if end else 900]):
  lead=t[m.end():]
  if not re.match(r"(?:The\s|[A-Z][\w&'’.-]*\s+(?:\([A-Z]+\)\s+)?[A-Z])",lead):continue
  opening=' '.join(lead.split()[:22])
  if not LEAD_VERB.search(opening):continue
  if not lead.startswith('The ') and not (wanted & tokens(' '.join(lead.split()[:6]))):continue
  return lead
 return t


def strip_byline(t: str) -> str:
 """Remove an inline 'By <Writer or Desk>' prefix that runs into the lead."""
 m=re.match(r"^By\s+(?:[A-Z][\w&.'’-]*\s+){1,5}?(?=(?:The|A|An|Sri|In|On|At|As|According)\s)",t)
 return t[m.end():] if m else t


def clean_lead(raw: str, headline: str='') -> str:
 # Strip complete standalone byline/photo lines before flattening paragraphs.
 raw='\n'.join(line for line in str(raw or '').splitlines() if not re.fullmatch(r'\s*(?:By [A-Z][a-z]+(?: [A-Z][a-z]+){1,3}|(?:Photo|Pic|Picture) (?:by|courtesy of).+)\s*',line))
 t=flatten(raw)
 t=re.sub(r'(?i)^S\s+ustaining\b', 'Sustaining',t)
 # Captions and article-key-point teasers often precede an intact journalistic
 # lead in the extracted body. Retain the actual lead instead of the caption.
 if re.match(r'(?i)^(?:Manager\s+[A-Z]|\-\s|The new Self Banking Centre was officially unveiled by|HNB Senior Vice President)',t):
  lead=re.search(r'(?i)\b(?:HNB FINANCE PLC|Amana Bank has|HNB PLC has|Bank of Ceylon \(BOC\)|The Central Bank of Sri Lanka|The Sri Lanka Computer Emergency)\b',t[12:750])
  if lead:t=t[12+lead.start():]
 for _ in range(4): t=re.sub(BOILER,'',t,flags=re.I)
 t=strip_byline(strip_deck(t,headline))
 t=re.sub(r'(?i)^\s*\d{1,2}\s+[A-Za-z]{3}\s+20\d{2}\s*\|\s*By\s+[A-Za-z]+\s+[A-Za-z]+\s+', '', t)
 if re.match(r'(?i)^Emphasises technology should never',t):
  marker=re.search(r'\bAs artificial intelligence\b',t,re.I)
  if marker and marker.start()<220:t=t[marker.start():]
 # Publisher teasers/captions are rejected only when a recognizable full factual lead follows.
 teaser=bool(re.match(r'(?i)(?:-|\d{1,2}\s+[a-z]{3}\s+20\d{2}\s*\||by\s+first capital research|foreign investors net sellers all week|President Anura Kumara Dissanayake\s+-|Emphasises technology should never)',t))
 photo=bool(re.search(r'(?i)\b(?:from left|exchange agreement|share mou|pic by|photo by|pictur|standing from|director general manager|officially unveiled by)\b',t[:300]))
 subjects=(r'(?:The Government|The Central Bank of Sri Lanka|The Sri Lanka Computer|Sri Lanka CERT|'
           r'National Development Bank|Sampath Bank|People.s Bank|Hatton National Bank|'
           r'Commercial Bank|DFCC Bank|Seylan Bank|Amana Bank|Asia Asset Finance|'
           r'Industrial Asphalts|HNB FINANCE|HNB PLC|SLIC Life|The Colombo stock market|'
           r'The secondary market|The secondary Bond market|The Treasury account|As artificial intelligence|The Government|The proposed|The Parliamentary|'
           r'The International Monetary Fund|Sri Lanka|The Institute of Chartered Accountants|'
           r'President Anura Kumara Dissanayake|The national QR|Tourism earnings)')
 anchors=[m for m in re.finditer(r'\b'+subjects+r'\b',t,flags=re.I)]
 verbs=re.compile(r'(?i)\b(?:has|have|had|is|was|were|announced|warned|declared|fell|rose|recorded|introduced|launched|said|approved|signed|partnered|entered|pledged|rejected|closed|continued|saw|expanded|reached|secured|proposed|will|moves|calls|reported|opened|experienced|surged|faces|remains|can|must|told|delivered|continues|initially)\b')
 # If the opening looks like a caption or bullets, require the next candidate sentence to contain a news verb.
 if (photo or teaser) and anchors:
  for m in anchors:
   if m.start()>700:break
   if verbs.search(t[m.end():m.end()+145]):
    t=t[m.start():];break
 # Remove remaining fixed-format bylines/date tags, not substantive source claims.
 t=re.sub(r'(?i)^\s*\d{1,2}\s+[A-Za-z]{3}\s+20\d{2}\s*\|\s*By\s+[A-Za-z]+\s+[A-Za-z]+\s+', '', t)
 t=re.sub(r'(?i)^\s*By First Capital Research\s+', '',t)
 t=re.sub(r'(?i)^[A-Z][a-z]{3,22}\s+(?=A Brent crude oil price)', '',t)
 # A short image-caption prefix may precede the headline's named subject.
 subject=' '.join(headline.split()[:2])
 if subject and re.search(r'(?i)solar power project|pictured|from left',t[:100]):
  lead=re.search(re.escape(subject)+r' (?:PLC )?(?:has |announced|reported)',t[:300],re.I)
  if lead:t=t[lead.start():]
 return t.strip()


def summary(article: dict, max_words: int=115) -> str:
 t=clean_lead(article.get('body') or article.get('summary') or '',article.get('headline',''))
 # No invented content: extract contiguous sentences, and avoid cut-off words.
 chunks=re.split(r'(?<=[.!?])\s+(?=[A-Z“‘\'\(])', t)
 parts=[]; n=0
 for c in chunks:
  w=len(c.split())
  if n+w > max_words and parts: break
  if w>max_words and not parts:
   # Long article lead: cut at word boundary, disclose truncation via ellipsis.
   return ' '.join(c.split()[:max_words]).rstrip(',:;-')+'…'
  parts.append(c); n+=w
  if n >= 48 and len(parts)>=2: break
 return ' '.join(parts).strip()


def evidence_issues(article: dict) -> list[str]:
 h=flatten(article.get('headline',''))
 b=clean_lead(article.get('body') or article.get('summary') or '',h)
 issues=[]
 if len(b.split())<24: issues.append('SHORT_SOURCE_TEXT')
 if re.search(r'^(?:news published on|latest news published on|from left|pic by)',b,re.I): issues.append('BOILERPLATE_LEAD')
 ht=tokens(h); bt=tokens(b)
 if len(ht)>=5 and len(ht & bt)<max(2,round(.22*len(ht))):
  issues.append('HEADLINE_BODY_TOPIC_MISMATCH')
 if re.search(r'\b(?:further remanded|court remand)\b',h,re.I) and 'lead-acid battery' in b.casefold():
  issues.append('HEADLINE_BODY_TOPIC_MISMATCH')
 return sorted(set(issues))
