"""Offline tests for Module 3. No second-model API requests are made."""
import asyncio
import json
from datetime import date

import httpx

from app.reasoning import apply_reasoning, reason_result, render_report
from app.reasoning_client import ReasoningClient
from app.reasoning_models import ReasonedAccount, ReasoningOutput
from app.scoring import score_result


NOW = date(2026, 10, 26)


def signal(**overrides):
    base = {
        "signal_id": "s1",
        "document_id": "d1",
        "organisation": "Example Plastics Sdn Bhd",
        "organisation_specific": True,
        "sector": "plastics manufacturing",
        "safety_topic": "vehicle-pedestrian safety",
        "trigger_type": "enforcement",
        "workflow": "forklift pedestrian proximity monitoring",
        "urgency": None,
        "excerpt": "Example Plastics Sdn Bhd was fined RM50,000 for lacking pedestrian walkways.",
        "need_hypothesis": "Forklift and pedestrian separation may benefit from monitoring.",
        "buyer_role_hypothesis": "Safety & Health Officer",
        "confidence": 0.9,
        "ambiguity_flags": [],
        "recommended_next_step": "human_review",
        "source_url": "https://example.com/a",
        "source_class": "regulatory",
        "published_at": "2026-08-01",
        "evidence_level": "requires_semantic_review",
        "eligible_for_scoring": True,
        "semantic_review": {"status": "supported"},
        "rule_checks": [],
    }
    base.update(overrides)
    return base


def scored_result(*signals):
    result = {
        "run_id": "run-1",
        "request": {"prompt": "Research Malaysian manufacturing safety workflows."},
        "signals": list(signals),
        "stages": {},
        "not_executed": ["filter_score", "llm_reasoning", "customer_review", "write_to_crm"],
    }
    result["filter_score"] = score_result(result, NOW)
    return result


class FakeReasoningClient:
    model = "gpt-4o-mini"
    base_url = "https://api.openai.com/v1"
    usage = []

    def __init__(self):
        self.payload = None

    def validate_distinct_model(self):
        return None

    async def generate(self, payload):
        self.payload = payload
        account = payload["accounts"][0]
        return ReasoningOutput(
            summary="One manufacturing account shows a strong enforcement signal and should be reviewed first.",
            accounts=[
                ReasonedAccount(
                    account_key=account["account_key"],
                    display_name="ignored model name",
                    recommendation="shortlist",
                    reason=(
                        "The account has a high enforcement-trigger score, manufacturing fit "
                        "and review-supported evidence."
                    ),
                    risks=["The cited fine does not prove current non-compliance."],
                    supporting_signal_ids=account["all_signal_ids"][:1],
                    next_step_hypothesis="Confirm current status before customer review.",
                )
            ],
            open_questions=["Has the enforcement matter been resolved?"],
        )


def test_reasoning_stage_uses_the_second_model_and_preserves_evidence():
    result = scored_result(signal())
    fake = FakeReasoningClient()
    reasoning = asyncio.run(reason_result(result, client=fake))

    assert reasoning["model"] == "gpt-4o-mini"
    assert reasoning["accounts"][0]["display_name"] == "Example Plastics Sdn Bhd"
    assert reasoning["accounts"][0]["supporting_signal_ids"] == ["s1"]
    assert "enforcement" in fake.payload["accounts"][0]["signals"][0]["trigger_type"]
    assert reasoning["input_prompt_sha256"]

    combined = apply_reasoning(result, reasoning)
    assert combined["stages"]["llm_reasoning"]["status"] == "completed"
    assert combined["stages"]["llm_reasoning"]["model_role"] == "second_model"
    assert "llm_reasoning" not in combined["not_executed"]
    assert combined["not_executed"] == ["customer_review", "write_to_crm"]

    report = render_report(combined)
    assert "Example Plastics Sdn Bhd" in report
    assert "SHORTLIST" in report
    assert "Why:" in report
    assert "RM50,000" in report
    assert "human-review aid" in report


def test_reasoning_client_sends_strict_schema_without_exposing_the_key(monkeypatch):
    monkeypatch.setenv("MOONSHOT_MODEL", "kimi-k3")
    monkeypatch.setenv("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1")
    monkeypatch.setenv("REASONING_API_KEY", "fake-second-key")
    monkeypatch.setenv("REASONING_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("REASONING_MODEL", "gpt-4o-mini")
    captured = []
    output = ReasoningOutput(
        summary="No candidates were selected in this short test.",
        accounts=[],
        open_questions=[],
    )

    async def send(self, request, **kwargs):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": output.model_dump_json()},
                    }
                ],
                "usage": {"prompt_tokens": 10},
            },
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    client = ReasoningClient()
    result = asyncio.run(client.generate({"stage": "llm_reasoning", "accounts": []}))
    assert result.summary == output.summary
    body = captured[0]
    assert body["model"] == "gpt-4o-mini"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["messages"][0]["role"] == "system"
    assert "fake-second-key" not in json.dumps(body)


def test_second_model_must_differ_from_kimi(monkeypatch):
    monkeypatch.setenv("MOONSHOT_MODEL", "kimi-k3")
    monkeypatch.setenv("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1")
    client = ReasoningClient(
        key="fake-key",
        model="kimi-k3",
        base_url="https://api.moonshot.ai/v1",
    )
    try:
        client.validate_distinct_model()
    except ValueError as exc:
        assert "different model" in str(exc)
    else:
        raise AssertionError("Same-model configuration should be rejected")


def test_reasoning_rejects_fabricated_supporting_signal_ids():
    result = scored_result(signal())

    class InvalidFakeClient(FakeReasoningClient):
        async def generate(self, payload):
            base = await super().generate(payload)
            base.accounts[0].supporting_signal_ids = ["ghost-signal"]
            return base

    try:
        asyncio.run(reason_result(result, client=InvalidFakeClient()))
    except ValueError as exc:
        assert "outside the supplied brief" in str(exc)
    else:
        raise AssertionError("Fabricated signal IDs should be rejected")


def test_reasoning_can_score_an_input_without_filter_score():
    result = {
        "run_id": "run-2",
        "signals": [signal()],
        "stages": {},
        "not_executed": ["filter_score", "llm_reasoning", "customer_review", "write_to_crm"],
    }
    reasoning = {
        "model": "gpt-4o-mini",
        "summary": "No separate reasoning call was needed for this structural test.",
        "accounts": [],
        "open_questions": [],
    }
    combined = apply_reasoning(result, reasoning)
    assert "filter_score" in combined
    assert combined["stages"]["filter_score"]["status"] == "completed"
    assert combined["stages"]["llm_reasoning"]["status"] == "completed"
    assert "llm_reasoning" not in combined["not_executed"]


if __name__ == "__main__":
    import sys

    failed = 0
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
                print("PASS", name)
            except Exception as exc:
                failed += 1
                print("FAIL", name, repr(exc))
    sys.exit(1 if failed else 0)
