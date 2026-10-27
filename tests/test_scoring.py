"""Run: python -m pytest tests/test_scoring.py -q
Signals are SYNTHETIC but use the same field names as Module 1 output."""
from datetime import date

import asyncio

from app.scoring import apply_scores, org_key, score_result, score_signal

NOW = date(2026, 10, 26)


def sig(**kw):
    base = dict(
        signal_id="s1", document_id="d1", organisation="Example Plastics Sdn Bhd",
        organisation_specific=True, sector="plastics manufacturing",
        safety_topic="vehicle-pedestrian safety", trigger_type="enforcement",
        workflow="forklift pedestrian proximity monitoring", urgency=None,
        excerpt="Example Plastics Sdn Bhd was fined RM50,000 for lacking pedestrian walkways.",
        need_hypothesis="Forklift and pedestrian separation may benefit from monitoring.",
        buyer_role_hypothesis="Safety & Health Officer", confidence=0.9,
        ambiguity_flags=[], recommended_next_step="human_review",
        source_url="https://example.com/a", source_class="regulatory",
        published_at="2026-08-01", evidence_level="requires_semantic_review",
        eligible_for_scoring=True, semantic_review={"status": "supported"},
        rule_checks=[])
    base.update(kw)
    return base


def run(*signals):
    return score_result({"run_id": "r", "signals": list(signals)}, NOW)


# ---- names ---------------------------------------------------------------
def test_org_key_merges_formatting_variants():
    assert org_key("Example Plastics Sdn. Bhd.") == org_key("EXAMPLE PLASTICS SDN BHD")
    assert org_key("Maju Steel Berhad") == org_key("Maju Steel Bhd")
    assert org_key("Alpha Foods (M) Sdn Bhd") == org_key("Alpha Foods Sdn Bhd")


def test_different_companies_not_merged():
    assert org_key("Alpha Foods Sdn Bhd") != org_key("Beta Foods Sdn Bhd")


# ---- scoring ---------------------------------------------------------------
def test_strong_enforcement_signal_scores_well_and_has_no_gates():
    d = score_signal(sig(), NOW)
    assert d["gates"] == []
    assert d["grade"] in ("B", "A")
    assert d["score"]["problem_fit"] == 25


def test_scores_respect_rubric_bounds():
    d = score_signal(sig(urgency="urgent", excerpt="HSE manager cctv camera digital software iot sensor"), NOW)
    caps = dict(problem_fit=25, trigger_strength=20, buyer_authority=20,
                digital_readiness=15, timing_budget=10, evidence_quality=10)
    for k, v in d["score"].items():
        assert 0 <= v <= caps[k]
    assert d["total"] <= 100


def test_discovery_only_is_capped_and_never_above_C():
    d = score_signal(sig(evidence_level="discovery_only", trigger_type="esg_index",
                         eligible_for_scoring=False, buyer_role_hypothesis=None,
                         recommended_next_step="research_more", sector="Food Producers"), NOW)
    assert d["score"]["problem_fit"] <= 8
    assert d["grade"] in ("C", "D")


def test_incident_goes_to_human_review():
    d = score_signal(sig(trigger_type="incident"), NOW)
    assert "sensitive_incident" in d["gates"] and d["grade"] == "H"


def test_unsupported_review_disqualifies_uncertain_escalates():
    assert score_signal(sig(semantic_review={"status": "unsupported"}), NOW)["grade"] == "D"
    assert score_signal(sig(semantic_review={"status": "uncertain"}), NOW)["grade"] == "H"
    assert score_signal(sig(semantic_review={"status": "pending"}), NOW)["grade"] == "H"


def test_rule_checks_without_supported_review_escalate():
    d = score_signal(sig(rule_checks=["Claim value not located"],
                         semantic_review={"status": "not_required"}), NOW)
    assert d["grade"] == "H"


def test_stale_signal_disqualified():
    d = score_signal(sig(published_at="2018-01-01"), NOW)
    assert "stale" in d["gates"] and d["grade"] == "D"


def test_unknown_date_not_gated_but_scores_lower_on_evidence():
    dated = score_signal(sig(), NOW)
    undated = score_signal(sig(published_at=None), NOW)
    assert "stale" not in undated["gates"]
    assert undated["total"] < dated["total"]


def test_non_manufacturing_sector_gated():
    d = score_signal(sig(sector="retail banking"), NOW)
    assert "non_manufacturing" in d["gates"] and d["grade"] == "D"


def test_food_producers_is_not_flagged_non_manufacturing():
    assert "non_manufacturing" not in score_signal(sig(sector="Food Producers"), NOW)["gates"]


# ---- entities and dedupe -----------------------------------------------------
def test_missing_organisation_is_unresolved_and_reviewed():
    out = run(sig(signal_id="x", organisation=None, organisation_specific=False))
    assert out["summary"]["unresolved"] == 1 and out["summary"]["accounts"] == 0
    assert out["unresolved_signals"][0]["grade"] == "H"


def test_same_company_two_outlets_become_one_account_with_duplicate_gate():
    a = sig(signal_id="a", source_url="https://example.com/a")
    b = sig(signal_id="b", organisation="EXAMPLE PLASTICS SDN. BHD.",
            source_url="https://other.example/b")
    out = run(a, b)
    assert out["summary"]["accounts"] == 1
    gates = [g for s in out["accounts"][0]["signals"] for g in s["gates"]]
    assert gates.count("duplicate") == 1
    assert len(out["accounts"][0]["aliases"]) == 2


def test_different_excerpts_same_company_both_kept():
    a = sig(signal_id="a")
    b = sig(signal_id="b", excerpt="Example Plastics Sdn Bhd was also fined for machine guarding.")
    out = run(a, b)
    gates = [g for s in out["accounts"][0]["signals"] for g in s["gates"]]
    assert "duplicate" not in gates


def test_near_names_flagged_as_merge_candidates_not_merged():
    out = run(sig(signal_id="a", organisation="Example Plastic Industries Sdn Bhd", excerpt="x" * 20 + " one"),
              sig(signal_id="b", organisation="Example Plastics Industries Sdn Bhd", excerpt="y" * 20 + " two"))
    assert out["summary"]["accounts"] == 2
    assert len(out["merge_candidates"]) == 1


def test_output_never_claims_later_stages_ran():
    out = run(sig())
    assert "write_to_crm" in out["not_executed"]
    assert all(a["needs_human_review"] for a in out["accounts"])


def test_empty_input_is_valid():
    out = run()
    assert out["summary"]["signals"] == 0 and out["accounts"] == []


def test_signals_without_ids_do_not_collide():
    # demo-mode signals have no signal_id
    a = {k: v for k, v in sig(excerpt="Example Plastics Sdn Bhd was fined for walkways one.").items() if k != "signal_id"}
    b = {k: v for k, v in sig(excerpt="Example Plastics Sdn Bhd was fined for guarding two.").items() if k != "signal_id"}
    out = run(a, b)
    gates = [g for s in out["accounts"][0]["signals"] for g in s["gates"]]
    assert "duplicate" not in gates and len(out["accounts"][0]["signals"]) == 2


def test_real_demo_signal_from_module1():
    demo = {
        "document_id": "doc_demo", "organisation": "Demo Manufacturing Sdn Bhd",
        "organisation_specific": True, "sector": "manufacturing",
        "safety_topic": "safety inspection", "trigger_type": "initiative",
        "workflow": "safety inspection and corrective-action tracking", "urgency": None,
        "excerpt": "Demo Manufacturing Sdn Bhd is piloting digital safety inspections and corrective-action tracking at its Malaysian factory.",
        "need_hypothesis": "Synthetic example: inspection evidence and corrective-action records may benefit from a shared workflow.",
        "buyer_role_hypothesis": "HSE manager (hypothesis only)", "confidence": 0.9,
        "ambiguity_flags": ["synthetic_example"], "recommended_next_step": "human_review",
        "source_url": "https://example.com/synthetic-safety", "source_class": "company_owned",
        "evidence_level": "requires_semantic_review", "published_at": "2026-10-01",
        "requires_human_review": True}
    out = run(demo)
    assert out["summary"]["accounts"] == 1
    assert out["accounts"][0]["signals"][0]["gates"] == []


def test_apply_scores_records_stage_and_keeps_later_stages_unexecuted():
    result = {"stages": {}, "signals": [sig()],
              "not_executed": ["filter_score", "llm_reasoning", "customer_review", "write_to_crm"]}
    out = apply_scores(result, score_result(result, NOW))
    assert out["stages"]["filter_score"]["status"] == "completed"
    assert "filter_score" not in out["not_executed"]
    assert out["not_executed"] == ["llm_reasoning", "customer_review", "write_to_crm"]


def test_tool_runner_dispatches_filter_score_but_model_cannot_see_it():
    from app.tools import TOOLS, ToolRunner
    runner = ToolRunner()
    out = asyncio.run(runner.call("filter_score", result={"signals": [sig()]}))
    assert out["summary"]["accounts"] == 1
    assert runner.trace[-1]["tool"] == "filter_score" and runner.trace[-1]["status"] == "ok"
    assert len(TOOLS) == 6
    assert all(t["function"]["name"] != "filter_score" for t in TOOLS)


if __name__ == "__main__":  # tiny runner so tests work without pytest
    import sys
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as e:  # noqa
                failed += 1; print("FAIL", name, repr(e))
    sys.exit(1 if failed else 0)
