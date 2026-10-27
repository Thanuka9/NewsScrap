#!/usr/bin/env python3
"""Safely apply a complete, source-URL-keyed editorial decisions CSV to an UNSENT report.

This script does NOT claim release approval, change published manifests, or send mail.
It creates a new QA candidate without rerunning ingestion/ML and leaves the old QA intact.
"""
import argparse
import csv
import hashlib
import html
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ARTICLE_RE = re.compile(r'(?P<block><article\b[^>]*>.*?</article>)', re.S | re.I)
H3_RE = re.compile(r'<h3\b[^>]*>(.*?)</h3\s*>', re.S | re.I)
HREF_RE = re.compile(r'<a\b[^>]*\bhref\s*=\s*(["\'])(.*?)\1', re.S | re.I)
TAGS_RE = re.compile(r'<[^>]+>')
DATE_RE = re.compile(r'<div\s+class=["\']date-value["\']\s*>\s*(\d{4}-\d\d-\d\d)\s*</div>', re.I)
KEEP = {'RETAIN', 'RETAIN_WITH_FOLLOWUP_REVIEW'}
DROP = {'HOLD_ALREADY_REPORTED_EVENT'}


def norm_url(u):
    u = html.unescape((u or '').strip())
    try:
        s = urlsplit(u)
    except ValueError:
        return ''
    return urlunsplit((s.scheme.lower(), s.netloc.lower(), s.path.rstrip('/'), '', '')).casefold() if s.scheme in {'http','https'} and s.netloc else ''


def story_from_block(block):
    heading = H3_RE.search(block)
    urls = [norm_url(m.group(2)) for m in HREF_RE.finditer(block)]
    urls = [u for u in urls if u]
    if not heading or not urls:
        raise RuntimeError('Malformed article block: requires headline and source link')
    title = html.unescape(TAGS_RE.sub('', heading.group(1)))
    title = ' '.join(title.split())
    return title, urls


def read_decisions(path):
    with path.open(newline='',encoding='utf-8-sig') as f:
        records = list(csv.DictReader(f))
    needed = {'headline','source_url','decision','reason','prior_sent_headline'}
    if not records or not needed <= set(records[0]):
        raise RuntimeError('Decision CSV must include: ' + ', '.join(sorted(needed)))
    result = {}
    for row in records:
        url = norm_url(row.get('source_url'))
        decision = (row.get('decision') or '').strip()
        if not url or url in result or decision not in KEEP | DROP:
            raise RuntimeError(f'Blank or repeated URL or invalid decision: {row}')
        result[url] = row
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--date',required=True,help='Report date YYYY-MM-DD')
    p.add_argument('--project-root',type=Path,default=Path.cwd())
    p.add_argument('--source',type=Path,help='Existing 26-story QA.html; never modified')
    p.add_argument('--decisions',type=Path,required=True)
    p.add_argument('--output',type=Path,help='New EDITORIAL QA.html; distinct from source')
    p.add_argument('--additions-review',type=Path,
                   help='Optional, human-reviewed additions CSV with verified article summaries')
    args=p.parse_args()
    d=date.fromisoformat(args.date)
    root=args.project_root.resolve()
    folder=root/'data'/'processed'/'reports'/d.isoformat()/'window_1d'
    base=f'Daily Financial Sector News Brief {d.isoformat()}'
    source=(args.source or folder/(base+' QA.html')).resolve()
    output=(args.output or folder/(base+' EDITORIAL QA.html')).resolve()
    if source==output: raise RuntimeError('Source and output must differ; original QA must be preserved')
    if output.parent!=folder.resolve(): raise RuntimeError('Output must be in report date window_1d folder')
    if not source.is_file(): raise FileNotFoundError(source)
    if not args.decisions.is_file():raise FileNotFoundError(args.decisions)
    if list(folder.glob('PUBLISHED_URLS.json')) or list(folder.glob('PUBLISHED.ok')):
        raise RuntimeError('ALREADY_SENT_EDITION_LOCK: do not modify a distributed report')
    if (folder/(base+'.html')).exists() or (folder/'supervisory_digest.html').exists():
        raise RuntimeError('Normal attachment exists. Do not overwrite an already promoted report')
    txt=source.read_text(encoding='utf-8-sig')
    hdr=DATE_RE.search(txt)
    if not hdr or hdr.group(1)!=d.isoformat(): raise RuntimeError('HTML report header date missing or mismatched')
    decisions=read_decisions(args.decisions)
    previous=(root/'data'/'processed'/'reports'/(d-timedelta(days=1)).isoformat()/'window_1d'/'PUBLISHED_URLS.json')
    if not previous.is_file():
        raise RuntimeError('Missing yesterday actual-sent publication manifest: '+str(previous))
    prev=json.loads(previous.read_text(encoding='utf-8-sig'))
    prior_titles={(' '.join(str(r.get('headline','')).casefold().split())) for r in prev.get('events',[])}
    if not prior_titles:raise RuntimeError('Prior manifest missing prior sent event titles')
    seen=set()
    retained=[]
    removed=[]
    def replace(match):
        block=match.group('block')
        title,urls=story_from_block(block)
        matching=[u for u in urls if u in decisions]
        if len(matching)!=1:
            raise RuntimeError(f'Expected exactly one reviewed source URL for report article: {title!r}, found {len(matching)}')
        row=decisions[matching[0]]
        key=matching[0]
        if key in seen:raise RuntimeError('Repeated article URL in HTML: '+key)
        seen.add(key)
        if title.casefold() != ' '.join(str(row.get('headline','')).split()).casefold():
            raise RuntimeError(f'Headline mismatch for {key}: {title!r} / {row.get("headline")!r}')
        if row['decision'] in DROP:
            assert row['decision']=='HOLD_ALREADY_REPORTED_EVENT'
            past=' '.join(str(row['prior_sent_headline']).casefold().split())
            if not past or past not in prior_titles or not str(row['reason']).strip():
                raise RuntimeError('Cannot exclude as already reported without linked prior-sent headline evidence: '+title)
            removed.append(title)
            return ''
        # Remove unmistakable embedded photographer credits without changing
        # article facts, figures, source links, or editorial selection.
        block = re.sub(r'\s*-\s*Pic\s+by\s+[A-Za-z .]+?\s*-\s*', ' — ', block, count=1, flags=re.I)
        retained.append(title)
        return block
    result=ARTICLE_RE.sub(replace,txt)
    if not retained:raise RuntimeError('Cannot generate empty report')
    if seen!=set(decisions):
        missing=set(decisions)-seen
        raise RuntimeError('Editorial decision rows not in actual QA report: '+str(sorted(missing)))
    if len(retained)+len(removed)!=len(decisions):raise RuntimeError('Story/decision count mismatch')
    if len(removed)==0:raise RuntimeError('No reviewed duplicate removals; expected a revised report')
    added=[]
    if args.additions_review:
        if not args.additions_review.is_file():raise FileNotFoundError(args.additions_review)
        with args.additions_review.open(newline='',encoding='utf-8-sig') as f:
            extras=list(csv.DictReader(f))
        if extras:
            required={'headline','source_url','publication_date','source_name','section','summary','why_it_matters','review_decision','reviewer','review_notes'}
            if not required <= set(extras[0]):raise RuntimeError('Missing required columns in additions review CSV')
            section_names={
                'BANKING_SECTOR_DEVELOPMENTS':'Banking Sector Developments &amp; Risk Context',
                'OTHER_FINANCIAL_SECTOR':'Other Financial-Sector Developments',
                'MONETARY_MARKETS_EXTERNAL':'Monetary, Markets &amp; External Sector',
                'MACRO_SYSTEMIC_CONTEXT':'Macro &amp; Systemic Context',
                'KEY_BANKING_SUPERVISORY':'CBSL &amp; Bank Supervisory Developments'
            }
            prior_urls={norm_url(url) for url in prev.get('source_urls',[])}
            seen_extra=set()
            for row in extras:
                if row.get('review_decision','').strip().upper()!='INCLUDE':continue
                src=norm_url(row.get('source_url',''))
                pub=row.get('publication_date','').strip()
                try:pubday=date.fromisoformat(pub)
                except ValueError:raise RuntimeError('Invalid addition publication date: '+pub)
                if not d-timedelta(days=1)<=pubday<=d:
                    raise RuntimeError('Addition outside today/yesterday: '+pub)
                if not src or src in seen_extra or src in seen or src in prior_urls:
                    raise RuntimeError('Addition duplicates a URL in this or previous edition: '+src)
                seen_extra.add(src)
                section=row.get('section','').strip()
                if section not in section_names:raise RuntimeError('Invalid addition section: '+section)
                if not row.get('reviewer','').strip() or len(row.get('review_notes','').split())<4:
                    raise RuntimeError('Unsigned addition: '+src)
                summary=' '.join((row.get('summary') or '').split())
                if len(summary.split())<25 or not row.get('why_it_matters','').strip():
                    raise RuntimeError('Full verified summary (25+ words) and relevance required: '+src)
                if not row.get('headline','').strip() or not row.get('source_name','').strip():
                    raise RuntimeError('Incomplete headline or publisher for addition: '+src)
                safe=html.escape
                # Use the same markup as existing context articles, not an
                # unsubstantiated generated summary or external web fetch.
                card=('''<article class="context-card">
<div class="context-card-head"><div class="story-topline"><span>'''+safe(row['source_name'])+'</span><span class="dot">•</span><span>'+safe(pub)+'</span></div><h3>'+safe(row['headline'])+'''</h3></div>
<div class="summary-block"><div class="block-label">What happened</div><p class="context-summary">'''+safe(summary)+'''</p></div>
<div class="context-bottom"><div class="context-relevance"><strong>Why it matters:</strong> '''+safe(row['why_it_matters'])+'''</div>
<div class="source-list"><strong>Source:</strong> <a href="'''+safe(row['source_url'],quote=True)+'" target="_blank" rel="noopener noreferrer">'+safe(row['source_name'])+' ↗</a></div></div></article>')
                # Identify existing section by its exact structural header.
                section_pat=re.compile(r'<section\s+class="report-section"[^>]*>.*?</section>', re.S|re.I)
                found=False
                def insert_sec(m):
                    nonlocal found
                    block=m.group(0)
                    if section_names[section] not in block:return block
                    if found:raise RuntimeError('Ambiguous duplicate section '+section)
                    found=True
                    return block.rsplit('</section>',1)[0]+'\n'+card+'\n</section>'
                result=section_pat.sub(insert_sec,result)
                if not found:raise RuntimeError('Missing section '+section)
                added.append(row['headline'])

    # Keep the existing report CSS, summaries, ordering and all non-duplicate stories unmodified.
    # The final gate will audit this file. Do not add a banner to the report.
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():
        if output.read_bytes()!=result.encode('utf8'):
            raise FileExistsError('Existing editorial candidate differs. Move it aside before recreating')
    else:
        output.write_text(result,encoding='utf8')
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    meta={'date':d.isoformat(),'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
          'output':str(output),'output_sha256':digest,'retained_count':len(retained),'suppressed_count':len(removed),'reviewed_addition_count':len(added),'reviewed_addition_headlines':added,
          'suppressed_headlines':removed,'status':'EDITORIAL_CANDIDATE_NOT_RELEASE_APPROVED',
          'note':'The final news_release_gate audit and human source-coverage review remain mandatory.'}
    output.with_suffix('.provenance.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf8')
    print('[EDITORIAL CANDIDATE GENERATED]', output)
    print('[RETAINED]',len(retained),'[REVIEWED ADDITIONS]',len(added),'[PREVIOUSLY_REPORTED EVENTS REMOVED]',len(removed))
    print('[SHA256]',digest)
    print('[NOT APPROVED FOR SENDING] Run the existing release-gate audit; only promote after PASS.')
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:
        print('[STOP]',type(exc).__name__+':',exc,file=sys.stderr)
        sys.exit(2)
