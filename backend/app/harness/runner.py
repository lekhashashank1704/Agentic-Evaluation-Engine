"""Durable eval runner, re-grading, run comparison and the agent promotion gate.

Durability: each case result is committed the moment it completes. A run can be
paused, stopped by its budget, or interrupted, and ``execute`` resumes it,
skipping finished cases. Outputs are keyed by sha256(fixture + agent config), so
identical work is reused across runs instead of being recomputed.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.common import audit, next_id
from app.core.rbac import Actor, authorize
from app.harness.gateway import ModelGateway
from app.harness.graders import grade_case
from app.harness.suites import content_hash, latest_dataset, latest_grader
from app.harness.targets import TARGETS, TransientError
from app.models import AgentVersion, EvalCaseResult, EvalDataset, EvalGrader, EvalRun, EvalSuite
from app.schemas import utcnow
from app.services.policies import active_policy

CASE_PASS = 0.99


def _dataset(db: Session, suite_id: str, version: str) -> EvalDataset:
    ds = db.execute(select(EvalDataset).where(EvalDataset.suite_id == suite_id, EvalDataset.version == version)
                    ).scalar_one_or_none()
    if ds is None:
        raise HTTPException(404, f"Dataset {suite_id}@{version} not found")
    return ds


def _grader(db: Session, suite_id: str, version: str) -> EvalGrader:
    g = db.execute(select(EvalGrader).where(EvalGrader.suite_id == suite_id, EvalGrader.version == version)
                   ).scalar_one_or_none()
    if g is None:
        raise HTTPException(404, f"Grader {suite_id}@{version} not found")
    return g


def cases_of(db: Session, run_id: str) -> list[EvalCaseResult]:
    return db.execute(select(EvalCaseResult).where(EvalCaseResult.run_id == run_id).order_by(EvalCaseResult.id)
                      ).scalars().all()


def create_run(db: Session, *, suite_id: str, agent_version: str, actor: Actor, dataset_version: str | None = None,
               grader_version: str | None = None, budget_usd: float | None = None) -> EvalRun:
    suite = db.get(EvalSuite, suite_id)
    if suite is None:
        raise HTTPException(404, "Suite not found")
    av = db.get(AgentVersion, agent_version)
    if av is None or av.target != suite.target:
        raise HTTPException(422, f"Agent version {agent_version} does not target {suite.target}")
    ds = _dataset(db, suite_id, dataset_version) if dataset_version else latest_dataset(db, suite_id)
    gr = _grader(db, suite_id, grader_version) if grader_version else latest_grader(db, suite_id)
    policy = active_policy(db)
    run = EvalRun(id=next_id(db, "RUN"), suite_id=suite_id, dataset_version=ds.version, grader_version=gr.version,
                  agent_version=agent_version, mode="run", status="PENDING",
                  budget_usd=budget_usd if budget_usd is not None else policy["harness"]["default_budget_usd"],
                  created_by=actor.id, summary={"total_cases": len(ds.fixtures), "policy_version": policy["version"],
                                                "dataset_sha256": ds.sha256, "grader_sha256": gr.sha256,
                                                "agent_config": av.config})
    db.add(run)
    audit(db, actor_id=actor.id, actor_kind=actor.kind, actor_role=actor.role, action="create_eval_run",
          entity_type="eval_run", entity_id=run.id, details={"suite": suite_id, "agent": agent_version,
                                                            "dataset": ds.version, "grader": gr.version})
    db.commit()
    return run


def _summarize(db: Session, run: EvalRun) -> None:
    cases = cases_of(db, run.id)
    done = [c for c in cases if c.status == "DONE"]
    suite = db.get(EvalSuite, run.suite_id)
    score = round(sum(c.score for c in done) / len(done), 4) if done else 0.0
    run.summary = {**run.summary, "completed_cases": len(done), "errored_cases": len(cases) - len(done),
                   "score": score, "pass_rate": round(sum(c.passed for c in done) / len(done), 4) if done else 0.0,
                   "reused_cases": sum(1 for c in done if c.reused_from), "pass_threshold": suite.pass_threshold,
                   "meets_threshold": score >= suite.pass_threshold and len(done) == run.summary["total_cases"]}


def execute(db: Session, run_id: str, max_cases: int | None = None) -> EvalRun:
    """Run (or resume) until finished, paused, out of budget, or ``max_cases`` new cases are done."""
    run = db.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    if run.mode != "run":
        raise HTTPException(409, "Re-grade runs are computed when created")
    if run.status == "COMPLETED":
        return run
    policy = active_policy(db)
    gateway = ModelGateway(db, policy)
    suite = db.get(EvalSuite, run.suite_id)
    ds = _dataset(db, run.suite_id, run.dataset_version)
    spec = _grader(db, run.suite_id, run.grader_version).spec
    av = db.get(AgentVersion, run.agent_version)
    target = TARGETS[suite.target]
    max_retries = policy["harness"]["max_retries"]
    run.status, run.pause_requested = "RUNNING", False
    db.commit()
    finished = {c.case_id for c in cases_of(db, run.id) if c.status == "DONE"}
    processed = 0
    for fx in ds.fixtures:
        if fx["case_id"] in finished:
            continue
        db.refresh(run)
        if run.pause_requested:
            run.status, run.pause_requested = "PAUSED", False
            break
        if max_cases is not None and processed >= max_cases:
            run.status = "PAUSED"
            break
        if run.spent_usd >= run.budget_usd:
            run.status = "BUDGET_EXCEEDED"
            break
        _run_case(db, run, fx, av, target, spec, policy, gateway, max_retries)
        processed += 1
        db.commit()  # durable: persist each case as soon as it completes
    else:
        run.status = "COMPLETED"
    _summarize(db, run)
    db.commit()
    return run


def _run_case(db, run, fx, av, target, spec, policy, gateway, max_retries) -> None:
    key = content_hash({"fixture": fx["input"], "agent": av.id, "config": av.config, "target": run.suite_id})
    existing = db.execute(select(EvalCaseResult).where(EvalCaseResult.run_id == run.id,
                                                       EvalCaseResult.case_id == fx["case_id"])).scalar_one_or_none()
    prior = db.execute(select(EvalCaseResult).where(EvalCaseResult.output_key == key, EvalCaseResult.status == "DONE",
                                                    EvalCaseResult.run_id != run.id)
                       .order_by(EvalCaseResult.id)).scalars().first()
    t0 = time.time()
    attempts, output, error, reused = 0, None, None, None
    if prior is not None:
        output, reused = prior.output, prior.run_id
    else:
        injected = fx.get("transient_failures", 0)
        while attempts <= max_retries:
            attempts += 1
            try:
                if attempts <= injected:
                    raise TransientError(f"simulated transient failure on attempt {attempts}")
                output = target(db, policy, av, fx, run.id, gateway)
                error = None
                break
            except TransientError as exc:
                error = str(exc)
    ctx = {"gateway": gateway, "run_id": run.id}
    rec = existing or EvalCaseResult(run_id=run.id, case_id=fx["case_id"], output_key=key)
    if output is None:
        rec.status, rec.error, rec.attempts = "ERROR", error, attempts
    else:
        usage = output.get("_usage", {}) if not reused else {"tokens": 0, "cost_usd": 0.0}
        grades, score = grade_case(spec, fx, output, ctx)
        cost = usage.get("cost_usd", 0.0) + ctx.get("judge_cost", 0.0)
        rec.status, rec.output, rec.grades, rec.score = "DONE", output, grades, score
        rec.passed, rec.attempts, rec.reused_from, rec.error = score >= CASE_PASS, attempts, reused, None
        rec.cost_usd, rec.tokens = round(cost, 6), int(usage.get("tokens", 0))
        run.spent_usd = round(run.spent_usd + cost, 6)
        run.tokens += rec.tokens
        if error is None and attempts > 1:
            rec.error = f"recovered after {attempts - 1} retr{'y' if attempts == 2 else 'ies'}"
    rec.duration_ms = int((time.time() - t0) * 1000)
    db.add(rec)


def request_pause(db: Session, run_id: str) -> EvalRun:
    run = db.get(EvalRun, run_id)
    run.pause_requested = True
    if run.status in ("PENDING",):
        run.status = "PAUSED"
    db.commit()
    return run


def regrade(db: Session, parent_id: str, grader_version: str, actor: Actor) -> EvalRun:
    """Re-score stored outputs with another grader version: no agent work is repeated."""
    parent = db.get(EvalRun, parent_id)
    if parent is None:
        raise HTTPException(404, "Run not found")
    gr = _grader(db, parent.suite_id, grader_version)
    ds = _dataset(db, parent.suite_id, parent.dataset_version)
    fixtures = {f["case_id"]: f for f in ds.fixtures}
    gateway = ModelGateway(db, active_policy(db))
    run = EvalRun(id=next_id(db, "RUN"), suite_id=parent.suite_id, dataset_version=parent.dataset_version,
                  grader_version=gr.version, agent_version=parent.agent_version, mode="regrade",
                  parent_run_id=parent.id, status="RUNNING", budget_usd=parent.budget_usd, created_by=actor.id,
                  summary={**parent.summary, "grader_sha256": gr.sha256})
    db.add(run)
    db.flush()
    for c in cases_of(db, parent.id):
        if c.status != "DONE":
            continue
        ctx = {"gateway": gateway, "run_id": run.id}
        grades, score = grade_case(gr.spec, fixtures[c.case_id], c.output, ctx)
        cost = round(ctx.get("judge_cost", 0.0), 6)
        db.add(EvalCaseResult(run_id=run.id, case_id=c.case_id, output_key=c.output_key, status="DONE",
                              attempts=0, reused_from=parent.id, output=c.output, grades=grades, score=score,
                              passed=score >= CASE_PASS, cost_usd=cost, tokens=0, duration_ms=0))
        run.spent_usd = round(run.spent_usd + cost, 6)
    db.flush()
    run.status = "COMPLETED" if parent.status == "COMPLETED" else "PAUSED"
    _summarize(db, run)
    audit(db, actor_id=actor.id, actor_kind=actor.kind, actor_role=actor.role, action="regrade_eval_run",
          entity_type="eval_run", entity_id=run.id, details={"parent": parent.id, "grader": gr.version})
    db.commit()
    return run


def _score_with(db: Session, run: EvalRun, grader: EvalGrader) -> dict[str, float]:
    ds = _dataset(db, run.suite_id, run.dataset_version)
    fixtures = {f["case_id"]: f for f in ds.fixtures}
    gateway = ModelGateway(db, active_policy(db))
    out = {}
    for c in cases_of(db, run.id):
        if c.status == "DONE":
            out[c.case_id] = grade_case(grader.spec, fixtures[c.case_id], c.output, {"gateway": gateway})[1]
    return out


def compare(db: Session, a_id: str, b_id: str) -> dict[str, Any]:
    a, b = db.get(EvalRun, a_id), db.get(EvalRun, b_id)
    if a is None or b is None or a.suite_id != b.suite_id:
        raise HTTPException(422, "Both runs must exist and belong to the same suite")
    ca = {c.case_id: c for c in cases_of(db, a.id)}
    cb = {c.case_id: c for c in cases_of(db, b.id)}
    rows = []
    for cid in sorted(set(ca) | set(cb)):
        x, y = ca.get(cid), cb.get(cid)
        rows.append({"case_id": cid, "a_score": x.score if x else None, "b_score": y.score if y else None,
                     "delta": round(y.score - x.score, 4) if x and y else None,
                     "a_passed": x.passed if x else None, "b_passed": y.passed if y else None,
                     "output_changed": bool(x and y and x.output_key != y.output_key),
                     "a_decision": (x.output or {}).get("decision") or (x.output or {}).get("risk_level") if x else None,
                     "b_decision": (y.output or {}).get("decision") or (y.output or {}).get("risk_level") if y else None})
    sa, sb = a.summary.get("score", 0.0), b.summary.get("score", 0.0)
    differs = {"dataset": a.dataset_version != b.dataset_version, "grader": a.grader_version != b.grader_version,
               "agent": a.agent_version != b.agent_version}
    attribution: dict[str, Any] = {"total_delta": round(sb - sa, 4)}
    if not differs["dataset"]:
        if differs["grader"] and not differs["agent"]:
            attribution.update(grader_effect=round(sb - sa, 4), agent_effect=0.0)
        elif differs["agent"] and not differs["grader"]:
            attribution.update(grader_effect=0.0, agent_effect=round(sb - sa, 4))
        elif differs["agent"] and differs["grader"]:
            b_with_a = _score_with(db, b, _grader(db, a.suite_id, a.grader_version))
            mid = round(sum(b_with_a.values()) / len(b_with_a), 4) if b_with_a else 0.0
            attribution.update(agent_effect=round(mid - sa, 4), grader_effect=round(sb - mid, 4),
                               method=f"B's outputs re-scored with grader {a.grader_version} = {mid}")
    else:
        attribution["note"] = "Different datasets: scores are not directly comparable."
    def efficiency(cases: dict[str, EvalCaseResult]) -> dict[str, Any]:
        outs = [c.output for c in cases.values() if c.status == "DONE"]
        its = [o["iterations"] for o in outs if "iterations" in o]
        return {"agent_cost_usd": round(sum(o.get("cost_usd", 0.0) for o in outs), 6),
                "agent_tokens": sum(o.get("tokens", 0) for o in outs),
                "avg_iterations": round(sum(its) / len(its), 2) if its else None}

    return {"a": run_view(a), "b": run_view(b), "differs": differs, "attribution": attribution, "cases": rows,
            "efficiency": {"a": efficiency(ca), "b": efficiency(cb)},
            "new_failures": [r["case_id"] for r in rows if r["a_passed"] and r["b_passed"] is False],
            "fixed": [r["case_id"] for r in rows if r["a_passed"] is False and r["b_passed"]]}


def run_view(run: EvalRun) -> dict[str, Any]:
    return {c.key: getattr(run, c.key) for c in EvalRun.__table__.columns}


# ---------------------------------------------------------------------------- promotion gate
def request_promotion(db: Session, version_id: str, run_id: str, actor: Actor) -> AgentVersion:
    authorize(db, actor, "comment", entity_type="agent_version", entity_id=version_id, require_human=True)
    av = db.get(AgentVersion, version_id)
    run = db.get(EvalRun, run_id)
    if av is None or run is None:
        raise HTTPException(404, "Agent version or run not found")
    if av.status not in ("CANDIDATE", "REJECTED"):
        raise HTTPException(409, f"{av.id} is {av.status}")
    suite = db.get(EvalSuite, run.suite_id)
    ds, gr = latest_dataset(db, suite.id), latest_grader(db, suite.id)
    checks = []

    def check(name: str, ok: bool, detail: str):
        checks.append({"check": name, "passed": bool(ok), "detail": detail})

    check("Run evaluates this agent version", run.agent_version == av.id, f"run agent {run.agent_version}")
    check("Run is complete", run.status == "COMPLETED", run.status)
    check("Uses the latest golden dataset", run.dataset_version == ds.version, f"{run.dataset_version} (latest {ds.version})")
    check("Uses the latest grader", run.grader_version == gr.version, f"{run.grader_version} (latest {gr.version})")
    score = run.summary.get("score", 0.0)
    check("Score meets suite threshold", score >= suite.pass_threshold, f"{score:.0%} vs {suite.pass_threshold:.0%}")
    active = db.execute(select(AgentVersion).where(AgentVersion.target == av.target, AgentVersion.status == "ACTIVE")
                        ).scalars().first()
    baseline = None
    if active:
        baseline = db.execute(select(EvalRun).where(EvalRun.suite_id == suite.id, EvalRun.agent_version == active.id,
                                                    EvalRun.dataset_version == run.dataset_version,
                                                    EvalRun.grader_version == run.grader_version,
                                                    EvalRun.status == "COMPLETED").order_by(EvalRun.created_at.desc())
                              ).scalars().first()
    allowed = active_policy(db)["harness"]["promotion_max_case_regressions"]
    if baseline:
        cmp_ = compare(db, baseline.id, run.id)
        regress = cmp_["new_failures"]
        check(f"No case regressions vs active {active.id}", len(regress) <= allowed,
              f"baseline {baseline.id} score {baseline.summary.get('score', 0):.0%}; regressions: {regress or 'none'}")
    else:
        check("Baseline run for the active version exists", False,
              f"Run the active version {active.id if active else '-'} on the same dataset and grader first")
    passed = all(c["passed"] for c in checks)
    av.promotion = {"requested_by": actor.id, "requested_at": utcnow().isoformat(), "eval_run_id": run.id,
                    "baseline_run_id": baseline.id if baseline else None, "checks": checks, "gate_passed": passed}
    av.status = "PROMOTION_PENDING" if passed else "CANDIDATE"
    audit(db, actor_id=actor.id, actor_kind=actor.kind, actor_role=actor.role, action="request_agent_promotion",
          entity_type="agent_version", entity_id=av.id, details={"run": run.id, "gate_passed": passed})
    db.flush()
    return av


def decide_promotion(db: Session, version_id: str, actor: Actor, decision: str, comment: str) -> AgentVersion:
    av = db.get(AgentVersion, version_id)
    if av is None:
        raise HTTPException(404, "Agent version not found")
    roles = active_policy(db)["harness"]["promotion_approval_roles"]
    authorize(db, actor, "promote_agent", entity_type="agent_version",
              entity_id=av.id, require_human=True, allowed_roles=roles)
    if av.status != "PROMOTION_PENDING":
        raise HTTPException(409, f"{av.id} is {av.status}; the eval gate must pass first")
    if actor.id == av.promotion.get("requested_by"):
        raise HTTPException(403, "Separation of duties: the requester cannot approve the promotion")
    if decision == "APPROVE":
        for other in db.execute(select(AgentVersion).where(AgentVersion.target == av.target,
                                                           AgentVersion.status == "ACTIVE")).scalars():
            other.status = "RETIRED"
        av.status = "ACTIVE"
    else:
        av.status = "REJECTED"
    av.promotion = {**av.promotion, "decided_by": actor.id, "decided_role": actor.role, "decision": decision,
                    "decided_at": utcnow().isoformat(), "comment": comment}
    audit(db, actor_id=actor.id, actor_kind=actor.kind, actor_role=actor.role, action=f"{decision.lower()}_agent_promotion",
          entity_type="agent_version", entity_id=av.id, details={"comment": comment, "eval_run": av.promotion.get("eval_run_id")})
    db.flush()
    return av


def add_grader_version(db: Session, suite_id: str, spec: dict, notes: str, actor: Actor) -> EvalGrader:
    authorize(db, actor, "comment", entity_type="eval_suite", entity_id=suite_id, require_human=True)
    from app.harness.graders import GRADERS
    for g in spec.get("graders", []):
        if g.get("type") not in GRADERS:
            raise HTTPException(422, f"Unknown grader type {g.get('type')}")
    last = latest_grader(db, suite_id)
    version = f"v{int(last.version[1:]) + 1}" if last else "v1"
    g = EvalGrader(suite_id=suite_id, version=version, spec=spec, sha256=content_hash(spec), notes=notes,
                   created_by=actor.id)
    db.add(g)
    db.flush()
    return g


def add_dataset_version(db: Session, suite_id: str, fixtures: list, notes: str, actor: Actor) -> EvalDataset:
    authorize(db, actor, "comment", entity_type="eval_suite", entity_id=suite_id, require_human=True)
    ids = [f.get("case_id") for f in fixtures]
    if len(set(ids)) != len(ids) or None in ids:
        raise HTTPException(422, "Every fixture needs a unique case_id")
    last = latest_dataset(db, suite_id)
    version = f"v{int(last.version[1:]) + 1}" if last else "v1"
    d = EvalDataset(suite_id=suite_id, version=version, fixtures=json.loads(json.dumps(fixtures)),
                    sha256=content_hash(fixtures), notes=notes, created_by=actor.id)
    db.add(d)
    db.flush()
    return d
