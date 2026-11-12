from conftest import as_user


def _create_and_run(client, **sim):
    body = {"ticket_id": "TICKET-104", "simulation": {"scenario": "standard", **sim}}
    exe = client.post("/api/executions", json=body, headers=as_user("user-alex")).json()
    r = client.post(f"/api/executions/{exe['id']}/run", headers=as_user("user-alex"))
    assert r.status_code == 200, r.text
    return client.get(f"/api/executions/{exe['id']}").json()


def test_iterative_loop_improves_to_threshold_and_requires_tech_lead(client):
    d = _create_and_run(client)
    q = [e for e in d["evaluations"] if e["evaluation_type"] == "quality"]
    scores = [e["quality_score"] for e in q]
    assert scores == sorted(scores) and scores[-1] >= 0.95 and len(scores) >= 3
    assert q[1]["iteration_comparison"]["resolved_criteria"]
    assert d["execution"]["status"] == "AWAITING_APPROVAL" and d["execution"]["risk_level"] == "HIGH"
    assert [a["required_role"] for a in d["approvals"]] == ["tech_lead"]


def test_agents_and_wrong_roles_cannot_approve(client):
    d = _create_and_run(client)
    ap = d["approvals"][0]["id"]
    for actor in ("agent-coding", "agent-release", "user-alex", "user-sam"):
        r = client.post(f"/api/approvals/{ap}/decision", json={"decision": "APPROVE"}, headers=as_user(actor))
        assert r.status_code == 403, actor
    denied = client.get("/api/audit", params={"entity_id": ap}).json()
    assert sum(1 for a in denied if a["outcome"] == "DENIED") == 4
    ok = client.post(f"/api/approvals/{ap}/decision", json={"decision": "APPROVE", "comment": "LGTM"},
                     headers=as_user("user-priya"))
    assert ok.status_code == 200 and ok.json()["approver_role"] == "tech_lead"
    assert client.get(f"/api/executions/{d['execution']['id']}").json()["execution"]["status"] == "READY_FOR_RELEASE"
    assert client.get("/api/audit/verify").json()["valid"]


def test_request_changes_invalidates_hash_bound_approval(client):
    d = _create_and_run(client)
    exe_id, ap = d["execution"]["id"], d["approvals"][0]
    client.post(f"/api/approvals/{ap['id']}/decision", json={"decision": "REQUEST_CHANGES", "comment": "Add limits"},
                headers=as_user("user-priya"))
    r = client.post(f"/api/executions/{exe_id}/rework", headers=as_user("user-alex"))
    assert r.status_code == 200, r.text
    d2 = client.get(f"/api/executions/{exe_id}").json()
    new = [a for a in d2["approvals"] if a["status"] == "PENDING"]
    assert len(new) == 1 and new[0]["artifact_hash"] != ap["artifact_hash"]


def test_release_blocked_without_approval_and_breach_triggers_incident(client):
    d = _create_and_run(client)
    exe_id = d["execution"]["id"]
    r = client.post(f"/api/executions/{exe_id}/release", json={"health_profile": "unhealthy"},
                    headers=as_user("user-alex"))
    assert r.status_code == 409
    client.post(f"/api/approvals/{d['approvals'][0]['id']}/decision", json={"decision": "APPROVE"},
                headers=as_user("user-priya"))
    r = client.post(f"/api/executions/{exe_id}/release", json={"health_profile": "unhealthy"},
                    headers=as_user("agent-release"))
    assert r.status_code == 403  # agents cannot trigger a release on their own
    dep = client.post(f"/api/executions/{exe_id}/release", json={"health_profile": "unhealthy"},
                      headers=as_user("user-alex")).json()
    assert dep["status"] == "ROLLED_BACK" and dep["traffic_percent"] == 0
    incs = client.get("/api/incidents").json()
    inc = next(i for i in incs if i["execution_id"] == exe_id)
    assert inc["status"] == "MITIGATED" and inc["data"]["violated_metrics"]
    assert any(n["recipient_role"] == "sre_oncall" for n in client.get("/api/notifications").json())


def test_feedback_engine_proposes_but_never_auto_applies(client):
    r = client.post("/api/demo/breach_and_learn").json()
    assert r["status"] == "ROLLED_BACK"
    props = client.get("/api/policy-proposals").json()
    assert props and props[0]["status"] == "PENDING"
    assert client.get("/api/policies").json()["active"]["version"] == "1.0"
    pid = props[0]["id"]
    assert client.post(f"/api/policy-proposals/{pid}/decision", json={"decision": "APPROVE"},
                       headers=as_user("user-priya")).status_code == 403
    ok = client.post(f"/api/policy-proposals/{pid}/decision", json={"decision": "APPROVE", "comment": "Agreed"},
                     headers=as_user("user-morgan"))
    assert ok.status_code == 200
    pol = client.get("/api/policies").json()
    assert pol["active"]["version"] == "1.1"
    assert pol["active"]["policy"]["risk"]["factors"]["infrastructure_impact"]["db_schema_change"] == 60
    assert len(pol["versions"]) == 2


def test_healthy_low_risk_release_promotes_to_full(client):
    r = client.post("/api/demo/healthy_release").json()
    assert r["status"] == "DEPLOYED"


def test_block_scenarios(client):
    for name, expected in (("security_block", "BLOCK"), ("protected_path", "BLOCK"),
                           ("stagnation", "STAGNATION_DETECTED"), ("budget_exceeded", "BUDGET_EXCEEDED")):
        r = client.post(f"/api/demo/{name}").json()
        assert r["decision"] == expected, (name, r)


def test_evaluation_result_matches_required_schema(client):
    d = _create_and_run(client)
    rid = next(e for e in d["evaluations"] if e["evaluation_type"] == "quality_and_risk")["evaluation_id"]
    res = client.get(f"/api/evaluations/{rid}").json()
    for key in ("evaluation_id", "project_id", "ticket_id", "artifact_id", "artifact_version", "artifact_hash",
                "lifecycle_stage", "evaluation_type", "quality_score", "quality_threshold", "risk_score", "risk_level",
                "evaluation_status", "decision", "checks", "failed_criteria", "evidence", "explanation",
                "evaluated_at", "policy_version"):
        assert key in res
    assert res["decision"] == "HUMAN_APPROVAL_REQUIRED" and len(res["artifact_hash"]) == 64
