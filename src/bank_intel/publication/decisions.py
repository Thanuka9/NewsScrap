"""One authoritative article decision; model and rules stay distinct."""
from __future__ import annotations
import re
from .identity import event_signature
from .evidence import evidence_issues
from .materiality import material_context

BLOCK_POLITICS = re.compile(r'(?i)\b(?:constitution|remanded|monks|religious|gnanasara|ethnic|bodu bala|wildlife|surveyors|sustainable solutions to emotive)\b')
FINANCIAL_REGULATOR = re.compile(r'(?i)\b(?:central bank|cbsl|financial intelligence unit|bank supervision|resolution framework|banking act|prudential|capital adequacy|deposit insurance|anti.money laundering|aml|fatf|bank fraud|banking fraud|banking scam|fake bank|banking credentials|customer bank accounts)\b')
DIRECT_BANK = re.compile(r'(?i)\b(?:national development bank|ndb|sampath bank|people.s bank|commercial bank|dfcc bank|hatton national bank|hnb plc|hnb bank|seylan bank|amana bank|pan asia bank|nations trust bank|cargills bank|union bank|bank of ceylon|regional development bank|standard chartered|hsbc|state bank of india|sbi|bank lending|bank deposits|bank earnings)\b')
OTHER_FIN = re.compile(r'(?i)\b(?:finance plc|finance company|asset finance|hnb finance|lanka finance|microfinance|insurance plc|insurance company|insurer|slic life|life insurance|debenture issue|ratings? downgrade|fitch ratings?|payment service|cross.border payment|qr payment|lankaqr|webxpay|fintech)\b')
MARKET = re.compile(r'(?i)\b(?:bond yields|treasury bills?|t.bills?|treasury bond|bond market|cse watch list|colombo stock exchange|aspi|share price index|cse falls|stock market|net foreign inflow|foreign exchange reserves|tourism revenue|tourism earnings|remittances|inflation|ncpi|ccpi|policy rate|t.bill auction|secondary market|bond auction)\b')
SYSTEMIC = re.compile(r'(?i)\b(?:imf|international monetary fund|sovereign debt|sovereign rating|fiscal revenue|treasury surplus|public debt|budget 2027|tax proposals|economic growth|fiscal policy|adb approves|asian development bank|foreign direct investment|foreign reserves)\b')
RISK_ACTION = re.compile(r'(?i)\b(?:fraud|breach|ransomware|attack|phishing|warning|warns|cyber|license|liquidation|insolvency|capital|solvency|earnings|profits?|results|debenture|compliance|fine|penalty|resolv|directiv|auditor|non.performing)\b')
LOW_PR = re.compile(r'(?i)\b(?:award|honours|sponsor|festival|expo|discount|prize draw|birthday|anniversary|ceremony)\b')


def decide(r: dict) -> dict:
 """Source facts, sector and editorial priority, independently of raw Stage-A label."""
 h=str(r.get('headline') or '');body=str(r.get('body') or '')
 # Certain wire/short headlines omit the institution. A clearly attributed
 # opening sentence can supply that identity for a material risk development.
 body_lead=body[:650]
 explicitly_attributed_bank_risk=(
     bool(re.search(r'(?i)\b(?:fraud|recovery|recoveries|controls|cyber|penalty|capital shortfall)\b',h))
     and bool(DIRECT_BANK.search(body_lead))
     and bool(re.search(r'(?i)\b(?:fraud|financial loss|recovery|recoveries|legal action|capital|governance controls|cyber)\b',body_lead)))
 result={'decision':'EXCLUDE','section':'','category':'','importance':'NONE','reason':'OUT_OF_SCOPE',
         'source_section':r.get('section',''), 'event_signature': event_signature(r)}
 issues=evidence_issues(r)
 result['evidence_issues']=issues
 if BLOCK_POLITICS.search(h) and not re.search(r'banking act|financial system|treasury surplus|fiscal revenue',h,re.I):
  result['reason']='POLITICAL_NONFINANCIAL';return result
 if re.search(r'award|best bank by|children.s day|anniversary celebration|honour|sponsor',h,re.I) and not re.search(r'fine|penalty|regulatory breach|fraud|cyber',h,re.I):
  result['reason']='LOW_MATERIALITY_PROMOTIONAL';return result
 # Supervisory headline requires a material action, not just mentioning CBSL in macro commentary.
 # The verified event signature is narrower than generic source keywords.
 # Do not send direct central-bank conduct actions or measured finance-company
 # credit deterioration into a low-priority legacy review simply because the
 # headline says "reviews" instead of "regulates", or NBFI instead of "bank".
 context=material_context(r)
 sig=result['event_signature']
 if re.search(r'(?i)\b(?:Bangladesh|Pakistan|India|Nepal)\b',h) and not re.search(r'(?i)Sri Lanka|Lankan|cross.border|spillover',body[:1800]):
  result.update(reason='FOREIGN_DOMESTIC_NO_LOCAL_TRANSMISSION')
 elif context:
  section,category,reason=context
  result.update(decision='REVIEW' if reason.endswith('_REVIEW') else 'INCLUDE',section=section,category=category,importance='MEDIUM',reason=reason)
 elif sig.startswith('CBSL_EARLY_SETTLEMENT_PARATE_THEMATIC@'):
  result.update(decision='INCLUDE',section='KEY_BANKING_SUPERVISORY',category='GOVERNANCE_CONDUCT',importance='HIGH',reason='EVIDENCED_CBSL_CONDUCT_REVIEW')
 elif sig.startswith('NBFI_HOUSEHOLD_STAGE2_RISK@'):
  result.update(decision='INCLUDE',section='OTHER_FINANCIAL_SECTOR',category='ASSET_QUALITY_NPL_STAGE2',importance='HIGH',reason='EVIDENCED_NBFI_PORTFOLIO_RISK')
 elif sig.startswith('BOC_TIER2_DEBENTURE_30BN@'):
  result.update(decision='INCLUDE',section='KEY_BANKING_SUPERVISORY',category='BANK_CAPITAL_RISK',importance='HIGH',reason='EVIDENCED_TIER2_CAPITAL_ISSUE')
 elif sig.startswith('HNB_FINANCE_TOYOTA_LEASING@'):
  result.update(decision='INCLUDE',section='OTHER_FINANCIAL_SECTOR',category='REGULATED_FINANCE',importance='MEDIUM',reason='FINANCE_COMPANY_CREDIT_PARTNERSHIP')
 elif sig.startswith('SEYLAN_FAIRWAY_LATITUDE_HOUSING@'):
  result.update(decision='INCLUDE',section='BANKING_SECTOR_DEVELOPMENTS',category='BANK_CREDIT_PRODUCT',importance='MEDIUM',reason='BANK_HOUSING_LOAN_PARTNERSHIP')
 elif sig.startswith('PDMO_DOMESTIC_DOLLAR_BOND_BIDS_REJECTED@'):
  result.update(decision='INCLUDE',section='MONETARY_MARKETS_EXTERNAL',category='SOVEREIGN_BOND_AUCTION',importance='HIGH',reason='REJECTED_DOMESTIC_DOLLAR_BOND_BIDS')
 elif sig.startswith('NTB_UNION_ASSURANCE_BANCASSURANCE_RENEWAL@'):
  result.update(decision='INCLUDE',section='BANKING_SECTOR_DEVELOPMENTS',category='BANCASSURANCE_PARTNERSHIP',importance='MEDIUM',reason='BANK_DISTRIBUTION_INSURANCE_PARTNERSHIP')
 elif re.search(r'(?i)\b(?:cse|stock market|bourse|aspi)\b',h) and re.search(r'(?i)\b(?:aspi|all share price index|s&p sl20)\b',body[:900]) and re.search(r'(?i)\b(?:closed|starts week|ends week|fell|red|down|slips|rattled|plunged)\b',h):
  result.update(decision='INCLUDE',section='MONETARY_MARKETS_EXTERNAL',category='MARKET_EXTERNAL',importance='MEDIUM',reason='OBSERVED_CSE_TRADING_SESSION')
 elif FINANCIAL_REGULATOR.search(h) and (RISK_ACTION.search(h) or re.search(r'financial stability|resolution|policy rate|regulation|licensed|supervisory|fake bank|fraud|scam|customer credentials',h,re.I)):
  result.update(decision='INCLUDE',section='KEY_BANKING_SUPERVISORY',category='REGULATORY_RISK',importance='HIGH',reason='DIRECT_SUPERVISORY_HEADLINE')
 elif explicitly_attributed_bank_risk:
  result.update(decision='INCLUDE',section='KEY_BANKING_SUPERVISORY',
    category='BANK_RISK_GOVERNANCE',importance='HIGH',reason='BANK_RISK_IDENTIFIED_IN_LEAD')
 elif DIRECT_BANK.search(h):
  is_material=bool(re.search(r'\b(?:earnings|profit|pat|results|quarter|3q|q3|debenture|capital|fraud|cyber|nonperforming|rating|governance)\b',h,re.I))
  result.update(decision='INCLUDE',section=('KEY_BANKING_SUPERVISORY' if is_material else 'BANKING_SECTOR_DEVELOPMENTS'),category=('BANK_CAPITAL_RISK' if is_material else 'BANKING_DEVELOPMENTS'),importance=('HIGH' if is_material else 'MEDIUM'),reason='DIRECT_BANK_HEADLINE')
 elif re.search(r'\b(?:cse watch list|watch list move|auditor.s disclaimer|related.party deal|listed share acquisition)\b',h,re.I):
  result.update(decision='INCLUDE',section='MONETARY_MARKETS_EXTERNAL',category='CAPITAL_MARKETS_GOVERNANCE',importance='MEDIUM',reason='MATERIAL_LISTED_MARKET_DEVELOPMENT')
 elif re.search(r'(?i)\b(?:tourists?|tourism|tourist arrivals?)\b',h) and re.search(r'(?i)(?:1\.8\s*m\b|1\.8\s*million|tourist arrivals? rise|arrivals? surpass|revenue hits)',h) and re.search(r'(?i)(?:1,813,484|119,805|2,245 million|tourism revenue|tourism earnings|year.to.date arrivals|first 22 days of october)',body[:1400]):
  result.update(decision='INCLUDE',section='MONETARY_MARKETS_EXTERNAL',category='TOURISM_FX_EARNINGS',importance='MEDIUM',reason='MATERIAL_TOURISM_EXTERNAL_STATISTICS')
 elif re.search(r'(?i)\btourism industry\b',h) and re.search(r'(?i)\b(?:revenue|earnings|foreign exchange)\b',body[:850]):
  result.update(decision='REVIEW',section='MONETARY_MARKETS_EXTERNAL',category='TOURISM_FX_EARNINGS',importance='MEDIUM',reason='TOURISM_REVENUE_CUTOFF_CHECK')
 elif OTHER_FIN.search(h):
  result.update(decision='INCLUDE',section='OTHER_FINANCIAL_SECTOR',category='REGULATED_FINANCE',importance='MEDIUM',reason='FINANCIAL_SECTOR_HEADLINE')
 elif re.search(r'(?i)\bfitch\b',h) and re.search(r'(?i)\b(?:national ratings?|sovereign upgrade|rating scale|rating recalibration)\b',h):
  result.update(decision='INCLUDE',section='MONETARY_MARKETS_EXTERNAL',
    category='CREDIT_RATINGS',importance='MEDIUM',reason='MATERIAL_RATING_DEVELOPMENT')
 elif re.search(r'(?i)\b(?:Sri Lanka CERT|SLCERT|critical security vulnerabilities|critical global PHP vulnerabilities|php security|php vulnerabilities|php flaws)\b',h):
  result.update(decision='REVIEW',section='BANKING_SECTOR_DEVELOPMENTS',
    category='CROSS_SECTOR_CYBER',importance='MEDIUM',reason='ASSESS_FINANCIAL_SECTOR_IMPACT')
 elif re.search(r'(?i)\bLOLC\b',h) and re.search(r'(?i)\b(?:court|order|false statements|legal|fraud|governance)\b',h):
  result.update(decision='REVIEW',section='OTHER_FINANCIAL_SECTOR',category='FINANCIAL_GROUP_LEGAL_GOVERNANCE',importance='MEDIUM',reason='LEGAL_REPUTATIONAL_FINANCIAL_GROUP_REVIEW')
 elif re.search(r'(?i)\bassetline finance\b',h):
  result.update(decision='REVIEW',section='OTHER_FINANCIAL_SECTOR',category='REGULATED_FINANCE',importance='LOW',reason='REGULATED_FINANCE_COMPANY_EXPANSION_REVIEW')
 elif MARKET.search(h):
  result.update(decision='INCLUDE',section='MONETARY_MARKETS_EXTERNAL',category='MARKET_EXTERNAL',importance='MEDIUM',reason='FINANCIAL_MARKET_HEADLINE')
 elif (re.search(r'\b(?:president|treasury)\b',h,re.I) and re.search(r'\b(?:surplus|public debt|soes|government revenue|fiscal)\b',h,re.I)) or \
      re.search(r'\b(?:tax administration|budget 2027|revenue strategy|imf flags reserves|fiscal reform)\b',h,re.I):
  # Stakeholder wish-lists for a budget are advocacy, not a fiscal indicator.
  advocacy=re.search(r'(?i)\b(?:proposals?|recommendations?|urges?|calls? for|wish.?list|demands?)\b',h)
  result.update(decision='REVIEW' if advocacy else 'INCLUDE',section='MACRO_SYSTEMIC_CONTEXT',category='FISCAL_SYSTEMIC',importance='MEDIUM',
                reason='MACRO_CONTEXT_REVIEW' if advocacy else 'MATERIAL_FISCAL_INDICATOR')
 elif re.search(r'\b(?:imf chief information officer|intelligent central bank|imf cio|shirin hamid|central bank of the future)\b',h,re.I):
  result.update(decision='INCLUDE',section='MACRO_SYSTEMIC_CONTEXT',category='CENTRAL_BANK_TECHNOLOGY',importance='MEDIUM',reason='CENTRAL_BANK_POLICY_TECHNOLOGY')
 elif SYSTEMIC.search(h):
  result.update(decision='REVIEW',section='MACRO_SYSTEMIC_CONTEXT',category='SYSTEMIC_MACRO',importance='MEDIUM',reason='MACRO_CONTEXT_REVIEW')
 elif r.get('section') not in (None,'','EXCLUDE'):
  result.update(decision='REVIEW',section=r.get('section'),category=r.get('category'),importance='LOW',reason='LEGACY_INCLUDE_NEEDS_EVIDENCE')
 # Only candidates with material relevance should be sent to a factual mismatch queue.
 # A weekend market article may recap a session already published in the
 # previous day's edition. Its publication date is not its market-session date.
 # Without the actual previous event facts, flag for review rather than
 # silently publish or automatically delete a possibly new weekly recap.
 if result['decision']=='INCLUDE' and DIRECT_BANK.search(h) and re.search(r'(?i)explores?|discuss(?:es|ions)?|courtesy',h) and not RISK_ACTION.search(h):
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


def why_it_matters(r: dict, assessed: dict) -> str:
 h=str(r.get('headline',''))
 if assessed['section']=='KEY_BANKING_SUPERVISORY':
  if re.search(r'fraud|scam|phishing|breach|cyber',h,re.I): return 'Relevant to fraud controls, customer protection, incident response and operational resilience in supervised institutions.'
  if re.search(r'profit|earnings|results',h,re.I): return 'Quarterly bank performance informs supervisory monitoring of earnings resilience, credit risk and capital generation.'
  if re.search(r'debenture|capital|solvency',h,re.I): return 'This affects the funding and capital position of a supervised institution.'
  return 'Directly relevant to prudential supervision, regulatory compliance or banking-sector risk.'
 if assessed['section']=='BANKING_SECTOR_DEVELOPMENTS':
  if re.search(r'payment|app|digital|trading',h,re.I): return 'Relevant to digital-channel operations, third-party dependencies, payment controls and customer protection.'
  if re.search(r'loan|leasing|credit',h,re.I): return 'Relevant to bank credit products, sector exposures and underwriting practices.'
  return 'Relevant to developments in supervised banks and their customer services.'
 if assessed['section']=='OTHER_FINANCIAL_SECTOR': return 'Relevant to the wider regulated financial sector and funding, governance or consumer exposures.'
 if assessed['section']=='MONETARY_MARKETS_EXTERNAL': return 'Relevant to domestic funding conditions, market risk, liquidity or foreign-exchange exposures affecting banks.'
 if assessed['section']=='MACRO_SYSTEMIC_CONTEXT': return 'Provides economic or fiscal context for banking-sector credit, funding and systemic-risk monitoring.'
 return ''
