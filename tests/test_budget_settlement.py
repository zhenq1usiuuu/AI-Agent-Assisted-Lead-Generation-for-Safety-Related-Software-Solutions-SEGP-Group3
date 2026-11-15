import pytest
from app.budget import EvaluationBudget, COST_REPORTER
from app.content import prioritize_chunks


def test_cache_breakdown_releases_reservation_and_survives_reload(tmp_path):
    path = tmp_path/'budget.json'
    budget = EvaluationBudget(path, .2)
    body = {'messages':[], 'max_tokens':3000}
    i = budget.reserve(body, 'extract_signals')
    budget.record(i, {'prompt_tokens':1000, 'completion_tokens':100,
        'prompt_tokens_details':{'cached_tokens':600, 'cache_write_tokens':300}},
        headers={'msh-usage-cache-write-tokens-5m':'100', 'msh-usage-cache-write-tokens-1h':'200'})
    expected = (100*3+600*.3+100*3+200*6+100*15)/1000000
    assert budget.committed() == pytest.approx(expected)
    assert budget.data['calls'][i]['released_usd'] > 0
    assert EvaluationBudget(path, .2).committed() == pytest.approx(expected)
    assert budget.data['calls'][i]['billing_tokens']['uncached'] == 100


@pytest.mark.parametrize('usage', [{}, {'prompt_tokens':100},
    {'prompt_tokens':100,'completion_tokens':10,'prompt_tokens_details':{'cached_tokens':101}},
    {'prompt_tokens':-1,'completion_tokens':10}])
def test_incomplete_usage_retains_reservation(tmp_path, usage):
    b = EvaluationBudget(tmp_path/'budget.json', 1)
    i = b.reserve({'max_tokens':100}, 'test')
    before = b.committed()
    b.record(i, usage)
    assert b.committed() == before
    assert 'settled_usd' not in b.data['calls'][i]


def test_terminal_distinguishes_estimate_and_next_reservation(tmp_path):
    messages = []
    token = COST_REPORTER.set(messages.append)
    try:
        b = EvaluationBudget(tmp_path/'budget.json', 1)
        i = b.reserve({'max_tokens':100}, 'test')
        b.record(i, {'prompt_tokens':100,'completion_tokens':10})
    finally:
        COST_REPORTER.reset(token)
    assert 'Estimated used:' in messages[0] and 'Next reservation:' in messages[0]
    assert 'Next reservation: $0.000000' in messages[-1]


def test_categories_get_first_pass_before_repeat():
    docs = [{'document_id':'a','source_class':'esg_index','text':'Berhad '*20},
            {'document_id':'b','source_class':'regulatory','text':'accident'}]
    chunks = [{'document_id':'a','start':0,'end':30},
              {'document_id':'a','start':30,'end':60},
              {'document_id':'b','start':0,'end':8}]
    ordered = prioritize_chunks(chunks, docs)
    assert {c['document_id'] for c in ordered[:2]} == {'a','b'}
    assert len(ordered) == len(chunks)
