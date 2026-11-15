"""Offline tests for Module 3. No second-model API requests are made."""
import asyncio
import json
from datetime import date

import httpx

from app.reasoning import (
    apply_reasoning,
    reason_result,
    recommendation_for,
    render_report,
)
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
        "buyer_role_hypothesis": "HSE manager (hypothesis only)",
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


def valid_output(payload):
    accounts = []
    for brief in payload.get("accounts", []):
        accounts.append(
            ReasonedAccount(
                account_key=brief["account_key"],
                display_name=brief["display_name"],
                reason=(
                    f"Score {brief['best_score']}/100, grade {brief['best_grade']}. "
                    f"Use the supplied {brief['source_class'] if 'source_class' in brief else 'evidence'}."
                ),
                risks=["The evidence still requires human review."],
                supporting_signal_ids=brief["all_signal_ids"][:1],
                next_step_hypothesis="Review the source before proceeding.",
            )
        )
    return ReasoningOutput(
        summary="The deterministic scoring is explained account by account.",
        accounts=accounts,
        open_questions=[],
    )


class FakeReasoningClient:
    def __init__(self, model="gpt-4o-mini", provider="api.openai.com"):
        self.model = model
        self.provider = provider
        self.base_url = "https://api.openai.com/v1"
        self.usage = []
        self.payload = None
        self.calls = 0
        self.outputs = []

    def validate_distinct_model(self):
        return None

    def cost_info(self, usage):
        prompt = usage.get("prompt_tokens", 0)
        output = usage.get("completion_tokens", 0)
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": prompt,
            "completion_tokens": output,
            "estimated_cost_usd": 0.0001,
            "pricing_usd_per_million": {"input": 0.15, "output": 0.60},
            "pricing_basis": "test_fixture",
            "note": "Test estimate.",
        }

    async def generate(self, payload):
        self.payload = payload
        self.calls += 1
        self.usage.append(
            {
                "stage": "llm_reasoning",
                "model": self.model,
                "provider": self.provider,
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
        )
        if self.outputs and self.calls - 1 < len(self.outputs):
            return self.outputs[self.calls - 1](payload)
        return valid_output(payload)


def test_recommendation_mapping_is_owned_by_code():
    assert recommendation_for({"best_grade": "D", "gates": []}) == "exclude"
    assert recommendation_for({"best_grade": "H", "gates": []}) == "review"
    assert recommendation_for({"best_grade": "C", "gates": []}) == "research"
    assert recommendation_for({"best_grade": "B", "gates": []}) == "shortlist"


def test_reasoning_stage_preserves_evidence_and_records_second_model_metadata():
    result = scored_result(signal())
    fake = FakeReasoningClient()
    reasoning = asyncio.run(reason_result(result, client=fake))

    account = reasoning["accounts"][0]
    assert reasoning["validation"]["status"] == "valid"
    assert account["display_name"] == "Example Plastics Sdn Bhd"
    assert account["recommendation"] == "shortlist"
    assert account["model"] == "gpt-4o-mini"
    assert account["provider"] == "api.openai.com"
    assert account["estimated_call_cost_usd"] == 0.0001
    assert account["estimated_account_cost_share_usd"] == 0.0001
    assert account["supporting_signal_ids"] == ["s1"]
    assert "enforcement" in fake.payload["accounts"][0]["signals"][0]["trigger_type"]
    assert reasoning["input_prompt_sha256"]
    assert "Moonshot" in reasoning["architecture_note"]

    combined = apply_reasoning(result, reasoning)
    assert combined["stages"]["llm_reasoning"]["status"] == "completed"
    assert combined["stages"]["llm_reasoning"]["model_role"] == "second_model"
    assert combined["not_executed"] == ["customer_review", "write_to_crm"]

    report = render_report(combined)
    assert "Example Plastics Sdn Bhd" in report
    assert "SHORTLIST" in report
    assert "Provider: api.openai.com" in report
    assert "RM50,000" in report
    assert "human-review aid" in report


def test_incident_gets_code_review_recommendation():
    result = scored_result(
        signal(
            signal_id="incident-1",
            trigger_type="incident",
            excerpt="Example Plastics Sdn Bhd reported a serious forklift incident.",
        )
    )
    reasoning = asyncio.run(reason_result(result, client=FakeReasoningClient()))
    account = reasoning["accounts"][0]
    assert account["grade"] == "H"
    assert account["recommendation"] == "review"


def test_esg_discovery_only_gets_research_recommendation():
    result = scored_result(
        signal(
            signal_id="esg-1",
            organisation="Green Energy Bhd",
            sector="Food Producers",
            trigger_type="esg_index",
            buyer_role_hypothesis=None,
            urgency="pending",
            source_class="industry_procurement",
            evidence_level="discovery_only",
            eligible_for_scoring=False,
            recommended_next_step="research_more",
            excerpt="Green Energy Bhd is an ESG index constituent using digital safety analytics and CCTV monitoring.",
            ambiguity_flags=["discovery_only", "no_direct_workflow_evidence"],
        )
    )
    reasoning = asyncio.run(reason_result(result, client=FakeReasoningClient()))
    account = reasoning["accounts"][0]
    assert account["grade"] == "C"
    assert account["recommendation"] == "research"


def test_missing_organisation_stays_unresolved():
    result = scored_result(
        signal(
            signal_id="unresolved-1",
            organisation=None,
            organisation_specific=False,
            trigger_type="guidance",
        )
    )
    assert result["filter_score"]["summary"]["accounts"] == 0
    assert result["filter_score"]["summary"]["unresolved"] == 1
    reasoning = asyncio.run(reason_result(result, client=FakeReasoningClient()))
    assert reasoning["accounts"] == []
    assert reasoning["validation"]["status"] == "valid"


def test_multiple_signals_are_grouped_into_one_account():
    result = scored_result(
        signal(signal_id="a"),
        signal(
            signal_id="b",
            excerpt="Example Plastics Sdn Bhd also introduced CCTV pedestrian monitoring.",
        ),
    )
    fake = FakeReasoningClient()
    reasoning = asyncio.run(reason_result(result, client=fake))
    assert len(reasoning["accounts"]) == 1
    assert fake.payload["accounts"][0]["signal_count"] == 2
    assert len(fake.payload["accounts"][0]["all_signal_ids"]) == 2


def test_invalid_quote_is_retried_then_marked_for_human_review():
    result = scored_result(signal())

    def invalid(payload):
        output = valid_output(payload)
        output.accounts[0].reason = (
            'The evidence includes "Invented claim not present in the source".'
        )
        return output

    fake = FakeReasoningClient()
    fake.outputs = [invalid, invalid, invalid]
    reasoning = asyncio.run(reason_result(result, client=fake))
    assert reasoning["validation"]["status"] == "needs_human_review"
    assert reasoning["validation"]["attempts"] == 3
    assert any("quoted text" in issue for issue in reasoning["validation"]["issues"])


def test_score_or_grade_or_banned_word_is_flagged():
    result = scored_result(signal())

    def bad_score(payload):
        output = valid_output(payload)
        output.accounts[0].reason = "Score 20/100, grade Z. The company is non-compliant."
        return output

    fake = FakeReasoningClient()
    fake.outputs = [bad_score, bad_score, bad_score]
    reasoning = asyncio.run(reason_result(result, client=fake))
    issues = reasoning["validation"]["issues"]
    assert reasoning["validation"]["status"] == "needs_human_review"
    assert any("does not match" in issue for issue in issues)
    assert any("Banned phrase" in issue for issue in issues)


def test_reasoning_client_sends_strict_schema_without_exposing_the_key(monkeypatch):
    monkeypatch.setenv("REASONING_USE_KIMI", "false")
    monkeypatch.setenv("MOONSHOT_MODEL", "kimi-k3")
    monkeypatch.setenv("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1")
    monkeypatch.setenv("REASONING_API_KEY", "fake-second-key")
    monkeypatch.setenv("REASONING_BASE_URL", "https://api.openai.com/v1")
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
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
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
    cost = client.cost_info({"prompt_tokens": 10, "completion_tokens": 2})
    assert cost["estimated_cost_usd"] == 0.0000027


def test_deepseek_client_uses_json_object_mode(monkeypatch):
    monkeypatch.setenv("REASONING_USE_KIMI", "false")
    monkeypatch.setenv("MOONSHOT_MODEL", "kimi-k3")
    monkeypatch.setenv("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1")
    monkeypatch.setenv("REASONING_API_KEY", "fake-deepseek-key")
    monkeypatch.setenv("REASONING_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("REASONING_MODEL", "deepseek-chat")
    captured = []
    output = ReasoningOutput(
        summary="The DeepSeek JSON mode test completed successfully.",
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
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    client = ReasoningClient()
    result = asyncio.run(
        client.generate({"stage": "llm_reasoning", "accounts": []})
    )
    assert result.summary == output.summary
    body = captured[0]
    assert body["response_format"] == {"type": "json_object"}
    user_message = json.loads(body["messages"][1]["content"])
    assert "output_schema" in user_message
    assert "fake-deepseek-key" not in json.dumps(body)
    assert client.cost_info({"prompt_tokens": 100, "completion_tokens": 10})[
        "estimated_cost_usd"
    ] == 0.000038


def test_second_model_must_differ_from_kimi(monkeypatch):
    monkeypatch.setenv("REASONING_USE_KIMI", "false")
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


def test_reasoning_can_score_an_input_without_filter_score():
    result = {
        "run_id": "run-2",
        "signals": [signal()],
        "stages": {},
        "not_executed": ["filter_score", "llm_reasoning", "customer_review", "write_to_crm"],
    }
    reasoning = {
        "model": "gpt-4o-mini",
        "provider": "api.openai.com",
        "summary": "No separate reasoning call was needed for this structural test.",
        "accounts": [],
        "open_questions": [],
        "validation": {"status": "valid", "issues": [], "attempts": 1},
        "architecture_note": "Two providers are separated.",
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
