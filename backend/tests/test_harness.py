"""Eval harness: durability, retries, reuse, re-grading, attribution, gateway and the promotion gate."""

from conftest import as_user

ALEX, PRIYA, RAVI, MORGAN = (as_user(u) for u in ("user-alex", "user-priya", "user-ravi", "user-morgan"))


def _run(client, **body):
    r = client.post("/api/harness/runs", json=body, headers=ALEX)
    assert r.status_code == 200, r.text
    return r.json()


def test_suites_are_seeded_with_versioned_datasets_and_graders(client):
    suites = {s["id"]: s for s in client.get("/api/harness/suites").json()}
    assert set(suites) == {"coding-agent-core", "risk-calibration", "sre-incident-summaries"}
    detail = client.get("/api/harness/suites/coding-agent-core").json()
    assert [g["version"] for g in detail["graders"]] == ["v1", "v2"]
    assert len(detail["datasets"][0]["sha256"]) == 64 and suites["coding-agent-core"]["cases"] == 10


def test_run_is_durable_pauses_resumes_and_skips_finished_cases(client):
    run = _run(client, suite_id="risk-calibration", grader_version="v1", max_cases=3)
    assert run["status"] == "PAUSED" and len(run["cases"]) == 3
    first_ids = [c["id"] for c in run["cases"]]
    resumed = client.post(f"/api/harness/runs/{run['id']}/resume", json={}, headers=ALEX).json()
    assert resumed["status"] == "COMPLETED" and len(resumed["cases"]) == 8
    assert [c["id"] for c in resumed["cases"]][:3] == first_ids  # finished cases were not recomputed
    assert resumed["summary"]["score"] == 0.75


def test_transient_failures_are_retried_and_outputs_are_reused(client):
    a = _run(client, suite_id="coding-agent-core", grader_version="v2")
    flaky = next(c for c in a["cases"] if c["case_id"] == "std-flaky-infra")
    assert flaky["status"] == "DONE" and flaky["attempts"] == 2 and "recovered" in flaky["error"]
    assert a["spent_usd"] > 0 and a["status"] == "COMPLETED"
    b = _run(client, suite_id="coding-agent-core", grader_version="v2")
    assert b["summary"]["reused_cases"] == 10 and b["spent_usd"] == 0.0
    assert b["summary"]["score"] == a["summary"]["score"]


def test_budget_cap_stops_the_run_and_raising_it_resumes(client):
    run = _run(client, suite_id="coding-agent-core", budget_usd=0.05)
    assert run["status"] == "BUDGET_EXCEEDED" and len(run["cases"]) < 10
    done = client.post(f"/api/harness/runs/{run['id']}/resume", json={"budget_usd": 5.0}, headers=ALEX).json()
    assert done["status"] == "COMPLETED"


def test_regrade_attributes_score_change_to_the_grader(client):
    v1 = _run(client, suite_id="coding-agent-core", grader_version="v1")
    v2 = client.post(f"/api/harness/runs/{v1['id']}/regrade", json={"grader_version": "v2"}, headers=ALEX).json()
    assert v2["mode"] == "regrade" and v2["spent_usd"] == 0.0
    assert v1["summary"]["score"] < 0.85 <= v2["summary"]["score"]
    cmp = client.get("/api/harness/compare", params={"a": v1["id"], "b": v2["id"]}).json()
    assert cmp["differs"] == {"dataset": False, "grader": True, "agent": False}
    assert cmp["attribution"]["agent_effect"] == 0.0 and cmp["attribution"]["grader_effect"] > 0.2
    assert set(cmp["fixed"]) == {"std-secret", "std-protected", "std-stagnation"}


def test_llm_judge_goes_through_gateway_and_replays(client):
    v1 = _run(client, suite_id="sre-incident-summaries", grader_version="v1")
    first = client.post(f"/api/harness/runs/{v1['id']}/regrade", json={"grader_version": "v2"}, headers=ALEX).json()
    again = client.post(f"/api/harness/runs/{v1['id']}/regrade", json={"grader_version": "v2"}, headers=ALEX).json()
    assert first["spent_usd"] > 0 and again["spent_usd"] == 0.0
    assert first["summary"]["score"] == again["summary"]["score"]
    judge = [r for r in client.get("/api/gateway/summary").json()["by_caller_model"] if r["model"] == "simulated-judge-v1"]
    assert judge[0]["calls"] == 8 and judge[0]["cache_hits"] == 4


def test_production_and_eval_costs_are_metered_separately(client):
    exe = client.post("/api/executions", json={"simulation": {"scenario": "standard"}}, headers=ALEX).json()
    client.post(f"/api/executions/{exe['id']}/run", headers=ALEX)
    _run(client, suite_id="coding-agent-core", grader_version="v2", max_cases=1)
    callers = {r["caller"] for r in client.get("/api/gateway/summary").json()["by_caller_model"]}
    assert callers == {"production", "eval"}


def test_promotion_gate_requires_passing_eval_and_a_separate_human(client):
    _run(client, suite_id="coding-agent-core", grader_version="v2")  # baseline for the ACTIVE version
    weak = _run(client, suite_id="coding-agent-core", agent_version="coding-agent@0.9-exp")
    av = client.post("/api/harness/agent-versions/coding-agent@0.9-exp/request-promotion", json={"run_id": weak["id"]},
                     headers=ALEX).json()
    assert av["status"] == "CANDIDATE" and not av["promotion"]["gate_passed"]

    cand = _run(client, suite_id="coding-agent-core", agent_version="coding-agent@1.1")
    r = client.post("/api/harness/agent-versions/coding-agent@1.1/request-promotion", json={"run_id": cand["id"]},
                    headers=as_user("agent-coding"))
    assert r.status_code == 403
    av = client.post("/api/harness/agent-versions/coding-agent@1.1/request-promotion", json={"run_id": cand["id"]},
                     headers=PRIYA).json()
    assert av["status"] == "PROMOTION_PENDING" and av["promotion"]["gate_passed"]
    for headers in (RAVI, PRIYA):  # wrong role; requester cannot approve their own promotion
        r = client.post("/api/harness/agent-versions/coding-agent@1.1/decision", json={"decision": "APPROVE"},
                        headers=headers)
        assert r.status_code == 403
    ok = client.post("/api/harness/agent-versions/coding-agent@1.1/decision",
                     json={"decision": "APPROVE", "comment": "Same quality, fewer iterations"}, headers=MORGAN)
    assert ok.status_code == 200 and ok.json()["status"] == "ACTIVE"
    versions = {v["id"]: v["status"] for v in client.get("/api/harness/agent-versions").json()}
    assert versions["coding-agent@1.0"] == "RETIRED"

    exe = client.post("/api/executions", json={"simulation": {"scenario": "standard"}}, headers=ALEX).json()
    client.post(f"/api/executions/{exe['id']}/run", headers=ALEX)
    detail = client.get(f"/api/executions/{exe['id']}").json()
    assert detail["execution"]["loop_state"]["agent_version"] == "coding-agent@1.1"


def test_risk_policy_what_if_candidate_improves_calibration(client):
    base = _run(client, suite_id="risk-calibration")
    cand = _run(client, suite_id="risk-calibration", agent_version="risk-evaluator@1.1-db60")
    cmp = client.get("/api/harness/compare", params={"a": base["id"], "b": cand["id"]}).json()
    assert cmp["attribution"]["agent_effect"] > 0 and "std-high" in cmp["fixed"]
