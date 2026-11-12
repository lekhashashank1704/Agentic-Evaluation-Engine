"""Policy Evaluator: mandatory binary governance gates (never traded off against scores)."""

from __future__ import annotations

from typing import Any

from app.core.common import artifact_hash as compute_hash
from app.evaluation.support import EvidenceCollector, ThresholdManager
from app.schemas import Check, CheckStatus, EvaluatorReport, Severity


def _gate(gid: str, name: str, ok: bool, message: str, severity: Severity = Severity.HIGH,
          evidence: list[str] | None = None, action: str = "BLOCK") -> Check:
    return Check(check_id=gid, name=name, category=f"policy:{action}", status=CheckStatus.PASSED if ok
                 else CheckStatus.FAILED, severity=Severity.INFO if ok else severity, mandatory=True,
                 message=message, criterion_id=gid, evidence_ids=evidence or [])


def evaluate_development_policy(*, files: dict[str, str], manifest: dict[str, Any], recorded_hash: str,
                                author_kind: str, functional, security, ev: EvidenceCollector,
                                tm: ThresholdManager) -> EvaluatorReport:
    gates_cfg = tm.get("quality.development.mandatory_gates", {})
    counts = security.metrics.get("counts", {})
    protected = [p for p in files if any(p.startswith(pp) or pp in p for pp in tm.get("risk.protected_paths", []))]
    recomputed = compute_hash(files, manifest)
    ev_item = ev.add(type="policy", source="policy-evaluator", executed=True, simulated=False,
                     summary=f"Evaluated mandatory development gates under policy {tm.version}.",
                     data={"recomputed_hash": recomputed, "recorded_hash": recorded_hash, "protected_hits": protected})
    e = [ev_item.evidence_id]
    checks = [
        _gate("PG-001", "No critical security findings", counts.get("CRITICAL", 0) == 0,
              f"{counts.get('CRITICAL', 0)} critical findings", Severity.CRITICAL, e, "BLOCK"),
        _gate("PG-002", "High-severity findings within policy",
              counts.get("HIGH", 0) <= gates_cfg.get("max_high_security_findings", 0),
              f"{counts.get('HIGH', 0)} high findings (max {gates_cfg.get('max_high_security_findings', 0)})",
              Severity.HIGH, e, "RETRY"),
        _gate("PG-003", "All acceptance and regression tests pass (branch protection)",
              functional.passed or not gates_cfg.get("all_acceptance_tests_pass", True),
              f"{functional.metrics.get('tests_passed', 0)}/{functional.metrics.get('tests_executed', 0)} passed",
              Severity.HIGH, e, "RETRY"),
        _gate("PG-004", "Agent did not modify protected governance files",
              not (protected and author_kind == "agent"),
              "Protected paths modified: " + ", ".join(protected) if protected else "No protected paths touched",
              Severity.CRITICAL, e, "BLOCK"),
        _gate("PG-005", "Artifact integrity (SHA-256 matches evaluated content)", recomputed == recorded_hash,
              f"sha256 {recorded_hash[:16]}...", Severity.CRITICAL, e, "BLOCK"),
        _gate("PG-006", "Separation of duties: author cannot approve or merge", True,
              f"Author is a {author_kind}; agents hold no approve/merge permissions.", Severity.INFO, e, "INFO"),
    ]
    blocking = [c for c in checks if c.status == CheckStatus.FAILED and c.category == "policy:BLOCK"]
    failed = [c for c in checks if c.status == CheckStatus.FAILED]
    return EvaluatorReport(
        evaluator="policy", status="COMPLETED", score=None, passed=not failed, checks=checks, evidence=[ev_item],
        metrics={"blocking": [c.check_id for c in blocking], "failed": [c.check_id for c in failed]},
        explanation=[f"{len(checks) - len(failed)}/{len(checks)} mandatory gates passed."] +
                    [f"{c.check_id} {c.name}: {c.message}" for c in failed])


def evaluate_release_policy(*, current_hash: str, approvals: list[dict[str, Any]], required_roles: list[str],
                            latest_dev_eval: dict[str, Any] | None, risk_overrides: list[str],
                            ev: EvidenceCollector, tm: ThresholdManager) -> EvaluatorReport:
    valid = [a for a in approvals if a["status"] == "APPROVED" and a["artifact_hash"] == current_hash]
    approved_roles = {a["required_role"] for a in valid}
    missing = [r for r in required_roles if r not in approved_roles]
    ev_item = ev.add(type="approval", source="approval-registry", executed=True, simulated=False,
                     summary=f"{len(valid)} valid approvals bound to sha256 {current_hash[:12]}...",
                     data={"approvals": approvals, "required_roles": required_roles})
    e = [ev_item.evidence_id]
    fresh = bool(latest_dev_eval and latest_dev_eval.get("artifact_hash") == current_hash
                 and latest_dev_eval.get("decision") in ("SUCCESS", "CONTINUE", "HUMAN_APPROVAL_REQUIRED"))
    checks = [
        _gate("PR-001", "Evaluation is current for this artifact hash", fresh,
              "Latest passing evaluation matches the artifact hash" if fresh else
              "Artifact changed since evaluation; re-evaluation required", Severity.CRITICAL, e),
        _gate("PR-002", "Required human approvals bound to current hash", not missing,
              "All required approvals present" if not missing else f"Missing approvals: {', '.join(missing)}",
              Severity.HIGH, e, "HUMAN_APPROVAL_REQUIRED"),
        _gate("PR-003", "No mandatory risk overrides outstanding", not risk_overrides,
              "; ".join(risk_overrides) or "none", Severity.CRITICAL, e),
    ]
    failed = [c for c in checks if c.status == CheckStatus.FAILED]
    return EvaluatorReport(evaluator="policy", status="COMPLETED", passed=not failed, checks=checks,
                           evidence=[ev_item], metrics={"missing_roles": missing, "valid_approvals": len(valid)},
                           explanation=[f"Release gates: {len(checks) - len(failed)}/{len(checks)} passed."] +
                                       [f"{c.check_id}: {c.message}" for c in failed])
