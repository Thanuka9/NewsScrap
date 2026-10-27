"""One-time repair for News Scrap: real publication evidence, unpublished late
stories, and narrowly defined high-priority financial relevance omissions.

Run from the News Scrap project root:
    python .\\install_news_publication_and_relevance.py

Never uses a QA/test report as proof that an edition was actually sent.
Backs up existing scripts, validates expected integration points, and rolls
back if the installation fails. Does not fetch news or regenerate reports.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import ast
import json
import re
import shutil
import tempfile

ROOT = Path.cwd()
SCRIPTS = ROOT / 'scripts'
BUILDER = SCRIPTS / 'build_financial_brief_v2.py'
EDITORIAL = SCRIPTS / 'news_editorial_final.py'
MARKER = SCRIPTS / 'mark_news_published.py'

for path in (BUILDER, EDITORIAL):
    if not path.is_file():
        raise SystemExit(f'[STOP] Required existing file missing: {path}')

builder = BUILDER.read_text(encoding='utf-8-sig')
editorial = EDITORIAL.read_text(encoding='utf-8-sig')

if 'MATERIAL FINANCIAL RESCUE' in builder and MARKER.is_file():
    print('[PASS] Publication and relevance integration already installed. No changes made.')
    raise SystemExit(0)
if 'MATERIAL FINANCIAL RESCUE' in builder or MARKER.exists() or 'def recover_material_exclusions(' in editorial:
    raise SystemExit('[STOP] Partial or different installation detected. No files modified.')


def replace_function(source: str, start: str, following: str, replacement: str) -> str:
    a = source.find(start)
    b = source.find(following, a + len(start)) if a >= 0 else -1
    if a < 0 or b < 0 or source.count(start) != 1:
        raise RuntimeError(f'Unknown production layout for {start.strip()}. No files modified.')
    return source[:a] + replacement.rstrip() + '\n' + source[b:]


builder = replace_function(
    builder,
    'def _news_prior_reviewed_urls(before_date: date) -> set[str]:\n',
    '\ndef _news_staged_late_rows(',
    '''def _news_prior_reviewed_urls(before_date: date) -> set[str]:
    """Use ONLY reports explicitly registered as ACTUALLY distributed."""
    seen: set[str] = set()
    if not REPORT_ROOT.is_dir():
        return seen
    for day_root in REPORT_ROOT.iterdir():
        if not day_root.is_dir():
            continue
        day = _news_text_date(day_root.name)
        if day is None or day >= before_date:
            continue
        manifest = day_root / "window_1d" / "PUBLISHED_URLS.json"
        if not manifest.is_file():
            continue
        obj = load_json(manifest)
        urls = obj.get("source_urls")
        if not isinstance(urls, list):
            raise RuntimeError(f"Invalid published URL inventory: {manifest}")
        seen.update(str(u).split("?", 1)[0].rstrip("/") for u in urls if u)
    return seen
''',
)

# Reconcile existing saved articles from both the report date and the preceding
# day. Different local revisions of News Scrap format their collection-date
# check differently. Inspect the *actual Python AST* rather than requiring one
# exact text/whitespace representation. Unknown logic remains untouched.
def relax_collection_date_guard(source: str) -> tuple[str, str]:
    tree = ast.parse(source)
    candidates = [n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == "_news_staged_late_rows"]
    if len(candidates) != 1:
        raise RuntimeError("Expected exactly one staged late-story function; no files modified.")
    func = candidates[0]
    lines = source.splitlines(keepends=True)

    def text_of(node):
        return ast.get_source_segment(source, node) or ast.unparse(node)

    def is_date_inequality(node):
        return (isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Name)
                and node.left.id == "collected_on"
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.NotEq)
                and len(node.comparators) == 1
                and isinstance(node.comparators[0], ast.Name)
                and node.comparators[0].id == "end_date")

    def is_none_guard(node):
        return ((isinstance(node, ast.UnaryOp)
                 and isinstance(node.op, ast.Not)
                 and isinstance(node.operand, ast.Name)
                 and node.operand.id == "collected_on")
                or (isinstance(node, ast.Compare)
                    and isinstance(node.left, ast.Name)
                    and node.left.id == "collected_on"
                    and len(node.ops) == 1
                    and isinstance(node.ops[0], (ast.Is, ast.Eq))
                    and isinstance(node.comparators[0], ast.Constant)
                    and node.comparators[0].value is None))

    def approved_test(test):
        if is_date_inequality(test):
            return True
        return (isinstance(test, ast.BoolOp)
                and isinstance(test.op, ast.Or)
                and len(test.values) == 2
                and any(is_date_inequality(v) for v in test.values)
                and any(is_none_guard(v) for v in test.values))

    # Look only in this function's statements, never in nested functions.
    guarded = []
    other_collection_tests = []
    for node in ast.walk(func):
        if not isinstance(node, ast.If):
            continue
        condition = text_of(node.test)
        if "collected_on" not in condition:
            continue
        if (approved_test(node.test)
                and len(node.body) == 1
                and isinstance(node.body[0], ast.Continue)
                and not node.orelse):
            guarded.append(node)
        elif "end_date" in condition:
            other_collection_tests.append(condition)

    if len(guarded) > 1:
        raise RuntimeError("Multiple collection date guards found; no files modified.")
    if other_collection_tests:
        # A later local version may already have the desired bounded window.
        if not guarded and all(
            re.fullmatch(
                r"collected_on\s+not\s+in\s*\(\s*end_date\s*,\s*"
                r"end_date\s*-\s*timedelta\(days\s*=\s*1\)\s*\)",
                expr.strip(),
            )
            for expr in other_collection_tests
        ):
            return source, "Already allows collection on previous day"
        raise RuntimeError(
            "Unrecognized additional collection-date condition: "
            + "; ".join(other_collection_tests[:2])
            + ". No files modified."
        )
    if guarded:
        n = guarded[0]
        # Prior day gets through; dates older than one day and future dates
        # remain excluded. Actual article publication-date checks are preserved.
        indent = ' ' * n.col_offset
        replacement = (
            indent + "if collected_on not in (end_date, end_date - timedelta(days=1)):\n"
            + indent + "    continue\n"
        )
        lines[n.lineno - 1:n.end_lineno] = [replacement]
        source = ''.join(lines)
        ast.parse(source)
        return source, "Restricted collection-date guard expanded to current + preceding day"

    # There may already be a bounded 2-day check in a newer revision.
    segment = text_of(func)
    bounded = bool(re.search(r"collected_on\s+not\s+in", segment)
                   and ("timedelta(days=1)" in segment
                        or "timedelta(days = 1)" in segment))
    if bounded:
        return source, "Already allows collection on previous day"

    # If the function has no collection-vs-report-date filter at all, no
    # removal is required. The source-date and existing dedup policy remain.
    if not any("collected_on" in text_of(n.test) and "end_date" in text_of(n.test)
               for n in ast.walk(func) if isinstance(n, ast.If)):
        return source, "No restrictive collection-vs-report-day guard found"

    raise RuntimeError("Unsupported collection-date structure. No files modified.")


builder, date_status = relax_collection_date_guard(builder)
print('[CHECK] Staged late intake:', date_status)

# A row may be collected on either day; save the actual collection metadata.
# This optional normalization is safe and is not a required installer anchor.
old_date = 'row["_collection_run_date"] = collected_on.isoformat()'
if old_date in builder:
    builder = builder.replace(
        old_date,
        'row["_collection_run_date"] = collected_on.isoformat() if collected_on else ""',
        1,
    )

# Rescue only high-signal financial articles already in the evidence audit;
# no imaginary articles, dates, source URLs or summaries are generated.
hook = '    retained, _dropped = _newsfinal.prepare_rows(retained, audit, REPORT_ROOT, end_date)\n'
if builder.count(hook) != 1:
    raise RuntimeError('Expected editorial stage hook missing. No files modified.')
builder = builder.replace(
    hook,
    '    retained, _material_recovered = _newsfinal.recover_material_exclusions(retained, audit)\n'
    '    for _r in _material_recovered:\n'
    '        print("[MATERIAL FINANCIAL RESCUE]", _r["headline"])\n'
    + hook,
    1,
)

# Other than an explicit sent-edition inventory, neither a QA PASS nor an
# old HTML preview is evidence of publication.
editorial = replace_function(
    editorial,
    'def _read_prior_events(root, today, days=3):\n',
    '\ndef _prior_exact(',
    '''def _read_prior_events(root, today, days=3):
    prior = []
    for offset in range(1, days + 1):
        d = today - timedelta(days=offset)
        report_dir = Path(root) / d.isoformat() / "window_1d"
        audit_path = report_dir / "stakeholder_brief_v2_audit.json"
        sent_path = report_dir / "PUBLISHED_URLS.json"
        if not (audit_path.is_file() and sent_path.is_file()):
            continue
        sent = json.loads(sent_path.read_text(encoding="utf-8-sig"))
        urls = {_url(u) for u in sent.get("source_urls", [])}
        report = json.loads(audit_path.read_text(encoding="utf-8-sig"))
        for event in report.get("events") or []:
            if any(_url(s.get("source_url")) in urls
                   for s in event.get("sources") or []):
                prior.append(event)
    return prior
''',
)

# Narrow event identity; never combine different inflation indicators.
start = editorial.find('def _event_kind(row):')
end = editorial.find('\ndef _is_noise(', start) if start >= 0 else -1
if start < 0 or end < 0:
    raise RuntimeError('Event identity hook missing. No files modified.')
block = editorial[start:end]
if block.count('    return ""') != 1:
    raise RuntimeError('Event identity end not unique. No files modified.')
block = block.replace(
    '    return ""',
    '''    if ("ncpi" in t or "national consumer price index" in t) and (
        "ncpi" in h or "inflation" in h
    ):
        number = re.search(r"(\\d+(?:\\.\\d+)?)\\s*%", h)
        period = _s(row.get("publication_date"))[:7]
        if number and period:
            return f"NCPI_RATE_{number.group(1)}_{period}"
    return ""
''',
    1,
)
editorial = editorial[:start] + block + editorial[end:]

RELEVANCE = r'''
# Permanent material-finance rescue. Only uses real existing audit rows.
def material_rule(headline):
    h = ' '.join(str(headline or '').casefold().split())
    if re.search(r'\b(?:fitch|national)\b.*\b(?:rating|ratings)\b.*\bscale\b', h):
        return ('MONETARY_MARKETS_EXTERNAL', 'CAPITAL_MARKETS',
                'NATIONAL_RATING_SCALE_CHANGE', 'MEDIUM')
    if re.search(r'\b(?:sdf|sarvodaya development finance)\b.*\bgreen bond\b.*\b(?:funds|proceeds|deploy|deployment)\b', h):
        return ('OTHER_FINANCIAL_SECTOR', 'CAPITAL_MARKETS',
                'FINANCE_COMPANY_GREEN_BOND_PROCEEDS', 'MEDIUM')
    if re.search(r'\b(?:industry|construction)\b.*\b(?:credit|lending)\b', h):
        return ('KEY_BANKING_SUPERVISORY', 'CREDIT_ASSET_QUALITY',
                'BANK_SECTOR_CREDIT_DISTRIBUTION', 'HIGH')
    if re.search(r'\b(?:pawned|pawning|gold loan)\b', h) and re.search(r'\b(?:bank|banks|commercial)\b', h):
        return ('BANKING_SECTOR_DEVELOPMENTS', 'CREDIT_ASSET_QUALITY',
                'REPORTED_BANK_PAWNING_EXPOSURE_UNVERIFIED', 'MEDIUM')
    if re.search(r'\b(?:ncpi|national.*inflation|overall inflation)\b', h) and re.search(r'\b(?:unchanged|rose|fell|increased|declined|rate|\d+\.\d+%)\b', h):
        return ('MONETARY_MARKETS_EXTERNAL', 'MACRO_GROWTH_INFLATION_FISCAL',
                'INFLATION_INDICATOR', 'MEDIUM')
    if re.search(r'\badb\b.*\b(?:approves|approved)\b.*(?:\$|usd|million|bn|billion)', h):
        return ('MACRO_SYSTEMIC_CONTEXT', 'MACRO_GROWTH_INFLATION_FISCAL',
                'MULTILATERAL_FINANCING_APPROVAL', 'MEDIUM')
    if re.search(r'\bhsbc\b.*\b(?:winds down|closure|closes|shuts|exit)\b', h):
        return ('BANKING_SECTOR_DEVELOPMENTS', 'OPERATIONAL_TECHNOLOGY',
                'BANK_GROUP_SERVICE_OPERATION_NOT_LICENCE_EXIT', 'LOW')
    if re.search(r'\b(?:budget|fiscal)\b.*\b(?:2027|spending|projects)\b', h) and re.search(r'\b(?:rs|bn|billion|allocated)\b', h):
        return ('MACRO_SYSTEMIC_CONTEXT', 'MACRO_GROWTH_INFLATION_FISCAL',
                'BUDGET_SPENDING_PROPOSAL', 'LOW')
    return None


def recover_material_exclusions(retained, audit):
    existing = {str(r.get('source_url') or '').split('?', 1)[0] for r in retained}
    rescued = []
    for row in audit:
        if row.get('section') != 'EXCLUDE':
            continue
        rule = material_rule(row.get('headline'))
        if rule is None:
            continue
        url = str(row.get('source_url') or '').split('?', 1)[0]
        if not url or url in existing:
            continue
        row.update(zip(('section', 'category', 'reason', 'importance'), rule))
        retained.append(dict(row))
        existing.add(url)
        rescued.append({'headline': row.get('headline'), 'source_url': url,
                        'reason': rule[2]})
    return retained, rescued
'''
editorial += '\n' + RELEVANCE

# This helper is the only way a report is considered distributed by the new
# logic. Its --confirm-sent flag is intentionally mandatory.
MARKER_SOURCE = r'''"""Record proof that a financial brief WAS ACTUALLY DISTRIBUTED."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
import argparse
import hashlib
import json

p = argparse.ArgumentParser()
p.add_argument('--date', required=True)
p.add_argument('--html', required=True, help='Exact HTML file sent to stakeholders')
p.add_argument('--confirm-sent', action='store_true')
a = p.parse_args()
if not a.confirm_sent:
    p.error('Use --confirm-sent only AFTER the report has actually been sent.')
source = Path(a.html).resolve()
if not source.is_file():
    p.error(f'Not found: {source}')
raw = source.read_bytes()
soup = BeautifulSoup(raw, 'html.parser')
date_tag = soup.select_one('.date-value')
if date_tag is None or date_tag.get_text(' ', strip=True) != a.date:
    p.error('Report date differs from --date')
articles = soup.select('article')
urls = sorted({str(anchor['href']).split('?', 1)[0].rstrip('/')
              for article in articles for anchor in article.select('a[href]')
              if str(anchor['href']).startswith(('http://', 'https://'))})
if not articles or not urls:
    p.error('No article cards and source links in sent HTML')
root = Path(__file__).resolve().parent.parent
out = root / 'data' / 'processed' / 'reports' / a.date / 'window_1d' / 'PUBLISHED_URLS.json'
if out.exists():
    p.error(f'Published record already exists and will not be overwritten: {out}')
out.parent.mkdir(parents=True, exist_ok=True)
record = {'date': a.date, 'sent_html': str(source),
          'sha256': hashlib.sha256(raw).hexdigest(),
          'created_at': datetime.now(ZoneInfo('Asia/Colombo')).isoformat(),
          'story_cards': len(articles), 'source_urls': urls}
out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
print('[PUBLISHED] Verified sent edition registered:', out)
'''

# Validate all three files BEFORE writing any change.
for path, value in ((BUILDER, builder), (EDITORIAL, editorial), (MARKER, MARKER_SOURCE)):
    compile(value, str(path), 'exec')
ns = {}
exec(compile('import re\n' + RELEVANCE, 'relevance_regression.py', 'exec'), ns)
expected = ['Fitch Ratings recalibrates national rating scale',
            'SDF seeks six more months to deploy green Bond funds',
            'Industry and construction lag personal credit',
            'Sri Lankans have pawned over Rs.1200 Bn worth jewellery to commercial banks',
            'NCPI unchanged at 8.1% in Sept.',
            'ADB approves $ 100 m to strengthen social protection']
for headline in expected:
    assert ns['material_rule'](headline), f'Rescue regression failed: {headline}'
for headline in ['ComBank wins Best Bank Award', 'Island cricket team wins match']:
    assert ns['material_rule'](headline) is None, f'Noise regression failed: {headline}'

backup = ROOT / 'data' / 'integration_backups' / ('publication_relevance_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
backup.mkdir(parents=True, exist_ok=False)
shutil.copy2(BUILDER, backup / BUILDER.name)
shutil.copy2(EDITORIAL, backup / EDITORIAL.name)
try:
    BUILDER.write_text(builder, encoding='utf-8')
    EDITORIAL.write_text(editorial, encoding='utf-8')
    MARKER.write_text(MARKER_SOURCE, encoding='utf-8')
    for path in (BUILDER, EDITORIAL, MARKER):
        compile(path.read_text(encoding='utf-8'), str(path), 'exec')
except Exception:
    shutil.copy2(backup / BUILDER.name, BUILDER)
    shutil.copy2(backup / EDITORIAL.name, EDITORIAL)
    MARKER.unlink(missing_ok=True)
    print('[ROLLBACK] Original production scripts restored')
    raise
print('[PASS] QA/test reports no longer count as distributed')
print('[PASS] Prior-day unpublished articles remain eligible for carry-forward')
print('[PASS] Known material finance omissions rescued')
print('[PASS] Publication inventory helper installed')
print('[BACKUP]', backup)
print('[NEXT] Rebuild the 2026-10-22 report only after recording any actually sent editions')
