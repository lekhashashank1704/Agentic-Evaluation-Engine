import copy

from app.evaluation import decision as D
from app.evaluation.support import ObjectiveFunction, ThresholdManager
from app.governance.default_policy import DEFAULT_POLICY
from app.schemas import Check, CheckStatus, Decision, EvaluatorReport

SPEC = DEFAULT_POLICY["quality"]["development"]["objective"]
LIMITS = DEFAULT_POLICY["loop"]


def test_development_objective_is_weighted_sum():
    sb = ObjectiveFunction("dev", SPEC, 0.95).evaluate(
        {"test_pass_rate": 0.5, "requirement_coverage": 1.0, "code_hygiene": 0.0}, {}, {})
    assert sb.score == 0.7 * 0.5 + 0.2 * 1.0
    assert not sb.passed
    assert {m.metric for m in sb.metrics} == set(SPEC)
    assert all(m.formula and m.data_source and m.definition for m in sb.metrics)


def test_risk_bands_are_configurable():
    policy = copy.deepcopy(DEFAULT_POLICY)
    tm = ThresholdManager(policy)
    assert [tm.risk_level(x) for x in (10, 45, 70, 82)] == ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    policy["risk"]["levels"][0]["max"] = 50
    policy["risk"]["levels"][1]["min"] = 51
    assert ThresholdManager(policy).risk_level(45) == "LOW"


def _reports(critical=False):
    sec = EvaluatorReport(evaluator="security", status="COMPLETED", passed=not critical,
                          metrics={"blocking": 1 if critical else 0})
    pol = EvaluatorReport(evaluator="policy", status="COMPLETED", passed=not critical,
                          checks=[Check(check_id="PG-001", name="crit", category="policy:BLOCK",
                                        status=CheckStatus.FAILED if critical else CheckStatus.PASSED,
                                        message="1 critical")],
                          metrics={"blocking": ["PG-001"] if critical else [], "failed": []})
    return sec, pol


def _loop(**kw):
    return {"iteration": 1, "tokens": 0, "cost_usd": 0.0, "elapsed_s": 0, **kw}


def test_quality_never_overrides_mandatory_security_block():
    perfect = ObjectiveFunction("dev", SPEC, 0.95).evaluate(
        {"test_pass_rate": 1, "requirement_coverage": 1, "code_hygiene": 1}, {}, {})
    sec, pol = _reports(critical=True)
    decision, _ = D.decide_development(quality=perfect, security=sec, policy=pol, loop=_loop(), limits=LIMITS,
                                       history=[], comparison={})
    assert decision == Decision.BLOCK


def test_budget_max_iterations_and_stagnation():
    low = ObjectiveFunction("dev", SPEC, 0.95).evaluate({"test_pass_rate": 0.3}, {}, {})
    sec, pol = _reports()
    pol.passed = False
    kw = dict(quality=low, security=sec, policy=pol, comparison={})
    assert D.decide_development(loop=_loop(cost_usd=5), limits=LIMITS, history=[], **kw)[0] == Decision.BUDGET_EXCEEDED
    assert D.decide_development(loop=_loop(iteration=5), limits=LIMITS, history=[], **kw)[0] == \
        Decision.MAX_ITERATIONS_REACHED
    hist = [{"quality_score": low.score}, {"quality_score": low.score}]
    assert D.decide_development(loop=_loop(iteration=3), limits=LIMITS, history=hist, **kw)[0] == \
        Decision.STAGNATION_DETECTED
    assert D.decide_development(loop=_loop(), limits=LIMITS, history=[], **kw)[0] == Decision.RETRY


def test_iteration_comparison_tracks_resolved_and_new_failures():
    cmp_ = D.compare_iterations({"quality_score": 0.32, "failed_criteria": ["a", "b"]}, 0.65, 0.95, ["b", "c"])
    assert cmp_["improvement_pct"] == 33.0
    assert cmp_["resolved_criteria"] == ["a"] and cmp_["new_failures"] == ["c"] and cmp_["regression"]
