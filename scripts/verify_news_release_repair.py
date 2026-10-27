"""Read-only offline regression tests for publication reconciliation fixes.

No network calls, no production mutations. Run from News Scrap project root:
    python .\\scripts\\verify_news_release_repair.py
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import tempfile

import news_publication_history as history
import build_financial_brief_v2 as builder
import news_editorial_final as editorial


def main() -> None:
    assert history.earliest_eligible_day(date(2026,10,23)) == date(2026,10,22)
    assert history.earliest_eligible_day(date(2026,10,26)) == date(2026,10,23)
    with tempfile.TemporaryDirectory(prefix='news_repair_test_') as tmp:
        root=Path(tmp)
        folder=root/'2026-10-22'/'window_1d'
        folder.mkdir(parents=True)
        # A QA-passed preview is never considered sent.
        (folder/'stakeholder_brief_v2_audit.json').write_text(
            json.dumps({'qa':{'status':'PASS'},'events':[{'headline':'Unsold sample',
                    'sources':[{'source_url':'https://example.org/preview'}]}]}), encoding='utf-8')
        assert not history.published_cards(root,date(2026,10,23))
        events=[{'headline':'NDB third quarter earnings report',
                 'publication_date':'2026-10-22',
                 'source_urls':['https://www.example.org/ndb-results']}]
        (folder/'PUBLISHED_URLS.json').write_text(json.dumps({
             'report_date':'2026-10-22','source_urls':['https://example.org/ndb-results'],
             'events':events}),encoding='utf-8')
        assert len(history.published_cards(root,date(2026,10,23)))==1
        cards=history.published_cards(root,date(2026,10,23))
        assert builder._news_sent_match({'headline':'NDB third quarter earnings report',
                 'source_url':'https://another-news.org/ndb'},cards)
        assert not builder._news_sent_match({'headline':'NDB announces a new prudential audit programme',
                 'source_url':'https://another-news.org/new'},cards)
        prior=editorial._read_prior_events(root,date(2026,10,23))
        assert len(prior)==1
        assert editorial._prior_exact({'headline':'NDB third quarter earnings report',
                  'source_url':'https://example.org/ndb-results','publication_date':'2026-10-23'},prior)
        (folder/'PUBLISHED_URLS.json').write_text(json.dumps({
             'report_date':'2026-10-21','source_urls':['https://example.org/ndb-results'],
             'events':events}),encoding='utf-8')
        try:
            history.published_cards(root,date(2026,10,23))
        except RuntimeError:
            pass
        else:
            raise AssertionError('An invalid published manifest was accepted')
    rows=[{'source_url':'https://a.lk/graduate','headline':'Graduate growth falls short of skills needs: IPS',
           'event_key':'IPS_STATE_OF_ECONOMY'},
          {'source_url':'https://a.lk/tax','headline':'IPS calls for targeted wealth taxation VAT cuts premature',
           'event_key':'IPS_STATE_OF_ECONOMY'}]
    retained,audit=builder._news_separate_unrelated_members([dict(x) for x in rows],
                                                             [dict(x) for x in rows])
    assert retained[0]['event_key']!=retained[1]['event_key']
    print('[PASS] 8 offline checks: date windows, QA preview, sent manifest, exact and event')
    print('       reconciliation, new follow-up preservation, malformed record, grouping')
    real_root=Path(__file__).resolve().parents[1]/'data/processed/reports'
    real_records=history.published_editions(real_root,date(2026,10,23))
    if real_records:
        print('[FOUND] Local confirmed sent editions:',
              ', '.join(f"{x['date']} ({len(x['cards'])} stories)" for x in real_records[:10]))
    else:
        print('[WARNING] No local PUBLISHED_URLS.json before 23 October was found')
    # Refuse silent changes to a previously emailed HTML, including a
    # regenerated draft accidentally written to the original report path.
    for record in real_records:
        for check in history.sent_html_integrity(real_root, record['date']):
            status='PASS' if check['matches'] else 'MISMATCH'
            print(f'[SENT HTML SHA256 {status}] {record["date"]}: {check["sent_html"]}')
            if not check['matches']:
                raise RuntimeError('A sent HTML file differs from its immutable publication manifest. '
                                   'Restore the ACTUALLY EMAILED copy before continuing.')
    print('[NOTE] Collection and full 23 October pipeline still require live local verification')


if __name__ == '__main__':
    main()
