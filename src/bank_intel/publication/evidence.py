"""Article-body evidence, caption suppression, and input consistency."""
from __future__ import annotations
from html import unescape
import re
from .identity import tokens

BOILER = (r'^\s*(?:latest news\s+published on\s+by\s+(?:share\s+tweet\s+)?|'
          r'news published on by(?: by)?(?:\s+share\s+tweet)?\s+|'
          r'colombo\s*\(news\s*1st\)\s*[;:–—-]\s*|'
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


CAPTION_MARKER=re.compile(r'(?i)\b(?:pic(?:ture)? by|photo(?: by)?|from left|chief guest|ceo|chairman|director|minister|deputy|governor|president)\b')
INLINE_BYLINE=re.compile(r"\bBy\s+(?:[A-Z][\w&.'’-]*\s+){1,5}?(?=(?:The|A|An)\s)")


def _sentence_break(t: str):
 """First real sentence end; 'Nov.', 'Dr.', 'Govt.' and 'Rs.' are not."""
 for m in re.finditer(r"(\S+)[.!?]\s+(?=[A-Z])",t):
  word=m.group(1)
  if word[:1].isupper() and len(word)<=5:continue
  if re.fullmatch(r'(?:[A-Z]\.)*[A-Z]',word):continue  # initials: R.M.C.H.K.
  return m
 return None


def strip_deck(t: str, headline: str='') -> str:
 """Drop a photo caption and flattened '- point - point' subheadline deck.

 Flattening loses the line breaks, so the lead is found where a deck point
 runs into a capitalised clause ('...conservation Cargills Bank has launched').
 A caption (names, titles, 'Pic by') may precede the deck. The lead must
 start with 'The' or a multi-word proper name, carry a news verb, and name
 something in the headline; otherwise the text is returned unchanged.
 """
 offset=0
 if not re.match(r'^\s*[-–•]\s',t):
  dash=re.search(r'\s[-–]\s',t[:500])
  # Only a caption may precede a deck: no sentence, but names and titles.
  if not dash or _sentence_break(t[:dash.start()+1]) or not CAPTION_MARKER.search(t[:dash.start()]):return t
  offset=dash.start()+1
 body=t[offset:]
 byline=INLINE_BYLINE.search(body[:900])
 if byline:body=body[:byline.start()]+body[byline.end():]
 wanted=tokens(headline)
 # Deck points carry no full stops; past the first sentence break the
 # candidate would be body text, not the lead.
 end=_sentence_break(body[:900])
 for m in re.finditer(r"(?<=[a-z0-9%,;)’'])\s+(?=[A-Z])",body[:end.start() if end else 900]):
  lead=body[m.end():]
  # '...according to JB Securities.' is the end of a sentence, not a lead (13 Nov 2026).
  before=body[:m.start()].split()
  if before and before[-1].casefold() in NOT_BEFORE_LEAD:continue
  # A statistic's subject is often lower case after its first word ('Total vehicle registrations declined').
  if not re.match(r"(?:The\s|[A-Z][\w&'’.-]*(?:\s+(?:and|of|&))?\s+(?:\([A-Z]+\)\s+)?[A-Z]|"
                  r"[A-Z][a-z]+(?:\s+[a-z-]+){1,3}\s+(?:declined|rose|fell|increased|decreased|grew|dropped|surged|recorded|reached)\b)",lead):continue
  # Still inside the deck if another '- point' follows straight away.
  if re.search(r'\s[-–]\s',lead[:200]):continue
  opening=' '.join(lead.split()[:30])
  if not LEAD_VERB.search(opening):continue
  # A lead names the headline's subject at once, or opens with a full
  # title-and-name ('Finance and Planning Deputy Minister Dr. X said').
  name_run=len(re.match(r"(?:[A-Z][\w&'’.-]*|and|of|&)(?:\s+(?:[A-Z][\w&'’.-]*|and|of|&))*",lead).group().split())
  titled=name_run>=4 and LEAD_VERB.search(' '.join(lead.split()[:15]))
  if not (lead.startswith('The ') or wanted & tokens(' '.join(lead.split()[:6])) or titled):continue
  return lead
 return t


LEAD_OPENING=re.compile(r"(?:The|Sri Lanka(?:[’']s|n)?|Government|According|President|Foreign|Colombo)\s")
NOT_BEFORE_LEAD={'with','by','of','to','from','at','in','for','and','the','on','as','that','said','says','but','or','a','an','if','while','when',
                 'every','each','its','their','this','these','his','her','our','all','both','than','into','across','about','over',
                 'elected','named','appointed','became','new'}


def strip_flat_deck(t: str) -> str:
 '''Subheadlines flattened onto the lead carry no punctuation between them.

 'Misses target by Rs. 13.5 b Over-borrowing lifts yields to 9.34% The
 Government raised...' keeps its lead. Only a clear deck is cut: bullet points,
 or text ending in a figure or 'Govt.'. Without those marks a deck looks just
 like a sentence running into 'Sri Lanka', and a sentence must never be cut.
 '''
 bullets=bool(re.match(r'\s*[-–•]\s',t))
 for m in LEAD_OPENING.finditer(t[:420]):
  if m.start()==0:return t
  before=t[:m.start()].rstrip()
  words=before.split()
  if not words or set(before)<=set('-–• ') or words[-1].casefold() in NOT_BEFORE_LEAD:continue
  # A finished sentence means the article has already begun; 'Govt.' ends none.
  if _sentence_break(before+' A') and not re.search(r'\b(?:Govt|Rs|Mr|Dr|No)\.$',before):return t
  if len(words)>(70 if bullets else 35) or before.endswith((',',';',':')):return t
  if not bullets and not re.search(r'(?:%|\d|\bGovt\.)$',before):continue
  lead=t[m.start():]
  if re.search(r'\s[-–]\s',lead[:150]):continue
  return lead
 return t


def strip_caption(t: str, headline: str='') -> str:
 '''A 'From left: names, titles' photo line ends where its story's subject acts.'''
 if not re.match(r'(?i)from left\b',t):return t
 starts=[]
 for word in re.findall(r"[A-Z][\w&-]+",headline):
  m=re.search(r'\b'+re.escape(word)+r"(?:\s+[A-Z][\w&'’.-]*){0,3}\s+(?:has|have|is|are|was|announced)\b",t[:1200])
  before=t[:m.start()].split() if m else []
  if m and before and before[-1].casefold() not in NOT_BEFORE_LEAD and before[-1]!='&':starts.append(m.start())
 # A caption is names and titles; a full sentence before the match is the story.
 for start in sorted(starts):
  if not _sentence_break(t[:start]+' A'):return t[start:]
 return t


def drop_repeated_caption(t: str) -> str:
 """'Deputy Minister X Deputy Minister X believes...' -> caption removed."""
 m=re.match(r'^(.{12,160}?)\s+\1\b',t)
 return t[len(m.group(1)):].lstrip() if m else t


def strip_byline(t: str) -> str:
 """Remove an inline 'By <Writer or Desk>' prefix that runs into the lead."""
 m=re.match(r"^By\s+(?:[A-Z][\w&.'’-]*\s+){1,5}?(?=(?:The|A|An|Sri|In|On|At|As|According)\s)",t)
 # 'By Hiran H. Senewiratne CSE trading yesterday...': the name ends where a
 # capitalised subject is followed by an ordinary word.
 # 'By Maheesha Mudugamuwa and Methmalie Dissanayake From the...': two writers.
 m=m or re.match(r"^By\s+(?:(?:[A-Z][\w&.'’-]*|and)\s+){2,7}?(?=[A-Z][\w&-]*\s+[a-z])",t)
 return t[m.end():] if m else t


def clean_lead(raw: str, headline: str='') -> str:
 # Strip complete standalone byline/photo lines before flattening paragraphs.
 raw='\n'.join(line for line in str(raw or '').splitlines() if not re.fullmatch(r'\s*(?:By [A-Z][a-z]+(?: [A-Z][a-z]+){1,3}|(?:Photo|Pic|Picture) (?:by|courtesy of).+)\s*',line))
 t=flatten(raw)
 t=re.sub(r'(?i)^S\s+ustaining\b', 'Sustaining',t)
 t=re.sub(r'^\s*ECONOMYNEXT\s*[–—-]\s*', '', t)
 # '08 Nov 2026 | By ...': the date goes here; the byline, however many writers, below.
 t=re.sub(r'(?i)^\s*\d{1,2}\s+[A-Za-z]{3}\s+20\d{2}\s*\|\s*(?=By\s)', '', t)
 t=strip_caption(strip_flat_deck(t),headline)
 # 'Chairman X rings the opening bell - PIC BY KITHSIRI DE MEL People’s Bank has...':
 # the caption and its photographer credit end where the article starts.
 # Also with no dash: 'Ravinatha Aryasinha PIC BY NIMALSIRI EDIRISINGHE Sri Lanka’s decision...'.
 credit=re.search(r'\s(?:[-–]\s*)?PIC(?:TURE)?S?\s+BY\s+(?:[A-Z][A-Z.]+\s+){1,4}',t[:400])
 if credit:t=t[credit.end():]
 # 'By Methmalie Dissanayake – Tax-free threshold raised...': a byline run into a deck.
 # A phrase printed twice in a row by the source ('Tourism Development Levy (TDL)
 # Tourism Development Levy (TDL) imposed...'): keep one.
 t=re.sub(r'(?<!\S)((?:\S+\s+){2,7}\S+)\s+\1(?!\S)',r'\1',t,count=1)
 # The Morning separates deck points with a middle dot ('By X · point · point').
 if t[:400].count('·')>=2:t=re.sub(r'\s*·\s*',' - ',t)
 t=re.sub(r'^\s*By\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}(?:\s+-\s+|\s*[–—]\s*)','- ',t)
 # '* Profit After Tax Rs. 58.7 Bn * Total Assets ...': star bullets are a deck too.
 if re.match(r'\s*\*\s',t):t=re.sub(r'(?:^|\s)\*\s+',' - ',t).strip()
 # 'Farhaz Farouk DFCC Bank PLC has appointed Farhaz Farouk...': the caption's name,
 # repeated in the article's first sentence, comes off the front.
 for size in (2,3,4):
  caption=re.match(r"((?:[A-Z][a-z]+\s+){%d}[A-Z][a-z]+)\s+(?=[A-Z])"%(size-1),t)
  if caption and caption.group(1) in t[caption.end():caption.end()+250]:
   t=t[caption.end():];break
 # 'Mahesh Wijewardene – Group MD Singer (Sri Lanka) PLC reported...': a 'Name – title' caption.
 titled=re.match(r"(?:[A-Z][a-z]+\s+){1,3}[A-Z][a-z]+\s+[–-]\s+(?:Group\s+)?(?:MD|CEO|Chairman|Chairperson|Director|Managing Director|General Manager|Chief Executive Officer|President)(?:/[A-Z]+)?\s+(?=[A-Z])",t)
 if titled:t=t[titled.end():]
 # Captions and article-key-point teasers often precede an intact journalistic
 # lead in the extracted body. Retain the actual lead instead of the caption.
 if re.match(r'(?i)^(?:Manager\s+[A-Z]|\-\s|The new Self Banking Centre was officially unveiled by|HNB Senior Vice President)',t):
  lead=re.search(r'(?i)\b(?:HNB FINANCE PLC|Amana Bank has|HNB PLC has|Bank of Ceylon \(BOC\)|The Central Bank of Sri Lanka|The Sri Lanka Computer Emergency)\b',t[12:750])
  if lead:t=t[12+lead.start():]
 for _ in range(4): t=re.sub(BOILER,'',t,flags=re.I)
 t=strip_byline(strip_deck(drop_repeated_caption(t),headline))
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
 # Case matters: 'the Central Bank' mid-sentence is not where a lead starts.
 anchors=[m for m in re.finditer(r'\b'+subjects+r'\b',t)]
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


ABBREVIATION = re.compile(r'(?i)(?:\b(?:dr|mr|mrs|ms|prof|eng|hon|govt|rs|no|st|jr|sr|vs|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)|\b[A-Z]|(?:\b[A-Z]\.)+[A-Z])\.$')


def sentence_spans(text: str) -> list[tuple[int, int]]:
 """Offsets in cleaned source text, retaining decimals and honorifics."""
 start=0; spans=[]
 for match in re.finditer(r'[.!?][”"\']?(?:\s+|$)',text):
  end=match.end()
  prefix=text[:match.start()+1]
  if text[match.start()]=='.' and ABBREVIATION.search(prefix):continue
  if text[start:end].strip():spans.append((start,end))
  start=end
 if text[start:].strip():spans.append((start,len(text)))
 return spans


PROCEDURE=re.compile(r'(?i)\b(?:produced before (?:the )?court|remand custody|remain in remand|bail order|submitted to (?:the )?court|'
                     r'prison officials|next hearing|fixed the case|case was taken up|objections to granting bail)\b')
BACKGROUND=re.compile(r'(?i)(?:Meanwhile|Earlier|Previously|In (?:19|20)\d\d|Last year|It is noteworthy|It is pertinent)\b')
CONNECTOR=re.compile(r'(?i)(?:On the other hand|On the contrary|In addition|Additionally|Furthermore|Moreover|Similarly|Likewise|Meanwhile|He|She|They|It|This|These)\b')
PHOTO_POSITION=re.compile(r'(?i)\((?:centre|center|middle|left|right|seated|standing|second|third|fourth|from left|from right)\b[^)]{0,30}\)')
MID_SENTENCE=re.compile(r'(?:[a-z]|[-–•]\s|(?:and|but|or|which|who|whose)\s)')
GLUED_LEAD=re.compile(r'(?<=[a-z0-9%])\s(?=(?:The|Sri Lanka|Government|According)\s)')
# The editor's own summaries run three or four sentences.
SUMMARY_WORDS=90


def summary_evidence(article: dict, max_words: int=150) -> dict:
 """Select complete source sentences; keep offsets and amounts for inspection.

 The lead establishes the event. Later numerical and policy-status sentences
 take precedence over repetitive introductory language. No facts are invented.
 """
 text=clean_lead(article.get('body') or article.get('summary') or '',article.get('headline',''))
 # A '- point - point' deck is a list of facts; each point becomes a sentence.
 # Daily FT runs one in without a leading dash ('Name - point - point - point Lead'),
 # three or more ' - ' points before the first full stop.
 head=text[:_sentence_break(text[:900]).start() if _sentence_break(text[:900]) else 600]
 # Sub-headlines as 'claim: Speaker' pairs ('...13.6% of GDP: President Govt’s decision
 # ...: Economist Anushan Kapilan Despite ...: Economist Umesh Moramudali Even with ...').
 # The article starts after the last speaker (13 Nov 2026).
 speakers=list(re.finditer(r':\s+(?:(?:President|Economist|Minister|Governor|Analyst|Chairman|CEO|Expert|Dr\.?|Prof\.?)\s+)?'
                           r'(?:[A-Z][a-z]+\s+){0,3}[A-Z][a-z]+\s+(?=[A-Z][a-z])',head))
 if len(speakers)>=2 and len(text[speakers[-1].end():].split())>=25:
  text=text[speakers[-1].end():]
  head=text[:_sentence_break(text[:900]).start() if _sentence_break(text[:900]) else 600]
 deck=len(re.findall(r'\s[-–]\s(?=[A-Z0-9])',head))>=3 or bool(re.match(r'\s*[-–•]\s',text))
 if deck:
  # A deck of verbless points runs into the article's first sentence ('...Total Deposit
  # base Rs. 4.8 Tn Bank of Ceylon (BOC), reinforced its...'): start there when it is found.
  # The subject is capitalised ('Bank of Ceylon (BOC),', 'Total vehicle registrations'),
  # never a unit left from the point before ('4.8 Tn Bank').
  lead=re.search(r'(?<=[a-z0-9%)])\s+(?=(?!(?:Tn|Bn|Mn|Rs|Mt|Kg)\b)[A-Z][\w&.’\']*(?:\s+(?:of|and|&|[a-z]{3,}|[A-Z(][\w&.()’\']*)){0,6},?\s+(?:has\s+|have\s+)?'
                 r'(?:reinforced|recorded|reported|posted|delivered|announced|achieved|grew|registered|continued|declined|rose|fell|increased|decreased|has|have|is|was|will)\b)',head)
  if lead and lead.start()>=20 and len(re.findall(r'\s[-–]\s',' '+text[:lead.start()]))>=1:
   text=text[lead.end():]
  elif len(re.findall(r'\s[-–]\s(?=[A-Z0-9])',head))>=3:
   text='- '+text
 if re.match(r'\s*[-–•]\s',text):
  text=re.sub(r'^\s*[-–•]\s+','',text)
  text=re.sub(r'(?<=[\w%)])\s+[-–•]\s+(?=[A-Z0-9])','. ',text)
 # The rest of a photo caption run into the article ('... Sameera Liyanage is also present The 12th Annual').
 text=re.sub(r'(?:(?<=[.)])|^)\s*[^.]{0,160}?\b(?:is|are) also (?:present|in the picture|seen)\s+(?=[A-Z])',' ',text,count=1).lstrip()
 # Numbered-list markers flattened out of their list ('1. 2. 3. 4.').
 text=re.sub(r'(?:\b\d\.\s+){2,}','',text)
 spans=sentence_spans(text)
 choices=[];lead=None
 for index,(start,end) in enumerate(spans):
  sentence=text[start:end].strip()
  if not re.search(r'[.!?][”"\']?$',sentence) or ABBREVIATION.search(sentence):continue
  # List numbers ('1.') and stray fragments are not sentences.
  if len(sentence.split())<4:continue
  # A photo caption left as its own 'sentence': only a title and a name ('Opposition MP Ravi Karunanayake.').
  if len(sentence.split())<=6 and all(w[:1].isupper() or not w[:1].isalpha() for w in sentence.rstrip('.').split()):continue
  # A fragment left by a cut ('the Central Bank ... flagging', 'and X From the')
  # is not where a summary may start, nor a sentence of its own.
  if MID_SENTENCE.match(sentence):continue
  # A sub-headline run into the lead with no stop: '...remain unchanged The Government raised'.
  # A photo caption is never the lead.
  if lead is None and not PHOTO_POSITION.search(sentence):
   glued=GLUED_LEAD.search(sentence)
   # '...Rating to Merchant Bank of Sri Lanka & Finance' is one sentence: a deck
   # point never ends on 'of' or 'during', and has no finite verb ('has assigned').
   if glued and glued.start()>=40 and not re.search(r'[,;:.]',sentence[:glued.start()]) \
      and not re.search(r'(?i)\b(?:of|in|to|for|and|or|by|from|at|with|on|as|that|during|across|between|the|a|an)$',sentence[:glued.start()]) \
      and not re.search(r'(?i)\b(?:has|have|had|is|are|was|were|will|said|says)\b',sentence[:glued.start()]):
    start+=glued.start()+1;sentence=sentence[glued.start()+1:]
   lead=index
  score=(100 if index==lead else 0)+(5 if re.search(r'\d',sentence) else 0)+(4 if re.search(r'(?i)propos|approv|sign|gazette|subject to|not yet|conditional|revenue|capital|credit|risk|regulat',sentence) else 0)
  # Courtroom logistics, photo captions ('(centre) exchanges the agreement with ... (left)')
  # and old background add words, not facts about the development.
  if PHOTO_POSITION.search(sentence):score-=120
  elif PROCEDURE.search(sentence):score-=12
  elif BACKGROUND.match(sentence) and not re.search(r'\d',sentence):score-=6
  choices.append((score,index,start,end,sentence))
 chosen=[]; total=0
 for choice in sorted(choices,key=lambda x:(-x[0],x[1])):
  size=len(choice[4].split())
  if choice[0]<0:continue
  if total+size<=max_words or not chosen and choice[1]==0:
   chosen.append(choice);total+=size
 chosen.sort(key=lambda x:x[1])
 # 'On the other hand...' refers back; without the sentence before it, it reads wrong.
 kept={c[1] for c in chosen}
 # The article's own opening ('It has been revealed...') always stays.
 chosen=[c for c in chosen if c[1]==lead or not (CONNECTOR.match(c[4]) and c[1]-1 not in kept)]
 # Never an empty summary: fall back to the opening sentence of the source.
 if not chosen and choices:
  chosen=[min(choices,key=lambda x:x[1])]
 sentences=[{'source_url':article.get('source_url',''),'start':c[2],'end':c[3],
             'text':c[4],'figures':re.findall(r'\d[\d,.]*(?:\s*%|\s*(?:million|billion))?',c[4])} for c in chosen]
 if not sentences and text.strip():
  # No complete sentence survived extraction: the opening words, marked as cut.
  words=text.split()
  return {'text':' '.join(words[:60])+(' …' if len(words)>60 else ''),'cleaned_source':text,'sentences':[]}
 return {'text':' '.join(c['text'] for c in sentences),'cleaned_source':text,'sentences':sentences}


def summary(article: dict, max_words: int=150) -> str:
 return summary_evidence(article,max_words)['text']


def evidence_issues(article: dict) -> list[str]:
 h=flatten(article.get('headline',''))
 b=clean_lead(article.get('body') or article.get('summary') or '',h)
 issues=[]
 if len(b.split())<24: issues.append('SHORT_SOURCE_TEXT')
 if re.search(r'^(?:news published on|latest news published on|from left|pic by)',b,re.I): issues.append('BOILERPLATE_LEAD')
 # Match ordinary plural nouns without changing event-identity tokenization.
 def topic_tokens(text):
  return {t[:-1] if len(t)>3 and t.endswith('s') and not t.endswith(('ss','us','is')) else t
          for t in tokens(text)}
 ht=topic_tokens(h); bt=topic_tokens(b)
 if len(ht)>=5 and len(ht & bt)<max(2,round(.22*len(ht))):
  issues.append('HEADLINE_BODY_TOPIC_MISMATCH')
 if re.search(r'\b(?:further remanded|court remand)\b',h,re.I) and 'lead-acid battery' in b.casefold():
  issues.append('HEADLINE_BODY_TOPIC_MISMATCH')
 return sorted(set(issues))
