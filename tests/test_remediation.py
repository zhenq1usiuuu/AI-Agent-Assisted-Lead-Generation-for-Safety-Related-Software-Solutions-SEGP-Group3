import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path

import pytest

from app.budget import BudgetExceeded, EvaluationBudget
from app.connectors import inspect_public_source
from app.content import chunk_document, duplicate_of, normalized_url
from app.engine import execute_live, rule_checks
from app.models import Plan, ChunkExtraction, RunRequest, SemanticReview, ReviewItem
from app.tools import PublicFetcher, clean_html, clean_pdf


@pytest.fixture
def isolated_runs(tmp_path, monkeypatch):
    monkeypatch.setattr('app.engine.run_folder',lambda run_id:tmp_path/run_id)
    return tmp_path


def plan():
    return Plan(status='ready',workflow='safety inspection',country='Malaysia',sectors=[],
                keywords=['workplace safety'],seed_ids=[],questions=[],assumptions=[])


class EmptyModel:
    model='mock'
    def __init__(self):
        self.usage=[]
        self.calls=[]
    async def generate(self,stage,payload,schema):
        self.calls.append(stage)
        if stage=='receive_prompt':
            return plan()
        return ChunkExtraction(signals=[],abstention_reason='No company evidence.',coverage_complete=True,coverage_note=None)


def raw(url, text):
    return {'html':text,'url':url,'requested_url':url,'http_status':200,'captured_at':'2026-10-25',
            'content_hash':hashlib.sha256(text.encode()).hexdigest()}


def test_depth_limit_cycles_and_root_fairness(monkeypatch, isolated_runs):
    visited=[]
    async def fetch(self,url):
        visited.append(url)
        number=int(url.rsplit('/',1)[-1])
        return raw(url,f'<main>Public workplace safety page number {number}. No named company evidence here.'
                   f'<a href="/{number+1}">Workplace safety next</a><a href="/0">Workplace safety start</a></main>')
    monkeypatch.setattr(PublicFetcher,'fetch_public_page',fetch)
    request=RunRequest(prompt='Research workplace safety',additional_urls=['https://example.com/0','https://other.example.com/0'],
                       permissions_confirmed=True,max_pages=12,max_depth=2,semantic_review='off')
    result=asyncio.run(execute_live(request,EmptyModel()))
    assert visited[:2]==request.additional_urls
    assert len(visited)==6
    assert all(d['depth']<=2 for d in result['documents'])


def test_full_text_chunking_no_silent_18000_truncation():
    body='\n'.join(f'Company {n}: workplace safety inspection records.' for n in range(700))
    doc=clean_html('<main>'+body+'</main>','https://example.com/')
    assert len(doc['text'])>18000 and not doc['text_truncated']
    doc['document_id']='d'
    chunks=chunk_document(doc)
    assert ''.join(doc['text'][c['start']:c['end']] for c in chunks)==doc['text']
    assert chunks[-1]['end']==len(doc['text'])


def test_dedup_keeps_dates_and_query_identifiers():
    old={'document_id':'old','text':'Company A inspection on 2026-01-01. '*8,'source_class':'company_owned','evidence_scope':'records'}
    copied={**old,'text':old['text'].replace(' ', '  ')}
    assert duplicate_of(copied,[old])[0]==old
    changed={**old,'text':old['text'].replace('2026-01-01','2026-02-01')}
    assert duplicate_of(changed,[old])[0] is None
    assert normalized_url('https://example.com/?id=1&utm_source=x')=='https://example.com/?id=1'
    assert normalized_url('https://example.com/?id=1')!=normalized_url('https://example.com/?id=2')


def test_public_connectors_never_guess_or_submit_post():
    html='<form action="/search" method="get"><input name="company"></form><iframe src="https://other.example/frame"></iframe>'
    result=inspect_public_source(html,'https://example.com/','sirim_directory','A & B')
    assert result['targets'][0]['url']=='https://example.com/search?company=A+%26+B'
    assert result['limitations']
    for html in ['<form method="post"><input name="company"></form>',
                 '<form><input name="company"><input name="csrf" type="hidden"></form>']:
        assert not inspect_public_source(html,'https://example.com/','sirim_directory','A')['targets']
    result=inspect_public_source('<iframe src="/case.pdf"></iframe>','https://example.com/','dosh_enforcement_dashboard')
    assert result['targets'][0]['url']=='https://example.com/case.pdf'


def test_budget_pause_resume_reuses_plan_and_fetched_document(monkeypatch, isolated_runs):
    visited=[]
    async def fetch(self,url):
        visited.append(url)
        return raw(url,'<main>A real public article about workplace safety inspection. No named company.</main>')
    monkeypatch.setattr(PublicFetcher,'fetch_public_page',fetch)
    class StopModel(EmptyModel):
        async def generate(self,stage,payload,schema):
            if stage=='extract_signals':
                raise BudgetExceeded('Test cap')
            return await super().generate(stage,payload,schema)
    request=RunRequest(prompt='Research safety inspections',additional_urls=['https://example.com/'],permissions_confirmed=True)
    first=asyncio.run(execute_live(request,StopModel()))
    assert first['stop_reason']=='budget_limit' and first['remaining_chunks']
    second_model=EmptyModel()
    second=asyncio.run(execute_live(request.model_copy(update={'resume_run_id':first['run_id'],'budget_usd':2}),second_model))
    assert visited==['https://example.com/']
    assert second_model.calls==['extract_signals']
    assert second['extraction_complete'] and not second['remaining_chunks']
    assert second['status']=='completed'


def test_chunk_limit_resume_and_no_repeated_successful_chunks(monkeypatch,isolated_runs):
    async def fetch(self,url):
        return raw(url,'<main>'+'\n'.join(f'Line {n}: public safety guidance without company evidence.' for n in range(40))+'</main>')
    monkeypatch.setattr(PublicFetcher,'fetch_public_page',fetch)
    request=RunRequest(prompt='Research safety inspections',additional_urls=['https://example.com/'],permissions_confirmed=True,max_chunks=1)
    first=asyncio.run(execute_live(request,EmptyModel()))
    assert first['coverage']['processed_chunks']==1 and first['coverage']['remaining_chunks']>0
    second=asyncio.run(execute_live(request.model_copy(update={'resume_run_id':first['run_id'],'max_chunks':10}),EmptyModel()))
    assert second['coverage']['processed_chunks']>1 and second['coverage']['remaining_chunks']==0


def test_runtime_budget_settles_known_usage_keeps_uncertain_attempt(tmp_path):
    budget=EvaluationBudget(tmp_path/'budget.json',.2,settle=True)
    body={'messages':[],'max_tokens':3000}
    index=budget.reserve(body,'receive_prompt')
    budget.record(index,{'prompt_tokens':100,'completion_tokens':100})
    assert budget.committed()<.01
    budget.reserve(body,'extract_signals')
    assert budget.committed()>.09
    budget.reserve(body,'extract_signals')
    with pytest.raises(BudgetExceeded):
        budget.reserve(body,'extract_signals')


def test_pdf_tables_are_context_and_quotes_keep_page_offsets():
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=500,height=400)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 30 350 Td (Company / Safety record) Tj 0 -25 Td (Example Ltd / inspection programme announced.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream);buf=io.BytesIO();writer.write(buf)
    doc=clean_pdf(base64.b64encode(buf.getvalue()).decode(),'https://example.com/test.pdf')
    assert 'Example Ltd' in doc['text'] and doc['page_spans'][0]['method']=='pdf_text'
    assert doc['page_spans'][0]['end']==len(doc['text'])
    assert 'tables' in doc and doc['published_at'] is None


def test_rule_checks_do_not_invent_dates():
    signal={'evidence_level':'discovery_only','buyer_role_hypothesis':'Purchasing manager','recommended_next_step':'human_review',
            'ambiguity_flags':[],'need_hypothesis':'ISO 45001 valid until 2028'}
    assert len(rule_checks(signal,{'text':'A listed company.'}))==5


def test_repeated_quote_uses_target_chunk_page_not_first_occurrence():
    from app.tools import verify_evidence
    quote='Example Ltd announced a safety inspection programme.'
    text=quote+'\n\n'+quote
    second=len(quote)+2
    doc={'document_id':'d','text':text,'quote_span':{'start':second,'end':len(text)},
         'source_class':'company_owned','url':'https://example.com/','title':'Test',
         'published_at':None,'captured_at':'2026-10-25','content_hash':'x',
         'page_spans':[{'page':1,'start':0,'end':len(quote)},{'page':2,'start':second,'end':len(text)}]}
    result=verify_evidence([{'document_id':'d','excerpt':quote,'organisation':'Example Ltd','organisation_specific':True}],doc)
    assert result['accepted'][0]['evidence_start']==second
    assert result['accepted'][0]['evidence_pages']==[2]


def test_more_than_six_records_are_retained_and_review_can_reject(monkeypatch,isolated_runs):
    import re
    lines=[f'Company {n} announced a safety inspection programme.' for n in range(25)]
    async def fetch(self,url):
        return raw(url,'<main>'+'\n'.join(lines)+'</main>')
    monkeypatch.setattr(PublicFetcher,'fetch_public_page',fetch)
    class Model(EmptyModel):
        async def generate(self,stage,payload,schema):
            self.calls.append(stage)
            if stage=='receive_prompt':return plan()
            if stage=='review_signals':
                return SemanticReview(reviews=[ReviewItem(signal_id=i['signal']['signal_id'],verdict='uncertain',issues=['Case outcome needs verification']) for i in payload['items']])
            signals=[]
            for line in payload['document']['text'].splitlines():
                match=re.match(r'Company \d+',line)
                if not match:continue
                signals.append({'document_id':payload['document']['document_id'],'organisation':match[0],'organisation_specific':True,
                    'sector':None,'safety_topic':'inspection','trigger_type':'enforcement','workflow':'safety inspection','urgency':None,
                    'excerpt':line,'need_hypothesis':'Review announced workflow.','buyer_role_hypothesis':None,'confidence':.5,
                    'ambiguity_flags':[],'recommended_next_step':'research_more'})
            return ChunkExtraction(signals=signals,abstention_reason=None,coverage_complete=True,coverage_note=None)
    model=Model()
    result=asyncio.run(execute_live(RunRequest(prompt='Research safety inspections',additional_urls=['https://example.com/'],permissions_confirmed=True),model))
    assert len(result['signals'])==25
    assert [s['organisation'] for s in result['signals']]==[f'Company {n}' for n in range(25)]
    assert all(s['semantic_review']['status']=='uncertain' and s['eligible_for_scoring'] is False for s in result['signals'])
    assert model.calls.count('review_signals')==5
