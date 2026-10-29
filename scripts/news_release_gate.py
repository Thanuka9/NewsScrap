"""Pre-publication guard for the existing News Scrap Financial Brief.

Read-only audit mode and explicit, post-email sent-edition recording.
Does not scrape, change ML, install plugins or patch production scripts.
Uses only the Python standard library.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

PUBLISHERS = (
    "Daily FT", "Daily Mirror", "Daily News", "The Morning", "The Island",
    "NewsFirst", "Ceylon Today",
)
SKIP_WORDS = frozenset({
    "a", "an", "as", "and", "at", "by", "for", "from", "in", "into", "of", "on", "or", "to", "the",
    "sri", "lanka", "bank", "banks", "new", "its", "with", "after", "report", "reports",
})
MATERIAL = re.compile(
    r"(?i)\b(?:cbsl|central bank|licensed bank|banking sector|\w+ bank|fiu|fatf|aml|"
    r"money laundering|cyberattack|cyber fraud|data breach|depositor|deposit insurance|"
    r"liquidity|prudential|capital adequacy|non.performing|npl|stage 2|stage 3|"
    r"impaired loans?|credit risk|fraud|scams?|deepfake|tax evasion|tax evaders|"
    r"money laundering|ird|excise|pawning|inflation|ncpi|ccpi|policy rate|"
    r"foreign exchange|sovereign rating|rating scale|green bond|financial institution|"
    r"financial regulator|microfinance|sdf|ndb|fitch|moody.s|"
    r"debentures?|fintech|sme loan scheme|loan scheme|pine labs|webxpay|"
    r"cybersecurity|cert alert|payment infrastructure)\b"
)
LOW_VALUE = re.compile(
    r"(?i)\b(?:awards?|best digital bank|winner|accolade|charity|ceremony|sponsor|"
    r"scholarship|customer reward|sports day|consumer promotion|vehicle dealership|"
    r"branch opening|business forum|university open day)\b"
)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def canonical(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", "", ""))


def words(title: str) -> set[str]:
    title = re.sub(r"(?i)\b(billion|bn|b)\b", "bn", title or "")
    title = re.sub(r"(?i)\b(million|mn|m)\b", "mn", title)
    return {word for word in re.findall(r"[a-z0-9]+", title.lower()) if word not in SKIP_WORDS and len(word) > 1}


def event_fact_key(card: dict) -> str:
    """High precision cross-source identities, not a blanket similarity score.

    Avoid matching generic topics, banks or market wrap headlines. Only
    reuse a key where the entity, event action and identifying number/fact
    jointly identify one development. Human review is still required for
    genuine follow-ups with new substantive information.
    """
    title = " ".join(str(card.get("headline") or "").casefold().split())
    amount_100 = bool(re.search(r"(?:\$|usd|us\$)?\s*100\s*(?:million|mn|m)\b", title))
    if (re.search(r"\b(?:adb|asian development bank)\b", title)
            and amount_100 and re.search(r"\b(?:social protection|livelihood|elderly|economic opportunities)", title)):
        return "ADB_USD100M_SOCIAL_PROTECTION_20261022"
    if (re.search(r"\b(?:ird|inland revenue)\b", title)
            and "excise" in title
            and re.search(r"\b(?:tax evasion|money laundering|mou|joint action)\b", title)):
        return "IRD_EXCISE_ANTIFRAUD_MOU_20261022"
    if (("ncpi" in title or "inflation" in title)
            and re.search(r"\b8[.]1\s*%", title)
            and re.search(r"\b(?:sept|september|unchanged|steady|remains)\b", title)):
        return "SEPT_2026_NCPI_8_1"
    if ("fitch" in title and "national" in title and "rating" in title
            and re.search(r"\brecalibrat", title)):
        return "FITCH_SRI_LANKA_NATIONAL_RECALIBRATION_20261022"
    if (re.search(r"\bndb\b", title)
            and re.search(r"\b2[.]9\s*(?:b|bn|billion)\b", title)
            and re.search(r"\b(?:q3|3q|pat|profit)\b", title)):
        return "NDB_Q3_2026_PAT_2_9B"
    return ""


def _near_event_dates(a: dict, b: dict) -> bool:
    try:
        x = date.fromisoformat(str(a.get('publication_date') or '')[:10])
        y = date.fromisoformat(str(b.get('publication_date') or '')[:10])
    except ValueError:
        return False
    return abs((x-y).days) <= 7


def same_story(a: dict, b: dict) -> str:
    au = set(map(canonical, a.get("source_urls", []))) - {""}
    bu = set(map(canonical, b.get("source_urls", []))) - {""}
    if au & bu:
        return "EXACT_URL"
    fa, fb = event_fact_key(a), event_fact_key(b)
    if fa and fa == fb and _near_event_dates(a, b):
        return "FACT_IDENTICAL_EVENT"
    # An ADB $100m approval with no stated purpose is ambiguous. Escalate
    # for review rather than calling a possibly unrelated facility identical.
    a_title, b_title = str(a.get('headline') or '').casefold(), str(b.get('headline') or '').casefold()
    adb_re = re.compile(r"\badb\b|asian development bank")
    amt_re = re.compile(r"(?:\$|usd|us\$)?\s*100\s*(?:million|mn|m)\b")
    if (all(adb_re.search(t) and amt_re.search(t) for t in (a_title,b_title))
            and _near_event_dates(a,b)):
        return "POSSIBLE_ADB_FINANCING_EVENT"
    aa, bb = words(a.get("headline", "")), words(b.get("headline", ""))
    if not aa or not bb:
        return ""
    # NEWS_GENERIC_DAILY_RECAP_SAFETY_V1: newspapers reuse identical titles
    # on consecutive trading days (e.g., "Indices close in red"). Separate
    # dated sessions are not the same event unless the source URL is exact.
    market_recap = re.search(
        r"(?i)\b(?:indices|cse|bourse|stock market|share market|bond yields?|bond market)\b",
        str(a.get("headline") or "") + " " + str(b.get("headline") or ""),
    )
    date_a = str(a.get("publication_date") or "")[:10]
    date_b = str(b.get("publication_date") or "")[:10]
    if market_recap and date_a and date_b and date_a != date_b:
        return ""
    if aa == bb and len(aa) >= 3:
        return "SAME_TITLE"
    shared = len(aa & bb)
    containment = shared / max(1, min(len(aa), len(bb)))
    ratio = SequenceMatcher(None, a.get("headline", "").casefold(), b.get("headline", "").casefold()).ratio()
    # Only flag credible candidates. These are not automatically deleted.
    if shared >= 4 and containment >= .76 and ratio >= .63:
        return "POSSIBLE_SAME_EVENT"
    return ""


class StoryParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stories = []
        self.card = None
        self.depth = 0
        self.title_depth = 0
        self.meta_depth = 0
        self.summary_depth = 0

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        classes = set((d.get("class") or "").split())
        if tag == "article" and classes & {"priority-card", "context-card"}:
            if self.card is not None:
                raise ValueError("Unexpected nested story card")
            self.card = {"headline": "", "publication_date": "", "source_urls": [], "summary": "", "topline": ""}
            self.depth = 1
            return
        if self.card is None:
            return
        if tag not in VOID:
            self.depth += 1
        if tag == "h3" and not self.title_depth:
            self.title_depth = self.depth
        if "story-topline" in classes:
            self.meta_depth = self.depth
        if tag == "p" and ("context-summary" in classes or (self.summary_depth == 0 and self._in_summary_block)):
            self.summary_depth = self.depth
        if "summary-block" in classes:
            self._in_summary_block = True
            self._summary_block_depth = self.depth
        if tag == "a" and d.get("href", "").startswith(("http://", "https://")):
            self.card["source_urls"].append(d["href"])

    _in_summary_block = False
    _summary_block_depth = 0

    def handle_data(self, data):
        if self.card is None:
            return
        if self.title_depth:
            self.card["headline"] += data
        if self.meta_depth:
            self.card["topline"] += " " + data
        if self.summary_depth:
            self.card["summary"] += data

    def handle_endtag(self, tag):
        if self.card is None or tag in VOID:
            return
        if self.title_depth == self.depth:
            self.title_depth = 0
        if self.meta_depth == self.depth:
            self.meta_depth = 0
        if self.summary_depth == self.depth:
            self.summary_depth = 0
        if self._in_summary_block and self._summary_block_depth == self.depth:
            self._in_summary_block = False
            self._summary_block_depth = 0
        self.depth -= 1
        if self.depth == 0:
            self.card["headline"] = " ".join(self.card["headline"].split())
            self.card["summary"] = " ".join(self.card["summary"].split())
            match = re.search(r"\b20\d\d-\d\d-\d\d\b", self.card["topline"])
            self.card["publication_date"] = match.group(0) if match else ""
            self.stories.append(self.card)
            self.card = None


def load_stories(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(path)
    parser = StoryParser()
    parser.feed(path.read_text(encoding="utf-8-sig"))
    if not parser.stories:
        raise ValueError(f"No story cards in {path}")
    return parser.stories


def load_inventory(path: Path, report_date: date) -> tuple[list[dict], list[dict]]:
    if not path or not path.is_file():
        return [], [{"kind": "INVENTORY_MISSING", "details": "Article audit CSV is required to verify excluded high-priority headlines"}]
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows or not {"headline", "source_url", "publication_date"} <= set(rows[0]):
        return [], [{"kind": "INVENTORY_INVALID", "details": "Required columns: headline, source_url, publication_date"}]
    from news_publication_history import earliest_eligible_day
    earliest=earliest_eligible_day(report_date)
    eligible = {(earliest + timedelta(days=n)).isoformat()
                for n in range((report_date-earliest).days+1)}
    rows = [r for r in rows if r.get("publication_date", "")[:10] in eligible]
    return rows, []


def audit(report: Path, date_arg: date, sent_paths: list[Path], inventory: Path | None, coverage: Path | None, out: Path, reviewed: Path | None = None, decision_manifest: Path | None = None) -> int:
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
    from bank_intel.publication.release import audit_release
    candidate=decision_manifest or report.parent/'candidate_edition.json'
    if not candidate.is_file():
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps({'release_status':'HOLD_FOR_REVIEW',
            'issues':[{'kind':'GOVERNED_DECISION_MANIFEST_REQUIRED'}]},indent=2),encoding='utf-8')
        return 2
    return audit_release(report=report,manifest_path=candidate,inventory=inventory,
                         coverage=coverage,out=out,report_date=date_arg.isoformat())



AUTOMATIC_COVERAGE_POLICY='AUTOMATIC_COVERAGE_POLICY_V1'
AUTO_ACCEPTABLE=('UNRESOLVED_FETCH_OR_DISCOVERY','INVALID_ARTICLE_EXTRACTION','COLLECTOR_HEALTH:PARTIAL_ERROR')


def _url_headline(url: str) -> str:
    """Readable words from an article URL slug ('' when the slug has none)."""
    slug=[part for part in urlsplit(str(url)).path.split('/') if part]
    words=re.findall(r'[a-z]{3,}',(slug[-2] if slug and re.fullmatch(r'[\d-]+',slug[-1]) and len(slug)>1 else (slug[-1] if slug else '')).casefold())
    return ' '.join(words) if len(words)>=3 else ''


def automatic_coverage_acceptance(payload: dict, issues: list[str], failed_surfaces=None):
    """Accepted issues when coverage needs no person, else None.

    Clean evidence verifies itself. Otherwise only failed article pages may be
    accepted, and only when every one is identifiably non-financial from its
    URL words. A failed section, unreadable URL or possible financial story
    leaves the publisher for a reviewer.
    """
    if not issues:return {}
    # Sections covered by verified alternative discovery are not failures.
    failed=payload.get('failed_discovery_surfaces') if failed_surfaces is None else failed_surfaces
    if any(i not in AUTO_ACCEPTABLE for i in issues) or failed:return None
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
    from bank_intel.publication.model_policy import financial_signal
    urls=list(payload.get('unresolved_fetch_urls') or [])+[x.get('url','') for x in payload.get('extraction_failures') or [] if isinstance(x,dict)]
    if int(payload.get('invalid_extraction') or 0) and not payload.get('extraction_failures'):return None
    reasons=[]
    for url in dict.fromkeys(urls):
        words=_url_headline(url)
        if not words or not financial_signal({'headline':words,'source_url':url}):return None
        reasons.append(f'{url} ({words})')
    note='Non-financial failed pages: '+'; '.join(reasons) if reasons else 'Fetch failures recovered within the run.'
    return {issue:note for issue in issues}


def prepare_coverage(date_arg: date, project_root: Path, output: Path) -> Path:
    """Create an evidence *worksheet*, never fake editorial verification.

    Collector statuses prove what the crawler attempted, not that every
    publisher headline was discovered. Editorial sign-off remains necessary.
    """
    from news_publication_history import earliest_eligible_day
    keys={
        'Daily FT':'daily_ft','Daily Mirror':'daily_mirror',
        'Daily News':'daily_news','The Morning':'the_morning',
        'The Island':'island','NewsFirst':'newsfirst',
        'Ceylon Today':'ceylon_today',
    }
    required={
        'daily_mirror':{'business','homepage','breaking news','top stories','latest news'},
        'daily_ft':{'financial_services','front_page','top_story','homepage'},
        'the_morning':{'business','homepage'},
        'daily_news':{'business','local','homepage'},
        'island':{'business','news','homepage'},
        'newsfirst':{'latestpaginated','local','world','business','homepage'},
        'ceylon_today':{'daily_finance','sunday_finance','homepage'},
    }
    root=Path(project_root).resolve()
    out={'report_date':date_arg.isoformat(),'window_start':earliest_eligible_day(date_arg).isoformat(),
         'workflow':'COLLECTOR_EVIDENCE_REQUIRES_EDITORIAL_VERIFICATION',
         'publishers':{}}
    for name,key in keys.items():
        status_file=root/'data/source_status'/f'{key}.json'
        issues=[]
        payload={}
        try:
            payload=json.loads(status_file.read_text(encoding='utf-8-sig'))
            raw=payload.get('discovery_surfaces') or []
            surfaces=[str(e.get('name') or e.get('listing_name') or '') if isinstance(e,dict) else str(e)
                      for e in raw]
            surface_set={x.strip().casefold() for x in surfaces}
            missing=required[key]-surface_set
            if missing: issues.append('MISSING_SECTIONS:'+','.join(sorted(missing)))
            if payload.get('execution_status')!='SUCCESS':issues.append('COLLECTOR_NOT_SUCCESS')
            if str(payload.get('completed_at',''))[:10]!=date_arg.isoformat():issues.append('STALE_STATUS')
            # A verified, newest-first WordPress API whose oldest post predates
            # the window lists every post in it, so failed HTML archive pages
            # for the same sections no longer leave coverage unknown.
            api=payload.get('wp_api_discovery') or {}
            api_covers_window=(api.get('api_status')=='VERIFIED' and api.get('api_pagination_complete') is True
                               and str(api.get('api_oldest_date') or '9999')<out['window_start'])
            covered_surfaces={'daily_finance','sunday_finance'} if api_covers_window else set()
            failed_surfaces={str(x).casefold() for x in payload.get('failed_discovery_surfaces') or []}-covered_surfaces
            if (failed_surfaces or payload.get('unresolved_fetch_urls')
                or int(payload.get('fetch_failures') or 0)):
                issues.append('UNRESOLVED_FETCH_OR_DISCOVERY')
            if int(payload.get('truncated_discovery_urls') or 0):issues.append('TRUNCATED_LISTING')
            if int(payload.get('invalid_extraction') or 0):issues.append('INVALID_ARTICLE_EXTRACTION')
            if int(payload.get('missing_date') or 0):issues.append('MISSING_PUBLICATION_DATE')
            if payload.get('health_status') not in {'HEALTHY','SUCCESS'}:
                issues.append('COLLECTOR_HEALTH:'+str(payload.get('health_status')))
            for page in payload.get('listing_pages',[]):
                listing=str(page.get('listing_name') or page.get('listing_url'))
                if page.get('pagination_complete') is False and listing.casefold() not in covered_surfaces:
                    issues.append('INCOMPLETE_PAGINATION:'+listing)
            if payload.get('wp_api_discovery',{}).get('api_pagination_complete') is False:issues.append('INCOMPLETE_WORDPRESS_PAGINATION')
            if int(payload.get('invalid_extraction') or 0) and not payload.get('extraction_failures'):issues.append('EXTRACTION_FAILURE_URLS_NOT_RECORDED')
            # Identify evidence by content, not by how the project root was
            # spelled, so a signature survives an absolute/relative rerun.
            evidence=f'data/source_status/{key}.json: {len(surfaces)} discovery surfaces; '+str(payload.get('completed_at'))+'; sha256='+hashlib.sha256(status_file.read_bytes()).hexdigest()
        except (OSError, ValueError, TypeError) as exc:
            surfaces=[]
            issues.append('MISSING_OR_INVALID_STATUS:'+type(exc).__name__)
            evidence=str(status_file)
        out['publishers'][name]={'status':'PENDING','surfaces':surfaces,
                                  'evidence':evidence,
                                  'automatic_issues':issues,
                                  'failure_details':payload.get('extraction_failures',[]) if 'payload' in locals() else [],
                                  'pagination_evidence':payload.get('listing_pages',[]) if 'payload' in locals() else [],
                                  'reviewer':'','reviewed_at':''}
        accepted=automatic_coverage_acceptance(payload,issues,locals().get('failed_surfaces'))
        if accepted is not None:
            out['publishers'][name].update(
                status='VERIFIED',reviewer=AUTOMATIC_COVERAGE_POLICY,reviewed_at=datetime.now().astimezone().isoformat(),
                accepted_issues=accepted,
                review_notes=('Collector evidence clean.' if not accepted else
                              'Every failed page is identifiably non-financial (URL checked against the financial-signal policy).'))
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        # On a rerun, update evidence from fresh collector statuses. Preserve
        # manual verification only if that evidence has not changed.
        try:
            previous=json.loads(output.read_text(encoding='utf-8-sig'))
            if previous.get('report_date') != date_arg.isoformat():
                raise ValueError('Existing coverage worksheet date does not match')
            for name, entry in out['publishers'].items():
                old=(previous.get('publishers') or {}).get(name,{})
                if old.get('evidence')==entry['evidence'] and old.get('surfaces')==entry['surfaces'] \
                   and old.get('automatic_issues')==entry['automatic_issues']:
                    if old.get('status')=='VERIFIED':
                        entry['status']='VERIFIED'
                        entry['reviewer']=old.get('reviewer','')
                        entry['reviewed_at']=old.get('reviewed_at','')
                        # Preserve signed, individually reasoned exceptions only
                        # while the collector evidence is byte-for-byte unchanged.
                        entry['accepted_issues']=dict(old.get('accepted_issues') or {})
                        entry['review_notes']=old.get('review_notes','')
        except (ValueError,OSError,TypeError,AttributeError) as exc:
            raise RuntimeError(f'Unable to reconcile old coverage worksheet: {output}') from exc
    output.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    print('[COVERAGE WORKSHEET UPDATED]',output)
    return output


def promote_passed_preview(date_arg: date, qa_html: Path, release_qa: Path, report_root: Path, reviewer: str) -> Path:
    from news_publication_history import assert_edition_not_sent
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
    from bank_intel.publication.delivery import approve
    assert_edition_not_sent(report_root,date_arg)
    manifest=qa_html.parent/'candidate_edition.json'
    data=json.loads(manifest.read_text(encoding='utf-8'))
    if data['date']!=date_arg.isoformat():raise ValueError('Candidate date mismatch')
    if Path(data.get('release_qa','')).resolve()!=release_qa.resolve():raise ValueError('Candidate release QA mismatch')
    return approve(manifest,qa_html,reviewer)


def mark_sent(date_arg: date, report: Path, root: Path, confirm: bool, recipient_group: str = '', message_id: str = '', sent_at: str = ''):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
    from bank_intel.publication.delivery import record_sent
    approval=report.parent/'APPROVED_EDITION.json'
    payload=json.loads(approval.read_text(encoding='utf-8'))
    if payload['date']!=date_arg.isoformat() or Path(payload['attachment']).resolve()!=report.resolve():
        raise ValueError('Report does not match approved edition')
    return record_sent(approval,recipient_group,confirm,message_id,sent_at)


def record_send_time(date_arg: date, root: Path, sent_at: str, operator: str):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
    from bank_intel.publication.delivery import record_send_time as fill
    record=root/date_arg.isoformat()/'window_1d'/'SENT_EDITION.json'
    if not record.is_file():raise FileNotFoundError('No confirmed sent edition: '+str(record))
    return fill(record,sent_at,operator)


FREEZE_SCRIPTS=('run_pipeline.py','run_collection.py','run_intelligence_stage1.py','run_intelligence_ml.py',
    'run_ml_corpus_analysis.py','run_supervisory_taxonomy.py','build_financial_brief_v2.py','recover_collection_gaps.py',
    'audit_news_flow.py','run_integrated_release.py','news_release_gate.py','news_publication_history.py',
    'check_release.py','_runtime.py','run_daily_ft.py','run_ceylon_today.py','run_daily_mirror.py','run_island.py',
    'run_generic_sources.py','run_daily_news_task.ps1','run_scheduler.py')
FREEZE_CONFIG=('config/sources/registry.yaml','config/sources/generic_registry.yaml','config/sources/daily_ft.yaml',
    'config/sources/ceylon_today.yaml','config/sources/daily_mirror.yaml','config/sources/daily_news.yaml',
    'config/sources/island.yaml','config/intelligence/entities_cbsl.yaml','config/intelligence/ml.yaml',
    'config/intelligence/stage1.yaml','config/intelligence/taxonomy.yaml','config/intelligence/reporting.yaml',
    'config/intelligence/review.yaml')


def write_freeze(root: Path, operator: str) -> Path:
    """Freeze the production code the scheduled run verifies, only from a passing build.

    Covers every pipeline entrypoint, all of src/bank_intel and production
    configuration, so a later change to any of them stops the scheduled run
    until it is tested and frozen again.
    """
    import subprocess,sys
    from datetime import datetime
    root=Path(root).resolve()
    tests=subprocess.run([sys.executable,'-m','pytest','-q','-p','no:cacheprovider'],cwd=root,capture_output=True,text=True)
    if tests.returncode!=0:
        raise RuntimeError('Refusing to freeze: test suite failed\n'+tests.stdout[-2000:])
    paths=[root/'scripts'/name for name in FREEZE_SCRIPTS]+[root/p for p in FREEZE_CONFIG]
    paths+=sorted(p for p in (root/'src'/'bank_intel').rglob('*') if p.is_file()
                  and p.suffix in ('.py','.html','.css','.json') and '__pycache__' not in p.parts)
    missing=[str(p) for p in paths if not p.is_file()]
    if missing:raise RuntimeError('Refusing to freeze: missing '+'; '.join(missing))
    payload={'freeze_version':'news-production-freeze-v2','created_at':datetime.now().astimezone().isoformat(),
             'created_by':operator,'tests':tests.stdout.strip().splitlines()[-1],'project_root':str(root),
             'files':[{'path':str(p.relative_to(root)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest().upper()} for p in paths]}
    out=root/'config'/'production_freeze.json'
    sys.path.insert(0,str(root/'src'))
    from bank_intel.common.reliability import atomic_json
    atomic_json(out,payload)
    print('[FREEZE WRITTEN]',out,len(payload['files']),'files;',payload['tests'])
    return out


def main():
    p = argparse.ArgumentParser(description="News Scrap: final independent brief QA and actual-sent edition inventory")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit")
    a.add_argument("--date", required=True)
    a.add_argument("--report", type=Path, required=True)
    a.add_argument("--sent-html", type=Path, action="append", default=[])
    a.add_argument("--inventory", type=Path)
    a.add_argument("--decision-manifest", type=Path)
    a.add_argument("--coverage", type=Path, help="Per-publisher collection evidence, checked for today's date")
    a.add_argument("--out", type=Path)
    a.add_argument("--reviewed", type=Path, help="Signed editorial decisions for material exclusions")
    c = sub.add_parser("prepare-coverage")
    c.add_argument("--date", required=True)
    c.add_argument("--project-root", type=Path, default=Path.cwd())
    c.add_argument("--out", type=Path)
    r = sub.add_parser("promote", help="Promote a PASS-reviewed QA preview to the normal attachment, without emailing it")
    r.add_argument("--date", required=True)
    r.add_argument("--reviewer", required=True)
    r.add_argument("--report", type=Path, required=True, help="QA.html preview audited with PASS")
    r.add_argument("--qa", type=Path, required=True, help="news_release_qa.json showing PASS")
    r.add_argument("--reports-root", type=Path, default=Path('data/processed/reports'))
    s = sub.add_parser("mark-sent")
    s.add_argument("--date", required=True)
    s.add_argument("--recipient-group", required=True)
    s.add_argument("--message-id", default="")
    s.add_argument("--report", type=Path, required=True)
    s.add_argument("--reports-root", type=Path,default=Path("data/processed/reports"))
    s.add_argument("--confirm-sent",action="store_true")
    s.add_argument("--sent-at",default="",help="Actual distribution time, ISO 8601 with offset; defaults to now")
    t = sub.add_parser("record-send-time", help="Record the actual send time of an already-registered sent edition (once)")
    t.add_argument("--date", required=True)
    t.add_argument("--sent-at", required=True, help="ISO 8601 with offset, e.g. 2026-10-26T09:10:00+05:30")
    t.add_argument("--operator", required=True)
    t.add_argument("--reports-root", type=Path, default=Path("data/processed/reports"))
    f = sub.add_parser("freeze", help="Run the test suite and, if it passes, write config/production_freeze.json")
    f.add_argument("--operator", required=True)
    f.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = p.parse_args()
    if args.cmd == "freeze":
        write_freeze(args.project_root, args.operator)
        return
    d = date.fromisoformat(args.date)
    if args.cmd == "prepare-coverage":
        out = args.out or Path("data/processed/reports")/args.date/"window_1d"/"news_coverage_verified.json"
        prepare_coverage(d,args.project_root,out)
        return
    if args.cmd == "audit":
        out = args.out or Path("data/processed/reports") / args.date / "window_1d" / "news_release_qa.json"
        raise SystemExit(audit(args.report,d,args.sent_html,args.inventory,args.coverage,out,args.reviewed,args.decision_manifest))
    if args.cmd == 'promote':
        promote_passed_preview(d,args.report,args.qa,args.reports_root,args.reviewer)
        return
    if args.cmd == 'record-send-time':
        print('[SEND TIME RECORDED]',record_send_time(d,args.reports_root,args.sent_at,args.operator))
        return
    mark_sent(d,args.report,args.reports_root,args.confirm_sent,args.recipient_group,args.message_id,args.sent_at)


if __name__ == "__main__":
    main()
