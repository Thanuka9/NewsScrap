"""Failure injection for resumability, visible errors and sent-edition safety."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from bank_intel.common import readiness
from bank_intel.common.execution import run_logged_process
from bank_intel.publication.decisions import decide
from bank_intel.publication.editorial_rules import nontransactional_bank_visit
from bank_intel.publication.model_policy import reconcile


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def test_failed_child_error_is_visible_in_terminal_summary_and_log(tmp_path, monkeypatch, capsys):
    import run_pipeline
    monkeypatch.setattr(run_pipeline, 'PROJECT_ROOT', tmp_path)
    steps=[]
    with pytest.raises(RuntimeError, match='QA_FAILED: category mismatch'):
        run_pipeline.run_step(name='FINAL BRIEF', steps=steps,
            command=[sys.executable, '-c', "import sys; print('QA_FAILED: category mismatch',file=sys.stderr);sys.exit(1)"])
    assert 'QA_FAILED: category mismatch' in capsys.readouterr().out
    assert steps[0]['error_tail'] == ['QA_FAILED: category mismatch']
    assert 'QA_FAILED' in Path(steps[0]['log_path']).read_text()
    assert json.loads((tmp_path/'data/runtime/current_step.json').read_text())['status']=='FAILED'


def test_timeout_keeps_output_written_before_hang(tmp_path, capsys):
    log=tmp_path/'timeout.log'
    with pytest.raises(subprocess.TimeoutExpired):
        run_logged_process([sys.executable, '-c', "import time;print('collector started',flush=True);time.sleep(30)"],
                           log_path=log,timeout=1)
    assert 'collector started' in log.read_text()
    assert 'collector started' in capsys.readouterr().out


def test_review_exit_is_reported_without_claiming_success(tmp_path, monkeypatch):
    import run_pipeline
    monkeypatch.setattr(run_pipeline, 'PROJECT_ROOT', tmp_path)
    steps=[]
    code=run_pipeline.run_step(name='RELEASE',steps=steps,review_exit=True,
                               command=[sys.executable,'-c','raise SystemExit(2)'])
    assert code==2 and steps[0]['status']=='REVIEW_REQUIRED'


def ready_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(readiness, 'collection_issues', lambda *args: [])
    monkeypatch.setattr(readiness, 'sources_for_date', lambda *args: [])
    target='2026-10-29'
    source=tmp_path/'source.json';write(source, {'content_hash':'hash-a'})
    stage1=tmp_path/'analysis.json'
    write(stage1, {'source_url':'https://example.org/a','content_hash':'hash-a','input_file':str(source)})
    ml=tmp_path/'ml.json';write(ml, {'source_url':'https://example.org/a'})
    embedding=tmp_path/'embedding.npy';embedding.write_bytes(b'embedding')
    for kind, data in {
        'stage1':{'article_count':1,'failed_count':0,'analysis_files':[str(stage1)]},
        'ml':{'article_count':1,'articles':[{'analysis_file':str(ml),'embedding_file':str(embedding)}]},
        'ml_corpus':{'all_phase2_article_count':1,'stage_a_keep_article_count':1},
        'taxonomy':{'article_count':1},
        'collection_completeness':{'recovered_article_count':0},
    }.items():
        window='' if kind in ('stage1','ml') else 'window_1d'
        filename='corpus' if kind=='ml_corpus' else kind
        if kind == 'ml_corpus':
            for field in ('cluster_file','cluster_profile_file','candidate_event_groups_file'):
                path=tmp_path/(field+'.json');write(path,{})
                data[field]=str(path)
        if kind == 'taxonomy':
            for field in ('article_taxonomy_json','cluster_taxonomy_json','article_taxonomy_csv'):
                path=tmp_path/(field+'.json');write(path,{})
                data[field]=str(path)
        data.update(created_at=target+'T12:00:00+05:30',publication_date=target,days=1,status='PASS' if kind=='collection_completeness' else 'SUCCESS')
        write(tmp_path/'data/processed'/kind/target/window/(filename+'_manifest.json'),data)
    return target


def test_resume_rejects_a_newer_collection(tmp_path, monkeypatch):
    target=ready_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(readiness,'sources_for_date',lambda *a:['Daily FT'])
    write(tmp_path/'data/source_status/daily_ft.json',{'completed_at':target+'T12:30:00+05:30'})
    assert 'COLLECTION_NEWER_THAN_ANALYSIS:daily_ft' in readiness.final_stage_readiness(tmp_path,target)['issues']


def test_resume_rejects_missing_taxonomy_output(tmp_path, monkeypatch):
    target=ready_fixture(tmp_path,monkeypatch)
    (tmp_path/'article_taxonomy_json.json').unlink()
    assert 'MISSING_UPSTREAM_OUTPUT:taxonomy:article_taxonomy_json' in readiness.final_stage_readiness(tmp_path,target)['issues']


@pytest.mark.parametrize('damage,expected',[
    ('none',None),('source','SOURCE_CONTENT_CHANGED'),('missing_ml','MISSING_OR_INVALID_ANALYSIS:ml'),
    ('membership','STAGE1_ML_MEMBERSHIP_MISMATCH'),('embedding','MISSING_ML_EMBEDDING'),
    ('manifest','MISSING_OR_INVALID_MANIFEST:ml'),('malformed_files','INVALID_ANALYSIS_FILE_LIST:stage1'),
    ('malformed_articles','INVALID_ML_ARTICLE_LIST'),('recovered','UNRESOLVED_COLLECTION_RECOVERIES'),
])
def test_resume_rejects_incomplete_or_inconsistent_upstream(tmp_path, monkeypatch, damage, expected):
    target=ready_fixture(tmp_path,monkeypatch)
    if damage=='source':write(tmp_path/'source.json',{'content_hash':'changed'})
    if damage=='missing_ml':(tmp_path/'ml.json').unlink()
    if damage=='membership':write(tmp_path/'ml.json',{'source_url':'https://example.org/different'})
    if damage=='embedding':(tmp_path/'embedding.npy').unlink()
    if damage=='manifest':write(tmp_path/'data/processed/ml'/target/'ml_manifest.json',[])
    if damage=='malformed_files':write(tmp_path/'data/processed/stage1'/target/'stage1_manifest.json',{'analysis_files':[{}]})
    if damage=='malformed_articles':write(tmp_path/'data/processed/ml'/target/'ml_manifest.json',{'articles':[None]})
    if damage=='recovered':
        p=tmp_path/'data/processed/collection_completeness'/target/'window_1d/collection_completeness_manifest.json'
        d=json.loads(p.read_text());d['recovered_article_count']=1;write(p,d)
    result=readiness.final_stage_readiness(tmp_path,target)
    assert result['ready'] is (expected is None)
    if expected:assert any(s.startswith(expected) for s in result['issues'])


def test_sent_resume_does_not_build_or_change_files(tmp_path, monkeypatch):
    import resume_final_edition as resume
    monkeypatch.setattr(resume,'ROOT',tmp_path)
    monkeypatch.setattr(resume,'sent_record',lambda *a:{'status':'ALREADY_SENT'})
    monkeypatch.setattr(resume,'final_stage_readiness',lambda *a:pytest.fail('No work for a sent edition'))
    assert resume.main(['--date','2026-10-29'])==0
    assert not list(tmp_path.iterdir())


def test_resume_review_hold_removes_interim_attachment_and_records_review(tmp_path, monkeypatch):
    import resume_final_edition as resume
    import news_publication_history
    target=ready_fixture(tmp_path,monkeypatch)
    report=tmp_path/'data/processed/reports'/target/'window_1d';report.mkdir(parents=True)
    monkeypatch.setattr(resume,'ROOT',tmp_path)
    monkeypatch.setattr(resume,'REPORTS',tmp_path/'data/processed/reports')
    monkeypatch.setattr(resume,'sent_record',lambda *a:None)
    monkeypatch.setattr(news_publication_history,'assert_edition_not_sent',lambda *a:None)
    monkeypatch.setattr(resume,'validate_phase2_structural_contract',lambda *a:{})
    def fake_step(*,name,steps,**kwargs):
        (report/'supervisory_digest.html').write_text('unapproved preview')
        code=2 if name=='INTEGRATED RELEASE' else 0
        steps.append({'name':name,'return_code':code,'status':'REVIEW_REQUIRED' if code else 'SUCCESS'})
        return code
    monkeypatch.setattr(resume,'run_step',fake_step)
    assert resume.main(['--date',target])==2
    assert not (report/'supervisory_digest.html').exists()
    summary=next((tmp_path/'data/run_summaries').glob('final_resume_*.json'))
    assert json.loads(summary.read_text())['status']=='REVIEW_REQUIRED'


def test_status_prioritizes_sent_release_over_old_failure_and_stale_pid(tmp_path,monkeypatch):
    import pipeline_status
    monkeypatch.setattr(pipeline_status,'validate_release',lambda *a:{'status':'ALREADY_SENT'})
    monkeypatch.setattr(pipeline_status,'final_stage_readiness',lambda *a:{'ready':True,'issues':[]})
    monkeypatch.setattr(pipeline_status,'process_alive',lambda *a:False)
    write(tmp_path/'data/runtime/pipeline_owner.json',{'pid':123})
    write(tmp_path/'data/runtime/current_step.json',{'pid':123,'status':'RUNNING'})
    write(tmp_path/'data/run_summaries/old.json',{'target_publication_date':'2026-10-29','status':'FAILED'})
    result=pipeline_status.status(tmp_path,'2026-10-29')
    assert result['release']['status']=='ALREADY_SENT' and not result['pipeline_running']
    assert result['last_stage']['status']=='INTERRUPTED_OR_STALE'
    assert result['latest_run_status']=='FAILED'


def test_source_snapshot_excludes_retry_archives_but_preserves_current_evidence(tmp_path):
    from run_integrated_release import snapshot_source_statuses
    source=tmp_path/'statuses';dest=tmp_path/'snapshot'
    write(source/'daily_ft.json',{'source':'Daily FT'})
    write(source/'history/2026-10-28/daily_ft.json',{'old':True})
    snapshot_source_statuses(source,dest,'2026-10-29')
    assert [p.name for p in dest.rglob('*')]==['daily_ft.json']
    assert (source/'daily_ft.json').read_bytes()==(dest/'daily_ft.json').read_bytes()


@pytest.mark.parametrize('bank',['BOC','Sampath Bank','Commercial Bank'])
def test_courtesy_policy_is_shared_and_not_specific_to_one_bank(bank):
    from news_editorial_final import _news_courtesy_bank_meeting_v2
    row={'headline':f'{bank} welcomes Bank of China President for bilateral financial talks',
         'body':'The visiting delegation discussed future cooperation and funding opportunities.'}
    assert nontransactional_bank_visit(row) and _news_courtesy_bank_meeting_v2(row)
    result=reconcile(decide(row),{'raw_stage_a':'KEEP','operational_keep':True},row)
    assert result['decision']=='EXCLUDE'


@pytest.mark.parametrize('body',[
    'The banks signed a loan agreement during the visit.',
    'A financing agreement was executed during the visit.',
    'They launched a clearing product.',
])
def test_courtesy_exclusion_preserves_financial_outcomes(body):
    assert not nontransactional_bank_visit({'headline':'BOC welcomes visiting delegation president','body':body})


def test_bank_board_appointment_is_supervisory():
    h='Hashindra Silva joins Sampath Bank board'
    row={'headline':h,'body':(h+'. The appointment changes the composition of the board of directors. ')*12}
    result=decide(row)
    assert result['section']=='KEY_BANKING_SUPERVISORY'
    assert result['decision']=='INCLUDE'


@pytest.mark.parametrize('headline,body,reason',[
    ('SEC warns insider trading faces sanctions','The Securities and Exchange Commission in Sri Lanka set out its enforcement approach.','SEC_MARKET_CONDUCT_ACTION'),
    ('Sri Lanka sees foreign inflows into rupee bonds','Foreign investors bought 200 million rupees of government securities.','OBSERVED_FOREIGN_SECURITIES_FLOW'),
    ('Sri Lanka central bank explains reserve accumulation','Foreign exchange purchases support dollar reserves while limiting pressure on the rupee.','CENTRAL_BANK_RESERVE_POLICY'),
    ('CSE expands access for new investors','The Colombo Stock Exchange will open 3 new branches.','CSE_MARKET_ACCESS_OR_FUNDING'),
])
def test_material_rules_require_corroborating_source_context(headline,body,reason):
    from bank_intel.publication.materiality import material_context
    assert material_context({'headline':headline,'body':body})[2]==reason
    assert material_context({'headline':headline,'body':'A local sports club held an annual meeting.'}) is None


def test_replay_rejects_changed_frozen_evidence(tmp_path):
    import hashlib
    from replay_sent_edition import verify_replay_inputs
    ledger=tmp_path/'publication_decisions.json';write(ledger,{'articles':[]})
    sent=tmp_path/'SENT_EDITION.json'
    write(sent,{'decision_ledger_sha256':hashlib.sha256(ledger.read_bytes()).hexdigest()})
    source=tmp_path/'inputs/audit.json';write(source,{'articles':[]})
    write(tmp_path/'input_snapshot.json',{'files':[{'path':'inputs/audit.json','sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]})
    verify_replay_inputs(tmp_path,{'sent_record':str(sent)})
    write(source,{'articles':['changed']})
    with pytest.raises(RuntimeError,match='FROZEN_REPLAY_INPUT_CHANGED'):
        verify_replay_inputs(tmp_path,{'sent_record':str(sent)})
