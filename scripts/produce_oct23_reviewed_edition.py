#!/usr/bin/env python3
"""Rebuild the reviewed 23 Oct Financial Brief from *local* production evidence.

One-time, reproducible editorial reconciliation. Does NOT repair collector
completeness, fabricate QA PASS, email the file, or alter any sent manifest
until an explicit post-email ``sent`` command is run.

Commands:
  python scripts/produce_oct23_reviewed_edition.py prepare
  python scripts/produce_oct23_reviewed_edition.py release --reviewer <ID> --acknowledge-coverage-exceptions
  python scripts/produce_oct23_reviewed_edition.py sent
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError as e:
    raise SystemExit('Missing beautifulsoup4 in .venv (already used by the News Scrap scraper).') from e

DAY = '2026-10-23'
YESTERDAY = '2026-10-22'
NAME = f'Daily Financial Sector News Brief {DAY}'


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def stories(doc):
    return doc.select('article.priority-card, article.context-card')


def title(el):
    h = el.select_one('h3')
    if not h:
        raise ValueError('Article missing title')
    return h.get_text(' ', strip=True)


def source_urls(el):
    return [x.get('href') for x in el.select('a[href^="http"]')]


def locate(doc, headline):
    matching = [x for x in stories(doc) if title(x) == headline]
    if len(matching) != 1:
        raise RuntimeError(f'Expected exactly one story {headline!r}; found {len(matching)}')
    return matching[0]


def set_text(el, value):
    if el is None:
        raise RuntimeError('Missing article element')
    el.clear()
    el.append(str(value))


def publisher_section(header):
    if header.startswith('CBSL & Bank Supervisory'):
        return 'KEY_BANKING_SUPERVISORY'
    if header.startswith('Banking Sector Developments'):
        return 'BANKING_SECTOR_DEVELOPMENTS'
    if header.startswith('Monetary, Markets'):
        return 'MONETARY_MARKETS_EXTERNAL'
    if header.startswith('Macro & Systemic'):
        return 'MACRO_SYSTEMIC_CONTEXT'
    if header.startswith('Other Financial-Sector'):
        return 'OTHER_FINANCIAL_SECTOR'
    raise RuntimeError('Unknown section: ' + header)


def card_from_template(soup, article, priority=False):
    temp = next(x for x in stories(soup) if ('priority-card' in x.get('class', [])) == priority)
    card = BeautifulSoup(str(temp), 'html.parser').find('article')
    spans = card.select('.story-topline > span')
    if len(spans) < 3:
        raise RuntimeError('Unexpected topline format')
    set_text(spans[0], article['source_name'])
    set_text(spans[2], article['publication_date'])
    for x in card.select('.small-muted'):
        x.decompose()
    set_text(card.select_one('h3'), article['headline'])
    set_text(card.select_one('.summary-block p'), article['summary'])
    if priority:
        set_text(card.select_one('.entity-name'), article['topic'])
        set_text(card.select_one('.relevance-block p'), article['why_it_matters'])
    else:
        set_text(card.select_one('.topic-chip'), article['topic'])
        why = card.select_one('.context-relevance')
        why.clear()
        strong = BeautifulSoup('<strong></strong>', 'html.parser').strong
        strong.string = 'Why it matters:'
        why.append(strong)
        why.append(' ' + article['why_it_matters'])
    # Drop template's extra source links before setting the primary source.
    for linkgroup in card.select('.source-link, .source-list'):
        anchor = linkgroup.select_one('a[href]')
        if anchor is None:
            continue
        anchor['href'] = article['source_url']
        anchor.string = article['source_name'] + ' ↗'
        # If a template had multiple source-list divs, discard the extra ones.
    card['data-editorial-source'] = 'reviewed-2026-10-23-source-evidence'
    return card


def root_path():
    return Path(__file__).resolve().parent.parent


def paths(root):
    reports = root/'data'/'processed'/'reports'
    dest = reports/DAY/'window_1d'
    prev = reports/YESTERDAY/'window_1d'/'PUBLISHED_URLS.json'
    return {
        'root': root, 'dir':dest,
        'source':dest/(NAME + ' QA.html'),
        'reviewed':dest/(NAME + ' REVIEWED.html'),
        'named':dest/(NAME + '.html'),
        'alias':dest/'supervisory_digest.html',
        'prior':prev,
        'recipe':root/'review'/'2026-10-23_release_recipe.json',
        'audit':dest/'oct23_reconciliation_audit.json',
        'exception':dest/'manual_editorial_release_exception.json',
        'sent':dest/'PUBLISHED_URLS.json',
    }


def load_inputs(p):
    if p['sent'].exists():
        raise RuntimeError('ALREADY_SENT_EDITION_LOCK: October 23 is already registered as sent')
    for path in (p['source'],p['prior'],p['recipe']):
        if not path.is_file():
            raise FileNotFoundError(f'Required production input missing: {path}')
    recipe = json.loads(p['recipe'].read_text(encoding='utf-8-sig'))
    prior = json.loads(p['prior'].read_text(encoding='utf-8-sig'))
    if recipe['report_date'] != DAY or prior['report_date'] != YESTERDAY:
        raise RuntimeError('Review recipe or sent manifest is for the wrong date')
    html = p['source'].read_text(encoding='utf-8-sig')
    soup = BeautifulSoup(html,'html.parser')
    marker = soup.select_one('.date-value')
    if not marker or marker.get_text(' ',strip=True) != DAY:
        raise RuntimeError('The 26-story report date does not match October 23')
    if len(stories(soup)) != recipe['expected_original_headline_count']:
        raise RuntimeError(f'Expected {recipe["expected_original_headline_count"]} original report stories, found {len(stories(soup))}. Do not substitute another draft.')
    return soup,recipe,prior


def render(p):
    soup,recipe,prior = load_inputs(p)
    prior_events = prior.get('events') or []
    if len(prior_events) != 14:
        raise RuntimeError('Confirmed 22 October manifest should contain exactly 14 stories')
    prior_headlines = {e['headline'] for e in prior_events}
    prior_urls = {u for e in prior_events for u in e.get('source_urls', [])}
    all_original = [title(x) for x in stories(soup)]
    if len(set(all_original)) != 26:
        raise RuntimeError('Original report has repeated headlines; manual review required')
    removed = []
    for headline, prior_headline in recipe['drop_as_already_reported'].items():
        if prior_headline not in prior_headlines:
            raise RuntimeError('Cannot prove repeat event against sent manifest: ' + headline)
        locate(soup,headline).decompose()
        removed.append(headline)
    merged = []
    for pair in recipe['combine_same_market_session']:
        keep=locate(soup,pair['primary'])
        other=locate(soup,pair['secondary'])
        urls = source_urls(other)
        if len(urls)!=1:
            raise RuntimeError('Unexpected extra source for duplicate market article')
        pub = other.select_one('.story-topline span').get_text(' ',strip=True)
        new = soup.new_tag('div',attrs={'class':'source-list'})
        lead = soup.new_tag('strong'); lead.string='Additional report: '; new.append(lead)
        link = soup.new_tag('a',href=urls[0],target='_blank',rel='noopener noreferrer')
        link.string=pub+' ↗'; new.append(link)
        (keep.select_one('.context-bottom') or keep).append(new)
        other.decompose()
        merged.append({'primary':pair['primary'],'secondary':pair['secondary']})
    for headline in recipe['clean_photographer_credits_in']:
        ptext=locate(soup,headline).select_one('.summary-block p')
        txt=ptext.get_text(' ',strip=True)
        clean=re.sub(r'\s*-\s*Pic\s+by\s+Ruwan\s+Walpola\s*-\s*',' — ',txt,flags=re.I)
        if clean == txt:
            raise RuntimeError('Expected photo-credit fragment missing from ' + headline)
        set_text(ptext,clean)
    sects={publisher_section(sec.select_one('h2').get_text(' ',strip=True)):sec
           for sec in soup.select('section.report-section')}
    for article in recipe['approved_recovered_articles']:
        headline=article['headline']
        if headline in {title(a) for a in stories(soup)}:
            raise RuntimeError('Recovered article duplicated an existing title: '+headline)
        if article['source_url'] in prior_urls:
            raise RuntimeError('Recovered URL was already sent yesterday: '+headline)
        key=publisher_section(article['section'])
        card=card_from_template(soup,article,priority=(key=='KEY_BANKING_SUPERVISORY'))
        sects[key].append(card)
    for i, priority_card in enumerate(soup.select('article.priority-card'),1):
        set_text(priority_card.select_one('.priority-number'), f'{i:02d}')
    if len(stories(soup))!=24:
        raise RuntimeError(f'Expected 24 distinct stories; got {len(stories(soup))}')
    allurls = [u for a in stories(soup) for u in source_urls(a)]
    if len({title(a) for a in stories(soup)}) != len(stories(soup)):
        raise RuntimeError('Repeated headline in final report')
    if any(u in prior_urls for u in allurls):
        raise RuntimeError('Exact URL repeated from 22 October actually-sent edition')
    if any('Pic by Ruwan Walpola' in a.get_text(' ',strip=True) for a in stories(soup)):
        raise RuntimeError('Photo credits remain embedded in summaries')
    # Explicitly do not embed a misleading distribution-approval banner.
    html = str(soup)
    p['dir'].mkdir(parents=True, exist_ok=True)
    encoded=html.encode('utf-8')
    if p['reviewed'].exists() and p['reviewed'].read_bytes()!=encoded:
        raise RuntimeError('Existing reviewed file differs; refusing to overwrite it')
    if not p['reviewed'].exists():
        p['reviewed'].write_bytes(encoded)
    audit={
        'report_date':DAY,'status':'EDITORIAL_RECONCILED_RELEASE_EXCEPTION_REVIEW_REQUIRED',
        'qa_result':'NOT_PASS', 'publisher_completeness':'NOT_VERIFIED',
        'input_source':str(p['source']),'input_sha256':digest(p['source']),
        'sent_prior':str(p['prior']), 'sent_prior_sha256':digest(p['prior']),
        'output':str(p['reviewed']),'output_sha256':digest(p['reviewed']),
        'input_stories':26,'removed_already_reported_events':removed,
        'merged_same_session_market_pairs':merged,'added_verified_candidates':[
            a['headline'] for a in recipe['approved_recovered_articles']],
        'output_stories':24,'remaining_evidence_boundaries':recipe['known_release_exceptions'],
        'release_policy':'Do not label PASS. Explicit accountable manual release decision required.'
    }
    p['audit'].write_text(json.dumps(audit,indent=2,ensure_ascii=False),encoding='utf-8')
    print('[REPRODUCIBLE EDITORIAL BRIEF]',p['reviewed'])
    print('[STORIES] 26 original - 5 repeated - 2 consolidated + 5 additions = 24')
    print('[SHA256]',audit['output_sha256'])
    print('[COVERAGE] Unverified; four collector partial errors remain')
    print('[STATUS] REVIEWED EDITORIAL CANDIDATE, NOT AUTOMATED QA PASS')
    return audit


def release(p, reviewer):
    if not reviewer.strip():
        raise ValueError('A human reviewer ID is required for a manual coverage exception')
    audit=render(p)
    data=p['reviewed'].read_bytes()
    for dest in (p['named'],p['alias']):
        if dest.exists() and dest.read_bytes()!=data:
            raise FileExistsError(f'Will not overwrite an existing final attachment: {dest}')
    for dest in (p['named'],p['alias']):
        if not dest.exists():
            temp=dest.with_name(dest.name+'.tmp')
            temp.write_bytes(data)
            temp.replace(dest)
    record={
        'date':DAY,'status':'MANUAL_EDITORIAL_RELEASE_WITH_DOCUMENTED_COVERAGE_EXCEPTIONS',
        'reviewer':reviewer.strip(), 'accepted_at_utc':datetime.now(timezone.utc).isoformat(),
        'attachment':str(p['named']),'sha256':digest(p['named']),
        'exceptions':audit['remaining_evidence_boundaries'],
        'normal_automated_qa_status':'HOLD_FOR_REVIEW',
        'emailed':False,
        'note':'User-authorised editorial release with incomplete collection verification; never represent as QA PASS or complete coverage.'
    }
    if p['exception'].exists():
        old=json.loads(p['exception'].read_text(encoding='utf8'))
        if old.get('sha256') != record['sha256']:
            raise RuntimeError('Existing exception record is for a different HTML')
    else:
        p['exception'].write_text(json.dumps(record,indent=2,ensure_ascii=False),encoding='utf-8')
    print('[MANUAL EXCEPTION RECORDED] Seven publisher checks are NOT verified')
    print('[EMAIL ATTACHMENT READY]',p['named'])
    print('[IMPORTANT] No email sent; no PUBLISHED_URLS.json written. After actually emailing use the sent command.')


def sent(p):
    if not p['exception'].is_file() or not p['named'].is_file():
        raise RuntimeError('No previously prepared, acknowledged reviewed attachment found')
    record=json.loads(p['exception'].read_text(encoding='utf8'))
    if record.get('status') != 'MANUAL_EDITORIAL_RELEASE_WITH_DOCUMENTED_COVERAGE_EXCEPTIONS':
        raise RuntimeError('Cannot mark sent without recorded accountable manual exception')
    if digest(p['named']) != record.get('sha256'):
        raise RuntimeError('Attachment has changed since reviewer acknowledged it')
    # Reuse the existing exact sent-manifest logic and its immutable lock.
    import news_release_gate
    from datetime import date
    news_release_gate.mark_sent(date.fromisoformat(DAY),p['named'],p['root']/'data'/'processed'/'reports',True)
    print('[SENT STATUS] Confirmed by operator; this script did not send the email')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','release','sent'))
    parser.add_argument('--reviewer',default='')
    parser.add_argument('--acknowledge-coverage-exceptions',action='store_true')
    args=parser.parse_args()
    p=paths(root_path())
    if args.command=='prepare':render(p)
    elif args.command=='release':
        if not args.acknowledge_coverage_exceptions:
            parser.error('Manual release requires --acknowledge-coverage-exceptions. The seven publishers were NOT verified.')
        release(p,args.reviewer)
    else:
        sent(p)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        raise SystemExit(f'[STOP] {type(exc).__name__}: {exc}')
