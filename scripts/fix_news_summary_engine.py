"""One-time, backed-up fix for the source-grounded news-summary extractor.

Run in News Scrap project root: python .\\scripts\\fix_news_summary_engine.py
Only modifies scripts/news_editorial_final.py. Never changes saved news or ML.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import importlib.util
import re
import shutil
import sys

ROOT = Path.cwd()
TARGET = ROOT / 'scripts' / 'news_editorial_final.py'
if not TARGET.is_file():
    raise SystemExit('[STOP] scripts/news_editorial_final.py not found. Run from News Scrap project root.')

MARKER = 'NEWS_SOURCE_SUMMARY_V2_20261022'
REPLACEMENT = r'''def source_summary(body, headline="", min_words=75, max_words=155):
    """Summarize *only* source sentences; do not fabricate details to hit a quota.

    Keep Rs./Dr./financial decimals intact, retain short factual sentences, and
    avoid dropping an otherwise useful paragraph because a single sentence is long.
    Publication QA remains responsible for rejecting insufficient evidence.
    """
    # NEWS_SOURCE_SUMMARY_V2_20261022
    text = _clean(body)
    if not text:
        return ""
    text = re.sub(r"^\\d{1,2}\\s+[A-Za-z]{3,9}\\s+20\\d{2}\\s*\\|\\s*", "", text, flags=re.I)
    text = re.sub(r"^(?:PARTNER CONTENT|ADVERTISEMENT)\\s*\\|?\\s*", "", text, flags=re.I)
    text = re.sub(r"^By\\s+[A-Za-z .’'\\-]{2,70}(?=\\s+(?:The |Sri |[A-Z]{2,}))", "", text)

    # Strip a recognised caption only when a verifiable prose lead follows it.
    if _CAPTION.match(text):
        lead = re.search(
            r"\\b(?:[A-Z][A-Za-z&.’'\\-]*\\s+){0,4}"
            r"(?:Bank|PLC|Finance|Cabinet|Government|CSE|NDB)\\b.{0,75}?"
            r"\\b(?:said|reported|announced|approved|issued|recorded|launched|entered|expects|fell|rose)\\b",
            text[30:], re.I,
        )
        if lead:
            text = text[30 + lead.start():]

    # Preserve the periods inside abbreviations/amounts rather than treating
    # Rs. 2.9 billion or Dr. Silva as three separate sentences.
    stop = '\\uE000'
    protected = re.sub(r"(?<=\\d)\\.(?=\\d)", stop, text)
    protected = re.sub(
        r"\\b(?:Rs|Dr|Prof|Mr|Mrs|Ms|No|St|Ltd|Inc|Co|vs|e\\.g|i\\.e)\\.",
        lambda m: m.group(0).replace('.', stop), protected, flags=re.I,
    )
    protected = re.sub(r"\\bU\\.S\\.", 'U' + stop + 'S' + stop, protected, flags=re.I)
    parts = re.split(r"(?<=[.!?])\\s+(?=[‘“(]?[A-Z0-9])", protected)

    sentences = []
    for item in parts:
        item = item.replace(stop, '.').strip().strip(chr(34) + '“”')
        if not item:
            continue
        if re.match(r"^(?:By\\s|From left[:\\s]|Image[:\\s]|Photo[:\\s]|Source[:\\s])", item, re.I):
            continue
        if _CAPTION.match(item) or _PROMO.match(item):
            continue
        if len(item.split()) < 4 and len(parts) > 1:
            continue
        sentences.append(item)

    if not sentences:
        return ''

    chosen = []
    count = 0
    for sentence in sentences:
        n = len(sentence.split())
        if n > 180:
            # A very long run-on segment cannot safely be clipped mid-claim.
            # Prefer genuine punctuation boundaries, never add invented prose.
            pieces = re.split(r"(?<=[;:])\\s+(?=[A-Z0-9])", sentence)
            if len(pieces) == 1:
                if chosen:
                    break
                return ''
            for piece in pieces:
                pn = len(piece.split())
                if count + pn > 180:
                    break
                chosen.append(piece)
                count += pn
            break
        if chosen and count + n > max_words:
            # A final complete sentence may exceed the *target* length without
            # exceeding the existing 180-word QA limit.
            if count < min_words and count + n <= 180:
                chosen.append(sentence)
                count += n
            break
        if not chosen and n > max_words and n <= 180:
            chosen.append(sentence)
            count += n
            break
        chosen.append(sentence)
        count += n
        if count >= max(min_words, 95):
            break

    result = ' '.join(chosen).strip()
    # Only a genuine short source may yield a short summary; an adequately long
    # source with an unusable extract is deliberately left for the existing QA.
    return result
'''
# Literal replacement above is intentionally a raw string; unescape only the
# Python string escapes needed in the embedded function source.
REPLACEMENT = REPLACEMENT.replace('\\\\', '\\')

source = TARGET.read_text(encoding='utf-8-sig')
if MARKER in source:
    print('[PASS] Source-summary v2 is already installed. No changes made.')
    raise SystemExit(0)
start = re.search(r'(?m)^def source_summary\(.*', source)
end = re.search(r'(?m)^def summary_issues\(.*', source)
if not start or not end or end.start() <= start.start():
    raise SystemExit('[STOP] Unexpected editorial module structure. Nothing modified.')
patched = source[:start.start()] + REPLACEMENT + '\n\n' + source[end.start():]
compile(patched, str(TARGET), 'exec')

backup_dir = ROOT / 'data' / 'integration_backups' / ('summary_engine_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
backup_dir.mkdir(parents=True, exist_ok=True)
backup = backup_dir / TARGET.name
shutil.copy2(TARGET, backup)
try:
    TARGET.write_text(patched, encoding='utf-8')
    spec = importlib.util.spec_from_file_location('editorial_summary_validation', TARGET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert MARKER in TARGET.read_text(encoding='utf-8')
    # Synthetic regression cases, not assertions about published news facts.
    ndb = ('NDB reported a profit after tax of Rs. 2.9 billion in the third quarter of 2026. '
           'The bank stated that operating income increased while provisions for expected credit losses remained material. '
           'Net interest income, costs and changes in loan quality contributed to the quarterly performance. '
           'The result is subject to the disclosures made in the bank financial statements. '
           'Investors will examine the comparative prior-quarter figures alongside the capital ratios and impairment expenses. '
           'The bank also described the lending environment and changes to its customer deposit base during the period.')
    cse = ('The Colombo Stock Exchange experienced another subdued trading session on Thursday, with market turnover of Rs. 1.5 billion. '
           'The All Share Price Index moved lower as several large companies recorded price declines. '
           'Analysts reported that trading volumes were concentrated in a limited number of counters. '
           'The banking sector recorded mixed movements and foreign investors participated in both purchases and sales. '
           'The session followed a period of volatility in the equity market. '
           'The daily closing figures illustrate market movements and should not be interpreted as a longer-term market forecast.')
    for name, sample in [('NDB', ndb), ('CSE', cse)]:
        result = mod.source_summary(sample)
        assert 75 <= len(result.split()) <= 180, (name, len(result.split()), result)
        assert not re.search(r'&(?:amp|nbsp|#\\d+);', result)
        assert ('Rs. 2.9' if name == 'NDB' else 'Rs. 1.5') in result
        print(f'[PASS] {name} financial-abbreviation regression: {len(result.split())} words')
    # Verify that a short genuine article is not padded with invented claims.
    short = 'The financial institution confirmed the transaction. Further details were not disclosed.'
    assert len(mod.source_summary(short).split()) <= len(short.split())
    print('[PASS] Short-source no-fabrication regression')
except Exception:
    shutil.copy2(backup, TARGET)
    print('[ROLLBACK] Original editorial module restored after failed validation.')
    raise

# Optional read-only check against the actual staged October 22 article text.
# This does not affect production data and never treats sample text as ground truth.
for date_str in ('2026-10-22', '2026-10-21'):
    for staged in (ROOT / 'data' / 'staged').glob(f'*/{date_str}/*/v*.json'):
        try:
            import json
            record = json.loads(staged.read_text(encoding='utf-8-sig'))
            title = str(record.get('headline') or record.get('title') or '')
            if not any(part in title.lower() for part in ('ndb posts 3q', 'cse witnesses another downbeat')):
                continue
            source_text = str(record.get('article_text') or record.get('body') or '')
            result = mod.source_summary(source_text, title)
            print(f'[SOURCE CHECK] {title}: source={len(source_text.split())} words, summary={len(result.split())} words')
            if len(source_text.split()) >= 140 and len(result.split()) < 75:
                print('[REVIEW] This staged article still needs source-extraction review; do not weaken QA.')
        except (OSError, UnicodeError, ValueError):
            continue

print('[PASS] Shared source-summary engine installed')
print('[UNCHANGED] QA thresholds and all saved reports')
print('[BACKUP]', backup)
print('NEXT: python .\\scripts\\build_financial_brief_v2.py --end-date 2026-10-22 --days 1')
