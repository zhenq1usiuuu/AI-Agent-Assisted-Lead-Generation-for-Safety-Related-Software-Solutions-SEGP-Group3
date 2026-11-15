import asyncio
import sqlite3

from app.contact_discovery import ContactExtraction, contact_page, discover_automatic
from app.contacts import review_contacts
from app.crm import write_review
from test_workflow_crm import review_record


def test_footer_and_contact_links_preserved():
    text, links = contact_page('<footer>Company<a href="mailto:info@example.com">Email</a>'
        '<a href="tel:+60123456789">Call</a><a href="/contact">Contact</a>'
        '<a href="https://other.example/contact">Other</a></footer>', 'https://example.com/')
    assert 'info@example.com' in text and '+60123456789' in text
    assert links == ['https://example.com/contact']


def test_discovery_review_and_real_sqlite(tmp_path):
    class Fetcher:
        def __init__(self, hosts, allowed):
            assert hosts == ['example.com'] and allowed
        async def fetch_public_page(self, url):
            return {'url':url, 'captured_at':'2026-11-13T00:00:00+00:00',
                'html':'<footer>Company: Alice Smith, Safety Manager, alice@example.com</footer>'}
        async def close(self): pass
    class Model:
        usage = []
        async def generate(self, stage, payload, schema):
            assert stage == 'extract_contacts'
            return ContactExtraction(contacts=[dict(kind='person', name='Alice Smith',
                job_title='Safety Manager', work_email='alice@example.com', phone=None,
                excerpt='Company: Alice Smith, Safety Manager, alice@example.com', company_excerpt='Company')])
    async def search(name):
        return [{'url':'https://example.com/contact', 'title':name}]
    review = review_record()
    result = asyncio.run(discover_automatic(review['crm_queue'], None, ask=lambda _: '1',
        say=lambda _:None, search=search, model=Model(), fetcher_factory=Fetcher))
    assert len(result['candidates']) == 1 and not result['errors']
    answers = iter(['y', 'Public business directory, research storage', 'unknown'])
    review['contact_reviews'] = review_contacts(result, 'Tester', lambda _:next(answers), lambda _:None)
    db = tmp_path/'crm.db'
    write_review(review, db)
    with sqlite3.connect(db) as conn:
        assert conn.execute('select name,work_email,do_not_contact from contacts').fetchone() == ('Alice Smith','alice@example.com',1)


def test_search_failure_does_not_invent_contacts():
    async def search(name): raise ValueError('Search unavailable')
    result = asyncio.run(discover_automatic([{'account_key':'c','display_name':'Company'}], None,
        search=search, say=lambda _:None))
    assert result['candidates'] == [] and result['errors']


def test_skipped_host_is_never_fetched():
    async def search(name): return [{'url':'https://example.com/', 'title':name}]
    def forbidden(*args): raise AssertionError('No permission to fetch')
    result = asyncio.run(discover_automatic([{'account_key':'c','display_name':'Company'}], None,
        search=search, ask=lambda _:'', say=lambda _:None, fetcher_factory=forbidden))
    assert result['unprocessed_accounts'] == ['c'] and result['pages'] == []


def test_full_workflow_calls_automatic_discovery(tmp_path, monkeypatch):
    import app.workflow as workflow
    import app.contact_discovery as discovery
    from app.models import RunRequest
    calls = []
    async def automatic(companies, budget, **kwargs):
        calls.append(companies)
        return {'candidates':[], 'errors':[]}
    monkeypatch.setattr(discovery, 'discover_automatic', automatic)
    monkeypatch.setattr(workflow, 'run_review', lambda *args, **kwargs:review_record())
    answers = iter(['y', 'y'])
    initial = {'run_id':'test', 'status':'completed', 'signals':[],
               'filter_score':{'accounts':[]}, 'llm_reasoning':{'accounts':[]}}
    asyncio.run(workflow.run_workflow(RunRequest(prompt='Find safety leads'), 'Tester',
        tmp_path/'workflow', tmp_path/'crm.db', max_rounds=1, initial=initial,
        ask=lambda _:next(answers), say=lambda _:None, auto_contacts=True))
    assert len(calls) == 1
    assert (tmp_path/'workflow'/'round-1-contacts.json').exists()
    with sqlite3.connect(tmp_path/'crm.db') as conn:
        assert conn.execute('select count(*) from leads').fetchone()[0] == 1
