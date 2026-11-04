"""Run: python -m pytest tests/test_review.py -q
Fixture tests/fixtures/reasoned_demo.json is the real demo output of Module 3."""
import copy
import json
import tempfile
from pathlib import Path

import pytest

from app.review import (agrees, build_cards, evidence_key, render, review_card,
                        run_review, write_json)

FIXTURE = Path(__file__).parent / "fixtures" / "reasoned_demo.json"


def load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def scripted(*answers):
    it = iter(answers)
    return lambda prompt="": next(it)


def quiet(*_a, **_k):
    pass


def add_account(result, key, name, grade, rec, gates=()):
    """Clone the demo account under a new name (synthetic second account)."""
    sig = copy.deepcopy(result["signals"][0])
    sig["signal_id"] = f"idx_{len(result['signals'])}"
    sig["organisation"], sig["excerpt"] = name, f"{name} was fined for something specific."
    result["signals"].append(sig)
    fa = copy.deepcopy(result["filter_score"]["accounts"][0])
    fa.update(account_key=key, display_name=name)
    fa["signals"][0].update(signal_id=sig["signal_id"], organisation=name)
    result["filter_score"]["accounts"].append(fa)
    la = copy.deepcopy(result["llm_reasoning"]["accounts"][0])
    la.update(account_key=key, display_name=name, grade=grade, recommendation=rec,
              gates=list(gates), supporting_signal_ids=[sig["signal_id"]])
    result["llm_reasoning"]["accounts"].append(la)
    return result


# ---- cards ----------------------------------------------------------------
def test_card_built_from_real_output():
    cards = build_cards(load())
    assert len(cards) == 1
    c = cards[0]
    assert c["display_name"] == "Demo Manufacturing Sdn Bhd"
    assert (c["grade"], c["score"], c["recommendation"]) == ("D", 39, "exclude")
    assert c["ai_valid"] is True
    e = c["evidence"][0]
    assert e["verified_against_source"] is True
    assert e["source_url"] == "https://example.com/synthetic-safety"
    assert e["buyer_role_hypothesis"].startswith("HSE manager")


def test_tampered_excerpt_is_flagged_not_verified():
    r = load()
    r["signals"][0]["excerpt"] = "Demo Manufacturing Sdn Bhd won a national safety award."
    c = build_cards(r)[0]
    assert c["evidence"][0]["verified_against_source"] is False
    assert "NOT verified" in render(c)


def test_missing_reasoning_section_gives_clear_error():
    r = load(); del r["llm_reasoning"]
    with pytest.raises(ValueError, match="app.reasoning"):
        build_cards(r)


def test_failed_validation_is_shown_prominently():
    r = load()
    r["llm_reasoning"]["validation"] = {"status": "needs_human_review",
                                         "issues": ["quote not found"], "attempts": 3}
    c = build_cards(r)[0]
    assert c["ai_valid"] is False
    text = render(c)
    assert "did NOT pass validation" in text and "quote not found" in text


# ---- decisions ------------------------------------------------------------
def test_reject_matches_exclude_recommendation_and_needs_reason():
    c = build_cards(load())[0]
    ev = review_card(c, scripted("r", "", "pilot is generic inspection software"), quiet, "john")
    assert ev["decision"] == "reject" and ev["reason"]
    assert ev["agrees_with_recommendation"] is True


def test_approving_against_exclude_needs_reason_and_is_an_override():
    c = build_cards(load())[0]
    ev = review_card(c, scripted("a", "", "client wants this pilot"), quiet, "john")
    assert ev["decision"] == "approve" and ev["reason"] == "client wants this pilot"
    assert ev["agrees_with_recommendation"] is False


def test_invalid_choice_is_asked_again():
    c = build_cards(load())[0]
    ev = review_card(c, scripted("x", "reject", "not relevant"), quiet, "john")
    assert ev["decision"] == "reject"


def test_defer_needs_no_reason():
    c = build_cards(load())[0]
    ev = review_card(c, scripted("d", ""), quiet, "john")
    assert ev["decision"] == "defer" and ev["reason"] is None
    assert ev["agrees_with_recommendation"] is None


def test_edit_records_changes():
    c = build_cards(load())[0]
    ev = review_card(c, scripted("e", "Demo Manufacturing (M) Sdn Bhd", "Plant Manager", "fix name"),
                     quiet, "john")
    assert ev["edits"] == {"display_name": "Demo Manufacturing (M) Sdn Bhd",
                           "buyer_role": "Plant Manager"}


def test_approving_gated_account_needs_reason():
    r = add_account(load(), "beta", "Beta Plastics Sdn Bhd", "H", "human_review", ["sensitive_incident"])
    c = build_cards(r)[1]
    ev = review_card(c, scripted("a", "", "checked incident wording"), quiet, "john")
    assert ev["reason"] == "checked incident wording"
    assert ev["agrees_with_recommendation"] is None  # human-review recommendation


# ---- full run and CRM queue -------------------------------------------------
def test_only_approve_and_edit_reach_crm_queue_and_rejections_are_logged():
    r = add_account(load(), "beta", "Beta Plastics Sdn Bhd", "B", "pursue")
    r = add_account(r, "gamma", "Gamma Steel Sdn Bhd", "C", "research")
    out = run_review(r, "john", ask=scripted("r", "not relevant", "a", "e", "", "Safety Officer", "set role"),
                     say=quiet)
    assert out["complete"] and out["accounts_reviewed"] == 3
    assert out["summary"] == {"reject": 1, "approve": 1, "edit": 1}
    names = [q["display_name"] for q in out["crm_queue"]]
    assert names == ["Beta Plastics Sdn Bhd", "Gamma Steel Sdn Bhd"]
    assert out["crm_queue"][1]["target_buyer_role"] == "Safety Officer"
    assert len(out["events"]) == 3                      # rejection kept as evaluation data
    assert out["not_executed"] == ["write_to_crm"]


def test_unvalidated_ai_text_is_not_copied_into_crm_queue():
    r = load()
    r["llm_reasoning"]["validation"]["status"] = "needs_human_review"
    out = run_review(r, "john", ask=scripted("a", "client request", "ok, manual check done"), say=quiet)
    assert out["crm_queue"] and "ai_reason" not in out["crm_queue"][0]


def test_validated_ai_text_is_kept():
    out = run_review(load(), "john", ask=scripted("a", "", "client wants this pilot"), say=quiet)
    assert out["crm_queue"][0]["ai_reason"]


def test_stopping_early_saves_partial_review():
    r = add_account(load(), "beta", "Beta Plastics Sdn Bhd", "B", "pursue")

    def ask(prompt=""):
        if not hasattr(ask, "n"):
            ask.n = 0
        ask.n += 1
        if ask.n == 1:
            return "r"
        if ask.n == 2:
            return "not relevant"
        raise EOFError
    out = run_review(r, "john", ask=ask, say=quiet)
    assert out["complete"] is False and out["accounts_reviewed"] == 1 and out["accounts_total"] == 2


# ---- keys and files -----------------------------------------------------------
def test_account_key_is_stable_and_changes_with_evidence():
    ev = [{"source_url": "https://x.test/a", "excerpt": "Some  Text here"}]
    same = [{"source_url": "https://x.test/a", "excerpt": "some text HERE"}]
    other = [{"source_url": "https://x.test/a", "excerpt": "different text"}]
    assert evidence_key("acme", ev) == evidence_key("acme", same)
    assert evidence_key("acme", ev) != evidence_key("acme", other)


def test_agrees_mapping():
    assert agrees("reject", "exclude") is True
    assert agrees("approve", "exclude") is False
    assert agrees("approve", "pursue") is True
    assert agrees("reject", "pursue") is False
    assert agrees("approve", "human_review") is None
    assert agrees("defer", "exclude") is None


def test_output_file_is_never_overwritten():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "review.json"
        write_json(p, {"a": 1})
        with pytest.raises(FileExistsError):
            write_json(p, {"a": 2})
        assert json.loads(p.read_text())["a"] == 1


if __name__ == "__main__":  # tiny runner so tests also work without pytest
    import sys
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as e:  # noqa
                failed += 1; print("FAIL", name, repr(e))
    sys.exit(1 if failed else 0)
