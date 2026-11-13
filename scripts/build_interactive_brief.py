"""Build a browser-friendly view without changing the governed source edition.

Usage: python build_interactive_brief.py --report <final.html> --output <dashboard.html>
"""
import argparse
import hashlib
import html
import json
import re
from pathlib import Path
from bs4 import BeautifulSoup

BANKS = {
 'Bank of Ceylon': ['Bank of Ceylon','BOC'],
 "People’s Bank": ["People's Bank",'People’s Bank','Peoples Bank'],
 'Commercial Bank': ['Commercial Bank of Ceylon','Commercial Bank','ComBank'],
 'Hatton National Bank': ['Hatton National Bank','HNB'],
 'Sampath Bank': ['Sampath Bank'], 'Seylan Bank': ['Seylan Bank'],
 'National Development Bank': ['National Development Bank','NDB'],
 'DFCC Bank': ['DFCC'], 'Nations Trust Bank': ['Nations Trust Bank','NTB'],
 'National Savings Bank': ['National Savings Bank','NSB'],
 'Pan Asia Bank': ['Pan Asia Bank','Pan Asia Banking','PABC'],
 'Union Bank': ['Union Bank'], 'Amana Bank': ['Amana Bank','Amãna Bank'],
 'Cargills Bank': ['Cargills Bank'], 'Sanasa Development Bank': ['Sanasa Development Bank','SDB Bank'],
 'Regional Development Bank': ['Regional Development Bank','Pradeshiya Sanwardhana Bank','RDB'],
 'State Mortgage & Investment Bank': ['State Mortgage and Investment Bank','State Mortgage & Investment Bank','SMIB'],
 'HDFC Bank': ['HDFC'], 'Sri Lanka Savings Bank': ['Sri Lanka Savings Bank'],
 'HSBC': ['HSBC','Hongkong and Shanghai Banking'], 'Standard Chartered': ['Standard Chartered'],
 'Citibank': ['Citibank','Citi Bank'], 'Deutsche Bank': ['Deutsche Bank'],
 'Bank of China': ['Bank of China'], 'Indian Bank': ['Indian Bank'],
 'Indian Overseas Bank': ['Indian Overseas Bank'], 'State Bank of India': ['State Bank of India'],
 'MCB Bank': ['MCB Bank'], 'Habib Bank': ['Habib Bank'], 'Public Bank': ['Public Bank'],
 'CBSL': ['CBSL','Central Bank of Sri Lanka','Central Bank'],
}

def bank_mentions(text):
    # Mentions only: this is not an assessment of exposure or responsibility.
    # Names are matched as written: 'a commercial bank' or 'union bank workers'
    # are ordinary phrases, not Commercial Bank or Union Bank.
    return [name for name, aliases in BANKS.items() if any(
        re.search(r'(?<!\w)'+re.escape(alias)+r'(?!\w)', text) for alias in aliases)]

def publishers(card, fallback):
    # Every paper behind a combined story, so filtering by any one finds it.
    names = [re.sub(r'\s*[↗›»]+\s*$', '', a.get_text(' ', strip=True)) for a in card.select('.source-list a')]
    names = [n for n in dict.fromkeys(names) if n]
    return names or ([fallback] if fallback else [])

def article_news_text(card):
    """Match reported news only, never editorial headings or risk commentary."""
    nodes = card.select('h3, .summary-block p, .story-summary, .context-summary')
    # Some editions put a summary class on a parent containing paragraphs.
    selected = set(map(id, nodes))
    return ' '.join(node.get_text(' ', strip=True) for node in nodes
                    if not any(id(parent) in selected for parent in node.parents))

def static_stories(entries):
    """The stories written into the page, as the script would draw them unfiltered.

    Phones often open an attachment in a preview that runs no script (mail and
    chat apps); the brief must still show every story there. The script redraws
    this list only when a reader filters it.
    """
    out, group = [], None
    for e in entries:
        if e['section'] != group:
            group = e['section']
            count = sum(1 for x in entries if x['section'] == group)
            out.append(f'<h2 class="group-title">{html.escape(group)}<span>{count}</span></h2>')
        card = BeautifulSoup(e['markup'], 'html.parser')
        for link in card.select('.source-list a'):
            publisher = link.get_text(' ', strip=True)
            link.string = f'Read source · {publisher} ↗'
            link['target'], link['rel'] = '_blank', 'noopener noreferrer'
            link['title'] = f'Open original reporting from {publisher}'
        label = card.select_one('.source-list strong')
        if label:
            label.string = 'Original reporting'
        tags = ''.join(f'<span class="bank-tag">{html.escape(name)}</span>' for name in e['banks'])
        out.append(f'<div class="story-wrap">{card}' + (f'<div class="bank-tags">{tags}</div>' if tags else '') + '</div>')
    return ''.join(out)


def build(report, output):
    report,output=Path(report),Path(output)
    if report.resolve()==output.resolve():
        raise ValueError('Output must be separate from the source edition')
    raw=report.read_bytes()
    soup=BeautifulSoup(raw.decode('utf-8-sig'),'html.parser')
    cards=soup.select('article.priority-card, article.context-card')
    if not cards:
        raise ValueError('No final-edition article cards found')
    entries=[]
    for i,card in enumerate(cards):
        section=card.find_parent('section')
        heading=section.find('h2') if section else None
        topic=card.select_one('.topic-chip, .entity-name')
        topline=card.select_one('.story-topline')
        text=article_news_text(card)
        date=card.get('data-source-date','')
        if not date:
            match=re.search(r'20\d\d-\d\d-\d\d',topline.get_text() if topline else '')
            date=match.group() if match else ''
        provider=(topline.get_text(' ',strip=True).split('•')[0].strip() if topline else '')
        provider=re.sub(r'\s*20\d\d-\d\d-\d\d.*','',provider).strip()
        entries.append(dict(id=str(i),section=heading.get_text(' ',strip=True) if heading else 'News',
            topic=topic.get_text(' ',strip=True) if topic else '',date=date,provider=provider,
            providers=publishers(card,provider),
            banks=bank_mentions(text),search=text.casefold(),markup=str(card)))
    head=soup.select_one('.date-value')
    display=head.get_text(' ',strip=True) if head else report.stem
    coverage=soup.select_one('.review-window')
    data=json.dumps(entries,ensure_ascii=False).replace('<','\\u003c')
    template=Path(__file__).with_name('interactive_brief.html').read_text(encoding='utf-8')
    content=template.replace('__EDITION__',html.escape(display)).replace('__COVERAGE__',html.escape(coverage.get_text(' ',strip=True) if coverage else ''))
    content=content.replace('__STATIC_STORIES__',static_stories(entries)).replace('__STATIC_NOTE__',f'{len(entries)} of {len(entries)} developments shown')
    content=content.replace('__DATA__',data)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(content,encoding='utf-8')
    receipt={'source':str(report.resolve()),'source_sha256':hashlib.sha256(raw).hexdigest(),
             'output':str(output.resolve()),'stories':len(entries),
             'bank_mentions':{name:sum(name in e['banks'] for e in entries) for name in BANKS if any(name in e['banks'] for e in entries)},
             'sections':sorted(set(e['section'] for e in entries)),
             'publishers':{p:sum(p in e['providers'] for e in entries) for p in sorted({p for e in entries for p in e['providers']})},
             'source_unchanged':hashlib.sha256(report.read_bytes()).hexdigest()==hashlib.sha256(raw).hexdigest()}
    output.with_suffix('.validation.json').write_text(json.dumps(receipt,indent=2,ensure_ascii=False),encoding='utf-8')
    return receipt

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(build(args.report,args.output),indent=2,ensure_ascii=False))
