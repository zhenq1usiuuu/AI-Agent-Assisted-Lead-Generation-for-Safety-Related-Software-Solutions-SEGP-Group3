import asyncio
import json
import pytest
from app.reasoning_client import parse_reasoning_output, ReasoningStructureError
from app.reasoning import reason_result


def response():
    return {"summary": "Candidates require further research.", "accounts": [{
        "account_key": "example", "display_name": "Example",
        "reason": "Further evidence is required.", "risks": [],
        "supporting_signal_ids": [], "next_step_hypothesis": "Research further",
        "open_questions": ["Is there workflow evidence?"]}]}


def test_misplaced_questions_are_moved_without_changing_analysis():
    raw = response()
    parsed, repaired = parse_reasoning_output(json.dumps(raw))
    assert repaired
    assert parsed.open_questions == raw['accounts'][0]['open_questions']
    expected = dict(raw['accounts'][0]); expected.pop('open_questions')
    assert parsed.accounts[0].model_dump() == expected


@pytest.mark.parametrize('kind', ['conflict', 'wrong_type', 'other_extra', 'missing'])
def test_ambiguous_or_unrelated_errors_are_rejected(kind):
    raw = response()
    if kind == 'conflict': raw['open_questions'] = ['Root question']
    if kind == 'wrong_type': raw['accounts'][0]['open_questions'] = 'not a list'
    if kind == 'other_extra': raw['accounts'][0]['score'] = 100
    if kind == 'missing': del raw['accounts'][0]['open_questions']
    with pytest.raises(ReasoningStructureError):
        parse_reasoning_output(json.dumps(raw))


def test_schema_retry_is_bounded():
    class InvalidClient:
        usage = []
        calls = 0
        def validate_distinct_model(self): pass
        async def generate(self, payload):
            self.calls += 1
            if self.calls > 1: assert 'open_questions' in payload['correction_note']
            raise ReasoningStructureError('Invalid JSON')
    client = InvalidClient()
    with pytest.raises(ReasoningStructureError, match='after 3 attempts'):
        asyncio.run(reason_result({'signals': [], 'documents': []}, client=client))
    assert client.calls == 3
