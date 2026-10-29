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


def _indexed_signals(result: dict) -> dict[str, dict]:
    """Match the positional IDs used by Module 2 for demo-mode signals."""
    indexed: dict[str, dict] = {}
    for position, signal in enumerate(result.get("signals", [])):
        signal_id = signal.get("signal_id") or f"idx_{position}"
        indexed[signal_id] = {**signal, "signal_id": signal_id}
    return indexed


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
        "reasons": scored.get("reasons", {}),
    }


def _account_brief(account: dict, index: dict[str, dict]) -> dict:
    scored_signals = account.get("signals", [])
    live = [s for s in scored_signals if "duplicate" not in s.get("gates", [])]
    selected = sorted(live or scored_signals, key=lambda item: -item.get("total", 0))[
        :MAX_SIGNALS_PER_ACCOUNT
    ]
    return {
        "account_key": account.get("account_key"),
        "display_name": account.get("display_name"),
        "aliases": account.get("aliases", []),
        "best_grade": account.get("best_grade"),
        "best_score": account.get("best_score"),
        "gates": sorted({gate for item in scored_signals for gate in item.get("gates", [])}),
        "signal_count": len(scored_signals),
        "omitted_signal_count": max(0, len(scored_signals) - len(selected)),
        "all_signal_ids": [item.get("signal_id") for item in scored_signals if item.get("signal_id")],
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
            "reasons": item.get("reasons", {}),
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


def _validate_reasoning(
    output: ReasoningOutput, decisions: dict, payload: dict
) -> tuple[dict[str, str], dict[str, list[str]]]:
    expected = {account["account_key"] for account in decisions.get("accounts", [])}
    returned = {account.account_key for account in output.accounts}
    if returned != expected:
        missing = sorted(expected - returned)
        extra = sorted(returned - expected)
        raise ValueError(
            "Reasoning model returned an incomplete account list"
            + (f"; missing={missing}" if missing else "")
            + (f"; extra={extra}" if extra else "")
        )

    canonical = {
        account["account_key"]: account["display_name"]
        for account in payload["accounts"]
    }
    valid_ids = {
        account["account_key"]: set(account["all_signal_ids"])
        for account in payload["accounts"]
    }
    for reasoned in output.accounts:
        invalid = [
            signal_id
            for signal_id in reasoned.supporting_signal_ids
            if signal_id not in valid_ids[reasoned.account_key]
        ]
        if invalid:
            raise ValueError(
                f"Reasoning model referenced signal ID(s) outside the supplied brief: {invalid}"
            )
    return canonical, valid_ids


async def reason_result(result: dict, client: ReasoningClient | None = None) -> dict:
    decisions = result.get("filter_score") or score_result(result)
    client = client or ReasoningClient()
    client.validate_distinct_model()
    payload = build_reasoning_payload(result, decisions)
    output = await client.generate(payload)
    canonical, _ = _validate_reasoning(output, decisions, payload)

    accounts = []
    for reasoned in output.accounts:
        accounts.append(
            {
                "account_key": reasoned.account_key,
                "display_name": canonical[reasoned.account_key],
                "recommendation": reasoned.recommendation,
                "reason": reasoned.reason,
                "risks": reasoned.risks,
                "supporting_signal_ids": reasoned.supporting_signal_ids,
                "next_step_hypothesis": reasoned.next_step_hypothesis,
            }
        )

    usage = client.usage[-1] if client.usage else {}
    return {
        "stage": "llm_reasoning",
        "model": client.model,
        "summary": output.summary,
        "accounts": accounts,
        "open_questions": output.open_questions,
        "usage": usage,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rubric_version": decisions.get("rubric_version"),
        "source_run_id": result.get("run_id"),
        "input_prompt_sha256": hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
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
    decisions = result.get("filter_score", {})
    accounts_by_key = {
        account.get("account_key"): account
        for account in decisions.get("accounts", [])
    }
    signals = _indexed_signals(result)
    wrapper = textwrap.TextWrapper(width=100, break_long_words=False, break_on_hyphens=False)

    lines = [
        "LLM Reasoning report",
        f"Model: {reasoning.get('model')}",
        "",
        "Summary:",
    ]
    lines.extend(wrapper.wrap(reasoning.get("summary", "")))

    for position, account in enumerate(reasoning.get("accounts", []), 1):
        decision = accounts_by_key.get(account.get("account_key"), {})
        lines.extend(
            [
                "",
                (
                    f"{position}. {account.get('display_name')} | "
                    f"{str(account.get('recommendation')).upper()} | "
                    f"grade {decision.get('best_grade', '?')} | "
                    f"score {decision.get('best_score', '?')}"
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
        lines.extend(wrapper.wrap("Next step: " + account.get("next_step_hypothesis", "")))

    open_questions = reasoning.get("open_questions", [])
    if open_questions:
        lines.extend(["", "Open questions:"])
        for question in open_questions:
            lines.extend(wrapper.wrap("- " + question))

    lines.extend(
        [
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
