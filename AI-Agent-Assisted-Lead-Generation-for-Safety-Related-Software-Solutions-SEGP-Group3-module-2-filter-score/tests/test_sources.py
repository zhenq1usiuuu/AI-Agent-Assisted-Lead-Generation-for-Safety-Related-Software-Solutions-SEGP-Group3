import asyncio
import base64
import io
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from app import cli
from app.context import PREFIX
from app.models import Plan, RunRequest, Extraction
from app.pipeline import execute
from app.sources import SOURCES, SOURCE_BY_ID, SeedId, ROOT
from app.tools import PublicFetcher, clean_pdf, verify_evidence


def plan(ids):
    return Plan(status='ready', workflow='safety inspections', country='Malaysia', sectors=['manufacturing'],
                keywords=['inspection'], seed_ids=ids, questions=[], assumptions=[])


def pdf_fixture():
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 50 750 Td (Example Manufacturing plans digital safety inspection records.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return base64.b64encode(output.getvalue()).decode('ascii')


def test_registry_schema_context_and_docs_agree():
    expected = {s['id'] for s in SOURCES if s['purpose']=='lead_evidence'}
    assert {s.value for s in SeedId} == expected
    schema = Plan.model_json_schema()
    assert set(schema['$defs']['SeedId']['enum']) == expected
    assert {'sirim_directory','ftse4good_factsheet','dosh_prosecution','fmm_newsroom','cicm'} <= expected
    assert not {'dosh_portal','dosh_heights','twenty_api','jheks'} & expected
    guide = (ROOT/'SOURCES.md').read_text(encoding='utf-8')
    for source in SOURCES:
        assert source['url'] in PREFIX
        assert f"`{source['id']}`" in guide
        assert source['url'] in guide
    with pytest.raises(ValidationError):
        plan(['dosh_portal'])
    with pytest.raises(ValidationError):
        RunRequest(prompt='Research inspections', source_ids=['twenty_api'])


def test_explicit_context_source_overrides_model_and_skips_extraction(monkeypatch):
    calls=[]
    class LLM:
        model='mock'; usage=[]
        async def generate(self, stage, payload, schema):
            calls.append(stage)
            assert payload['requested_source_ids'] == ['sirim_directory']
            return plan(['dosh_prosecution'])
    async def fetch(self,url):
        assert url == SOURCE_BY_ID['sirim_directory']['url']
        return {'html':'<main>SIRIM directory landing page. Search the form for actual certificate records.</main>',
                'url':url,'requested_url':url,'content_hash':'b'*64,'captured_at':'2026-10-21T00:00:00Z', 'http_status':200}
    monkeypatch.setattr(PublicFetcher,'fetch_public_page',fetch)
    result=asyncio.run(execute(RunRequest(prompt='Research certificate evidence', permissions_confirmed=True,
                                         source_ids=['sirim_directory']), llm=LLM()))
    assert calls == ['receive_prompt']
    assert result['documents'][0]['evidence_scope']=='context_only'
    assert result['signals']==[]
    assert result['stages']['extract_signals']['abstentions']
    assert result['stages']['extract_signals']['status']=='skipped'
    assert result['stages']['extract_signals']['executor']=='not_run'
    assert result['source_decisions'][0]['source_id']=='sirim_directory'


@pytest.mark.parametrize('via_url', [False, True, 'query_variant'])
def test_manual_dashboard_wrapper_remains_context_only(monkeypatch, via_url):
    class LLM:
        model='mock'; usage=[]
        async def generate(self, stage, payload, schema):
            assert stage=='receive_prompt'
            return plan([] if via_url else ['dosh_enforcement_dashboard'])
    fetch=AsyncMock(return_value={'html':'<main>Public dashboard shell without accessible company records.</main>', 'url':SOURCE_BY_ID['dosh_enforcement_dashboard']['url'], 'content_hash':'e'*64, 'captured_at':'2026-10-25', 'http_status':200})
    monkeypatch.setattr(PublicFetcher,'fetch_public_page',fetch)
    request=RunRequest(prompt='Research case evidence', permissions_confirmed=True,
                       additional_urls=[SOURCE_BY_ID['dosh_enforcement_dashboard']['url'] + ('?view=companies' if via_url=='query_variant' else '')] if via_url else [])
    result=asyncio.run(execute(request, llm=LLM()))
    assert fetch.await_count==1
    assert result['status']=='partial'
    assert result['documents'][0]['evidence_scope']=='context_only'
    assert result['source_decisions'][0]['status']=='requires_connector'
    assert result['errors'] and not result['signals']


def test_pdf_extraction_and_page_provenance():
    doc=clean_pdf(pdf_fixture(), 'https://example.com/factsheet.pdf')
    assert 'Example Manufacturing' in doc['text']
    assert doc['published_at'] is None
    assert doc['pdf_page_count']==1 and doc['page_spans'][0]['page']==1
    doc.update(document_id='pdf', source_class='esg_index', source_id='ftse4good_factsheet', evidence_scope='records',
               url='https://example.com/factsheet.pdf', captured_at='2026-10-21T00:00:00Z', content_hash='c'*64)
    signal={'document_id':'pdf','organisation':'Example Manufacturing','organisation_specific':True,'excerpt':doc['text']}
    checked=verify_evidence([signal],doc)
    assert checked['accepted'][0]['evidence_pages']==[1]


def test_pdf_pipeline_uses_real_cleaner(monkeypatch):
    class LLM:
        model='mock'; usage=[]
        async def generate(self, stage, payload, schema):
            if stage=='receive_prompt': return plan(['ftse4good_factsheet'])
            assert payload['document']['pdf_page_count']==1
            return Extraction(signals=[], abstention_reason='No qualifying buyer evidence in this test fixture.')
    async def fetch(self,url):
        return {'pdf_base64':pdf_fixture(), 'format':'pdf', 'url':url, 'requested_url':url,
                'content_hash':'d'*64,'captured_at':'2026-10-21T00:00:00Z','http_status':200}
    monkeypatch.setattr(PublicFetcher,'fetch_public_page',fetch)
    result=asyncio.run(execute(RunRequest(prompt='Research industrial candidates', permissions_confirmed=True),llm=LLM()))
    assert result['status']=='completed'
    assert result['documents'][0]['source_class']=='esg_index'
    assert 'clean_pdf' in [item['tool'] for item in result['tool_trace']]
    assert 'pdf_base64' not in result['documents'][0]


def test_fetch_tool_accepts_pdf_transport_without_decoding_as_html():
    async def check():
        fetcher=PublicFetcher(['example.com'],True)
        fetcher.robots_allowed=AsyncMock(return_value='robots_checked')
        fetcher.request=AsyncMock(return_value=(200, {'content-type':'application/pdf'},base64.b64decode(pdf_fixture())))
        try:
            result=await fetcher.fetch_public_page('https://example.com/factsheet.pdf')
            assert result['format']=='pdf' and 'html' not in result
            assert clean_pdf(result['pdf_base64'],result['url'])['pdf_page_count']==1
        finally:
            await fetcher.close()
    asyncio.run(check())


def test_context_only_signals_rejected_before_provenance():
    checked=verify_evidence([{'document_id':'a'}],{'evidence_scope':'context_only'})
    assert checked['accepted']==[]
    assert checked['rejected'][0]['reason']=='context_page_is_not_company_evidence'


def test_cli_list_sources_needs_no_key_or_network(monkeypatch,capsys):
    monkeypatch.setenv('MOONSHOT_API_KEY','')
    assert cli.main(['--list-sources'])==0
    result=json.loads(capsys.readouterr().out)
    assert len(result['sources'])==len(SOURCES)
    assert any(s['connector']=='manual' for s in result['sources'])
