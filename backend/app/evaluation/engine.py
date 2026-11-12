"""Central Evaluation Engine: one reusable service invoked by every lifecycle agent.

Components: Quality (objective function), Functional Test, Security, Code Quality,
PR Review, Risk, Policy and Runtime Health evaluators, plus the Decision Engine,
Evidence Collector, Threshold Manager and Evaluation History Manager.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.common import next_id
from app.evaluation import decision as D
from app.evaluation.code_quality import evaluate_code_quality
from app.evaluation.functional import evaluate_functional
from app.evaluation.health import evaluate_health
from app.evaluation.policy import evaluate_development_policy, evaluate_release_policy
from app.evaluation.pr_review import evaluate_pr_review
from app.evaluation.risk import change_facts, evaluate_risk
from app.evaluation.security import evaluate_security
from app.evaluation.support import EvidenceCollector, ObjectiveFunction, ThresholdManager
from app.models import Evaluation, Postmortem
from app.schemas import (
    CheckStatus,
    Decision,
    EvaluationResult,
    EvaluatorReport,
    FeedbackItem,
    RiskBreakdown,
    Severity,
    utcnow,
)

FEEDBACK_PREFIX = {"functional_test": "test", "security": "security", "code_quality": "lint",
                   "pr_review": "pr_review", "policy": "policy"}


def change_categories(facts: dict[str, Any]) -> list[str]:
    cats = []
    if facts.get("schema_files"):
        cats.append("db_schema_change")
    if facts.get("auth_files"):
        cats.append("auth_change")
    if facts.get("payment_files"):
        cats.append("payment_change")
    if facts.get("dependencies_added") or facts.get("dependencies_changed"):
        cats.append("dependency_change")
    if facts.get("infra_files"):
        cats.append("infrastructure_change")
    if facts.get("config_files"):
        cats.append("config_change")
    if facts.get("api_files"):
        cats.append("api_change")
    return cats


def build_feedback(reports: list[EvaluatorReport]) -> list[FeedbackItem]:
    items: list[FeedbackItem] = []
    n = 0
    for r in reports:
        prefix = FEEDBACK_PREFIX.get(r.evaluator)
        if not prefix:
            continue
        for c in r.checks:
            if c.status not in (CheckStatus.FAILED, CheckStatus.ERROR):
                continue
            n += 1
            crit = c.criterion_id or c.check_id
            if r.evaluator == "functional_test":
                crit = c.name
            if r.evaluator == "policy" and c.check_id == "PG-004":
                crit = "protected_path"
            items.append(FeedbackItem(
                feedback_id=f"FB-{n:03d}", source=r.evaluator, criterion=f"{prefix}:{crit}",
                severity=c.severity if c.severity != Severity.INFO else Severity.MEDIUM,
                message=f"{c.name}: {c.message}"[:300],
                remediation=next((f.remediation for f in r.findings if f.rule_id == c.criterion_id), "")))
    return items


class EvaluationEngine:
    def __init__(self, db: Session, policy: dict[str, Any]):
        self.db = db
        self.tm = ThresholdManager(policy)

    # ------------------------------------------------------------------ helpers
    def _persist(self, result: EvaluationResult) -> EvaluationResult:
        self.db.add(Evaluation(
            id=result.evaluation_id, execution_id=result.execution_id, artifact_version=result.artifact_version,
            artifact_hash=result.artifact_hash, evaluation_type=result.evaluation_type,
            lifecycle_stage=result.lifecycle_stage, iteration=result.iteration, decision=result.decision.value,
            quality_score=result.quality_score, risk_score=result.risk_score, result=result.model_dump(mode="json")))
        self.db.flush()
        return result

    def _base(self, ctx: dict[str, Any], stage: str, etype: str) -> dict[str, Any]:
        return {"evaluation_id": next_id(self.db, "EVAL"), "project_id": ctx["project_id"],
                "ticket_id": ctx["ticket_id"], "artifact_id": ctx["artifact_id"],
                "artifact_version": ctx["artifact_version"], "artifact_hash": ctx["artifact_hash"],
                "lifecycle_stage": stage, "evaluation_type": etype, "policy_version": self.tm.version,
                "execution_id": ctx.get("execution_id")}

    def historical_incidents(self, service: str, categories: list[str]) -> list[dict[str, Any]]:
        lookback = self.tm.get("feedback.lookback_days", 180)
        since = utcnow() - timedelta(days=lookback)
        out = []
        for pm in self.db.execute(select(Postmortem)).scalars():
            completed = pm.completed_at if pm.completed_at.tzinfo else pm.completed_at.replace(tzinfo=since.tzinfo)
            if completed < since:
                continue
            shared = set(pm.change_categories or []) & set(categories)
            if shared or pm.data.get("service") == service:
                out.append({"id": pm.incident_id, "categories": sorted(shared), "service": pm.data.get("service")})
        return out

    # ------------------------------------------------------------------ development
    def evaluate_development(self, ctx: dict[str, Any], files: dict[str, str], diff: str, manifest: dict[str, Any],
                             ticket: dict[str, Any], iteration: int, history: list[dict[str, Any]],
                             loop: dict[str, Any], limits: dict[str, Any]) -> tuple[EvaluationResult,
                                                                                     list[FeedbackItem]]:
        ev = EvidenceCollector(prefix=f"EV-{ctx['artifact_version']}")
        functional = evaluate_functional(files, ticket, ev)
        security = evaluate_security(files, ev, self.tm)
        quality = evaluate_code_quality(files, ev, self.tm)
        pr = evaluate_pr_review(files, diff, ticket, ev)
        policy = evaluate_development_policy(files=files, manifest=manifest, recorded_hash=ctx["artifact_hash"],
                                             author_kind=ctx.get("author_kind", "agent"), functional=functional,
                                             security=security, ev=ev, tm=self.tm)
        spec = self.tm.get("quality.development.objective", {})
        threshold = self.tm.get("quality.development.threshold", 0.95)
        objective = ObjectiveFunction("development_quality", spec, threshold)
        breakdown = objective.evaluate(
            values={"test_pass_rate": functional.score or 0.0, "requirement_coverage": pr.score or 0.0,
                    "code_hygiene": quality.score or 0.0},
            evidence={"test_pass_rate": [e.evidence_id for e in functional.evidence],
                      "requirement_coverage": [e.evidence_id for e in pr.evidence],
                      "code_hygiene": [e.evidence_id for e in quality.evidence]},
            explanations={"test_pass_rate": functional.explanation[0], "requirement_coverage": pr.explanation[0],
                          "code_hygiene": quality.explanation[0]})
        reports = [functional, security, quality, pr, policy]
        failed = D.failed_criteria(reports)
        previous = history[-1] if history else None
        comparison = D.compare_iterations(previous, breakdown.score, threshold, failed)
        decision, reasons = D.decide_development(quality=breakdown, security=security, policy=policy, loop=loop,
                                                 limits=limits, history=history, comparison=comparison)
        explanation = [
            f"Quality score {breakdown.score:.1%} = " + " + ".join(
                f"{m.weight:.0%} x {m.metric} ({m.value:.0%})" for m in breakdown.metrics),
            *reasons,
        ]
        for r in reports:
            explanation.extend(f"[{r.evaluator}] {x}" for x in r.explanation[:2])
        result = EvaluationResult(
            **self._base(ctx, "development", "quality"), quality_score=breakdown.score, quality_threshold=threshold,
            decision=decision, checks=[c for r in reports for c in r.checks], failed_criteria=failed,
            evidence=ev.items, explanation=explanation, iteration=iteration, score_breakdown=breakdown,
            evaluator_reports=reports, iteration_comparison=comparison)
        self._persist(result)
        feedback = build_feedback(reports) if decision == Decision.RETRY else []
        return result, feedback

    # ------------------------------------------------------------------ risk gate
    def evaluate_risk_gate(self, ctx: dict[str, Any], files: dict[str, str], diff: str, ticket: dict[str, Any],
                           dev_result: EvaluationResult) -> tuple[EvaluationResult, RiskBreakdown, list[str]]:
        ev = EvidenceCollector(prefix=f"EV-{ctx['artifact_version']}-R")
        sec = next(r for r in dev_result.evaluator_reports if r.evaluator == "security")
        pr = next(r for r in dev_result.evaluator_reports if r.evaluator == "pr_review")
        facts = change_facts(files, diff, self.tm.get("risk.path_classes", {}))
        cats = change_categories(facts)
        history = self.historical_incidents(ticket.get("affected_application", ""), cats)
        report, breakdown = evaluate_risk(files, diff, ticket, sec.findings, history,
                                          pr.metrics.get("unnecessary_files", []), ctx.get("author_kind") == "agent",
                                          ev, self.tm)
        level_req = self.tm.get(f"risk.level_requirements.{breakdown.risk_level.value}", {})
        decision, reasons = D.decide_risk(breakdown, level_req)
        checks = [c for c in dev_result.checks if c.category.startswith("security")]
        result = EvaluationResult(
            **self._base(ctx, "development", "quality_and_risk"), quality_score=dev_result.quality_score,
            quality_threshold=dev_result.quality_threshold, risk_score=breakdown.risk_score,
            risk_level=breakdown.risk_level, decision=decision, checks=checks,
            failed_criteria=[c.criterion_id or c.check_id for c in checks if c.status == CheckStatus.FAILED],
            evidence=ev.items, explanation=reasons + report.explanation, risk_breakdown=breakdown,
            evaluator_reports=[report], iteration=dev_result.iteration)
        self._persist(result)
        return result, breakdown, cats

    # ------------------------------------------------------------------ release policy
    def evaluate_release(self, ctx: dict[str, Any], approvals: list[dict[str, Any]], required_roles: list[str],
                         latest_dev_eval: dict[str, Any] | None, overrides: list[str]) -> EvaluationResult:
        ev = EvidenceCollector(prefix=f"EV-{ctx['artifact_version']}-P")
        report = evaluate_release_policy(current_hash=ctx["artifact_hash"], approvals=approvals,
                                         required_roles=required_roles, latest_dev_eval=latest_dev_eval,
                                         risk_overrides=overrides, ev=ev, tm=self.tm)
        failed = [c for c in report.checks if c.status == CheckStatus.FAILED]
        decision = Decision.CONTINUE if report.passed else (
            Decision.HUMAN_APPROVAL_REQUIRED if all(c.category == "policy:HUMAN_APPROVAL_REQUIRED" for c in failed)
            else Decision.BLOCK)
        result = EvaluationResult(**self._base(ctx, "release", "policy"), decision=decision, checks=report.checks,
                                  failed_criteria=[c.check_id for c in failed], evidence=ev.items,
                                  explanation=report.explanation, evaluator_reports=[report])
        return self._persist(result)

    # ------------------------------------------------------------------ runtime health
    def evaluate_runtime(self, ctx: dict[str, Any], canary: dict[str, Any], baseline: dict[str, Any],
                         traffic: int, final_step: bool) -> EvaluationResult:
        ev = EvidenceCollector(prefix=f"EV-{ctx['artifact_version']}-H{traffic}")
        report = evaluate_health(canary, baseline, traffic, ev, self.tm, "synthetic-telemetry-generator")
        decision, reasons = D.decide_health(report, final_step, self.tm.get("health.breach_action", "ROLLBACK"))
        result = EvaluationResult(
            **self._base(ctx, "operations", "runtime_health"), decision=decision, checks=report.checks,
            failed_criteria=[v["metric"] for v in report.metrics["violated"]], evidence=ev.items,
            explanation=reasons + report.explanation[:1], evaluator_reports=[report],
            health_metrics={"canary": {k: v for k, v in canary.items() if k != "series"},
                            "baseline": {k: v for k, v in baseline.items() if k != "series"},
                            "series": canary.get("series", []), "traffic_percent": traffic,
                            "violated": report.metrics["violated"], "severity": report.metrics["severity"]})
        return self._persist(result)
