import json

from app import cli


def test_one_shot_demo_prints_json_and_saves_utf8(tmp_path, capsys):
    output = tmp_path / 'result.json'
    code = cli.main(['--mode', 'demo', '--prompt', 'Test safety inspection research', '--output', str(output)])
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert code == 0
    assert result['is_synthetic'] is True
    assert result['signals']
    assert json.loads(output.read_text(encoding='utf-8')) == result
    assert 'Running:' in captured.err


def test_existing_output_is_not_overwritten_or_executed(tmp_path, monkeypatch, capsys):
    output = tmp_path / 'existing.json'
    output.write_text('original', encoding='utf-8')
    async def unexpected(*args, **kwargs):
        raise AssertionError('Existing output must be checked before running the pipeline')
    monkeypatch.setattr(cli, 'execute', unexpected)
    assert cli.main(['--mode', 'demo', '--prompt', 'Test research', '--output', str(output)]) == 1
    assert output.read_text() == 'original'
    assert 'already exists' in capsys.readouterr().err


def test_prompt_file_accepts_powershell_utf8_bom(tmp_path, capsys):
    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('Research inspections\nKeep evidence verbatim.', encoding='utf-8-sig')
    assert cli.main(['--mode', 'demo', '--prompt-file', str(prompt)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'completed'


def test_interactive_demo_repeats_until_exit(monkeypatch, capsys):
    answers = iter(['ESG', '', 'exit'])
    monkeypatch.setattr('builtins.input', lambda message: next(answers))
    assert cli.main(['--mode', 'demo']) == 0
    captured = capsys.readouterr()
    assert 'Interactive terminal' in captured.out
    assert '"is_synthetic": true' in captured.out
    assert 'Goodbye.' in captured.err


def test_interactive_clarification_preserves_request(monkeypatch, capsys):
    monkeypatch.setenv('MOONSHOT_API_KEY', 'fake-test-key')
    requests = []
    async def fake_execute(request):
        requests.append(request)
        if len(requests) == 1:
            return {'status':'needs_clarification', 'stages':{'receive_prompt':{'result':{'questions':['Which workflow?']}}}}
        return {'status':'completed', 'signals':[]}
    monkeypatch.setattr(cli, 'execute', fake_execute)
    answers = iter(['DOSH,ESG', '', 'Safety inspection records', '', 'exit'])
    monkeypatch.setattr('builtins.input', lambda message: next(answers))
    assert cli.main(['--mode', 'live', '--permissions-confirmed']) == 0
    assert len(requests) == 2
    assert requests[1].prompt == requests[0].prompt + '\nAdditional details: Safety inspection records'
    assert requests[1].source_ids == requests[0].source_ids
    assert 'DOSH, ESG' in requests[0].prompt
    assert requests[1].permissions_confirmed is True
    assert 'Which workflow?' in capsys.readouterr().out


def test_live_requires_key_without_network(monkeypatch, capsys):
    monkeypatch.setenv('MOONSHOT_API_KEY', '')
    assert cli.main(['--prompt', 'Research inspection evidence', '--permissions-confirmed']) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'MOONSHOT_API_KEY' in json.loads(captured.err)['message']
