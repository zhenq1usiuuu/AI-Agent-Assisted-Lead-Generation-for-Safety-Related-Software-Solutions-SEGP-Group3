"""
Step 6: human review (v0, terminal).

Reads the JSON written by `python -m app.reasoning` and lets a person approve,
edit, reject, merge or defer each account. Nothing here calls a model, and
nothing is written to a CRM: approved/edited accounts are only placed in a
`crm_queue` for the later CRM-writer step.

    python -m app.review reasoned.json --reviewer john --out review.json

Design rules
  * The human gate is the persistence boundary: only approve/edit reach crm_queue.
  * Every decision is logged (rejections too) so overrides can be evaluated later.
  * A reason is required for reject / edit / merge, and for any approval that
    goes against the code-assigned recommendation, an unvalidated AI explanation
    or a gated account.
  * AI text that failed validation is never copied into the CRM queue.
  * Existing output files are never overwritten (same convention as the CLI).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

REVIEW_VERSION = "v0"
DECISIONS = {"a": "approve", "e": "edit", "r": "reject", "m": "merge", "d": "defer"}
REASON_REQUIRED = {"reject", "edit", "merge"}
WRITES_TO_CRM = {"approve", "edit"}
RECOMMEND_EXCLUDE = {"exclude"}
RECOMMEND_HUMAN = {"human_review", "needs_human_review", "review"}
WIDTH = 88


# ---- helpers ---------------------------------------------------------------
def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def evidence_key(account_key: str, evidence: list[dict]) -> str:
    """Stable per-account key (for CRM idempotency): same evidence -> same key."""
    parts = sorted(f"{e.get('source_url')}|{norm(e.get('excerpt') or '')}" for e in evidence)
    return sha256_text(account_key + "|" + "|".join(parts))


def signal_index(result: dict) -> dict:
    """signal_id -> signal. Demo signals have no id; the scorer names them idx_<n>."""
    return {(s.get("signal_id") or f"idx_{i}"): s
            for i, s in enumerate(result.get("signals", []))}


def agrees(decision: str, recommendation: str | None) -> bool | None:
    """Does the human decision match the code-assigned recommendation?
    None = not comparable (human-review recommendations, defer, merge).
    ASSUMPTION: any recommendation other than 'exclude'/'human_review' is
    positive. Only 'exclude' has been seen in sample output - check MODULE3 rules."""
    rec = (recommendation or "").lower()
    if decision in ("defer", "merge") or rec in RECOMMEND_HUMAN:
        return None
    positive = rec not in RECOMMEND_EXCLUDE
    if decision in WRITES_TO_CRM:
        return positive
    if decision == "reject":
        return not positive
    return None


# ---- building review cards -------------------------------------------------
def build_cards(result: dict) -> list[dict]:
    reasoning = result.get("llm_reasoning")
    if not reasoning:
        raise ValueError("No llm_reasoning section found. Run "
                         "`python -m app.reasoning <scored.json> --out reasoned.json` first.")
    fs_accounts = {a["account_key"]: a
                   for a in (result.get("filter_score") or {}).get("accounts", [])}
    sigs = signal_index(result)
    docs = {d.get("document_id"): d for d in result.get("documents", [])}
    validation = reasoning.get("validation") or {}

    cards = []
    for acc in reasoning.get("accounts", []):
        scored = {s["signal_id"]: s
                  for s in fs_accounts.get(acc["account_key"], {}).get("signals", [])}
        evidence = []
        for sid in acc.get("supporting_signal_ids") or list(scored):
            s, sc = sigs.get(sid, {}), scored.get(sid, {})
            doc = docs.get(s.get("document_id"), {})
            text, start, end = doc.get("text"), s.get("evidence_start"), s.get("evidence_end")
            excerpt = s.get("excerpt")
            verified = bool(excerpt and text is not None and start is not None
                            and end is not None
                            and norm(text[start:end]) == norm(excerpt))
            evidence.append(dict(
                signal_id=sid, source_url=s.get("source_url") or sc.get("source_url"),
                excerpt=excerpt, published_at=s.get("published_at"),
                source_class=s.get("source_class"), score=sc.get("score"),
                total=sc.get("total"), gates=sc.get("gates", []),
                buyer_role_hypothesis=s.get("buyer_role_hypothesis"),
                verified_against_source=verified))
        cards.append(dict(
            account_key=acc["account_key"], display_name=acc.get("display_name"),
            grade=acc.get("grade"), score=acc.get("score"), gates=acc.get("gates", []),
            recommendation=acc.get("recommendation"),
            recommendation_rule=acc.get("recommendation_rule"),
            reason=acc.get("reason"), risks=acc.get("risks", []),
            next_step=acc.get("next_step_hypothesis"), evidence=evidence,
            validation_status=validation.get("status"),
            validation_issues=validation.get("issues", []),
            ai_valid=validation.get("status") == "valid",
            model=acc.get("model"), provider=acc.get("provider")))
    return cards


def wrap(text: str, indent: str = "  ") -> str:
    return textwrap.fill(text or "-", WIDTH, initial_indent=indent, subsequent_indent=indent)


def render(card: dict, position: str = "") -> str:
    out = ["=" * WIDTH,
           f"ACCOUNT {position}: {card['display_name']}",
           f"Grade {card['grade']} | score {card['score']}/100 | gates: "
           f"{', '.join(card['gates']) or 'none'}",
           f"Recommendation (set by code): {str(card['recommendation']).upper()}"
           f"   [{card['recommendation_rule']}]"]
    if not card["ai_valid"]:
        out += ["!! The AI explanation did NOT pass validation "
                f"({card['validation_status']}). Treat it as unreviewed.",
                *[f"   - {i}" for i in card["validation_issues"]]]
    out.append("\nEVIDENCE")
    for e in card["evidence"]:
        mark = "verified against source text" if e["verified_against_source"] \
            else "NOT verified against source text"
        out += [f" [{e['signal_id']}] {e['source_class']} | {e['published_at'] or 'date unknown'}"
                f" | {mark}", f"  {e['source_url']}", wrap(f"\"{e['excerpt']}\"")]
        if e["score"]:
            out.append("  score parts: " + ", ".join(f"{k}={v}" for k, v in e["score"].items()))
    out += ["\nAI EXPLANATION (assistive only)", wrap(card["reason"]), "Risks:"]
    out += [textwrap.fill(f"- {r}", WIDTH, initial_indent="  ", subsequent_indent="    ")
            for r in card["risks"]] or ["  -"]
    out += ["Next step (hypothesis):", wrap(card["next_step"])]
    return "\n".join(out)


# ---- reviewing -------------------------------------------------------------
def review_card(card: dict, ask, say, reviewer: str, position: str = "") -> dict:
    say(render(card, position))
    while True:
        choice = ask("\n[a]pprove  [e]dit  [r]eject  [m]erge  [d]efer > ").strip().lower()
        decision = DECISIONS.get(choice) or (choice if choice in DECISIONS.values() else None)
        if decision:
            break
        say("Please type a, e, r, m or d.")

    edits: dict = {}
    if decision == "edit":
        name = ask("  New display name (blank = keep): ").strip()
        role = ask("  Target buyer role (blank = keep): ").strip()
        if name:
            edits["display_name"] = name
        if role:
            edits["buyer_role"] = role
    if decision == "merge":
        target = ask("  Merge into which account name? ").strip()
        if target:
            edits["merge_into"] = target

    needs_reason = decision in REASON_REQUIRED
    note = ""
    if decision == "approve":
        if agrees("approve", card["recommendation"]) is False:
            needs_reason, note = True, "approving against the recommendation"
        elif card["gates"]:
            needs_reason, note = True, "approving a gated account"
        elif not card["ai_valid"]:
            needs_reason, note = True, "AI explanation did not validate"
    reason = ask(f"  Reason{f' ({note})' if note else ''}: ").strip() \
        if (needs_reason or decision == "defer") else ""
    while needs_reason and not reason:
        reason = ask("  A reason is required: ").strip()

    return dict(
        account_key=card["account_key"],
        display_name=card["display_name"], idempotency_key=evidence_key(
            card["account_key"], card["evidence"]),
        reviewer=reviewer, decision=decision, reason=reason or None, edits=edits,
        recommendation=card["recommendation"], recommendation_rule=card["recommendation_rule"],
        agrees_with_recommendation=agrees(decision, card["recommendation"]),
        grade=card["grade"], score=card["score"], gates=card["gates"],
        ai_validation=card["validation_status"], reviewed_at=now_iso(),
        evidence=[{k: e[k] for k in ("signal_id", "source_url", "excerpt", "published_at",
                                    "verified_against_source")} for e in card["evidence"]])


def build_crm_queue(events: list[dict], cards: list[dict]) -> list[dict]:
    by_key = {c["account_key"]: c for c in cards}
    queue = []
    for ev in events:
        if ev["decision"] not in WRITES_TO_CRM:
            continue
        card = by_key[ev["account_key"]]
        role = ev["edits"].get("buyer_role") or next(
            (e["buyer_role_hypothesis"] for e in card["evidence"] if e["buyer_role_hypothesis"]), None)
        item = dict(
            account_key=ev["account_key"],
            display_name=ev["edits"].get("display_name") or ev["display_name"],
            idempotency_key=ev["idempotency_key"], grade=ev["grade"], score=ev["score"],
            target_buyer_role=role, evidence=ev["evidence"],
            review_reason=ev["reason"], reviewed_by=ev["reviewer"],
            reviewed_at=ev["reviewed_at"])
        if card["ai_valid"]:  # unvalidated AI text is never carried into the CRM queue
            item["ai_reason"] = card["reason"]
        queue.append(item)
    return queue


def run_review(result: dict, reviewer: str, ask=input, say=print) -> dict:
    cards = build_cards(result)
    events, complete = [], True
    started = now_iso()
    try:
        for i, card in enumerate(cards, 1):
            events.append(review_card(card, ask, say, reviewer, f"{i}/{len(cards)}"))
    except (EOFError, KeyboardInterrupt):
        complete = False
        say("\nReview stopped early. Accounts reviewed so far will be saved.")
    counts: dict[str, int] = {}
    for ev in events:
        counts[ev["decision"]] = counts.get(ev["decision"], 0) + 1
    return dict(
        review_version=REVIEW_VERSION, source_run_id=result.get("run_id"),
        reviewer=reviewer, started_at=started, finished_at=now_iso(), complete=complete,
        accounts_total=len(cards), accounts_reviewed=len(events), summary=counts,
        events=events, crm_queue=build_crm_queue(events, cards),
        not_executed=["write_to_crm"])


def write_json(path: Path, data: dict) -> None:
    with Path(path).open("x", encoding="utf-8", newline="\n") as f:  # never overwrite
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Human review of reasoned leads (step 6).")
    ap.add_argument("reasoned_json", type=Path)
    ap.add_argument("--reviewer", required=True, help="Your name, recorded in every decision.")
    ap.add_argument("--out", type=Path, required=True, help="New file for the review record.")
    args = ap.parse_args(argv)
    try:
        if args.out.exists():
            raise FileExistsError(f"Output file already exists: {args.out}")
        raw = args.reasoned_json.read_bytes()
        result = json.loads(raw.decode("utf-8-sig"))
        review = run_review(result, args.reviewer)
        review["input_file"] = args.reasoned_json.name
        review["input_sha256"] = hashlib.sha256(raw).hexdigest()
        write_json(args.out, review)
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 1
    print(f"\nSaved review: {args.out.resolve()}  |  decisions: {review['summary']}  |  "
          f"queued for CRM: {len(review['crm_queue'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
