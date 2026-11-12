"""Decision Engine and Evaluation History Manager.

Precedence (highest first) for the development loop:
  1. Mandatory governance / security blocks            -> BLOCK
  2. Quality threshold met and all mandatory gates pass -> SUCCESS
  3. Loop guards: budget, timeout, max iterations, stagnation
  4. Otherwise                                          -> RETRY
A quality score can never override a mandatory block.
"""

from __future__ import annotations

from typing import Any

from app.schemas import CheckStatus, Decision, EvaluatorReport, RiskBreakdown, ScoreBreakdown


def failed_criteria(reports: list[EvaluatorReport]) -> list[str]:
    out = []
    for r in reports:
        for c in r.checks:
            if c.status in (CheckStatus.FAILED, CheckStatus.ERROR):
                out.append(f"{r.evaluator}:{c.criterion_id or c.check_id}")
    return sorted(set(out))


def compare_iterations(previous: dict[str, Any] | None, score: float, threshold: float,
                       current_failed: list[str]) -> dict[str, Any]:
    """Evaluation History Manager: compare this iteration with the previous one."""
    if previous is None:
        return {"previous_score": None, "current_score": score, "threshold": threshold, "improvement": None,
                "improvement_pct": None, "resolved_criteria": [], "new_failures": [],
                "remaining_issues": current_failed, "regression": False}
    prev_score = previous["quality_score"]
    prev_failed = set(previous.get("failed_criteria", []))
    cur = set(current_failed)
    improvement = round(score - prev_score, 4)
    return {
        "previous_score": prev_score, "current_score": score, "threshold": threshold, "improvement": improvement,
        "improvement_pct": round(improvement * 100, 1),
        "relative_improvement_pct": round(improvement / prev_score * 100, 1) if prev_score else None,
        "resolved_criteria": sorted(prev_failed - cur), "new_failures": sorted(cur - prev_failed),
        "remaining_issues": sorted(cur), "regression": improvement < 0 or bool(cur - prev_failed),
    }


def decide_development(*, quality: ScoreBreakdown, security: EvaluatorReport, policy: EvaluatorReport,
                       loop: dict[str, Any], limits: dict[str, Any], history: list[dict[str, Any]],
                       comparison: dict[str, Any]) -> tuple[Decision, list[str]]:
    reasons: list[str] = []
    blocking = policy.metrics.get("blocking", [])
    if blocking:
        msgs = [c.message for c in policy.checks if c.check_id in blocking]
        return Decision.BLOCK, [f"Mandatory governance block ({', '.join(blocking)}): {'; '.join(msgs)}"]
    if security.metrics.get("blocking") and limits.get("critical_security_stop", True):
        return Decision.BLOCK, ["Critical security finding: loop stopped (critical security stop condition)."]
    gates_ok = policy.passed and security.passed
    if quality.passed and gates_ok:
        return Decision.SUCCESS, [f"Quality {quality.score:.0%} >= threshold {quality.threshold:.0%} and all "
                                  "mandatory gates passed."]
    if quality.passed and not gates_ok:
        reasons.append(f"Quality {quality.score:.0%} meets threshold but mandatory gates failed: "
                       f"{', '.join(policy.metrics.get('failed', []))}.")
    else:
        reasons.append(f"Quality {quality.score:.0%} below threshold {quality.threshold:.0%}.")
    if loop["cost_usd"] >= limits["budget_usd"] or loop["tokens"] >= limits["max_tokens"]:
        return Decision.BUDGET_EXCEEDED, reasons + [
            f"Spent ${loop['cost_usd']:.4f} / {loop['tokens']} tokens; budget ${limits['budget_usd']:.2f} / "
            f"{limits['max_tokens']} tokens."]
    if loop["elapsed_s"] >= limits["timeout_seconds"]:
        return Decision.ESCALATE, reasons + [f"Execution timeout after {loop['elapsed_s']:.0f}s."]
    if loop["iteration"] >= limits["max_iterations"]:
        return Decision.MAX_ITERATIONS_REACHED, reasons + [f"Reached max iterations ({limits['max_iterations']})."]
    window = limits.get("stagnation_window", 2)
    scores = [h["quality_score"] for h in history] + [quality.score]
    if len(scores) > window:
        recent = [scores[i] - scores[i - 1] for i in range(len(scores) - window, len(scores))]
        if all(d < limits.get("min_improvement", 0.02) and d >= -limits.get("regression_tolerance", 0) for d in recent):
            return Decision.STAGNATION_DETECTED, reasons + [
                f"Improvement below {limits.get('min_improvement', 0.02):.0%} for {window} consecutive iterations."]
    if comparison.get("regression"):
        streak = 1
        for h in reversed(history):
            if (h.get("iteration_comparison") or {}).get("regression"):
                streak += 1
            else:
                break
        if streak >= limits.get("max_consecutive_regressions", 2):
            return Decision.ESCALATE, reasons + [f"{streak} consecutive regressions."]
        reasons.append("Regression detected: " + (", ".join(comparison.get("new_failures", [])) or "score decreased")
                       + ". Feedback includes the regressed criteria.")
    return Decision.RETRY, reasons + ["Structured feedback sent to the Fix Agent."]


def decide_risk(risk: RiskBreakdown, level_req: dict[str, Any]) -> tuple[Decision, list[str]]:
    if any("BLOCK" in o for o in risk.overrides):
        return Decision.BLOCK, [f"Mandatory override: {o}" for o in risk.overrides]
    decision = Decision(level_req.get("decision", "HUMAN_APPROVAL_REQUIRED"))
    reasons = [f"Risk {risk.risk_score}/100 ({risk.risk_level.value})."]
    if decision == Decision.CONTINUE:
        reasons.append("Eligible for automated progression; mandatory validation and branch protection still apply.")
    else:
        reasons.append("Required approvals: " + ", ".join(risk.required_approvals))
    if risk.additional_validation:
        reasons.append("Additional validation: " + ", ".join(risk.additional_validation))
    return decision, reasons


def decide_health(report: EvaluatorReport, final_step: bool, breach_action: str) -> tuple[Decision, list[str]]:
    if not report.passed:
        return Decision(breach_action), ["HEALTH BREACH: " + "; ".join(report.explanation[1:])]
    if final_step:
        return Decision.SUCCESS, ["All health metrics within thresholds at 100% traffic."]
    return Decision.CONTINUE, ["Canary healthy; eligible for promotion to the next rollout step."]
