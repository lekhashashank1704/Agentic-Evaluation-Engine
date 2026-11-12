"""Grader registry. Graders are pure functions of (fixture, output, params); the LLM judge goes
through the model gateway so its cost is metered and its verdicts are replayable."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.harness.gateway import ModelGateway

LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
Grade = dict[str, Any]


def _g(score: float, reason: str) -> Grade:
    return {"score": round(max(0.0, min(1.0, score)), 4), "reason": reason}


def decision_is_success(fx, out, p, ctx) -> Grade:
    ok = out.get("decision") == "SUCCESS"
    return _g(1.0 if ok else 0.0, f"decision {out.get('decision')} {'is' if ok else 'is not'} SUCCESS")


def expected_decision(fx, out, p, ctx) -> Grade:
    exp = fx["expected"]["decision"]
    ok = out.get("decision") == exp
    return _g(1.0 if ok else 0.0, f"decision {out.get('decision')} vs expected {exp}")


def final_quality(fx, out, p, ctx) -> Grade:
    if not p.get("apply_to_all", False) and fx["expected"]["decision"] != "SUCCESS":
        return _g(1.0, "not applicable: case expects a stop decision")
    q, target = out.get("final_quality") or 0.0, fx["expected"].get("min_quality", 0.95)
    return _g(q / target if target else 1.0, f"final quality {q:.0%} vs target {target:.0%}")


def iterations_within(fx, out, p, ctx) -> Grade:
    n, limit = out.get("iterations", 99), fx["expected"].get("max_iterations", 5)
    return _g(1.0 if n <= limit else max(0.0, 1 - (n - limit) / limit), f"{n} iterations (limit {limit})")


def exact_risk_level(fx, out, p, ctx) -> Grade:
    exp, got = fx["expected"]["risk_level"], out.get("risk_level")
    return _g(1.0 if exp == got else 0.0, f"{got} ({out.get('risk_score')}) vs labelled {exp}")


def risk_level_distance(fx, out, p, ctx) -> Grade:
    exp, got = fx["expected"]["risk_level"], out.get("risk_level")
    d = LEVELS.index(got) - LEVELS.index(exp)
    score = 1.0 if d == 0 else 0.6 if d == 1 else 0.4 if d == -1 else 0.0
    kind = "exact" if d == 0 else "over-estimate" if d > 0 else "under-estimate"
    return _g(score, f"{got} ({out.get('risk_score')}) vs labelled {exp}: {kind}")


def _text(out: dict) -> str:
    return (out.get("summary", "") + " " + " ".join(out.get("recommendations", []))).lower()


def keyword_recall(fx, out, p, ctx) -> Grade:
    kws = fx["expected"]["keywords"]
    text = _text(out)
    hits = [k for k in kws if k.lower() in text]
    missing = [k for k in kws if k not in hits]
    return _g(len(hits) / len(kws), f"{len(hits)}/{len(kws)} expected points" + (f"; missing {missing}" if missing else ""))


def _simulated_judge(prompt: str) -> dict:
    """Deterministic stand-in for an LLM judge model (clearly simulated)."""
    req = json.loads(prompt)
    text = req["candidate"].lower()
    checks = {
        "names the change that caused the breach": any(w in text for w in ("migration", "schema", "ledger", "token",
                                                                           "query", "index")),
        "gives a concrete next step": any(w in text for w in ("inspect", "profile", "verify", "add ", "check")),
        "keeps the human in control of remediation": "until the root cause is confirmed" in text,
    }
    met = [c for c in req["rubric"] if checks.get(c, False)]
    return {"score": len(met) / len(req["rubric"]), "met": met, "simulated": True}


def llm_judge(fx, out, p, ctx) -> Grade:
    gateway: ModelGateway = ctx["gateway"]
    prompt = json.dumps({"rubric": p["rubric"], "candidate": _text(out)}, sort_keys=True)
    resp, call = gateway.complete(caller="eval", agent="judge", purpose="llm_judge", model="simulated-judge-v1",
                                  prompt=prompt, responder=_simulated_judge, run_id=ctx.get("run_id"))
    ctx["judge_cost"] = ctx.get("judge_cost", 0.0) + call.cost_usd
    return {**_g(resp["score"], f"judge: {len(resp['met'])}/{len(p['rubric'])} rubric items met"
                 + (" (replayed)" if call.cache_hit else "")), "simulated": True}


GRADERS: dict[str, Callable[..., Grade]] = {
    "decision_is_success": decision_is_success, "expected_decision": expected_decision, "final_quality": final_quality,
    "iterations_within": iterations_within, "exact_risk_level": exact_risk_level,
    "risk_level_distance": risk_level_distance, "keyword_recall": keyword_recall, "llm_judge": llm_judge,
}


def grade_case(spec: dict, fixture: dict, output: dict, ctx: dict) -> tuple[list[Grade], float]:
    grades = []
    total_w = sum(g.get("weight", 1.0) for g in spec["graders"]) or 1.0
    score = 0.0
    for g in spec["graders"]:
        fn = GRADERS[g["type"]]
        res = fn(fixture, output, g, ctx)
        w = g.get("weight", 1.0)
        grades.append({"grader": g["type"], "weight": w, **res})
        score += res["score"] * w / total_w
    return grades, round(score, 4)
