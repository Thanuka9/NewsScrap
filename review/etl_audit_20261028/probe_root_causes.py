"""Reproduce defects using local evidence without modifying production files."""
import ast
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from bank_intel.publication.reconcile import load_confirmed_history, prior_matches
from bank_intel.publication.evidence import summary
from bank_intel.common.state import JsonArticleState


def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


report = ROOT / 'data/processed/reports/2026-10-28/window_1d'
manifest = read(report / 'integrated_edition/candidate_edition.json')
articles = read(Path(manifest['source_audit_path']))['audit']
adb = next(r for r in articles if '799234' in r['source_url'])
prior, history_issues = load_confirmed_history(ROOT / 'data/processed/reports', '2026-10-28')
matches, updates = prior_matches(adb, prior)
result = {'adb_suppression': {'current_headline': adb['headline'], 'current_url': adb['source_url'],
          'current_lead': adb['body'][:1800], 'matched_prior': matches, 'possible_updates': updates,
          'history_issues': history_issues}}
apg = next(r for r in articles if '1047137' in r['source_url'])
text = summary(apg)
result['apg_summary'] = {'text': text, 'word_count': len(text.split()), 'ends_with_dr': text.endswith('Dr.')}

# Load only the scheduler validator's AST to avoid its import-time log handler.
source = ROOT / 'scripts/run_scheduler.py'
tree = ast.parse(source.read_text(encoding='utf-8-sig'))
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'validate_pipeline_outputs')
namespace = {'PROJECT_ROOT': ROOT, 'Path': Path, 'load_json': read}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
try:
    namespace['validate_pipeline_outputs']('2026-10-28')
    result['scheduler_validation'] = 'ACCEPTED'
except Exception as exc:
    result['scheduler_validation'] = {'type': type(exc).__name__, 'message': str(exc)}

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'state.json'
    path.write_text('{broken json', encoding='utf-8')
    state = JsonArticleState(path)
    result['corrupt_state_load'] = {'raised_error': False, 'loaded_articles': len(state.data['articles'])}

result['replacement_character_count'] = sum(str(r.get('body') or '').count('\ufffd') for r in articles)
result['input_hashes'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in [source, ROOT / 'src/bank_intel/publication/identity.py',
                                  ROOT / 'src/bank_intel/publication/reconcile.py',
                                  ROOT / 'src/bank_intel/publication/evidence.py']}
(OUT / 'root_cause_evidence.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps({k: v for k, v in result.items() if k not in ('adb_suppression', 'input_hashes')}, ensure_ascii=True, indent=2))
