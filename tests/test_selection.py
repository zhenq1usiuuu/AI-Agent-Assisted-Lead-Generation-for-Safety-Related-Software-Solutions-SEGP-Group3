import json

import pytest

from app import cli
from app.models import RunRequest
from app.selection import CATEGORIES, build_selection, parse_categories
from app.sources import SOURCES


def test_categories_cover_exactly_all_evidence_sources():
    prompt, ids, budget = build_selection(parse_categories('ALL'))
    assert set(ids) == {s['id'] for s in SOURCES if s['purpose'] == 'lead_evidence'}
    assert len(ids) == len(set(ids)) == 16
    assert budget == 20
    assert len(prompt) <= 6000
    RunRequest(prompt=prompt, source_ids=ids, max_pages=budget)
    assert parse_categories('0') == list(CATEGORIES)


def test_selection_parsing_and_scope():
    assert parse_categories('1, esg; 1 FMM') == ['DOSH', 'ESG', 'FMM']
    _, ids, _ = build_selection(parse_categories('ESG'))
    assert ids == ['ftse4good_factsheet', 'bursa_indices']
    for invalid in ('', 'unknown', '9', 'ALL unknown'):
        with pytest.raises(ValueError):
            parse_categories(invalid)


def test_one_shot_categories_pass_exact_scope_and_budget(monkeypatch, capsys):
    requests = []
    async def run(request):
        requests.append(request)
        return {'status': 'completed', 'signals': []}
    monkeypatch.setattr(cli, 'run_task', run)
    assert cli.main(['--categories', 'ALL', '--max-pages', '24']) == 0
    assert len(requests[0].source_ids) == 16
    assert requests[0].max_pages == 24
    assert 'quarantine' in requests[0].prompt
    assert json.loads(capsys.readouterr().out)['status'] == 'completed'


def test_invalid_and_conflicting_selections_do_not_run(monkeypatch, capsys):
    async def unexpected(request):
        pytest.fail('Invalid selection must not run')
    monkeypatch.setattr(cli, 'run_task', unexpected)
    assert cli.main(['--categories', 'unknown']) == 1
    assert cli.main(['--categories', 'ALL', '--source', 'mtuc']) == 1
    assert cli.main(['--source', 'mtuc']) == 1


def test_interactive_reprompts_then_runs_selected_scope(monkeypatch, capsys):
    requests = []
    async def run(request):
        requests.append(request)
        return {'status': 'completed', 'signals': []}
    monkeypatch.setattr(cli, 'run_task', run)
    answers = iter(['', 'wrong', '1,3', '', 'exit'])
    monkeypatch.setattr('builtins.input', lambda message: next(answers))
    assert cli.main(['--mode', 'demo']) == 0
    assert len(requests) == 1
    assert len(requests[0].source_ids) == 5
    output = capsys.readouterr()
    assert 'Generated research prompt:' in output.out
    assert 'Unknown category' in output.err
