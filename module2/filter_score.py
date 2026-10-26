"""
Module 2 (v0): Filter + score.

Consumes the result JSON exported by Module 1 (fetch + clean + extract) and
produces deterministic, explainable decisions. NO LLM calls, NO CRM writes.

    python filter_score.py result.json --out decisions.json

Input fields used (from Module 1 signals): signal_id, organisation,
organisation_specific, sector, trigger_type, workflow, safety_topic,
need_hypothesis, buyer_role_hypothesis, urgency, excerpt, source_url,
source_class, published_at, evidence_level, eligible_for_scoring,
semantic_review{status}, rule_checks, ambiguity_flags.

!! All weights/keyword lists below are PLACEHOLDERS for the team to calibrate
!! and document (rubric dimensions and bounds follow Research Brief 1a).
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from datetime import date, datetime, timezone

RUBRIC_VERSION = "v0-placeholder"

# ---- rubric configuration (calibrate these) --------------------------------
MAX_POINTS = dict(problem_fit=25, trigger_strength=20, buyer_authority=20,
                  digital_readiness=15, timing_budget=10, evidence_quality=10)

# Words showing the signal touches the target workflow (CV safety monitoring).
TARGET_KEYWORDS = [
    "forklift", "pedestrian", "walkway", "vehicle", "machine", "machinery",
    "guard", "pinch", "entrapment", "amputat", "ppe", "helmet", "protective equipment",
    "proximity", "danger zone", "camera", "cctv", "video analytic",
]
MFG_TERMS = ["manufactur", "factory", "kilang", "plant", "fabricat", "assembly",
             "plastic", "rubber", "metal", "steel", "electronic", "electrical",
             "semiconductor", "chemical", "food", "beverage", "textile",
             "furniture", "automotive", "packaging", "paper", "glass", "cement",
             "industrial"]
NON_MFG_TERMS = ["bank", "insurance", "financial services", "real estate",
                 "retail", "hospital", "school", "education", "hotel",
                 "restaurant", "software", "media", "telecommunication"]
DIGITAL_TERMS = ["digital", "software", "iot", "cctv", "camera", "automation",
                 "smart factory", "industry 4.0", "sensor", "analytics", "erp"]
ROLE_TERMS = ["safety officer", "safety and health", "safety & health", "hse",
              "ehs", "sho", "plant manager", "factory manager", "operations manager"]

TRIGGER_POINTS = dict(enforcement=18, incident=14, tender=16, project=12,
                      initiative=12, operational_change=12, hiring=10,
                      guidance=6, esg_index=6, certification=6,
                      association_activity=4, other=3)

STALE_YEARS = 5
NEAR_NAME_RATIO = 0.88

# Gates that send a lead to human review (H); others disqualify (D).
GATES_TO_H = {"unverified_entity", "sensitive_incident", "pending_review"}
GATES_TO_D = {"non_manufacturing", "stale", "duplicate", "unsupported_claim"}


# ---- helpers ---------------------------------------------------------------
def parse_date(v) -> date | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def norm_text(s: str) -> str:
    s = (s or "").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", s).strip().casefold()


_SUFFIXES = re.compile(
    r"\b(sendirian berhad|sdn bhd|bhd|berhad|plc|ltd|limited|pte)\b|\(m\)|\(malaysia\)")


def org_key(name: str) -> str:
    """Normalised key for grouping: drops legal suffixes and punctuation.
    Deterministic; no guessing. 'Example Plastics Sdn. Bhd.' == 'EXAMPLE PLASTICS SDN BHD'."""
    s = (name or "").casefold().replace(".", "")
    s = _SUFFIXES.sub(" ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _blob(sig: dict) -> str:
    parts = [sig.get(k) or "" for k in
             ("sector", "workflow", "safety_topic", "need_hypothesis", "excerpt")]
    return norm_text(" ".join(parts))


def _hits(text: str, terms: list[str]) -> int:
    return sum(1 for t in terms if t in text)


def _recency(age_years: float | None) -> float:
    if age_years is None:
        return 0.5
    if age_years <= 1:
        return 1.0
    if age_years <= 3:
        return 0.7
    return 0.4


# ---- scoring one signal ------------------------------------------------------
def score_signal(sig: dict, now: date | None = None) -> dict:
    now = now or datetime.now(timezone.utc).date()
    text = _blob(sig)
    discovery = sig.get("evidence_level") == "discovery_only"
    pub = parse_date(sig.get("published_at"))
    age = ((now - pub).days / 365.25) if pub else None
    rec = _recency(age)
    review = (sig.get("semantic_review") or {}).get("status")
    why: dict[str, str] = {}

    # problem fit
    mfg = _hits(norm_text(sig.get("sector") or "") + " " + text, MFG_TERMS) > 0
    kw = _hits(text, TARGET_KEYWORDS)
    fit = (8 if mfg else 0) + (17 if kw >= 2 else 10 if kw == 1 else 0)
    if discovery:
        fit = min(fit, 8)
        why["problem_fit"] = "discovery-only: no direct workflow evidence, capped at 8"
    else:
        why["problem_fit"] = f"manufacturing={mfg}, workflow keyword hits={kw}"

    # trigger strength
    trig = round(TRIGGER_POINTS.get(sig.get("trigger_type"), 3) * rec)
    why["trigger_strength"] = f"{sig.get('trigger_type')} x recency {rec}"

    # buyer authority (role is a hypothesis; stronger if excerpt names a role)
    auth = 0
    if sig.get("buyer_role_hypothesis"):
        auth = 8
        if _hits(norm_text(sig.get("excerpt") or ""), ROLE_TERMS):
            auth = 14
    why["buyer_authority"] = f"role hypothesis={'yes' if sig.get('buyer_role_hypothesis') else 'no'}"

    # digital readiness
    dig = min(15, 5 * _hits(text, DIGITAL_TERMS))
    why["digital_readiness"] = "digital/system terms in the cited text"

    # timing / budget
    timing = (6 if sig.get("trigger_type") in ("tender", "project", "hiring") else 0) \
        + (4 if sig.get("urgency") else 0)
    why["timing_budget"] = "tender/project/hiring and stated urgency"

    # evidence quality
    ev = (4 if sig.get("source_class") == "regulatory" else 2) \
        + (2 if pub else 0) + (2 if rec == 1.0 else 0) + (2 if review == "supported" else 0)
    why["evidence_quality"] = "primary source, dated, recent, review-supported"

    breakdown = dict(problem_fit=min(fit, 25), trigger_strength=min(trig, 20),
                     buyer_authority=min(auth, 20), digital_readiness=min(dig, 15),
                     timing_budget=min(timing, 10), evidence_quality=min(ev, 10))
    total = sum(breakdown.values())

    # hard gates
    gates: list[str] = []
    if not sig.get("organisation") or not sig.get("organisation_specific"):
        gates.append("unverified_entity")
    if sig.get("trigger_type") == "incident":
        gates.append("sensitive_incident")
    if review == "unsupported":
        gates.append("unsupported_claim")
    elif review in ("uncertain", "pending") or (sig.get("rule_checks") and review != "supported"):
        gates.append("pending_review")
    if age is not None and age > STALE_YEARS:
        gates.append("stale")
    sector_txt = norm_text(sig.get("sector") or "")
    if sector_txt and _hits(sector_txt, NON_MFG_TERMS) and not _hits(sector_txt, MFG_TERMS):
        gates.append("non_manufacturing")

    return dict(signal_id=sig.get("signal_id"), organisation=sig.get("organisation"),
                source_url=sig.get("source_url"), score=breakdown, total=total,
                gates=gates, grade=grade_from(total, gates, discovery),
                discovery_only=discovery, reasons=why)


def grade_from(total: int, gates: list[str], discovery_only: bool = False) -> str:
    if any(g in GATES_TO_H for g in gates):
        return "H"
    if gates:
        return "D"
    g = "A" if total >= 80 else "B" if total >= 60 else "C" if total >= 40 else "D"
    if discovery_only and g in ("A", "B"):
        g = "C"  # discovery-only evidence can never exceed 'research candidate'
    return g


# ---- grouping + dedupe -------------------------------------------------------
def score_result(result: dict, now: date | None = None) -> dict:
    # Demo-mode signals carry no signal_id; give them a positional one so
    # grouping and duplicate detection never collide on None.
    signals = [{**s, "signal_id": s.get("signal_id") or f"idx_{i}"}
               for i, s in enumerate(result.get("signals", []))]
    scored = [score_signal(s, now) for s in signals]
    by_id = {s["signal_id"]: s for s in signals}

    unresolved, groups = [], {}
    for d in scored:
        name = d["organisation"]
        if not name:
            unresolved.append(d)
            continue
        groups.setdefault(org_key(name), []).append(d)

    # duplicate excerpts within an account (same text, different URL/outlet)
    for items in groups.values():
        items.sort(key=lambda d: -d["total"])
        seen: set[str] = set()
        for d in items:
            ex = norm_text(by_id.get(d["signal_id"], {}).get("excerpt", ""))
            if ex in seen:
                d["gates"].append("duplicate")
                d["grade"] = grade_from(d["total"], d["gates"], d["discovery_only"])
            seen.add(ex)

    accounts = []
    for key, items in groups.items():
        live = [d for d in items if "duplicate" not in d["gates"]] or items
        best = max(live, key=lambda d: d["total"])
        accounts.append(dict(
            account_key=key,
            display_name=best["organisation"],
            aliases=sorted({d["organisation"] for d in items}),
            best_grade=best["grade"], best_score=best["total"],
            needs_human_review=True,
            signals=items))
    accounts.sort(key=lambda a: -a["best_score"])

    keys = [a["account_key"] for a in accounts]
    merge_candidates = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            r = difflib.SequenceMatcher(None, keys[i], keys[j]).ratio()
            if r >= NEAR_NAME_RATIO:
                merge_candidates.append(dict(a=keys[i], b=keys[j], ratio=round(r, 3)))

    counts: dict[str, int] = {}
    for a in accounts:
        counts[a["best_grade"]] = counts.get(a["best_grade"], 0) + 1
    return dict(
        rubric_version=RUBRIC_VERSION,
        source_run_id=result.get("run_id"),
        summary=dict(signals=len(scored), accounts=len(accounts),
                     unresolved=len(unresolved), grades=counts),
        accounts=accounts, unresolved_signals=unresolved,
        merge_candidates=merge_candidates,
        not_executed=["llm_reasoning", "customer_review", "write_to_crm"])


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("result_json")
    ap.add_argument("--out")
    a = ap.parse_args()
    with open(a.result_json, encoding="utf-8") as f:
        out = score_result(json.load(f))
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if a.out:
        with open(a.out, "x", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text + "\n")
