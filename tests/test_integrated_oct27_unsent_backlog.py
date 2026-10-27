"""27-Oct carry-forward regressions; no live requests and no sending."""
from __future__ import annotations
import csv
from datetime import date, datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bank_intel.publication.backlog import earliest_unpublished_day
from bank_intel.common.collection_window import get_collection_window
from bank_intel.publication.workflow import prepare


class Oct27UnsentBacklog(unittest.TestCase):
    def _report(self, root: Path, status='HOLD_FOR_REVIEW', start='2026-10-23', sent=False):
        folder = root / '2026-10-26' / 'window_1d'
        (folder / 'integrated_edition').mkdir(parents=True)
        (folder / 'integrated_release_result.json').write_text(json.dumps({'status': status}))
        (folder / 'integrated_edition' / 'candidate_edition.json').write_text(
            json.dumps({'earliest_inclusive_date':start}))
        if sent:
            (folder / 'PUBLISHED_URLS.json').write_text('{}')
        return folder

    def test_tuesday_default_is_monday(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(earliest_unpublished_day(Path(d), date(2026,10,27)), date(2026,10,26))

    def test_unreleased_monday_does_not_expand_tuesday_before_monday(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); self._report(root)
            self.assertEqual(earliest_unpublished_day(root,date(2026,10,27)),date(2026,10,26))

    def test_sent_lock_prevents_unsigned_backlog_extension(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); self._report(root,sent=True)
            self.assertEqual(earliest_unpublished_day(root,date(2026,10,27)),date(2026,10,26))

    def test_approved_or_qa_pass_alone_not_a_sent_record(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); self._report(root,status='QA_PASS_NOT_DELIVERED')
            # Only an explicitly held edition extends the backlog. A QA pass
            # is still NOT interpreted as a sent record for sent matching.
            self.assertEqual(earliest_unpublished_day(root,date(2026,10,27)),date(2026,10,26))

    def test_compiler_rejects_friday_article_for_tuesday(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); reports=base/'reports'; self._report(reports)
            rows=[{'publication_date':'2026-10-23','source_name':'Daily Mirror',
                   'source_url':'https://www.dailymirror.lk/business/supervision',
                   'headline':'NDB bank announces financial results for quarter',
                   'body':'National Development Bank reports quarterly results and impairment costs. '*12,
                   'section':'BANKING_SECTOR_DEVELOPMENTS'},
                  {'publication_date':'2026-10-27','source_name':'Daily FT',
                   'source_url':'https://www.ft.lk/front-page/cbsl/44-999999',
                   'headline':'CBSL issues new regulatory bank notice',
                   'body':'Central Bank of Sri Lanka issued new banking supervision guidance. '*12,
                   'section':'KEY_BANKING_SUPERVISORY'}]
            au=base/'audit.json';au.write_text(json.dumps({'audit':rows}))
            inv=base/'inventory.csv'
            with inv.open('w',encoding='utf-8-sig',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=('source_url','publication_date','headline','source_name'))
                writer.writeheader();writer.writerows([{k:row[k] for k in writer.fieldnames} for row in rows])
            decisions,events,counts,earliest=prepare(date_iso='2026-10-27',audit=au,inventory=inv,
                 sent_paths=[],sent_report_root=reports)
            self.assertEqual(earliest,date(2026,10,26))
            self.assertEqual(counts['in_window'],1)
            self.assertEqual(decisions[0]['assessment']['reason'],'OUTSIDE_DATE_WINDOW')

    def test_live_collection_window_stays_monday_tuesday(self):
        dt=datetime(2026,10,27,8,0,tzinfo=ZoneInfo('Asia/Colombo'))
        with patch('bank_intel.publication.backlog.earliest_unpublished_day',return_value=date(2026,10,26)):
            window=get_collection_window(dt)
            self.assertEqual((window.start_date,window.end_date),
                             (date(2026,10,26),date(2026,10,27)))
            self.assertNotEqual(window.mode,'UNSENT_PREVIOUS_EDITION_CATCHUP')

    def test_carryover_audit_detects_missing_priority_and_is_not_a_send(self):
        from bank_intel.publication.backlog_audit import audit_unpublished_carryover
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder=self._report(root)
            old=folder/'integrated_edition'/'article_decision_trace.csv'
            fields=['source_url','headline','publication_date','decision']
            with old.open('w',newline='',encoding='utf-8-sig') as f:
                w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
                w.writerow({'source_url':'https://www.dailymirror.lk/business/critical',
                            'headline':'CBSL regulatory update','publication_date':'2026-10-26',
                            'decision':'INCLUDE'})
            today=root/'today.csv'
            with today.open('w',newline='',encoding='utf-8-sig') as f:
                w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
                w.writerow({'source_url':'https://www.ft.lk/front-page/other/44-9',
                            'headline':'Current day report','publication_date':'2026-10-27',
                            'decision':'INCLUDE'})
            report=audit_unpublished_carryover(root,date(2026,10,27),today)
            self.assertEqual(report['previous_priority_candidates'],1)
            self.assertEqual(len(report['missing_priority_candidates']),1)
            self.assertEqual(report['issues'][0]['kind'],'HELD_EDITION_MATERIAL_CARRYOVER_GAP')

    def test_recovery_lookback_is_dynamic_in_runner(self):
        runner=(ROOT/'scripts'/'run_pipeline.py').read_text(encoding='utf-8')
        self.assertIn('earliest_unpublished_day(',runner)
        self.assertIn('str(lookback_for_recovery)',runner)

if __name__=='__main__':unittest.main()
