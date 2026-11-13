"""No change may let more of the editor's corrections leak than the recorded baseline.

The baseline comes from scripts/leak_audit.py --baseline: every signed correction
since 20 October replayed against the automatic rules. Lower it whenever leaks
are fixed; this test only ever lets it go down.
"""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
BASELINE = ROOT / 'data/training/labelling/leak_baseline.json'


@pytest.mark.skipif(not BASELINE.is_file(), reason='no leak baseline recorded yet')
def test_corrections_do_not_leak_more_than_the_baseline():
    from leak_audit import fast_counts
    baseline = json.loads(BASELINE.read_text(encoding='utf-8'))
    allowed = baseline['fast_leaked']
    # The days the baseline was recorded on; later signed days join when it is re-recorded.
    now = fast_counts(baseline.get('days'))
    worse = {kind: (now[kind], allowed[kind]) for kind in allowed if now.get(kind, 0) > allowed[kind]}
    assert not worse, f'more corrections leak than the baseline (now, allowed): {worse}'
