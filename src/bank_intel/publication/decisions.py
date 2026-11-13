"""One authoritative article decision; model and rules stay distinct."""
from __future__ import annotations
import re
from .identity import event_signature,opening_market_update
from .evidence import evidence_issues
from .materiality import material_context
from .editorial_rules import nontransactional_bank_visit

BLOCK_POLITICS = re.compile(r'(?i)\b(?:constitution|remanded|monks|religious|gnanasara|ethnic|bodu bala|wildlife|surveyors|sustainable solutions to emotive)\b')
FINANCIAL_REGULATOR = re.compile(r'(?i)\b(?:central bank|cbsl|financial intelligence unit|bank supervision|resolution framework|banking act|prudential|capital adequacy|deposit insurance|anti.money laundering|money laundering|aml|fatf|bank fraud|banking fraud|banking scam|fake bank|banking credentials|customer bank accounts)\b')
DIRECT_BANK = re.compile(r'(?i)\b(?:national development bank|ndb|sampath bank|people.s bank|commercial bank|dfcc bank|hatton national bank|hnb plc|hnb bank|seylan bank|amana bank|pan asia bank|nations trust bank|cargills bank|union bank|bank of ceylon|regional development bank|standard chartered|hsbc|state bank of india|sbi|bank lending|bank deposits|bank earnings)\b')
OTHER_FIN = re.compile(r'(?i)\b(?:finance plc|finance company|asset finance|hnb finance|lanka finance|microfinance|insurance plc|insurance company|insurer|slic life|life insurance|debenture issue|'
                       r'assurance|takaful|ceylinco life|softlogic life|janashakthi|allianz|fairfirst|unit trust|money fund|'
                       r'ratings? downgrade|fitch ratings?|payment service|cross.border payment|qr payment|lankaqr|webxpay|fintech|epf|etf board|employees.? (?:provident|trust) fund)\b')
# Financial crime and the laws against it: schemes CBSL warns about, illicit transfers, AML legislation.
FINANCIAL_CRIME = re.compile(r'(?i)\b(?:pyramid|ponzi|prohibited schemes?|financial crimes? investigation|fcid|illegal (?:fund|money|foreign exchange) transfers?|'
                             r'hawala|undiyal|unauthori[sz]ed (?:deposits?|finance)|beneficial owner\w*|proceeds of crime|trusts? \(?amendment\)? bill|trust bill|fatf|mutual evaluation|'
                             r'online (?:financial )?fraud|financial fraud|cyber fraud|investment scam|online scam|co.?op\w*(?: society)? fraud|cooperative fraud)\b')
# Loans and grants from multilateral lenders are sovereign financing facts.
MULTILATERAL = re.compile(r'(?i)\b(?:adb|asian development bank|world bank|aiib|jica)\b.{0,60}\b(?:loans?|grants?|financing|budget support|facility|tranche|disburse\w*)\b'
                          r'|\b(?:loans?|grants?|financing|budget support)\b.{0,60}\b(?:adb|asian development bank|world bank|aiib|jica)\b')
# Fiscal facts the edition carries: revenue outturns, supplementary estimates, excise data.
FISCAL_FACT = re.compile(r'(?i)\b(?:supplementary estimates?|vote on account|appropriation bill|tax revenue|government revenue|excise (?:duty|revenue|income)|'
                         r'cope|copa|copf|committee on public (?:finance|enterprises|accounts)|finance commission|'
                         r'paye|tax.free threshold|tax relief|tax concessions?|fuel subsidy|fuel pric\w*)\b|\bexcise\b.{0,40}\b(?:jumps?|rises?|rose|falls?|fell|drops?|up|down)\b')
# A tax or levy changed ('abolishes Tourism Development Levy', 'cut CESS on 800 import items').
FISCAL_MEASURE = re.compile(r'(?i)\b(?:levy|levies|cess|vat|value added tax|tax break|tax holiday|tax exemptions?|import (?:tax|taxes|duty|duties)|pal)\b'
                            r'.{0,60}\b(?:abolish\w*|exempt\w*|cut|cuts|slash\w*|raise[sd]?|rais\w*|revis\w*|reduc\w*|remov\w*|scrap\w*|impos\w*|introduc\w*|waive\w*)\b'
                            r'|\b(?:abolish\w*|exempt\w*|cut|cuts|slash\w*|raise[sd]?|revis\w*|reduc\w*|remov\w*|scrap\w*|impos\w*|introduc\w*|waive\w*|offers?)\b'
                            r'.{0,40}\b(?:levy|levies|cess|vat|value added tax|tax break|tax holiday|tax exemptions?|import (?:tax|taxes|duty|duties))\b'
                            r'|\bforegone revenue\b|\bforego\w*\b.{0,40}\b(?:revenue|tax)|\brevenue (?:loss|cost)\b')
TRADE_DEAL = re.compile(r'(?i)\b(?:FTAs?|free trade agreements?|economic partnership|CEPAs?|ETCA|trade agreements?|trade pact)\b')
MACRO_FACT = re.compile(r'(?i)\bpublic debt\b|\bdebt.to.gdp\b|\banother IMF\b|\bIMF (?:deal|programme|program|EFF|review|tranche)\b|\bprimary (?:balance|surplus)\b'
                        r'|\bvehicle (?:registrations?|imports?)\b|\bpublic.private partnership\b|\bPPP (?:bill|act|law)\b')
SOE = re.compile(r'(?i)\b(?:SriLankan(?: Airlines)?|national carrier|CPC|Ceylon Petroleum|CEB|Ceylon Electricity|SOEs?|state.owned enterprises?|state enterprises?)\b')
SOE_STRAIN = re.compile(r'(?i)\b(?:loss\w*|treasury|burden|debts?|restructur\w*|bail\w*|recapitali[sz]\w*|privati[sz]\w*|subsid\w*)\b')
# The Budget's subjects within the brief's domain, and those outside it.
BUDGET_DOMAIN = re.compile(r'(?i)\b(?:banks?|banking|financ\w*|cbsl|central bank|credit|loans?|lending|interest rates?|tax\w*|vat|apit|paye|wht|revenue|'
                           r'deficit|surplus|debt|borrow\w*|bonds?|t.?bills?|capital markets?|cse|stocks?|listed|soes?|state.owned|state enterprises?|'
                           r'insur\w*|epf|etf|provident|fiscal|imf|smes?|msmes?|microfinance|fintech|payments?|foreign exchange|forex|rupee|exports?|'
                           r'imports?|tariffs?|customs|excise|dut(?:y|ies)|investments?|investors?|fdi|subsid\w*|privati\w*|treasury|appropriation|primary balance|'
                           r'expenditure|spend\w*|capex|external sector|budget support)\b')
# The Budget being presented or passed (not a preview of it): always in.
BUDGET_PRESENTED = re.compile(r'(?i)\bbudget(?: 2027)?\b.{0,30}\b(?:presented|delivered|unveiled|tabled|passed|approved|defeated in parliament)\b(?!.{0,20}\b(?:tomorrow|today)\b.{0,0})'
                              r'|\b(?:president|akd|dissanayake|finance minister|govt\.?|government)\b.{0,40}\b(?:presents|presented|delivers|delivered|unveils|unveiled|tables|tabled)\b.{0,40}\bbudget\b')
BUDGET_OTHER = re.compile(r'(?i)\b(?:health|hospitals?|education|schools?|universit\w*|students?|teachers?|welfare|aswesuma|allowances?|samurdhi|pensioners?|'
                          r'salar\w*|wages?|roads?|expressways?|railways?|housing|sports?|culture|arts|religio\w*|agricultur\w*|farmers?|fisher\w*|'
                          r'tourism|elderly|disab\w*|children|women|youth)\b')
# A listed company's results, which the editor carries every time ('CIC Holdings Sept quarter net profit').
LISTED_RESULTS = re.compile(r'(?i)\b(?:net profit|profits?|pat|pbt|revenue|earnings|results?|turnover)\b|\b(?:posts?|records?|reports?|delivers?)\b.{0,30}\bgrowth\b')
RESULT_PERIOD = re.compile(r'(?i)\b(?:plc|holdings|group|quarter|quarterly|1h|2q|3q|q[1-4]|first.half|nine.month|9m|half.year|fy\s?\d\d|1hfy\d\d)\b')
# A finance company named as written ('Alliance Finance'), not the Finance Ministry or a Finance Minister.
NAMED_FINANCE = re.compile(r'\b(?<!Deputy )(?<!State )(?<!Public )[A-Z][A-Za-z]+ Finance\b(?! (?:Minist|Commission|Committee|Secretar|Bill|Act|Division|Department))')
MARKET = re.compile(r'(?i)\b(?:bond yields|treasury bills?|t.bills?|treasury bond|bond market|cse watch list|colombo stock exchange|aspi|share price index|cse falls|stock market|net foreign inflow|foreign exchange reserves|tourism revenue|tourism earnings|remittances|inflation|ncpi|ccpi|policy rate|t.bill auction|secondary market|bond auction)\b')
SYSTEMIC = re.compile(r'(?i)\b(?:imf|international monetary fund|sovereign debt|sovereign rating|fiscal revenue|treasury surplus|public debt|budget 2027|tax proposals|economic growth|fiscal policy|adb approves|asian development bank|foreign direct investment|foreign reserves|gsp\+?)(?:\b|(?<=\+))')
RISK_ACTION = re.compile(r'(?i)\b(?:fraud|laundering|tax evasion|enforcement|breach|ransomware|attack|phishing|warning|warns|cyber|license|liquidation|insolvency|capital|solvency|earnings|profits?|results|debenture|compliance|fine|penalty|resolv|directiv|auditor|non.performing)\b')
LOW_PR = re.compile(r'(?i)\b(?:award|honours|sponsor|festival|expo|discount|prize draw|birthday|anniversary|ceremony)\b')
MILESTONE = re.compile(r'(?i)(?:\bcelebrat\w*\s+\d+\s+years\b|\b\d+.year legacy\b|\b\d+(?:th|st|nd|rd)? anniversary\b|\bmarks? \d+.(?:branch|year)|\b\d+.branch milestone\b)')
CONDUCT = re.compile(r'(?i)\b(?:reviews?|complaints?|charges|fees|parate|consumer protection|conduct|borrowers?|lenders?|mis.?selling|grievances?)\b')
# Corrections from sent editions; none of these matched a story sent 16-30 Oct 2026.
CSR = re.compile(r'(?i)\b(?:hands? over|handed over|stem classrooms?|scholarships?|vocational training|leadership camp|great workplaces?|participates? in|empowers? next generation|employee commitment|thrift day|top performers|workshop)\b')
OUTREACH = re.compile(r'(?i)\b(?:inclusion drive|road ?shows?|business forum|investor forum)\b|\b(?:forum|festival)(?: \d{4})? to\b')
BANK_EXCHANGE = re.compile(r'(?i)\b(?:exchange with|knowledge exchange|study visit)\b|\bstrengthens?\b.{0,60}\blinks\b')
COURT_CASE = re.compile(r'(?i)\b(?:case|court|bail|magistrate)\b')
COURT_STEP = re.compile(r'(?i)\b(?:put off|postponed?|adjourned|bail|to resume|called for trial|fixed for|fixes .{0,60}for|referred to (?:january|february|march|april|may|june|july|august|september|october|november|december))\b')
NBFI = re.compile(r'(?i)\b(?:nbfis?|finance compan\w*|non.bank\w*|leasing compan\w*|microfinance)\b')
ASSET_QUALITY = re.compile(r'(?i)\b(?:stage.?[23]|npls?|non.performing|impair\w*|arrears|high.risk|defaults?|delinquen\w*)\b')
BANK_TYPES = ('LICENSED_COMMERCIAL_BANK','LICENSED_SPECIALISED_BANK')
NONBANK_TYPES = ('LICENSED_FINANCE_COMPANY','FINANCIAL_GROUP','INSURER')
# Payment systems, not any sum being paid ('Rs. 275 m payment' in a court case).
PAYMENTS = re.compile(r'(?i)\b(?:payment (?:systems?|services?|platforms?|apps?|gateways?|experience|acceptance|adoption|cards?)|digital payments?|indian payments?|cross.border payments?|qr|lankaqr|lankapay|webxpay|fintech|digital wallets?|card schemes?)\b')
LEGAL = re.compile(r'(?i)\b(?:court|lawsuit|false statements|legal action|indict\w*|fraud|governance)\b')


def licensed_bank_in(text: str) -> bool:
 # A CBSL-licensed bank (from the entity registry) is named in the text.
 from .identity import _registry
 registry=_registry()
 if not registry:return bool(DIRECT_BANK.search(text))
 for m in registry.match(text):
  if m.institution_type in BANK_TYPES and not any(re.search(re.escape(a)+r'\s+finance\b',text,re.I) for a in m.matched_aliases):
   return True
 return False


def nonbank_institution_in(text: str) -> bool:
 # A registered finance company, financial group or insurer is named.
 from .identity import _registry
 registry=_registry()
 return bool(registry) and any(m.institution_type in NONBANK_TYPES for m in registry.match(text))


def decide(r: dict) -> dict:
 """Source facts, sector and editorial priority, independently of raw Stage-A label."""
 h=str(r.get('headline') or '');body=str(r.get('body') or '')
 # Certain wire/short headlines omit the institution. A clearly attributed
 # opening sentence can supply that identity for a material risk development.
 body_lead=body[:650]
 explicitly_attributed_bank_risk=(
     bool(re.search(r'(?i)\b(?:fraud|recovery|recoveries|controls|cyber|penalty|capital shortfall)\b',h))
     and bool(DIRECT_BANK.search(body_lead))
     and bool(re.search(r'(?i)\b(?:fraud|financial loss|recovery|recoveries|legal action|capital|governance controls|cyber)\b',body_lead))
     # A bank sponsoring a cyber summit is not a cyber incident at the bank.
     and not re.search(r'(?i)\b(?:official (?:banking )?partner|sponsor\w*|summit|expo|exhibition|awards?)\b',h))
 result={'decision':'EXCLUDE','section':'','category':'','importance':'NONE','reason':'OUT_OF_SCOPE',
         'source_section':r.get('section',''), 'event_signature': event_signature(r)}
 issues=evidence_issues(r)
 result['evidence_issues']=issues
 if nontransactional_bank_visit(r):
  result['reason']='NONTRANSACTIONAL_BANK_COURTESY_MEETING';return result
 if BLOCK_POLITICS.search(h) and not re.search(r'banking act|financial system|treasury surplus|fiscal revenue',h,re.I) and not FINANCIAL_CRIME.search(h):
  result['reason']='POLITICAL_NONFINANCIAL';return result
 # Whole words: 'award' must not match inside a name such as 'Dharmawardena'.
 if re.search(r'\b(?:awards?|awarded|best bank by|children.s day|anniversary celebration|honou?r(?:s|ed)?|sponsor\w*|quiz|contest|competition|shines at|triumphs at|staff athletics)\b',h,re.I) and not re.search(r'fine|penalty|regulatory breach|fraud|cyber',h,re.I):
  result['reason']='LOW_MATERIALITY_PROMOTIONAL';return result
 # Staff social events and branch moves; the editor asked for these out on 6 Nov 2026 (a new branch stays).
 if re.search(r'(?i)\bfamily (?:fiesta|day|fun day|get.together)\b|\b(?:staff|employees?)\W+(?:get.together|outing|trip|party|day|carnival|sports)\b|\bannual get.together\b|\bteam.building\b',h) \
    or re.search(r'(?i)\brelocat\w*\b.{0,40}\bbranch|\bbranch\b.{0,40}\brelocat\w*',h):
  result['reason']='LOW_MATERIALITY_PROMOTIONAL';return result
 # Corporate sport and market ceremonies are not financial developments.
 if re.search(r'\b(?:netball|cricket|rugby|football|volleyball|athletics|tournament|championship|division title|rings? (?:the )?(?:opening|closing) bell|bell.ringing)\b',h,re.I):
  result['reason']='LOW_MATERIALITY_PROMOTIONAL';return result
 # A match result that names banks ("CDB and HNB 'A' qualify for final", 13 Nov 2026):
 # the sport is only in the text, so read the opening as well.
 if re.search(r'(?i)\bqualif\w* for (?:the )?(?:final|semi.?finals?)\b|\bsemi.?finals?\b|\bT20\b|\bsuper over\b|\bwickets?\b',h) or \
    (re.search(r'(?i)\b(?:final|champions?|beat|win)\b',h) and
     re.search(r'(?i)\b(?:T20|tournament|wickets?|super over|semi.?finals?|innings|league)\b',body_lead)):
  result['reason']='LOW_MATERIALITY_PROMOTIONAL';return result
 if (CSR.search(h) or OUTREACH.search(h)) and not RISK_ACTION.search(h):
  result['reason']='LOW_MATERIALITY_PROMOTIONAL';return result
 if BANK_EXCHANGE.search(h):
  result['reason']='NONTRANSACTIONAL_BANK_COURTESY_MEETING';return result
 # A hearing date or bail with no outcome is not a development.
 if COURT_CASE.search(h) and COURT_STEP.search(h):
  result['reason']='COURT_PROCEDURAL_STEP';return result
 # Dealers' daily colour; a secondary-market story with a figure or yield move stays.
 if re.search(r'(?i)\bsecondary (?:bond )?market\b|\bmarket activity\b',h) and not re.search(r'(?i)\d|\byields?\b',h):
  result['reason']='DAILY_MARKET_COMMENTARY';return result
 if re.search(r'(?i)\bsells? extra\b.*\btreasury bills?\b',h):
  result['reason']='DAILY_MARKET_COMMENTARY';return result
 if opening_market_update(r):
  result['reason']='INTRADAY_MARKET_UPDATE';return result
 # A weekend or Monday 'Week ends in green' round-up of Friday's session, which other
 # papers usually report already: an editor decides.
 if re.search(r'(?i)^week (?:ends|closes) in (?:green|red)\b',h) and re.match(r'\d{4}-\d\d-\d\d',str(r.get('publication_date') or '')) \
    and __import__('datetime').date.fromisoformat(str(r['publication_date'])[:10]).weekday() in (0,5,6):
  result.update(decision='REVIEW',section='MONETARY_MARKETS_EXTERNAL',category='MARKET_EXTERNAL',importance='LOW',reason='WEEKLY_MARKET_ROUNDUP_REVIEW')
  return result
 # Supervisory headline requires a material action, not just mentioning CBSL in macro commentary.
 # The verified event signature is narrower than generic source keywords.
 # Do not send direct central-bank conduct actions or measured finance-company
 # credit deterioration into a low-priority legacy review simply because the
 # headline says "reviews" instead of "regulates", or NBFI instead of "bank".
 # Editor, 12 Nov 2026: of the Budget, carry only what is in our domain (banks,
 # finance, tax and revenue, deficit and debt, markets, SOEs, FX and trade). A
 # Budget item on health, education, welfare or roads, or a Budget piece with no
 # such subject in its headline or opening, stays out.
 # The headline decides: an opening paragraph mentions tax or investment in nearly
 # every Budget piece. The presentation itself is always in.
 if re.search(r'(?i)\bbudget\w*',h) and not BUDGET_PRESENTED.search(h):
  domain_h=BUDGET_DOMAIN.search(h)
  if not domain_h or (BUDGET_OTHER.search(h) and not domain_h):
   result['reason']='BUDGET_OUTSIDE_DOMAIN';return result
 context=material_context(r)
 local_bank=licensed_bank_in(h);nonbank=nonbank_institution_in(h)
 if re.search(r'(?i)\b(?:Bangladesh|Pakistan|India|Nepal)\b',h) and not local_bank and not re.search(r'(?i)Sri Lanka|Lankan|cross.border|spillover',body[:1800]):
  result.update(reason='FOREIGN_DOMESTIC_NO_LOCAL_TRANSMISSION')
 elif MILESTONE.search(h) and not RISK_ACTION.search(h):
  result.update(reason='LOW_MATERIALITY_PROMOTIONAL')
 elif context:
  section,category,reason=context
  result.update(decision='REVIEW' if reason.endswith('_REVIEW') else 'INCLUDE',section=section,category=category,importance='MEDIUM',reason=reason)
 elif FINANCIAL_REGULATOR.search(h) and CONDUCT.search(h):
  result.update(decision='INCLUDE',section='KEY_BANKING_SUPERVISORY',category='GOVERNANCE_CONDUCT',importance='HIGH',reason='REGULATOR_CONDUCT_REVIEW')
 elif NBFI.search(h) and ASSET_QUALITY.search(h):
  result.update(decision='INCLUDE',section='OTHER_FINANCIAL_SECTOR',category='ASSET_QUALITY_NPL_STAGE2',importance='HIGH',reason='NBFI_PORTFOLIO_RISK')
 elif re.search(r'(?i)\b(?:pdmo|public debt management|dollar bonds?|treasury bonds?|t.bills?)\b',h) and re.search(r'(?i)\b(?:auction|bids?|reject\w*|accept\w*|raise[sd]?)\b',h):
  result.update(decision='INCLUDE',section='MONETARY_MARKETS_EXTERNAL',category='SOVEREIGN_BOND_AUCTION',importance='HIGH',reason='GOVERNMENT_SECURITIES_AUCTION')
 elif result['event_signature'].startswith('CSE_DAILY_SESSION_') or re.search(r'(?i)\b(?:cse|stock market|bourse|aspi)\b',h) and re.search(r'(?i)\b(?:aspi|all share price index|s&p sl20)\b',body[:900]) and re.search(r'(?i)\b(?:closed|starts week|ends week|fell|red|down|slips|rattled|plunged)\b',h):
  result.update(decision='INCLUDE',section='MONETARY_MARKETS_EXTERNAL',category='MARKET_EXTERNAL',importance='MEDIUM',reason='OBSERVED_CSE_TRADING_SESSION')
 elif FINANCIAL_REGULATOR.search(h) and (RISK_ACTION.search(h) or re.search(r'financial stability|resolution|policy rate|regulation|licensed|supervisory|fake bank|fraud|scam|customer credentials',h,re.I)):
  result.update(decision='INCLUDE',section='KEY_BANKING_SUPERVISORY',category='REGULATORY_RISK',importance='HIGH',reason='DIRECT_SUPERVISORY_HEADLINE')
 elif FINANCIAL_CRIME.search(h):
  result.update(decision='INCLUDE',section='KEY_BANKING_SUPERVISORY',category='FINANCIAL_CRIME_AML',importance='HIGH',reason='FINANCIAL_CRIME_OR_AML_LAW')
 elif re.search(r'(?i)\b(?:crypto\w*|virtual assets?|vasps?|digital assets?|bitcoin)\b',h) and re.search(r'(?i)sri lanka|lankan|\bcbsl\b|colombo',h+' '+body[:1500]):
  # Virtual-asset regulation and crypto scams sit with CBSL's remit; a foreign
  # exchange's hack with no Sri Lankan link does not.
  result.update(decision='INCLUDE',section='KEY_BANKING_SUPERVISORY',category='REGULATORY_RISK',importance='HIGH',reason='CRYPTO_VIRTUAL_ASSETS')
 elif MULTILATERAL.search(h):
  result.update(decision='INCLUDE',section='MACRO_SYSTEMIC_CONTEXT',category='SOVEREIGN_EXTERNAL_FINANCING',importance='MEDIUM',reason='MULTILATERAL_FINANCING')
 elif explicitly_attributed_bank_risk:
  result.update(decision='INCLUDE',section='KEY_BANKING_SUPERVISORY',
    category='BANK_RISK_GOVERNANCE',importance='HIGH',reason='BANK_RISK_IDENTIFIED_IN_LEAD')
 elif local_bank and re.search(r'(?i)\b(?:appoint\w*|joins?|resigns?|steps down)\b',h) and re.search(r'(?i)\b(?:board|directors?|chairman|chairperson|chief executive|ceo)\b',h):
  result.update(decision='INCLUDE',section='KEY_BANKING_SUPERVISORY',category='BANK_GOVERNANCE',importance='HIGH',reason='BANK_BOARD_OR_EXECUTIVE_CHANGE')
 elif local_bank or DIRECT_BANK.search(h):
  # Bank results however worded ('9-month performance', 'topline', 'PBT') sit with
  # CBSL & Bank Supervisory, as the editor expects (13 Nov 2026).
  is_material=bool(re.search(r'\b(?:earnings|profit|pat|pbt|results|quarter|3q|q3|[1-9]q|\d+.months?|nine.months?|top.?line|debentures?|capital|fraud|cyber|nonperforming|ratings?|governance|debt issue|bond issue|tier 2|tier ii|basel|subordinated|oversubscri\w*)\b',h,re.I) or re.search(r'(?i)\b(?:fitch|moody.s|s&p)\s+rates\b',h))
  # A sponsorship at a summit ('joins Cyber Security Summit as Official Banking Partner') is not a risk event.
  if re.search(r'(?i)\b(?:official (?:banking )?partner|sponsor\w*|summit|expo|exhibition|awards?)\b',h) and not re.search(r'(?i)\b(?:breach|attack|fraud|penalty|loss)\b',h):
   is_material=False
  result.update(decision='INCLUDE',section=('KEY_BANKING_SUPERVISORY' if is_material else 'BANKING_SECTOR_DEVELOPMENTS'),category=('BANK_CAPITAL_RISK' if is_material else 'BANKING_DEVELOPMENTS'),importance=('HIGH' if is_material else 'MEDIUM'),reason='DIRECT_BANK_HEADLINE')
 elif re.search(r'\b(?:cse watch list|watch list move|auditor.s disclaimer|related.party deal|listed share acquisition)\b',h,re.I):
  result.update(decision='INCLUDE',section='MONETARY_MARKETS_EXTERNAL',category='CAPITAL_MARKETS_GOVERNANCE',importance='MEDIUM',reason='MATERIAL_LISTED_MARKET_DEVELOPMENT')
 elif re.search(r'(?i)\b(?:tourists?|tourism|tourist arrivals?|hospitality)\b',h) and re.search(r'(?i)\b(?:arrivals?|revenue|earnings|receipts)\b',h) and re.search(r'\d',h+' '+body[:600]) and re.search(r'(?i)(?:tourism revenue|tourism earnings|arrivals|sltda|foreign exchange)',body[:1400]):
  result.update(decision='INCLUDE',section='MONETARY_MARKETS_EXTERNAL',category='TOURISM_FX_EARNINGS',importance='MEDIUM',reason='MATERIAL_TOURISM_EXTERNAL_STATISTICS')
 elif re.search(r'(?i)\btourism industry\b',h) and re.search(r'(?i)\b(?:revenue|earnings|foreign exchange)\b',body[:850]):
  result.update(decision='REVIEW',section='MONETARY_MARKETS_EXTERNAL',category='TOURISM_FX_EARNINGS',importance='MEDIUM',reason='TOURISM_REVENUE_CUTOFF_CHECK')
 elif re.search(r'(?i)\b(?:mandatory offer|takeover offer|rights issue|delisting|sec (?:nod|approval))\b',h):
  # A takeover or offer awaiting approval: an editor decides whether it is news yet.
  result.update(decision='REVIEW',section='MONETARY_MARKETS_EXTERNAL',category='CAPITAL_MARKETS_GOVERNANCE',importance='MEDIUM',reason='LISTED_MARKET_DEVELOPMENT_REVIEW')
 elif not nonbank and NAMED_FINANCE.search(h) and re.search(r'(?i)\b(?:bonds?|debentures?|profit|pat|pbt|results|deposits?|loans?|leasing|branch\w*|rating)\b',h):
  # A finance company named as written ('Alliance Finance plans Rs.1bn bond issue').
  result.update(decision='INCLUDE',section='OTHER_FINANCIAL_SECTOR',category='REGULATED_FINANCE',importance='MEDIUM',reason='FINANCIAL_SECTOR_HEADLINE')
 elif (nonbank or OTHER_FIN.search(h)) and LEGAL.search(h):
  result.update(decision='REVIEW',section='OTHER_FINANCIAL_SECTOR',category='FINANCIAL_GROUP_LEGAL_GOVERNANCE',importance='MEDIUM',reason='LEGAL_REPUTATIONAL_FINANCIAL_GROUP_REVIEW')
 elif nonbank and re.search(r'(?i)\b(?:branch\w*|footprint|outlets?|expan\w*)\b',h):
  result.update(decision='REVIEW',section='OTHER_FINANCIAL_SECTOR',category='REGULATED_FINANCE',importance='LOW',reason='REGULATED_FINANCE_COMPANY_EXPANSION_REVIEW')
 elif not nonbank and PAYMENTS.search(h):
  result.update(decision='INCLUDE',section='BANKING_SECTOR_DEVELOPMENTS',category='PAYMENTS_FINTECH',importance='MEDIUM',reason='PAYMENT_SYSTEM_HEADLINE')
 elif nonbank or OTHER_FIN.search(h):
  result.update(decision='INCLUDE',section='OTHER_FINANCIAL_SECTOR',category='REGULATED_FINANCE',importance='MEDIUM',reason='FINANCIAL_SECTOR_HEADLINE')
 elif re.search(r'(?i)\bfitch\b',h) and re.search(r'(?i)\b(?:national ratings?|sovereign upgrade|rating scale|rating recalibration)\b',h):
  result.update(decision='INCLUDE',section='MONETARY_MARKETS_EXTERNAL',
    category='CREDIT_RATINGS',importance='MEDIUM',reason='MATERIAL_RATING_DEVELOPMENT')
 elif re.search(r'(?i)\b(?:Sri Lanka CERT|SLCERT|CERT|vulnerabilit\w*|cyber.?security alert|ransomware|data breach)\b',h):
  result.update(decision='REVIEW',section='KEY_BANKING_SUPERVISORY',
    category='CROSS_SECTOR_CYBER',importance='MEDIUM',reason='ASSESS_FINANCIAL_SECTOR_IMPACT')
 elif re.search(r'(?i)\b(?:funding pool|orange bonds?|blue bonds?|green bonds?|social bonds?|sustainability.linked|impact investment)\b',h):
  result.update(decision='INCLUDE',section='MONETARY_MARKETS_EXTERNAL',category='CAPITAL_MARKETS',importance='MEDIUM',reason='SUSTAINABLE_FINANCE_MARKET')
 elif re.search(r'(?i)\b(?:single window|trade facilitation|customs (?:reform|automation))\b',h):
  result.update(decision='REVIEW',section='MONETARY_MARKETS_EXTERNAL',category='MARKET_EXTERNAL',importance='LOW',reason='TRADE_FACILITATION_REVIEW')
 elif LISTED_RESULTS.search(h) and (RESULT_PERIOD.search(h) or RESULT_PERIOD.search(body_lead[:300])):
  result.update(decision='INCLUDE',section='MONETARY_MARKETS_EXTERNAL',category='LISTED_COMPANY_RESULTS',importance='MEDIUM',reason='LISTED_COMPANY_RESULTS')
 elif MARKET.search(h):
  result.update(decision='INCLUDE',section='MONETARY_MARKETS_EXTERNAL',category='MARKET_EXTERNAL',importance='MEDIUM',reason='FINANCIAL_MARKET_HEADLINE')
 elif re.search(r'(?i)\bbudget\b',h) and re.search(r'(?i)\b(?:health|hospitals?|education|schools?|universit\w*|sports?|culture|arts)\b',h) \
      and not re.search(r'(?i)\b(?:tax\w*|revenue|deficit|surplus|debt|allocat\w*|rs\.?\s*\d+|bn|billion|fiscal)\b',h):
  # Editor, 10 Nov 2026: a Budget piece about a social sector is not financial-sector news.
  result.update(reason='OUT_OF_SCOPE')
 elif FISCAL_MEASURE.search(h) or TRADE_DEAL.search(h) or MACRO_FACT.search(h) or (SOE.search(h) and SOE_STRAIN.search(h)):
  # Budget tax measures, trade agreements, the debt and IMF path, SOE losses, vehicle
  # registrations: all added by the editor on 13 Nov 2026 after the rules left them out.
  # A politician's attack or a preview of the Budget is not the measure itself.
  doubt=re.search(r'(?i)\b(?:lash\w*|slams?|criticis\w*|accus\w*|alleg\w*|questions?|claims?|urges?|calls? for|demands?|preview|ahead of|to be presented|expected to)\b|^PREVIEW',h)
  result.update(decision='REVIEW' if doubt else 'INCLUDE',section='MACRO_SYSTEMIC_CONTEXT',category='FISCAL_SYSTEMIC',importance='MEDIUM',
                reason='MACRO_CONTEXT_REVIEW' if doubt else 'MATERIAL_FISCAL_INDICATOR')
 elif (re.search(r'\b(?:president|treasury)\b',h,re.I) and re.search(r'\b(?:surplus|public debt|soes|government revenue|fiscal)\b',h,re.I)) or \
      re.search(r'\b(?:tax administration|budget 2027|revenue strategy|imf flags reserves|fiscal reform)\b',h,re.I) or FISCAL_FACT.search(h):
  # Stakeholder wish-lists for a budget are advocacy, not a fiscal indicator.
  # A quote attributed to one person ('... in Budget 2027 - Vasu') is a demand, not a fiscal fact.
  advocacy=(re.search(r'(?i)\b(?:proposals?|recommendations?|urges?|calls? for|wish.?list|demands?|provide|should|must)\b',h)
            or re.search(r'\s[-–—]\s*[A-Z][\w.]*(?:\s[A-Z][\w.]*)?\s*$',h))
  result.update(decision='REVIEW' if advocacy else 'INCLUDE',section='MACRO_SYSTEMIC_CONTEXT',category='FISCAL_SYSTEMIC',importance='MEDIUM',
                reason='MACRO_CONTEXT_REVIEW' if advocacy else 'MATERIAL_FISCAL_INDICATOR')
 elif re.search(r'\b(?:central bank|cbsl)\b',h,re.I) and re.search(r'\b(?:digital|technology|ai|artificial intelligence|cbdc|fintech|innovation)\b',h,re.I):
  result.update(decision='INCLUDE',section='MACRO_SYSTEMIC_CONTEXT',category='CENTRAL_BANK_TECHNOLOGY',importance='MEDIUM',reason='CENTRAL_BANK_POLICY_TECHNOLOGY')
 elif re.search(r'(?i)\b(?:tax evaders?|tax evasion|inland revenue|ird)\b',h):
  # Tax enforcement is supervisory when it targets money laundering, else context.
  aml=re.search(r'(?i)money laundering|financial intelligence|suspicious transactions?',body_lead)
  result.update(decision='INCLUDE' if aml else 'REVIEW',section='KEY_BANKING_SUPERVISORY' if aml else 'MACRO_SYSTEMIC_CONTEXT',
                category='REGULATORY_RISK' if aml else 'FISCAL_SYSTEMIC',importance='MEDIUM',
                reason='TAX_ENFORCEMENT_AML' if aml else 'MACRO_CONTEXT_REVIEW')
 elif re.search(r'(?i)\bbudget\w*|\btax(?:es|ation|ed)?\b',h):
  # Budget and tax commentary ahead of a Budget: an editor decides, never a silent drop.
  result.update(decision='REVIEW',section='MACRO_SYSTEMIC_CONTEXT',category='FISCAL_SYSTEMIC',importance='MEDIUM',reason='MACRO_CONTEXT_REVIEW')
 elif SYSTEMIC.search(h):
  result.update(decision='REVIEW',section='MACRO_SYSTEMIC_CONTEXT',category='SYSTEMIC_MACRO',importance='MEDIUM',reason='MACRO_CONTEXT_REVIEW')
 elif r.get('section') not in (None,'','EXCLUDE'):
  # The legacy builder's inclusion earns a review only with a financial subject.
  from .model_policy import financial_signal
  why_not=financial_signal(r)
  if why_not:result.update(reason='LEGACY_INCLUDE_OVERRULED_'+why_not)
  else:result.update(decision='REVIEW',section=r.get('section'),category=r.get('category'),importance='LOW',reason='LEGACY_INCLUDE_NEEDS_EVIDENCE')
 # Only candidates with material relevance should be sent to a factual mismatch queue.
 # A weekend market article may recap a session already published in the
 # previous day's edition. Its publication date is not its market-session date.
 # Without the actual previous event facts, flag for review rather than
 # silently publish or automatically delete a possibly new weekly recap.
 if result['decision']=='INCLUDE' and (licensed_bank_in(h) or DIRECT_BANK.search(h)) and re.search(r'(?i)explores?|discuss(?:es|ions)?|courtesy|\bmeets?\b|welcomes?|visit\w*',h) and not RISK_ACTION.search(h):
  result.update(decision='REVIEW',reason='BANK_EXPLORATORY_ACTIVITY_MATERIALITY_REVIEW')
 if result['decision']=='INCLUDE' and result['category']=='MARKET_EXTERNAL' and re.search(r'(?i)\b(?:aspi|cse|indices|stock market|bourse)\b',h):
  from datetime import date as _Date
  try:
   published=_Date.fromisoformat(str(r.get('publication_date',''))[:10])
  except ValueError:published=None
  if published:
   for m in re.finditer(r'(?i)\b(?:Monday|Tuesday|Wednesday|Thursday|Friday)\s*\(\s*(\d{1,2})\s*\)',body[:900]):
    try:
     described=published.replace(day=int(m.group(1)))
    except ValueError:continue
    weekdays={'monday':0,'tuesday':1,'wednesday':2,'thursday':3,'friday':4}
    weekday_name=re.search(r'(?i)(Monday|Tuesday|Wednesday|Thursday|Friday)',m.group(0)).group().lower()
    if described.weekday()==weekdays[weekday_name] and (published-described).days>=2:
     result.update(decision='REVIEW',reason='OLDER_MARKET_SESSION_RECAP_VERIFY_SENT_HISTORY')
     break
 if result['decision'] in ('INCLUDE','REVIEW') and 'HEADLINE_BODY_TOPIC_MISMATCH' in issues:
  result.update(decision='REVIEW',importance='HIGH',reason='SOURCE_HEADLINE_BODY_MISMATCH')
 if 'SHORT_SOURCE_TEXT' in issues and result['decision']=='INCLUDE':
  result.update(decision='REVIEW',reason='INSUFFICIENT_EXTRACTION')
 return result


# Specific reasons by subject, in the operator's own wording; the section's
# general reason applies when none fits.
WHY_BY_TOPIC=(
 (r'\bfitch\b|\bratings?\b','Rating actions change relative credit-risk signals for supervised institutions and can affect their funding costs.'),
 (r'\bcurrent account\b|balance of payments','External-account pressure informs foreign-exchange availability, trade finance and external debt-service conditions.'),
 (r'\breserves?\b|\brupee\b.*\b(?:ytd|depreciat\w*|weaken\w*)','Exchange-rate weakness and reserve adequacy are relevant to banks’ foreign-currency liquidity, treasury and borrower exposures.'),
 (r'\bforeign\b.*\b(?:bonds?|securities|treasur\w*)','Non-resident selling can affect government-security prices, local-currency liquidity and sensitivity to exchange-rate moves.'),
 (r'\brupee\b','Foreign-exchange and bond-market closes inform bank treasury exposures and market-risk assessment.'),
 (r'\binflation\b|\bccpi\b|\bncpi\b','Persistent inflation affects monetary-policy expectations, borrowers’ repayment capacity and banks’ interest-rate risk.'),
 (r'\b(?:t-?bills?|t-?bonds?|treasury (?:bills?|bonds?))\b|\bauction\b','Primary sovereign-debt pricing affects benchmark yields, bank investment portfolios and funding-market conditions.'),
 (r'\byields?\b|\bsecondary (?:bond )?market\b','Yield movements are relevant to the valuation of banks’ government-securities portfolios and wholesale funding.'),
 (r'\b(?:fuel|oil|import|energy) bill\b','Higher import bills increase foreign-currency demand and can feed into inflation and exchange-rate pressure.'),
 (r'\bexports?\b','Export earnings affect foreign-currency inflows and the repayment capacity of exporters.'),
 (r'\b(?:soes?|state firms?|state institutions?|state.owned)\b','State-enterprise reform affects fiscal risk and the state-sector exposures of banks.'),
 (r'\b(?:cse|aspi|stocks|bourse|indices|listings?|ipos?)\b','Capital-market conditions are relevant to bank and finance-company investments and to corporate funding.'),
 (r'\b(?:tax\w*|revenue|budget|fiscal|arrears)\b','Tax and revenue policy affects fiscal funding needs, the sovereign-financing environment and credit conditions.'),
)
# An estimate, warning or promise is reported as one, not as an outcome.
NOT_YET_FACT=re.compile(r'(?i)\b(?:warns?|estimates?|could|may|might|likely|urges?|calls? (?:on|for)|proposes?|pledges?|plans?|hopes?|expects?|seeks?)\b')


def why_it_matters(r: dict, assessed: dict) -> str:
 reason=general_reason(r,assessed)
 # A 'supplementary estimate' is an appropriation, not a forecast; a company 'proposing'
 # a share split is a corporate action, not a view.
 h=re.sub(r'(?i)\bsupplementary estimates?\b','',str(r.get('headline','')))
 if assessed.get('category') in ('LISTED_COMPANY_RESULTS','CAPITAL_MARKETS_GOVERNANCE') or re.search(r'(?i)share (?:split|subdivision)',h):
  return reason
 if assessed['section'] in ('MONETARY_MARKETS_EXTERNAL','MACRO_SYSTEMIC_CONTEXT') and NOT_YET_FACT.search(h):
  reason+=' This is a stated view, estimate or intention, not a confirmed outcome.'
 return reason


# Subjects specific enough to name in any section; checked first.
WHY_SPECIFIC=(
 (r'\bunauthori[sz]ed deposit|\bdeposit.taking\b','Deposit-taking outside the Finance Business Act puts savers at risk and draws on funds that would otherwise sit with licensed institutions; CBSL warnings mark enforcement priorities.'),
 (r'\bco.?op\w*(?: society)? fraud|\bcooperative fraud','Misappropriation at co-operative societies hits members’ savings and credit outside the banking system, and the recovery of funds may run through bank accounts.'),
 (r'\b(?:share (?:split|subdivision)|subdivision of (?:its )?shares|bonus issue|rights issue)\b','A listed company’s capital action changes share liquidity and pricing on the CSE.'),
 (r'\b(?:pyramid|ponzi|prohibited schemes?)\b','Prohibited pyramid and deposit-taking schemes are a CBSL enforcement priority; such cases point to consumer-protection risks and to funds that may pass through bank accounts.'),
 (r'\billegal (?:fund|money|foreign exchange) transfers?\b|\bhawala\b|\bundiyal\b','Money moved abroad under the guise of import payments points to trade-based money laundering, relevant to banks’ trade-finance checks and suspicious-transaction reporting.'),
 (r'\btrusts?\b.{0,40}\b(?:bill|act)\b|\bbeneficial owner\w*','Registration and oversight of trusts improves beneficial-ownership transparency, which banks rely on for customer due diligence and AML compliance.'),
 (r'\bmoney laundering\b|\bfatf\b|\bmutual evaluation\b|\baml\b','Anti-money-laundering developments bear on banks’ customer due diligence, reporting obligations and Sri Lanka’s standing in international evaluations.'),
 (r'\btin\b|taxpayer identification','Changes to TIN requirements affect bank account opening and customer onboarding procedures.'),
 (r'\b(?:epf|etf|provident fund)\b','EPF investment decisions affect a large pool of retirement savings managed by the Central Bank and the depth of the equity and government-securities markets.'),
 (r'\b(?:real estate|property|housing) prices?\b','Rising property prices affect collateral values and banks’ exposure to real-estate and housing lending.'),
 (r'\binternational capital markets?\b|\bsovereign bonds?\b|\bisbs?\b','The timing of a return to international capital markets affects external financing, the sovereign rating path and banks’ foreign-currency funding.'),
 (r'\bpoverty\b|household incomes?','Falling real household incomes weigh on borrowers’ repayment capacity and on demand for retail and microfinance credit.'),
 (r'\b(?:electricity|power) tariffs?\b|\btariff (?:hike|increase|revision)\b','Electricity tariffs feed into inflation and into the costs of households and businesses that borrow from banks.'),
 (r'\bgsp\+?(?:\b|(?<=\+))','EU GSP+ access affects export earnings, foreign-currency inflows and the credit quality of export-oriented borrowers.'),
 (r'\bport city\b','Port City rules shape foreign investment and offshore financial activity in the special economic zone.'),
 (r'\bonline (?:financial )?fraud\b|\bfinancial fraud\b|\bcyber fraud\b|\b(?:investment|online) scam\b','Organised online fraud targets bank customers and their accounts; it bears on banks’ fraud monitoring, mule-account controls and customer awareness.'),
 (r'\b(?:crypto\w*|virtual assets?|vasps?|digital assets?|bitcoin)\b','Virtual-asset regulation and crypto scams bear on payment-system integrity, consumer protection and banks’ exposure to crypto-linked flows.'),
 (r'\bfuel pric\w*','Fuel pricing drives CPC’s finances, inflation and the costs of borrowers across the economy.'),
 (r'\b(?:adb|asian development bank|world bank|aiib|jica)\b','Multilateral loans add to external financing and reserves, and their reform conditions shape fiscal policy.'),
 (r'\bpaye\b|tax.free threshold|tax relief|tax concessions?','Changes to income-tax relief and concessions affect households’ disposable income, borrowers’ repayment capacity and government revenue.'),
 (r'\bsupplementary estimates?\b','Supplementary spending adds to the Government’s financing needs and so to domestic borrowing and bond-market supply.'),
 (r'\bexcise\b','Excise collections are a large part of government revenue and of the fiscal targets under the IMF programme.'),
 (r'\b(?:holdings|plc|group|hemas|jkh)\b.{0,60}\b(?:revenue|profit|pat|pbt|earnings)\b|\b(?:1h|2q|3q|q[1-4]|first.half)\b.{0,40}\b(?:revenue|profit)\b',
  'Results of large listed groups indicate corporate earnings and the repayment capacity of major bank borrowers.'),
)


def general_reason(r: dict, assessed: dict) -> str:
 h=str(r.get('headline',''))
 for pattern,reason in WHY_SPECIFIC:
  if re.search(pattern,h,re.I):return reason
 if assessed.get('category')=='LISTED_COMPANY_RESULTS' or (LISTED_RESULTS.search(h) and RESULT_PERIOD.search(h)):
  return 'Results of large listed groups indicate corporate earnings and the repayment capacity of major bank borrowers.'
 # A sponsorship or partnership at an event is not a risk development.
 if re.search(r'(?i)\b(?:official (?:banking )?partner|sponsor\w*|summit|expo|exhibition)\b',h):
  return 'A bank’s visibility at an industry event; context rather than a risk development.'
 if assessed['section'] in ('MONETARY_MARKETS_EXTERNAL','MACRO_SYSTEMIC_CONTEXT','KEY_BANKING_SUPERVISORY'):
  for pattern,reason in WHY_BY_TOPIC:
   if re.search(pattern,h,re.I):return reason
 if assessed['section']=='KEY_BANKING_SUPERVISORY':
  if re.search(r'fraud|scam|phishing|breach|cyber',h,re.I): return 'Relevant to fraud controls, customer protection, incident response and operational resilience in supervised institutions.'
  if re.search(r'profit|earnings|results|\bpat\b',h,re.I): return 'Quarterly bank performance informs supervisory monitoring of earnings resilience, credit risk and capital generation.'
  # 'Capital markets' is not a bank's capital.
  if re.search(r'debenture|capital adequacy|capital (?:raising|buffer|shortfall|requirement)|tier (?:1|2|i|ii)\b|solvency',h,re.I): return 'This affects the funding and capital position of a supervised institution.'
  return 'Directly relevant to prudential supervision, regulatory compliance or banking-sector risk.'
 if assessed['section']=='BANKING_SECTOR_DEVELOPMENTS':
  if re.search(r'payment|app|digital|trading',h,re.I): return 'Relevant to digital-channel operations, third-party dependencies, payment controls and customer protection.'
  if re.search(r'loan|leasing|credit',h,re.I): return 'Relevant to bank credit products, sector exposures and underwriting practices.'
  return 'Relevant to developments in supervised banks and their customer services.'
 if assessed['section']=='OTHER_FINANCIAL_SECTOR': return 'Relevant to the wider regulated financial sector and funding, governance or consumer exposures.'
 if assessed['section']=='MONETARY_MARKETS_EXTERNAL': return 'Relevant to domestic funding conditions, market risk, liquidity or foreign-exchange exposures affecting banks.'
 if assessed['section']=='MACRO_SYSTEMIC_CONTEXT': return 'Provides economic or fiscal context for banking-sector credit, funding and systemic-risk monitoring.'
 return ''
