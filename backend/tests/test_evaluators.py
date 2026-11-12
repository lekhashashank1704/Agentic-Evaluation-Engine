from app.agents.coding_agent import render_files, unified_diff
from app.evaluation.functional import evaluate_functional
from app.evaluation.health import evaluate_health
from app.evaluation.risk import evaluate_risk
from app.evaluation.security import evaluate_security
from app.evaluation.support import EvidenceCollector, ThresholdManager
from app.governance.default_policy import DEFAULT_POLICY
from app.schemas import Severity, TicketCreate
from app.simulation import telemetry

TM = ThresholdManager(DEFAULT_POLICY)
TICKET = TicketCreate().model_dump()


def test_functional_evaluator_really_executes_tests():
    ev = EvidenceCollector()
    good = evaluate_functional(render_files("standard", set()), TICKET, ev)
    bad = evaluate_functional(render_files("standard", {"D1"}), TICKET, ev)
    assert good.passed and good.score == 1.0
    assert not bad.passed and bad.metrics["acceptance_criteria"]["AC-1"] == "FAILED"
    assert all(e.executed and not e.simulated for e in good.evidence)


def test_security_detects_secret_sqli_and_vulnerable_dependency():
    files = render_files("critical_auth_payment", {"D4", "D5", "D11"})
    rep = evaluate_security(files, EvidenceCollector(), TM)
    rules = {f.rule_id for f in rep.findings}
    assert "SEC-SECRET-001" in rules and "SEC-SQLI-001" in rules
    assert any(r.startswith("SEC-DEP-CVE-2024-35195") for r in rules)
    assert not rep.passed and rep.metrics["blocking"] >= 1
    dep_ev = next(e for e in rep.evidence if e.type == "dependency_scan")
    assert dep_ev.simulated  # bundled advisory snapshot is labelled as such


def _risk(scenario, crit, findings=()):
    files = render_files(scenario, set())
    ticket = {**TICKET, "criticality": crit}
    return evaluate_risk(files, unified_diff(files), ticket, list(findings), [], [], True, EvidenceCollector(), TM)[1]


def test_risk_critical_for_auth_and_payment_changes():
    rb = _risk("critical_auth_payment", "critical")
    assert rb.risk_level.value == "CRITICAL"
    sec = next(d for d in rb.dimensions if d.dimension == "security_exposure")
    assert {f.factor for f in sec.factors} >= {"auth_change", "payment_change"}
    assert rb.required_approvals == ["tech_lead", "security_lead"]
    assert abs(sum(d.weighted_score for d in rb.dimensions) - rb.risk_score) < 0.2


def test_risk_low_for_small_change():
    ticket = {**TICKET, "criticality": "low", "downstream_services": []}
    files = render_files("low_risk", set())
    rb = evaluate_risk(files, unified_diff(files), ticket, [], [], [], True, EvidenceCollector(), TM)[1]
    assert rb.risk_level.value == "LOW" and rb.required_approvals == []


def test_critical_finding_overrides_aggregate_risk():
    from app.schemas import Finding
    f = Finding(finding_id="x", rule_id="SEC-SECRET-001", title="key", severity=Severity.CRITICAL)
    rb = _risk("low_risk", "low", [f])
    assert any("BLOCK" in o for o in rb.overrides)


def test_health_gate_detects_breach_and_passes_healthy():
    bad = telemetry.generate("unhealthy", 400, 42, 5)
    good = telemetry.generate("healthy", 400, 42, 5)
    base = telemetry.generate("baseline", 400, 42, 95)
    rb = evaluate_health(bad, base, 5, EvidenceCollector(), TM, "synthetic")
    rg = evaluate_health(good, base, 5, EvidenceCollector(), TM, "synthetic")
    assert not rb.passed and {"error_rate_pct", "p99_latency_ms"} <= {v["metric"] for v in rb.metrics["violated"]}
    assert rg.passed
    assert rb.evidence[0].simulated
