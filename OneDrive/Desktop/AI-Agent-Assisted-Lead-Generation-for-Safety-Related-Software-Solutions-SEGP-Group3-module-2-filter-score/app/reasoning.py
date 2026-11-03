"""Module 3: LLM reasoning and a user-facing summary with a second model.

Run against a result JSON produced by the earlier pipeline. If Filter + score
has not been attached, it is calculated locally first.

    python -m app.reasoning result.json --out reasoned-result.json

The second model is independent of app/llm.py and is configured with
REASONING_API_KEY, REASONING_BASE_URL and REASONING_MODEL.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .context import ROOT
from .reasoning_client import ReasoningClient
from .reasoning_models import ReasoningOutput
from .scoring import apply_scores, score_result


MAX_SIGNALS_PER_ACCOUNT = 4
MAX_REASONING_ATTEMPTS = 3
RESEARCH_CANDIDATE_THRESHOLD = 40

HUMAN_REVIEW_GATES = {
    "unverified_entity",
    "sensitive_incident",
    "pending_review",
}
DISQUALIFYING_GATES = {
    "non_manufacturing",
    "stale",
    "duplicate",
    "unsupported_claim",
}
BANNED_PATTERNS = {
    "non-compliant": re.compile(r"\bnon[-\s]compliant\b", re.IGNORECASE),
    "would have prevented": re.compile(r"\bwould\s+have\s+prevented\b", re.IGNORECASE),
    "prevents accidents": re.compile(r"\bprevents?\s+accidents?\b", re.IGNORECASE),
    "guarantees compliance": re.compile(r"\bguarantees?\s+compliance\b", re.IGNORECASE),
    "regulator-approved": re.compile(r"\bregulator[-\s]approved\b", re.IGNORECASE),
}
QUOTE_PATTERN = re.compile(r'["\u201c\u201d]([^"\u201c\u201d\n]{3,})["\u201c\u201d]')
SCORE_MENTION_PATTERN = re.compile(
    r"\bscore\s+(\d{1,3})(?:\s*/\s*100)?", re.IGNORECASE
)
SCORE_FRACTION_PATTERN = re.compile(r"(?<!\d)(\d{1,3})\s*/\s*100\b")
GRADE_MENTION_PATTERN = re.compile(r"\bgrade\s+([A-H])\b", re.IGNORECASE)


def _indexed_signals(result: dict) -> dict[str, dict]:
    """Match the positional IDs used by Module 2 for demo-mode signals."""
    indexed: dict[str, dict] = {}
    for position, signal in enumerate(result.get("signals", [])):
        signal_id = signal.get("signal_id") or f"idx_{position}"
        indexed[signal_id] = {**signal, "signal_id": signal_id}
    return indexed


def recommendation_for(account: dict) -> str:
    """Deterministic grade/gate mapping; the model never chooses this value."""
    grade = str(account.get("best_grade") or "").upper()
    gates = set(account.get("gates", []))
    if grade == "H" or gates & HUMAN_REVIEW_GATES:
        return "review"
    if grade == "D" or gates & DISQUALIFYING_GATES:
        return "exclude"
    if grade == "C":
        return "research"
    if grade in ("A", "B"):
        return "shortlist"
    return "review"


def _threshold_sentence(account: dict) -> str | None:
    grade = str(account.get("best_grade") or "").upper()
    if grade == "D" and not account.get("gates"):
        score = account.get("best_score", 0)
        return (
            f"Score {score}/100, below the {RESEARCH_CANDIDATE_THRESHOLD}-point "
            "threshold required for a research candidate."
        )
    return None


def _compact_signal(scored: dict, original: dict) -> dict:
    return {
        "signal_id": scored.get("signal_id"),
        "organisation": original.get("organisation"),
        "sector": original.get("sector"),
        "safety_topic": original.get("safety_topic"),
        "workflow": original.get("workflow"),
        "trigger_type": original.get("trigger_type"),
        "buyer_role_hypothesis": original.get("buyer_role_hypothesis"),
        "urgency": original.get("urgency"),
        "published_at": original.get("published_at"),
        "source_class": original.get("source_class"),
        "source_url": original.get("source_url"),
        "excerpt": original.get("excerpt"),
        "evidence_level": original.get("evidence_level"),
        "semantic_review": original.get("semantic_review", {}).get("status"),
        "rule_checks": original.get("rule_checks", []),
        "ambiguity_flags": original.get("ambiguity_flags", []),
        "confidence": original.get("confidence"),
        "total": scored.get("total"),
        "grade": scored.get("grade"),
        "gates": scored.get("gates", []),
        "rule_reasons": scored.get("reasons", {}),
    }


def _account_brief(account: dict, index: dict[str, dict]) -> dict:
    scored_signals = account.get("signals", [])
    live = [s for s in scored_signals if "duplicate" not in s.get("gates", [])]
    selected = sorted(live or scored_signals, key=lambda item: -item.get("total", 0))[
        :MAX_SIGNALS_PER_ACCOUNT
    ]
    grade = str(account.get("best_grade") or "").upper()
    score = account.get("best_score", 0)
    gates = sorted(
        {gate for item in (live or scored_signals) for gate in item.get("gates", [])}
    )
    recommendation = recommendation_for({"best_grade": grade, "gates": gates})
    return {
        "account_key": account.get("account_key"),
        "display_name": account.get("display_name"),
        "aliases": account.get("aliases", []),
        "best_grade": grade,
        "best_score": score,
        "gates": gates,
        "required_recommendation": recommendation,
        "recommendation_rule": f"{grade} -> {recommendation}",
        "score_out_of_100": f"{score}/100",
        "research_candidate_threshold": RESEARCH_CANDIDATE_THRESHOLD,
        "threshold_sentence": _threshold_sentence(
            {"best_grade": grade, "best_score": score, "gates": gates}
        ),
        "signal_count": len(scored_signals),
        "omitted_signal_count": max(0, len(scored_signals) - len(selected)),
        "all_signal_ids": [
            item.get("signal_id") for item in scored_signals if item.get("signal_id")
        ],
        "signals": [
            _compact_signal(item, index.get(item.get("signal_id"), {}))
            for item in selected
        ],
    }


def build_reasoning_payload(result: dict, decisions: dict) -> dict:
    index = _indexed_signals(result)
    accounts = sorted(
        decisions.get("accounts", []), key=lambda item: -item.get("best_score", 0)
    )
    request = result.get("request", {})
    prompt = request.get("prompt", "") if isinstance(request, dict) else ""
    unresolved = [
        {
            "signal_id": item.get("signal_id"),
            "grade": item.get("grade"),
            "total": item.get("total"),
            "gates": item.get("gates", []),
            "rule_reasons": item.get("reasons", {}),
        }
        for item in decisions.get("unresolved_signals", [])
    ]
    return {
        "stage": "llm_reasoning",
        "run_id": result.get("run_id"),
        "research_request": str(prompt)[:2000],
        "source_run_status": result.get("status"),
        "extraction_complete": result.get("extraction_complete"),
        "rubric_version": decisions.get("rubric_version"),
        "scoring_summary": decisions.get("summary", {}),
        "accounts": [_account_brief(account, index) for account in accounts],
        "unresolved_signals": unresolved,
        "merge_candidates": decisions.get("merge_candidates", []),
        "input_note": (
            "The evidence brief is compact; the original result JSON remains the source of truth. "
            "Do not claim that omitted signals or accounts were absent."
        ),
    }


def _quotes(text: str) -> set[str]:
    return {match.strip() for match in QUOTE_PATTERN.findall(text)}


def _mentioned_scores(text: str) -> set[int]:
    values = {int(match) for match in SCORE_MENTION_PATTERN.findall(text)}
    values.update(int(match) for match in SCORE_FRACTION_PATTERN.findall(text))
    return values


def _mentioned_grades(text: str) -> set[str]:
    return {match.upper() for match in GRADE_MENTION_PATTERN.findall(text)}


def validate_reasoning(
    output: ReasoningOutput,
    decisions: dict,
    payload: dict,
    index: dict[str, dict],
) -> list[str]:
    """Deterministic checks over generated text; returns human-readable issues."""
    issues: list[str] = []
    expected = {account["account_key"] for account in decisions.get("accounts", [])}
    returned = {account.account_key for account in output.accounts}
    if returned != expected:
        missing = sorted(expected - returned)
        extra = sorted(returned - expected)
        if missing:
            issues.append(f"Missing accounts: {missing}")
        if extra:
            issues.append(f"Unexpected accounts: {extra}")

    payload_accounts = {
        account["account_key"]: account for account in payload.get("accounts", [])
    }
    global_excerpts = {
        signal.get("excerpt", "")
        for account in payload_accounts.values()
        for signal in account.get("signals", [])
        if signal.get("excerpt")
    }

    for account in output.accounts:
        key = account.account_key
        brief = payload_accounts.get(key)
        if brief is None:
            continue
        text = " ".join(
            [account.reason, account.next_step_hypothesis, *account.risks]
        )
        evidence = {
            index.get(signal_id, {}).get("excerpt", "")
            for signal_id in brief.get("all_signal_ids", [])
            if index.get(signal_id, {}).get("excerpt")
        }
        for quote in _quotes(text):
            if quote not in evidence:
                issues.append(
                    f"Account {key}: quoted text is not an exact signal excerpt: {quote}"
                )

        actual_score = int(brief.get("best_score", 0))
        for score in _mentioned_scores(text):
            if score != actual_score:
                issues.append(
                    f"Account {key}: generated score {score} does not match {actual_score}"
                )
        actual_grade = str(brief.get("best_grade") or "").upper()
        for grade in _mentioned_grades(text):
            if grade != actual_grade:
                issues.append(
                    f"Account {key}: generated grade {grade} does not match {actual_grade}"
                )

        valid_ids = set(brief.get("all_signal_ids", []))
        invalid_ids = [
            signal_id
            for signal_id in account.supporting_signal_ids
            if signal_id not in valid_ids
        ]
        if invalid_ids:
            issues.append(
                f"Account {key}: signal ID(s) outside the supplied brief: {invalid_ids}"
            )

    all_text = " ".join(
        [
            output.summary,
            *[
                item
                for account in output.accounts
                for item in [
                    account.reason,
                    account.next_step_hypothesis,
                    *account.risks,
                ]
            ],
            *output.open_questions,
        ]
    )
    for phrase, pattern in BANNED_PATTERNS.items():
        if pattern.search(all_text):
            issues.append(f"Banned phrase used: {phrase}")

    for quote in _quotes(" ".join([output.summary, *output.open_questions])):
        if quote not in global_excerpts:
            issues.append(
                f"Summary/open question quotes text outside the supplied evidence: {quote}"
            )
    return issues


def _correction_note(issues: list[str]) -> str:
    return (
        "Your previous response failed deterministic validation. Correct only the "
        "following issues and return the complete JSON: " + "; ".join(issues)
    )


def _aggregate_usage(rows: list[dict]) -> dict:
    prompt_tokens = sum(
        int(row.get("usage", {}).get("prompt_tokens") or 0) for row in rows
    )
    completion_tokens = sum(
        int(row.get("usage", {}).get("completion_tokens") or 0) for row in rows
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "calls": len(rows),
    }


def _materialize_accounts(
    output: ReasoningOutput,
    payload: dict,
    client,
    usage: dict,
) -> list[dict]:
    returned = {account.account_key: account for account in output.accounts}
    cost_info = client.cost_info(usage) if hasattr(client, "cost_info") else {}
    estimated_cost = cost_info.get("estimated_cost_usd")
    account_count = max(1, len(payload.get("accounts", [])))
    cost_share = (
        round(estimated_cost / account_count, 8)
        if isinstance(estimated_cost, (int, float))
        else None
    )
    model = getattr(client, "model", "unknown")
    provider = getattr(client, "provider", "unknown")

    accounts = []
    for brief in payload.get("accounts", []):
        key = brief["account_key"]
        reasoned = returned.get(key)
        valid_ids = set(brief.get("all_signal_ids", []))
        if reasoned:
            reason = reasoned.reason
            risks = list(reasoned.risks)
            supporting_signal_ids = [
                signal_id
                for signal_id in reasoned.supporting_signal_ids
                if signal_id in valid_ids
            ]
            next_step = reasoned.next_step_hypothesis
        else:
            reason = brief.get("threshold_sentence") or (
                f"Score {brief.get('best_score', 0)}/100, grade "
                f"{brief.get('best_grade', '?')}; no model explanation was returned."
            )
            risks = [
                "The reasoning model omitted this account; a deterministic fallback was generated."
            ]
            supporting_signal_ids = []
            next_step = "Complete human review before proceeding."

        accounts.append(
            {
                "account_key": key,
                "display_name": brief.get("display_name"),
                "model": model,
                "provider": provider,
                "grade": brief.get("best_grade"),
                "score": brief.get("best_score"),
                "gates": brief.get("gates", []),
                "recommendation": brief.get("required_recommendation"),
                "recommendation_rule": brief.get("recommendation_rule"),
                "reason": reason,
                "risks": risks,
                "supporting_signal_ids": supporting_signal_ids,
                "next_step_hypothesis": next_step,
                "usage_tokens": {
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "calls": usage.get("calls"),
                },
                "estimated_call_cost_usd": estimated_cost,
                "estimated_account_cost_share_usd": cost_share,
                "cost_note": (
                    "A single reasoning batch covers all accounts; the per-account "
                    "figure is an even cost share and is not a provider invoice."
                ),
            }
        )
    return accounts


async def reason_result(result: dict, client: ReasoningClient | None = None) -> dict:
    decisions = result.get("filter_score") or score_result(result)
    client = client or ReasoningClient()
    client.validate_distinct_model()
    payload = build_reasoning_payload(result, decisions)
    index = _indexed_signals(result)
    payload_sha256 = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    usage_start = len(client.usage)
    output = None
    issues: list[str] = []
    attempts = 0

    for attempt in range(MAX_REASONING_ATTEMPTS):
        attempts = attempt + 1
        output = await client.generate(payload)
        issues = validate_reasoning(output, decisions, payload, index)
        if not issues:
            break
        if attempt < MAX_REASONING_ATTEMPTS - 1:
            payload["correction_note"] = _correction_note(issues)

    usage = _aggregate_usage(client.usage[usage_start:])
    accounts = _materialize_accounts(output, payload, client, usage)
    first_model = os.getenv("MOONSHOT_MODEL", "kimi-k3")
    provider = getattr(client, "provider", "unknown")
    model = getattr(client, "model", "unknown")
    architecture_note = (
        f"Extraction stages use Moonshot {first_model}. LLM reasoning uses "
        f"{provider} {model}. The providers are separated so upstream evidence "
        "extraction and downstream reasoning have independent traceability and "
        "do not depend on a single model vendor."
    )
    return {
        "stage": "llm_reasoning",
        "model": model,
        "provider": provider,
        "summary": output.summary if output else "",
        "accounts": accounts,
        "open_questions": output.open_questions if output else [],
        "validation": {
            "status": "valid" if not issues else "needs_human_review",
            "issues": issues,
            "attempts": attempts,
        },
        "usage": usage,
        "cost": client.cost_info(usage) if hasattr(client, "cost_info") else {},
        "architecture_note": architecture_note,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rubric_version": decisions.get("rubric_version"),
        "source_run_id": result.get("run_id"),
        "input_prompt_sha256": payload_sha256,
    }


def apply_reasoning(result: dict, reasoning: dict) -> dict:
    """Attach Module 3 output without changing the earlier stage contracts."""
    combined = copy.deepcopy(result)
    if "filter_score" not in combined:
        decisions = score_result(combined)
        combined = apply_scores(combined, decisions)
    combined["llm_reasoning"] = copy.deepcopy(reasoning)
    combined.setdefault("stages", {})["llm_reasoning"] = {
        "status": "completed",
        "executor": reasoning.get("model"),
        "model_role": "second_model",
        "account_count": len(reasoning.get("accounts", [])),
    }
    combined["not_executed"] = [
        stage
        for stage in combined.get("not_executed", [])
        if stage not in ("filter_score", "llm_reasoning")
    ]
    return combined


def render_report(result: dict) -> str:
    reasoning = result.get("llm_reasoning")
    if not reasoning:
        raise ValueError("Result has no llm_reasoning output.")
    signals = _indexed_signals(result)
    wrapper = textwrap.TextWrapper(width=100, break_long_words=False, break_on_hyphens=False)

    lines = [
        "LLM Reasoning report",
        f"Model: {reasoning.get('model')} | Provider: {reasoning.get('provider')}",
        "",
        "Summary:",
    ]
    lines.extend(wrapper.wrap(reasoning.get("summary", "")))

    for position, account in enumerate(reasoning.get("accounts", []), 1):
        lines.extend(
            [
                "",
                (
                    f"{position}. {account.get('display_name')} | "
                    f"{str(account.get('recommendation')).upper()} | "
                    f"grade {account.get('grade', '?')} | "
                    f"score {account.get('score', '?')}"
                ),
                "Why:",
            ]
        )
        lines.extend(wrapper.wrap(account.get("reason", "")))
        risks = account.get("risks", [])
        if risks:
            lines.append("Risks:")
            for risk in risks:
                lines.extend(wrapper.wrap("- " + risk))
        for signal_id in account.get("supporting_signal_ids", []):
            signal = signals.get(signal_id, {})
            excerpt = signal.get("excerpt", "")
            lines.append(f'Evidence ({signal_id}): "{excerpt}"')
            source_url = signal.get("source_url")
            if source_url:
                lines.append(f"Source: {source_url}")
        lines.extend(
            wrapper.wrap(
                "Next step: " + account.get("next_step_hypothesis", "")
            )
        )

    open_questions = reasoning.get("open_questions", [])
    if open_questions:
        lines.extend(["", "Open questions:"])
        for question in open_questions:
            lines.extend(wrapper.wrap("- " + question))

    validation = reasoning.get("validation", {})
    if validation.get("status") == "needs_human_review":
        lines.extend(["", "Validation: needs_human_review"])
        for issue in validation.get("issues", []):
            lines.extend(wrapper.wrap("- " + issue))

    lines.extend(
        [
            "",
            reasoning.get("architecture_note", ""),
            "",
            "This explanation is a human-review aid. It does not create a CRM record, "
            "authorize contact, or change the underlying evidence.",
        ]
    )
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Run the LLM reasoning stage with a second model on a result JSON."
    )
    command.add_argument("result_json", type=Path, help="Result JSON from the earlier pipeline.")
    command.add_argument("--out", type=Path, help="Save the complete reasoned JSON to a new file.")
    command.add_argument(
        "--json",
        action="store_true",
        help="Print the complete JSON instead of the human-readable report.",
    )
    command.add_argument("--model", help="Override REASONING_MODEL for this run.")
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    load_dotenv(ROOT / ".env")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        result = json.loads(args.result_json.read_text(encoding="utf-8-sig"))
        client = ReasoningClient(model=args.model) if args.model else None
        reasoning = asyncio.run(reason_result(result, client=client))
        combined = apply_reasoning(result, reasoning)
        encoded = json.dumps(combined, ensure_ascii=False, indent=2)
        print(encoded if args.json else render_report(combined), flush=True)
        if args.out:
            if args.out.exists():
                raise FileExistsError(f"Output file already exists: {args.out}")
            args.out.write_text(encoded + "\n", encoding="utf-8", newline="\n")
            print(f"Saved JSON: {args.out.resolve()}", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(
            json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
