import asyncio
import copy
import json
import sqlite3
from pathlib import Path
import pytest
from app.crm import write_review
from app.models import RunRequest
from app.workflow import run_workflow


def review_record(decision='approve'):
    event = dict(idempotency_key='key', account_key='company', display_name='Company',
                 reviewer='Tester', reviewed_at='2026-11-07', decision=decision,
                 evidence=[], grade='B', score=65, edits={}, reason=None)
    item = dict(idempotency_key='key', account_key='company', display_name='Company',
                reviewed_by='Tester', reviewed_at='2026-11-07', evidence=[], grade='B', score=65)
    return dict(complete=True,events=[event],crm_queue=[item] if decision=='approve' else [])


def test_crm_idempotent_and_conflict_rolls_back(tmp_path):
    db=tmp_path/'crm.db'; review=review_record()
    assert write_review(review,db)==(1,0)
    assert write_review(review,db)==(0,1)
    conflict=copy.deepcopy(review)
    conflict['events'][0]['score']=70; conflict['crm_queue'][0]['score']=70
    new=copy.deepcopy(review)
    new['events'][0]['idempotency_key']='new';new['crm_queue'][0]['idempotency_key']='new'
    conflict['events']=new['events']+conflict['events']
    conflict['crm_queue']=new['crm_queue']+conflict['crm_queue']
    with pytest.raises(ValueError,match='different reviewed content'): write_review(conflict,db)
    with sqlite3.connect(db) as conn: assert conn.execute('SELECT count(*) FROM leads').fetchone()[0]==1


@pytest.mark.parametrize('case',['reject','incomplete','tampered'])
def test_crm_requires_matching_completed_approval(tmp_path,case):
    review=review_record()
    if case=='reject':review['events'][0]['decision']='reject'
    if case=='incomplete':review['complete']=False
    if case=='tampered':review['crm_queue'][0]['display_name']='Other'
    with pytest.raises(ValueError):write_review(review,tmp_path/'crm.db')
    assert not (tmp_path/'crm.db').exists()


def test_full_workflow_review_feedback_and_fresh_approval(tmp_path,monkeypatch):
    import app.workflow as w
    requests=[]; reviews=[]
    async def execute(request):
        requests.append(request)
        return {'run_id':str(len(requests)), 'status':'completed', 'signals':[], 'filter_score':{'accounts':[]}}
    async def reason(result):return {'accounts':[]}
    def review(*args,**kwargs):
        record=review_record('reject' if not reviews else 'approve')
        record['events'][0]['reason']='Need named workplace evidence'
        for item in record['crm_queue']: item['review_reason']='Need named workplace evidence'
        reviews.append(record);return record
    monkeypatch.setattr(w,'execute',execute);monkeypatch.setattr(w,'reason_result',reason)
    monkeypatch.setattr(w,'apply_reasoning',lambda r,x:{**r,'llm_reasoning':x})
    monkeypatch.setattr(w,'run_review',review)
    answers=iter(['y','y','Find company accident evidence','','y','y','y'])
    folder=tmp_path/'workflow'
    result=asyncio.run(run_workflow(RunRequest(prompt='Research workplace safety'), 'Tester',folder,tmp_path/'crm.db',ask=lambda _:next(answers),say=lambda _:None))
    assert result['status']=='completed'
    assert len(requests)==2
    assert 'Need named workplace evidence' in requests[1].prompt
    assert requests[1].resume_run_id is None
    assert result['rounds'][1]['crm']['written']==1
    assert (folder/'round-1-feedback.json').exists()
    assert (folder/'workflow.json').exists()


def test_interrupted_review_never_writes_crm(tmp_path,monkeypatch):
    import app.workflow as w
    record=review_record();record['complete']=False
    monkeypatch.setattr(w,'run_review',lambda *a,**k:record)
    initial={'status':'completed','filter_score':{},'llm_reasoning':{}}
    result=asyncio.run(run_workflow(RunRequest(prompt='Research workplace safety'),'Tester',tmp_path/'flow',tmp_path/'crm.db',initial=initial,ask=lambda _:pytest.fail('No write prompt expected'),say=lambda _:None))
    assert result['status']=='review_incomplete'
    assert not (tmp_path/'crm.db').exists()
