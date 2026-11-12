"""Runtime Health Evaluator: compares telemetry with configurable thresholds and SLOs."""

from __future__ import annotations

from typing import Any

from app.evaluation.support import EvidenceCollector, ThresholdManager
from app.schemas import Check, CheckStatus, EvaluatorReport, Severity


def evaluate_health(canary: dict[str, Any], baseline: dict[str, Any], traffic_percent: int,
                    ev: EvidenceCollector, tm: ThresholdManager, telemetry_source: str) -> EvaluatorReport:
    thresholds = tm.get("health.thresholds", {})
    sev1 = set(tm.get("health.sev1_metrics", []))
    ev_item = ev.add(type="telemetry", source=telemetry_source, executed=True, simulated=True,
                     summary=f"Canary telemetry at {traffic_percent}% traffic over "
                             f"{canary.get('requests', 0)} requests (synthetic).",
                     data={"canary": canary, "baseline": baseline})
    checks, violated = [], []
    for metric, cfg in thresholds.items():
        value = canary.get(metric)
        if value is None:
            checks.append(Check(check_id=f"HG-{metric}", name=cfg.get("label", metric), category="health",
                                status=CheckStatus.SKIPPED, message="metric unavailable"))
            continue
        limit = cfg.get("max", cfg.get("min"))
        bad = (value > cfg["max"]) if "max" in cfg else (value < cfg["min"])
        op = "<=" if "max" in cfg else ">="
        base = baseline.get(metric)
        delta = f"; baseline {base}{cfg.get('unit', '')}" if base is not None else ""
        if bad:
            violated.append({"metric": metric, "label": cfg.get("label", metric), "value": value, "limit": limit,
                             "operator": op, "unit": cfg.get("unit", ""), "baseline": base})
        checks.append(Check(
            check_id=f"HG-{metric}", name=cfg.get("label", metric), category="health",
            status=CheckStatus.FAILED if bad else CheckStatus.PASSED,
            severity=(Severity.CRITICAL if metric in sev1 else Severity.HIGH) if bad else Severity.INFO,
            mandatory=True, criterion_id=metric, evidence_ids=[ev_item.evidence_id],
            message=f"{value}{cfg.get('unit', '')} (allowed {op} {limit}{cfg.get('unit', '')}{delta})"))
    healthy = not violated
    explanation = [f"Canary at {traffic_percent}% traffic: {'HEALTHY' if healthy else 'HEALTH BREACH'}."]
    explanation += [f"{v['label']} {v['value']}{v['unit']} violates {v['operator']} {v['limit']}{v['unit']}"
                    for v in violated]
    return EvaluatorReport(evaluator="runtime_health", status="COMPLETED", passed=healthy, checks=checks,
                           evidence=[ev_item], score=round(1 - len(violated) / max(1, len(thresholds)), 4),
                           metrics={"violated": violated, "traffic_percent": traffic_percent,
                                    "severity": "SEV1" if any(v["metric"] in sev1 for v in violated) else
                                    ("SEV2" if violated else None)},
                           explanation=explanation)
