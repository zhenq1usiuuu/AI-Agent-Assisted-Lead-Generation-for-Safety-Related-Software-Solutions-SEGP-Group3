import json
import asyncio

import pytest

from app.budget import EvaluationBudget
from app.tools import clean_html, discover_links, TOOLS


def test_wrapper_form_preserves_content_and_removes_controls():
    doc = clean_html('<form><input value="secret"><main><header><h1>Real article title</h1></header>'
                     '<p>A company announced safety inspections.</p><select><option>1950</option></select>'
                     '<div class="daterangepicker">Calendar noise</div></main></form>', 'https://example.com/')
    assert 'Real article title' in doc['text']
    assert 'company announced' in doc['text']
    assert all(s not in doc['text'] for s in ('secret', '1950', 'Calendar noise'))


def test_content_container_avoids_footer_and_keeps_missing_table_warning():
    doc = clean_html('<main><div id="page-content">Fatal Accident Case. Table not found.</div>'
                     '<div id="mainFooter">Contact and privacy boilerplate</div></main>', 'https://dosh.gov.my/')
    assert 'Contact' not in doc['text']
    assert 'missing_record_table' in doc['quality_warnings']


def test_link_ranking_ignores_domain_query_and_wrong_inspection_topic():
    links = [
        {'url': 'https://dosh.gov.my/privacy?topic=accident', 'label': 'Privacy'},
        {'url': 'https://dosh.gov.my/unrelated', 'label': 'Overview'},
        {'url': 'https://dosh.gov.my/fish', 'label': 'Waiver of inspection charges for export of fish'},
        {'url': 'https://dosh.gov.my/warehouse', 'label': 'Workplace safety inspection initiative'},
        {'url': 'https://dosh.gov.my/workers', 'label': 'Occupational safety at customs facilities'},
    ]
    assert discover_links(links, ['OSH', 'inspection', 'workplace safety', 'occupational safety'], 8) == [links[3]['url'], links[4]['url']]


def test_relevant_link_after_old_cap_is_not_lost():
    html = '<main>' + ''.join(f'<a href="/news/{i}">General news</a>' for i in range(120))
    html += '<a href="/safety">Workplace safety inspection</a></main>'
    doc = clean_html(html, 'https://example.com/')
    assert len(doc['links']) == 121
    assert discover_links(doc['links'], ['workplace safety'], 4) == ['https://example.com/safety']
    assert not doc['links_truncated']
    definition = next(t for t in TOOLS if t['function']['name'] == 'discover_links')
    assert definition['function']['parameters']['properties']['limit']['maximum'] == 32


def test_workplace_safety_aliases_match_labels_not_domains():
    links=[{'url':'https://example.com/event?id=1','label':'MEF OSH conference'},
           {'url':'https://dosh.gov.my/unrelated','label':'Overview'}]
    assert discover_links(links,['workplace safety'],4) == [links[0]['url']]


def test_budget_reservations_persist_and_failed_requests_still_count(tmp_path):
    ledger = tmp_path / 'budget.json'
    body = {'messages': [], 'max_tokens': 3000}
    budget = EvaluationBudget(ledger, .1)
    first = budget.reserve(body, 'receive_prompt')
    with pytest.raises(ValueError, match='budget guard'):
        EvaluationBudget(ledger, .1).reserve(body, 'receive_prompt')
    budget.record(first, {'prompt_tokens': 1000, 'completion_tokens': 100,
                          'prompt_tokens_details': {'cached_tokens': 500}})
    data = json.loads(ledger.read_text())
    assert data['calls'][0]['estimated_usd_default_cache'] == pytest.approx(.00315)
    assert data['reserved_usd'] > .09


def test_missing_table_skips_paid_extraction_and_reports_error(monkeypatch):
    from app.pipeline import execute
    from app.models import Plan, RunRequest
    from app.tools import PublicFetcher
    class LLM:
        model = 'mock'
        usage = []
        async def generate(self, stage, payload, schema):
            assert stage == 'receive_prompt', 'Missing table must not trigger paid extraction'
            return Plan(status='ready',workflow='safety inspections',country='Malaysia',sectors=[],
                        keywords=['OSH'],seed_ids=['dosh_fatal_cases'],questions=[],assumptions=[])
    async def fetch(self, url):
        return {'html':'<div class="entry-content">Fatal Accident Case. Table not found.</div>',
                'url':url,'requested_url':url,'content_hash':'x'*64,'captured_at':'2026-10-25','http_status':200}
    monkeypatch.setattr(PublicFetcher, 'fetch_public_page', fetch)
    result = asyncio.run(execute(RunRequest(prompt='Research safety inspections',permissions_confirmed=True),llm=LLM()))
    assert result['documents'][0]['evidence_scope'] == 'context_only'
    assert result['stages']['extract_signals']['status'] == 'skipped'
    assert result['errors'] and 'Table not found' in result['errors'][0]['message']
