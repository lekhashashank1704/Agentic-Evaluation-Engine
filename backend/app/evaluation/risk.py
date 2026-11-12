"""Risk Evaluation Engine: blast-radius analysis with configurable weighted dimensions.

Risk Score = sum(dimension_score * dimension_weight), every dimension normalised to 0-100.
Every point is attributed to an explicit factor with a human-readable reason, so the
classification is fully explainable. The evaluator derives the change facts itself
from the files and diff; it does not trust the coding agent's own claims.
"""

from __future__ import annotations

import re
import time
from typing import Any

from app.evaluation.security import parse_requirements
from app.evaluation.support import EvidenceCollector, ThresholdManager
from app.schemas import EvaluatorReport, Finding, RiskBreakdown, RiskDimension, RiskFactor, RiskLevel, Severity
from app.simulation.code_templates import BASE_REPO

DDL = re.compile(r"(?i)\b(create|alter|drop)\s+(table|index|column|view)\b")


def classify_path(path: str, classes: dict[str, list[str]]) -> set[str]:
    lowered = path.lower()
    for exclusive in ("docs", "tests"):
        if any(p.lower() in lowered for p in classes.get(exclusive, [])) and not lowered.endswith(".sql"):
            return {exclusive}
    return {name for name, pats in classes.items() if any(p.lower() in lowered for p in pats)}


def change_facts(files: dict[str, str], diff: str, classes: dict[str, list[str]]) -> dict[str, Any]:
    per_file = {p: sorted(classify_path(p, classes)) for p in files}
    added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    new_reqs = parse_requirements(files.get("requirements.txt", BASE_REPO.get("requirements.txt", "")))
    old_reqs = parse_requirements(BASE_REPO.get("requirements.txt", ""))
    schema_files = [p for p, c in files.items() if "db_schema" in per_file[p] or DDL.search(c)]
    return {
        "files_changed": len(files),
        "lines_added": added,
        "lines_removed": removed,
        "lines_changed": added + removed,
        "classes": per_file,
        "auth_files": [p for p, c in per_file.items() if "auth" in c],
        "payment_files": [p for p, c in per_file.items() if "payment" in c],
        "schema_files": schema_files,
        "infra_files": [p for p, c in per_file.items() if "infrastructure" in c],
        "config_files": [p for p, c in per_file.items() if "config" in c],
        "api_files": [p for p, c in per_file.items() if "api" in c],
        "new_files": [p for p in files if p not in BASE_REPO],
        "dependencies_added": sorted(set(new_reqs) - set(old_reqs)),
        "dependencies_changed": sorted(k for k in set(new_reqs) & set(old_reqs) if new_reqs[k] != old_reqs[k]),
    }


def evaluate_risk(files: dict[str, str], diff: str, ticket: dict[str, Any], security_findings: list[Finding],
                  historical_incidents: list[dict[str, Any]], unrelated_files: list[str], agent_authored: bool,
                  ev: EvidenceCollector, tm: ThresholdManager) -> tuple[EvaluatorReport, RiskBreakdown]:
    t0 = time.time()
    classes = tm.get("risk.path_classes", {})
    f = tm.get("risk.factors", {})
    weights = tm.get("risk.weights", {})
    facts = change_facts(files, diff, classes)
    facts_ev = ev.add(type="diff", source="risk-blast-radius-analyzer", executed=True, simulated=False,
                      summary=f"Derived change facts from {facts['files_changed']} files and "
                              f"{facts['lines_changed']} changed lines.", data=facts)
    dims: dict[str, list[RiskFactor]] = {k: [] for k in weights}

    def add(dim: str, factor: str, points: float, reason: str, evidence: list[str] | None = None):
        if points:
            dims[dim].append(RiskFactor(dimension=dim, factor=factor, points=points, reason=reason,
                                        evidence=evidence or []))

    # code change impact
    c = f.get("code_change_impact", {})
    add("code_change_impact", "files_changed", min(c.get("max_file_points", 30),
                                                   facts["files_changed"] * c.get("points_per_file", 6)),
        f"{facts['files_changed']} files changed", list(files))
    for limit, pts in c.get("lines_changed_tiers", []):
        if facts["lines_changed"] > limit:
            add("code_change_impact", "lines_changed", pts, f"{facts['lines_changed']} lines changed (> {limit})")
            break
    if facts["api_files"]:
        add("code_change_impact", "api_change", c.get("api_change", 25),
            "Public API surface changed (route registry / endpoints)", facts["api_files"])
    if unrelated_files:
        add("code_change_impact", "unrelated_change", c.get("unrelated_change", 10),
            "Changes outside the approved design", unrelated_files)

    # dependency impact
    d = f.get("dependency_impact", {})
    for dep in facts["dependencies_added"]:
        add("dependency_impact", f"new_dependency:{dep}", d.get("new_dependency", 20), f"New dependency '{dep}'",
            ["requirements.txt"])
    for dep in facts["dependencies_changed"]:
        add("dependency_impact", f"version_change:{dep}", d.get("version_change", 10),
            f"Dependency version changed '{dep}'", ["requirements.txt"])
    for fnd in security_findings:
        if fnd.category == "dependency":
            add("dependency_impact", f"vulnerable_dependency:{fnd.rule_id}", d.get("vulnerable_dependency", 40),
                f"Known-vulnerable dependency: {fnd.title}", ["requirements.txt"])
    downstream = ticket.get("downstream_services", [])
    if downstream:
        add("dependency_impact", "downstream_services",
            min(d.get("max_downstream_points", 40), len(downstream) * d.get("per_downstream_service", 8)),
            f"{len(downstream)} downstream services consume this API: {', '.join(downstream)}")

    # security exposure
    s = f.get("security_exposure", {})
    if facts["auth_files"]:
        add("security_exposure", "auth_change", s.get("auth_change", 45),
            "Authentication / authorization module modified", facts["auth_files"])
    if facts["payment_files"]:
        add("security_exposure", "payment_change", s.get("payment_change", 35),
            "Payment processing module modified", facts["payment_files"])
    text = (ticket.get("description", "") + " " + " ".join(ticket.get("functional_requirements", []))).lower()
    if any(k in text for k in ("transaction", "customer", "card", "account")):
        add("security_exposure", "sensitive_data", s.get("sensitive_data", 15),
            "Change handles customer financial data")
    pts = tm.get("security.severity_points", {})
    sev_points = sum(pts.get(x.severity.value, 0) for x in security_findings if x.category != "dependency")
    if sev_points:
        add("security_exposure", "security_findings", sev_points * s.get("findings_multiplier", 1.0),
            f"{len(security_findings)} open security findings", [x.rule_id for x in security_findings])

    # business criticality
    b = f.get("business_criticality", {})
    crit = ticket.get("criticality", "medium").lower()
    add("business_criticality", f"criticality:{crit}", b.get("levels", {}).get(crit, 45),
        f"Application '{ticket.get('affected_application')}' business criticality is {crit}")
    if historical_incidents:
        add("business_criticality", "historical_incidents",
            min(b.get("max_incident_points", 30), len(historical_incidents) * b.get("per_historical_incident", 10)),
            f"{len(historical_incidents)} recent incidents linked to similar changes or this service",
            [i["id"] for i in historical_incidents])

    # infrastructure impact
    i = f.get("infrastructure_impact", {})
    if facts["schema_files"]:
        add("infrastructure_impact", "db_schema_change", i.get("db_schema_change", 45), "Database schema change",
            facts["schema_files"])
    if facts["infra_files"]:
        add("infrastructure_impact", "infrastructure_change", i.get("infrastructure_change", 40),
            "Infrastructure / pipeline definitions changed", facts["infra_files"])
    if facts["config_files"]:
        add("infrastructure_impact", "config_change", i.get("config_change", 15), "Runtime configuration changed",
            facts["config_files"])

    dimensions = []
    total = 0.0
    for dim, weight in weights.items():
        score = min(100.0, sum(x.points for x in dims[dim]))
        weighted = score * weight
        total += weighted
        dimensions.append(RiskDimension(dimension=dim, score=round(score, 1), weight=weight,
                                        weighted_score=round(weighted, 2), factors=dims[dim]))
    total = round(min(100.0, total), 1)
    level = tm.risk_level(total)
    overrides = []
    mand = tm.get("risk.mandatory_overrides", {})
    if any(x.severity == Severity.CRITICAL for x in security_findings):
        overrides.append(f"critical_security_finding -> {mand.get('critical_security_finding', 'BLOCK')}")
    protected = [p for p in files if any(p.startswith(pp) or pp in p for pp in tm.get("risk.protected_paths", []))]
    if protected and agent_authored:
        overrides.append(f"protected_path_modified_by_agent -> {mand.get('protected_path_modified_by_agent', 'BLOCK')}"
                         f" ({', '.join(protected)})")
    req = tm.get(f"risk.level_requirements.{level}", {})
    all_factors = sorted((x for d in dimensions for x in d.factors),
                         key=lambda x: -x.points * weights.get(x.dimension, 0))
    drivers = [f"{x.reason} (+{x.points * weights.get(x.dimension, 0):.1f})" for x in all_factors[:4]]
    breakdown = RiskBreakdown(risk_score=total, risk_level=RiskLevel(level), dimensions=dimensions,
                              overrides=overrides, required_approvals=req.get("required_approvals", []),
                              additional_validation=req.get("additional_validation", []), primary_drivers=drivers)
    explanation = [f"Risk score {total}/100 classified {level} using policy {tm.version} bands."]
    explanation += [f"{d.dimension}: {d.score:.0f} x {d.weight:.0%} = {d.weighted_score:.1f}" for d in dimensions]
    explanation.append("Primary drivers: " + "; ".join(drivers))
    if overrides:
        explanation.append("Mandatory overrides: " + "; ".join(overrides))
    report = EvaluatorReport(evaluator="risk", status="COMPLETED", score=total, passed=level == "LOW" and not overrides,
                             evidence=[facts_ev], metrics={"facts": facts, "risk_level": level},
                             explanation=explanation, duration_ms=int((time.time() - t0) * 1000))
    return report, breakdown
