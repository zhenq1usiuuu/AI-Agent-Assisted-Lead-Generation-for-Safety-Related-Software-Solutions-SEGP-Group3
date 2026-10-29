import asyncio
import socket
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi.testclient import TestClient
from app.context import PREFIX, PREFIX_HASH
from app.main import app
from app.models import RunRequest, Plan, Extraction
from app.pipeline import execute
from app.tools import PublicFetcher, clean_html, public_ip, verify_evidence

def test_demo_end_to_end_and_no_network(monkeypatch):
    async def no_network(*args, **kwargs):
        raise AssertionError('Demo must not access network')
    monkeypatch.setattr(httpx.AsyncClient,'send',no_network)
    result=asyncio.run(execute(RunRequest(prompt='Test safety inspections',mode='demo')))
    assert result['status']=='completed'
    assert result['is_synthetic']
    assert len(result['signals'])==1
    doc=result['documents'][0]
    signal=result['signals'][0]
    assert doc['text'][signal['evidence_start']:signal['evidence_end']]==signal['excerpt']
    assert signal['source_url']==doc['url']
    assert result['not_executed']==['filter_score','llm_reasoning','customer_review','write_to_crm']

def test_html_removes_instructions_in_scripts_and_preserves_metadata():
    doc=clean_html('<html lang="ms"><head><title>Test</title><meta name="date" content="2026-01-02"></head><body><nav>junk</nav><main><h1>Heading</h1><p>Inspection evidence</p><script>leak_secret()</script></main></body></html>','https://example.com/')
    assert 'leak_secret' not in doc['text'] and 'junk' not in doc['text']
    assert doc['language']=='ms'
    assert doc['published_at']=='2026-01-02'
    assert doc['date_evidence']['verified'] is False

def test_fabricated_evidence_is_rejected():
    doc={'document_id':'one','text':'A real evidence sentence about inspection.'}
    result=verify_evidence([{'document_id':'one','excerpt':'Invented quotation'}],doc)
    assert result['accepted']==[]
    assert result['rejected'][0]['reason']=='excerpt_not_exact'

def test_modified_date_is_not_publication_date():
    doc=clean_html('<head><meta property="article:modified_time" content="2026-10-01"></head><main>Inspection</main>','https://example.com/')
    assert doc['published_at'] is None
    assert doc['updated_at']=='2026-10-01'

def test_live_orchestration_with_mocked_model_and_fetch(monkeypatch):
    from app.pipeline import DEMO_HTML
    calls=[]
    class FakeLLM:
        model='mock-kimi'; usage=[]
        async def generate(self,stage,payload,schema):
            calls.append(stage)
            if stage=='receive_prompt':
                return Plan(status='ready',workflow='inspection',country='Malaysia',sectors=[],keywords=['inspection'],seed_ids=['dosh_prosecution'],questions=[],assumptions=[])
            doc=payload['document']
            return Extraction(signals=[{'document_id':doc['document_id'],'organisation':None,'organisation_specific':False,
                'sector':None,'safety_topic':'inspection','trigger_type':'guidance','workflow':'inspection','urgency':None,
                'excerpt':'The HSE team is reviewing inspection evidence and follow-up records.',
                'need_hypothesis':'Review evidence workload','buyer_role_hypothesis':None,'confidence':0.5,
                'ambiguity_flags':['generic_context'],'recommended_next_step':'research_more'}],abstention_reason=None)
    async def fetch(self,url):
        return {'html':DEMO_HTML,'url':url,'requested_url':url,'content_hash':'a'*64,
            'captured_at':'2026-10-16T00:00:00Z','http_status':200}
    monkeypatch.setattr(PublicFetcher,'fetch_public_page',fetch)
    result=asyncio.run(execute(RunRequest(prompt='inspection workflow',permissions_confirmed=True,max_pages=1),llm=FakeLLM()))
    assert calls==['receive_prompt','extract_signals']
    assert result['status']=='completed' and not result['is_synthetic']
    assert result['signals'][0]['organisation'] is None
    assert [t['tool'] for t in result['tool_trace']]==['fetch_public_page','clean_html','inspect_public_source','discover_links','verify_evidence']

def test_entity_must_appear_in_cited_excerpt():
    doc={'document_id':'one','text':'A real evidence sentence about inspection.'}
    result=verify_evidence([{'document_id':'one','excerpt':doc['text'],'organisation':'Imaginary Corp'}],doc)
    assert result['rejected'][0]['reason']=='organisation_not_in_cited_excerpt'

@pytest.mark.parametrize('address',['127.0.0.1','10.0.0.1','169.254.169.254','::1','0.0.0.0'])
def test_ssrf_private_addresses_rejected(address,monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,0))])
    with pytest.raises(ValueError,match='blocked'): asyncio.run(public_ip('untrusted.example'))

def test_cross_host_redirect_blocked():
    async def check():
        fetcher=PublicFetcher(['example.com'],True)
        fetcher.robots_allowed=AsyncMock(return_value='robots_checked')
        fetcher.request=AsyncMock(return_value=(302,{'location':'http://127.0.0.1/private'},b''))
        try:
            with pytest.raises(ValueError,match='downgrade|allowlist'):
                await fetcher.fetch_public_page('https://example.com/')
            assert fetcher.request.await_count==1
        finally: await fetcher.close()
    asyncio.run(check())

def test_robots_same_host_redirect_supported():
    async def check():
        fetcher=PublicFetcher(['example.com'],True)
        fetcher.request=AsyncMock(side_effect=[
            (301,{'location':'/my/robots.txt/'},b''),
            (200,{'content-type':'text/plain'},b'User-agent: *\nDisallow: /private')])
        try:
            assert await fetcher.robots_allowed('https://example.com/public')=='robots_checked'
            assert fetcher.request.await_args_list[1].args[0]=='https://example.com/my/robots.txt/'
        finally: await fetcher.close()
    asyncio.run(check())

def test_robots_denial_prevents_page_fetch():
    async def check():
        fetcher=PublicFetcher(['example.com'],True)
        fetcher.request=AsyncMock(return_value=(200,{'content-type':'text/plain'},b'User-agent: *\nDisallow: /\n'))
        try:
            with pytest.raises(ValueError,match='robots_disallowed'):
                await fetcher.fetch_public_page('https://example.com/private')
            assert fetcher.request.await_count==1
        finally: await fetcher.close()
    asyncio.run(check())

def test_clarification_skips_network():
    class FakeLLM:
        model='test-kimi'; usage=[]
        async def generate(self,stage,payload,schema):
            assert stage=='receive_prompt'
            return Plan(status='needs_clarification',workflow='',country='Malaysia',sectors=[],keywords=[],seed_ids=[],questions=['Please select one safety workflow'],assumptions=[])
    result=asyncio.run(execute(RunRequest(prompt='Help me find potential customers',permissions_confirmed=True),llm=FakeLLM()))
    assert result['status']=='needs_clarification'
    assert result['documents']==[] and result['tool_trace']==[]

def test_api_contract_and_local_origin():
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        assert client.get('/static/app.js').status_code==200
        context=client.get('/api/context').json()
        assert context['system_prefix']==PREFIX and context['sha256']==PREFIX_HASH
        assert len(context['tools'])==6
        assert client.post('/api/run',json={'prompt':'test prompt'}).status_code==400
        result=client.post('/api/run',json={'prompt':'test prompt','mode':'demo'})
        assert result.status_code==200 and result.json()['signals']
        assert client.post('/api/run',headers={'Origin':'https://evil.example'},json={'prompt':'test prompt','mode':'demo'}).status_code==403

def test_kimi_payload_has_stable_prefix_tools_and_strict_schema(monkeypatch):
    from app.llm import KimiClient
    captured=[]
    monkeypatch.setenv('MOONSHOT_API_KEY','fake-test-value')
    plan=Plan(status='ready',workflow='inspection',country='Malaysia',sectors=[],keywords=['inspection'],seed_ids=['dosh_prosecution'],questions=[],assumptions=[])
    async def send(self, request, **kwargs):
        import json
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':plan.model_dump_json()}}],'usage':{'prompt_tokens':10}},request=request)
    monkeypatch.setattr(httpx.AsyncClient,'send',send)
    client=KimiClient()
    asyncio.run(client.generate('receive_prompt',{'prompt':'inspection'},Plan))
    body=captured[0]
    assert body['model']=='kimi-k3'
    assert body['messages'][0]=={'role':'system','content':PREFIX}
    assert body['tool_choice']=='none' and len(body['tools'])==6
    assert body['response_format']['json_schema']['strict']
    assert 'fake-test-value' not in str(body)
