"""Lifecycle orchestrator: intent -> coding -> evaluation loop -> risk gate -> approval -> release
-> health gate -> incident. Every agent action is authorized and audited; every decision comes
from the central Evaluation Engine."""

from __future__ import annotations

import time
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import ArtifactDraft, InMemoryRepository
from app.agents.coding_agent import unified_diff
from app.agents.ops_agents import ReleaseAgent, SREAgent
from app.core.common import artifact_hash, audit, next_id, notify
from app.core.rbac import Actor, authorize, load_actor
from app.evaluation.engine import EvaluationEngine
from app.evaluation.support import ThresholdManager
from app.harness.gateway import ModelGateway
from app.harness.registry import active_coding_agent
from app.models import Approval, Artifact, Deployment, Evaluation, Execution, Incident, Ticket
from app.schemas import Decision, FeedbackItem, Severity, utcnow
from app.services.policies import active_policy

release_agent = ReleaseAgent()
sre_agent = SREAgent()
repository = InMemoryRepository()

TERMINAL_DEV = {Decision.BLOCK, Decision.BUDGET_EXCEEDED, Decision.MAX_ITERATIONS_REACHED,
                Decision.STAGNATION_DETECTED, Decision.ESCALATE, Decision.FAIL}


# ---------------------------------------------------------------------------- helpers
def _event(exe: Execution, stage: str, message: str, actor: str, **data: Any) -> None:
    exe.timeline = [*(exe.timeline or []), {"ts": utcnow().isoformat(), "stage": stage, "message": message,
                                            "actor": actor, **data}]


def _audit_actor(db: Session, actor: Actor, action: str, entity_type: str, entity_id: str, **kw: Any) -> None:
    audit(db, actor_id=actor.id, actor_kind=actor.kind, actor_role=actor.role, action=action,
          entity_type=entity_type, entity_id=entity_id, **kw)


def get_execution(db: Session, exe_id: str) -> Execution:
    exe = db.get(Execution, exe_id)
    if exe is None:
        raise HTTPException(404, f"Execution {exe_id} not found")
    return exe


def current_artifact(db: Session, exe: Execution) -> Artifact | None:
    return db.execute(select(Artifact).where(Artifact.execution_id == exe.id, Artifact.version == exe.current_version)
                      ).scalar_one_or_none()


def ticket_of(db: Session, exe: Execution) -> dict[str, Any]:
    return db.get(Ticket, exe.ticket_pk).data


def _ctx(exe: Execution, art: Artifact, author_kind: str = "agent") -> dict[str, Any]:
    return {"project_id": exe.project_id, "ticket_id": exe.ticket_id, "artifact_id": exe.artifact_id,
            "artifact_version": art.version, "artifact_hash": art.sha256, "execution_id": exe.id,
            "author_kind": author_kind}


def _store_artifact(db: Session, exe: Execution, draft: ArtifactDraft, version_no: int) -> Artifact:
    agent = load_actor(db, "agent-coding")
    action = "generate_artifact" if version_no == 1 else "revise_artifact"
    authorize(db, agent, action, entity_type="execution", entity_id=exe.id)
    manifest = draft.manifest
    sha = artifact_hash(draft.files, manifest)
    art = Artifact(execution_id=exe.id, artifact_id=exe.artifact_id, version=f"v{version_no}", sha256=sha,
                   author_id=agent.id, pr=draft.pr, files=draft.files, diff=unified_diff(draft.files),
                   change_manifest=manifest, agent_state=draft.agent_state, usage=draft.usage)
    db.add(art)
    db.flush()
    exe.current_version = art.version
    repository.open_pull_request(exe.artifact_id, draft.pr["branch"], draft.pr["title"], draft.pr["description"])
    ModelGateway(db, active_policy(db)).meter(
        caller="production", agent=draft.usage.get("agent_version", agent.id), purpose=action,
        model=draft.usage.get("model", "unknown"), prompt_tokens=draft.usage.get("prompt_tokens", 0),
        completion_tokens=draft.usage.get("completion_tokens", 0), execution_id=exe.id)
    _audit_actor(db, agent, action, "artifact", f"{exe.artifact_id}@{art.version}", artifact_hash=sha,
                 details={"files": sorted(draft.files), "usage": draft.usage})
    _event(exe, "coding", f"{agent.name} produced {exe.artifact_id} {art.version} (sha256 {sha[:12]}...)", agent.id,
           version=art.version, actions=draft.pr.get("agent_actions", []))
    invalidate_stale_approvals(db, exe, sha)
    return art


def _draft_from(art: Artifact) -> ArtifactDraft:
    return ArtifactDraft(files=art.files, pr=art.pr, agent_state=art.agent_state, usage=art.usage,
                         manifest=art.change_manifest)


def invalidate_stale_approvals(db: Session, exe: Execution, current_hash: str) -> int:
    count = 0
    for ap in db.execute(select(Approval).where(Approval.execution_id == exe.id)).scalars():
        if ap.artifact_hash != current_hash and ap.status in ("PENDING", "APPROVED"):
            ap.status = "INVALIDATED"
            ap.invalidated_reason = f"Artifact changed to sha256 {current_hash[:12]}...; re-evaluation required"
            count += 1
            audit(db, actor_id="system-governance", actor_kind="system", actor_role="governance_engine",
                  action="invalidate_approval", entity_type="approval", entity_id=ap.id,
                  artifact_hash=ap.artifact_hash, details={"new_hash": current_hash})
    return count


# ---------------------------------------------------------------------------- stage 1
def create_execution(db: Session, ticket: dict[str, Any], actor: Actor) -> Execution:
    authorize(db, actor, "create_ticket", entity_type="ticket", entity_id=ticket["ticket_id"], require_human=True)
    t = Ticket(ticket_id=ticket["ticket_id"], project_id=ticket["project_id"], data=ticket, created_by=actor.id)
    db.add(t)
    db.flush()
    exe_id = next_id(db, "EXE")
    number = "".join(ch for ch in ticket["ticket_id"] if ch.isdigit()) or str(t.id)
    exe = Execution(id=exe_id, ticket_pk=t.id, ticket_id=ticket["ticket_id"], project_id=ticket["project_id"],
                    artifact_id=f"PR-{number}", status="CREATED", stage="intent", loop_state={}, timeline=[])
    db.add(exe)
    _event(exe, "intent", f"Ticket {ticket['ticket_id']} captured by {actor.name}", actor.id)
    _audit_actor(db, actor, "create_execution", "execution", exe_id, details={"ticket": ticket["ticket_id"]})
    db.flush()
    return exe


# ---------------------------------------------------------------------------- stages 2-5
def run_development(db: Session, exe_id: str, actor: Actor, human_feedback: list[FeedbackItem] | None = None
                    ) -> Execution:
    exe = get_execution(db, exe_id)
    authorize(db, actor, "run_execution", entity_type="execution", entity_id=exe_id)
    if exe.status not in ("CREATED", "CHANGES_REQUESTED", "RETRY_READY"):
        raise HTTPException(409, f"Execution is {exe.status}; development loop cannot start")
    ticket = ticket_of(db, exe)
    policy = active_policy(db)
    engine = EvaluationEngine(db, policy)
    limits = ThresholdManager(policy).loop_limits(ticket.get("simulation"))
    state = dict(exe.loop_state or {})
    state.setdefault("tokens", 0)
    state.setdefault("cost_usd", 0.0)
    state["run_started"] = time.time()
    state["run_iterations"] = 0
    exe.status, exe.stage = "IN_PROGRESS", "development"
    history = [db.get(Evaluation, eid).result for eid in state.get("quality_evaluations", [])]
    coding_agent = active_coding_agent(db)  # the ACTIVE agent version (changed only via the promotion gate)
    state["agent_version"] = coding_agent.version

    art = current_artifact(db, exe)
    if art is None:
        draft = coding_agent.generate(ticket)
        art = _store_artifact(db, exe, draft, 1)
        _charge(state, draft.usage, limits)
    elif human_feedback:
        draft = coding_agent.revise(ticket, _draft_from(art), human_feedback, int(art.version[1:]) + 1)
        art = _store_artifact(db, exe, draft, int(art.version[1:]) + 1)
        _charge(state, draft.usage, limits)

    while True:
        iteration = len(history) + 1
        state["run_iterations"] += 1
        loop = {"iteration": state["run_iterations"], "tokens": state["tokens"], "cost_usd": state["cost_usd"],
                "elapsed_s": time.time() - state["run_started"]}
        result, feedback = engine.evaluate_development(_ctx(exe, art), art.files, art.diff, art.change_manifest,
                                                       ticket, iteration, history, loop, limits)
        history.append(result.model_dump(mode="json"))
        state.setdefault("quality_evaluations", []).append(result.evaluation_id)
        exe.quality_score, exe.last_decision = result.quality_score, result.decision.value
        cmp_ = result.iteration_comparison or {}
        _event(exe, "evaluation", f"Iteration {iteration}: quality {result.quality_score:.0%} "
               f"(threshold {result.quality_threshold:.0%}) -> {result.decision.value}", "system-governance",
               evaluation_id=result.evaluation_id, version=art.version, decision=result.decision.value,
               improvement_pct=cmp_.get("improvement_pct"))
        audit(db, actor_id="system-governance", actor_kind="system", actor_role="governance_engine",
              action="evaluate", entity_type="evaluation", entity_id=result.evaluation_id, artifact_hash=art.sha256,
              details={"decision": result.decision.value, "quality": result.quality_score, "iteration": iteration})
        if result.decision == Decision.RETRY:
            draft = coding_agent.revise(ticket, _draft_from(art), feedback, int(art.version[1:]) + 1)
            art = _store_artifact(db, exe, draft, int(art.version[1:]) + 1)
            _charge(state, draft.usage, limits)
            _event(exe, "feedback", f"{len(feedback)} structured feedback items sent to Fix Agent", "system-governance",
                   feedback=[f.model_dump(mode="json") for f in feedback[:25]])
            continue
        if result.decision in TERMINAL_DEV:
            exe.status = "BLOCKED" if result.decision == Decision.BLOCK else "ESCALATED"
            exe.stage = "development"
            notify(db, recipient_role="tech_lead", subject=f"{exe.id} stopped: {result.decision.value}",
                   body="; ".join(result.explanation[:3]), entity_type="execution", entity_id=exe.id,
                   channel="simulated-email")
            break
        # SUCCESS -> Risk Gate
        exe.loop_state = dict(state)
        _risk_gate(db, exe, art, ticket, engine, result)
        state = dict(exe.loop_state)
        break
    state["run_started"] = None
    exe.loop_state = state
    db.flush()
    return exe


def _charge(state: dict[str, Any], usage: dict[str, Any], limits: dict[str, Any]) -> None:
    tokens = usage.get("total_tokens", 0)
    state["tokens"] = state.get("tokens", 0) + tokens
    state["cost_usd"] = round(state.get("cost_usd", 0.0) + tokens / 1000 * limits["cost_per_1k_tokens_usd"], 6)
    state.setdefault("usage_log", []).append({"tokens": tokens, "cumulative_tokens": state["tokens"],
                                              "cumulative_cost_usd": state["cost_usd"], "simulated": True})


def _risk_gate(db: Session, exe: Execution, art: Artifact, ticket: dict[str, Any], engine: EvaluationEngine,
               dev_result) -> None:
    exe.stage = "risk_gate"
    result, breakdown, cats = engine.evaluate_risk_gate(_ctx(exe, art), art.files, art.diff, ticket, dev_result)
    exe.risk_score, exe.risk_level, exe.last_decision = breakdown.risk_score, breakdown.risk_level.value, \
        result.decision.value
    state = dict(exe.loop_state or {})
    state.update({"risk_evaluation": result.evaluation_id, "change_categories": cats,
                  "required_approvals": breakdown.required_approvals, "risk_overrides": breakdown.overrides})
    exe.loop_state = state
    _event(exe, "risk_gate", f"Risk {breakdown.risk_score}/100 {breakdown.risk_level.value} -> "
           f"{result.decision.value}", "system-governance", evaluation_id=result.evaluation_id)
    audit(db, actor_id="system-governance", actor_kind="system", actor_role="governance_engine", action="risk_gate",
          entity_type="evaluation", entity_id=result.evaluation_id, artifact_hash=art.sha256,
          details={"risk_score": breakdown.risk_score, "level": breakdown.risk_level.value,
                   "decision": result.decision.value})
    if result.decision == Decision.BLOCK:
        exe.status = "BLOCKED"
        return
    if result.decision == Decision.CONTINUE:
        _merge(db, exe, art, "Low risk: automated progression")
        return
    exe.status, exe.stage = "AWAITING_APPROVAL", "approval"
    recommended, reasons = _recommend(dev_result, breakdown)
    for role in breakdown.required_approvals:
        ap = Approval(id=next_id(db, "APR"), execution_id=exe.id, artifact_version=art.version,
                      artifact_hash=art.sha256, required_role=role, risk_level=breakdown.risk_level.value,
                      risk_score=breakdown.risk_score, status="PENDING", recommended_decision=recommended,
                      recommendation_reasons=reasons, evaluation_id=result.evaluation_id, comments=[])
        db.add(ap)
        notify(db, recipient_role=role, subject=f"Approval required: {exe.artifact_id} {art.version} "
               f"({breakdown.risk_level.value} risk)", body="; ".join(breakdown.primary_drivers),
               entity_type="approval", entity_id=ap.id, channel="simulated-email")
        _event(exe, "approval", f"Approval {ap.id} requested from {role}", "system-governance", approval_id=ap.id)


def _recommend(dev_result, breakdown) -> tuple[str, list[str]]:
    reasons = [f"Quality {dev_result.quality_score:.0%} meets threshold {dev_result.quality_threshold:.0%}",
               f"Risk {breakdown.risk_score}/100 ({breakdown.risk_level.value})"]
    medium = [c for c in dev_result.checks if c.category.startswith("security") and c.severity == Severity.MEDIUM]
    if medium:
        reasons.append(f"{len(medium)} medium security findings need reviewer judgement")
        return "APPROVE_WITH_CONDITIONS", reasons + breakdown.primary_drivers[:2]
    return "APPROVE", reasons + breakdown.primary_drivers[:2]


def _merge(db: Session, exe: Execution, art: Artifact, reason: str) -> None:
    system = load_actor(db, "system-governance")
    authorize(db, system, "merge_to_main", entity_type="artifact", entity_id=f"{exe.artifact_id}@{art.version}",
              artifact_hash=art.sha256)
    repository.merge(exe.artifact_id, art.sha256, system.id)
    exe.status, exe.stage = "READY_FOR_RELEASE", "release"
    _audit_actor(db, system, "merge_to_main", "artifact", f"{exe.artifact_id}@{art.version}", artifact_hash=art.sha256,
                 details={"reason": reason, "simulated_repository": True})
    _event(exe, "merge", f"Governance engine merged {exe.artifact_id} {art.version} to main ({reason})", system.id)


# ---------------------------------------------------------------------------- stage 6
def approval_view(db: Session, ap: Approval) -> dict[str, Any]:
    return {c.key: getattr(ap, c.key) for c in Approval.__table__.columns}


def decide_approval(db: Session, approval_id: str, actor: Actor, decision: str, comment: str) -> Approval:
    ap = db.get(Approval, approval_id)
    if ap is None:
        raise HTTPException(404, "Approval not found")
    action = {"APPROVE": "approve", "REJECT": "reject", "REQUEST_CHANGES": "request_changes"}.get(decision)
    if action is None:
        raise HTTPException(422, "decision must be APPROVE, REJECT or REQUEST_CHANGES")
    allowed = [ap.required_role] + (["tech_lead"] if ap.required_role == "reviewer" else [])
    authorize(db, actor, action, entity_type="approval", entity_id=ap.id, artifact_hash=ap.artifact_hash,
              require_human=True, allowed_roles=allowed)
    exe = get_execution(db, ap.execution_id)
    art = current_artifact(db, exe)
    ticket = ticket_of(db, exe)
    if actor.id in (art.author_id, ticket.get("engineering_owner")) and action == "approve":
        _audit_actor(db, actor, action, "approval", ap.id, outcome="DENIED", artifact_hash=ap.artifact_hash,
                     details={"reason": "self-approval"})
        db.commit()
        raise HTTPException(403, "Separation of duties: the author/owner cannot approve their own change.")
    if ap.status != "PENDING":
        raise HTTPException(409, f"Approval is {ap.status}")
    if art.sha256 != ap.artifact_hash:
        ap.status, ap.invalidated_reason = "INVALIDATED", "Artifact hash changed before decision"
        db.commit()
        raise HTTPException(409, "Artifact changed since this approval was requested; re-evaluation required.")
    ap.status = {"approve": "APPROVED", "reject": "REJECTED", "request_changes": "CHANGES_REQUESTED"}[action]
    ap.approver_id, ap.approver_role, ap.decided_at = actor.id, actor.role, utcnow()
    if comment:
        ap.comments = [*ap.comments, {"author": actor.id, "role": actor.role, "ts": utcnow().isoformat(),
                                      "text": comment, "kind": "decision"}]
    _audit_actor(db, actor, action, "approval", ap.id, artifact_hash=ap.artifact_hash,
                 details={"decision": ap.status, "comment": comment, "artifact_version": ap.artifact_version})
    _event(exe, "approval", f"{actor.name} ({actor.role}) {ap.status} {ap.id} for {ap.artifact_version} "
           f"(sha256 {ap.artifact_hash[:12]}...)", actor.id)
    siblings = db.execute(select(Approval).where(Approval.execution_id == exe.id,
                                                 Approval.artifact_hash == ap.artifact_hash)).scalars().all()
    if ap.status == "REJECTED":
        exe.status, exe.last_decision = "REJECTED", Decision.FAIL.value
    elif ap.status == "CHANGES_REQUESTED":
        exe.status = "CHANGES_REQUESTED"
        for s in siblings:
            if s.status == "PENDING":
                s.status, s.invalidated_reason = "INVALIDATED", "Changes requested by another approver"
    elif all(s.status == "APPROVED" for s in siblings if s.status != "INVALIDATED"):
        _merge(db, exe, art, "All required approvals bound to the current hash")
    db.flush()
    return ap


def add_comment(db: Session, approval_id: str, actor: Actor, text: str) -> Approval:
    ap = db.get(Approval, approval_id)
    if ap is None:
        raise HTTPException(404, "Approval not found")
    authorize(db, actor, "comment", entity_type="approval", entity_id=ap.id)
    ap.comments = [*ap.comments, {"author": actor.id, "role": actor.role, "ts": utcnow().isoformat(), "text": text,
                                  "kind": "comment"}]
    _audit_actor(db, actor, "comment", "approval", ap.id, details={"text": text})
    return ap


def rework(db: Session, exe_id: str, actor: Actor) -> Execution:
    exe = get_execution(db, exe_id)
    if exe.status != "CHANGES_REQUESTED":
        raise HTTPException(409, "No changes were requested")
    comments = []
    for ap in db.execute(select(Approval).where(Approval.execution_id == exe.id,
                                                Approval.status == "CHANGES_REQUESTED")).scalars():
        comments += [c for c in ap.comments if c.get("kind") == "decision"]
    feedback = [FeedbackItem(feedback_id=f"HFB-{i + 1:03d}", source="human", criterion="human:review",
                             severity=Severity.MEDIUM, message=c["text"] or "Changes requested")
                for i, c in enumerate(comments)] or [
        FeedbackItem(feedback_id="HFB-001", source="human", criterion="human:review", severity=Severity.MEDIUM,
                     message="Changes requested")]
    return run_development(db, exe_id, actor, human_feedback=feedback)


# ---------------------------------------------------------------------------- stages 7-9
def deployment_view(dep: Deployment) -> dict[str, Any]:
    return {c.key: getattr(dep, c.key) for c in Deployment.__table__.columns}


def start_release(db: Session, exe_id: str, actor: Actor, health_profile: str, auto_promote: bool) -> Deployment:
    exe = get_execution(db, exe_id)
    policy = active_policy(db)
    tm = ThresholdManager(policy)
    authorize(db, actor, "trigger_release", entity_type="execution", entity_id=exe.id, require_human=True,
              allowed_roles=tm.get("rbac.release_trigger_roles"))
    if exe.status != "READY_FOR_RELEASE":
        raise HTTPException(409, f"Execution is {exe.status}; release requires READY_FOR_RELEASE")
    art = current_artifact(db, exe)
    engine = EvaluationEngine(db, policy)
    approvals = [approval_view(db, a) for a in db.execute(select(Approval).where(Approval.execution_id == exe.id))
                 .scalars()]
    state = exe.loop_state or {}
    latest_q = db.get(Evaluation, state["quality_evaluations"][-1]).result if state.get("quality_evaluations") else None
    gate = engine.evaluate_release(_ctx(exe, art), approvals, state.get("required_approvals", []), latest_q,
                                   state.get("risk_overrides", []))
    _event(exe, "release_gate", f"Release policy gate -> {gate.decision.value}", "system-governance",
           evaluation_id=gate.evaluation_id)
    if gate.decision != Decision.CONTINUE:
        exe.last_decision = gate.decision.value
        db.commit()
        raise HTTPException(409, "Release blocked by policy: " + "; ".join(gate.explanation))
    agent = load_actor(db, release_agent.agent_id)
    authorize(db, agent, "prepare_deployment", entity_type="execution", entity_id=exe.id, artifact_hash=art.sha256)
    ticket = ticket_of(db, exe)
    service = ticket.get("affected_application", "service")
    build_no = int(next_id(db, "BUILD").split("-")[1])
    prep = release_agent.prepare(service, art.version, art.sha256, build_no)
    dep = Deployment(id=next_id(db, "DEP"), execution_id=exe.id, service=service, version=prep["version"],
                     artifact_hash=art.sha256, feature_flag=prep["feature_flag"], traffic_percent=0,
                     status="PREPARED", health_profile=health_profile, steps=prep["steps"])
    db.add(dep)
    db.flush()
    _audit_actor(db, agent, "prepare_deployment", "deployment", dep.id, artifact_hash=art.sha256,
                 details={"version": dep.version, "triggered_by": actor.id})
    exe.status, exe.stage = "DEPLOYING", "release"
    _event(exe, "release", f"{agent.name} prepared {dep.version} ({dep.id}), triggered by {actor.name}", agent.id,
           deployment_id=dep.id)
    _advance(db, exe, dep, tm, engine)
    while auto_promote and dep.promotion_eligible:
        _advance(db, exe, dep, tm, engine)
    return dep


def promote(db: Session, dep_id: str, actor: Actor) -> Deployment:
    dep = db.get(Deployment, dep_id)
    if dep is None:
        raise HTTPException(404, "Deployment not found")
    policy = active_policy(db)
    tm = ThresholdManager(policy)
    authorize(db, actor, "trigger_release", entity_type="deployment", entity_id=dep.id, require_human=True,
              allowed_roles=tm.get("rbac.release_trigger_roles"))
    if not dep.promotion_eligible:
        raise HTTPException(409, f"Deployment {dep.id} is not eligible for promotion ({dep.status})")
    exe = get_execution(db, dep.execution_id)
    _advance(db, exe, dep, tm, EvaluationEngine(db, policy))
    return dep


def _advance(db: Session, exe: Execution, dep: Deployment, tm: ThresholdManager, engine: EvaluationEngine) -> None:
    """Move to the next rollout step, then run the Health Gate on it."""
    steps = tm.get("health.rollout_steps", [5, 25, 50, 100])
    nxt = next((s for s in steps if s > dep.traffic_percent), None)
    if nxt is None:
        return
    agent = load_actor(db, release_agent.agent_id)
    action = "deploy_canary" if dep.traffic_percent == 0 else "promote_traffic"
    authorize(db, agent, action, entity_type="deployment", entity_id=dep.id, artifact_hash=dep.artifact_hash)
    moved = release_agent.set_traffic(deployment_view(dep), nxt)
    dep.feature_flag, dep.traffic_percent = moved["feature_flag"], moved["traffic_percent"]
    dep.status, dep.promotion_eligible = "CANARY" if nxt < 100 else "FULL_ROLLOUT_PENDING_HEALTH", False
    _audit_actor(db, agent, action, "deployment", dep.id, artifact_hash=dep.artifact_hash, details={"traffic": nxt})
    seed = (ticket_of(db, exe).get("simulation") or {}).get("seed", 42)
    canary, baseline = release_agent.observe(dep.health_profile, nxt, tm.get("health.observation_window_samples", 400),
                                             seed)
    art = current_artifact(db, exe)
    result = engine.evaluate_runtime(_ctx(exe, art), canary, baseline, nxt, final_step=nxt == steps[-1])
    step = {**moved["step"], "evaluation_id": result.evaluation_id, "decision": result.decision.value,
            "ts": utcnow().isoformat()}
    dep.steps = [*dep.steps, step]
    exe.last_decision = result.decision.value
    _event(exe, "health_gate", f"Health Gate at {nxt}%: {result.decision.value}", "system-governance",
           evaluation_id=result.evaluation_id, deployment_id=dep.id)
    if result.decision == Decision.ROLLBACK:
        dep.status = "HEALTH_BREACH"
        handle_breach(db, exe, dep, result)
    elif result.decision == Decision.SUCCESS:
        dep.status, exe.status, exe.stage = "COMPLETED", "DEPLOYED", "operations"
    else:
        dep.status, dep.promotion_eligible = "CANARY_HEALTHY", True
    db.flush()


def handle_breach(db: Session, exe: Execution, dep: Deployment, health_result) -> Incident:
    agent = load_actor(db, sre_agent.agent_id)
    authorize(db, agent, "open_incident", entity_type="deployment", entity_id=dep.id)
    violated = health_result.health_metrics["violated"]
    cats = (exe.loop_state or {}).get("change_categories", [])
    seed = (ticket_of(db, exe).get("simulation") or {}).get("seed", 42)
    analysis = sre_agent.summarize(violated, dep.version, dep.traffic_percent, cats)
    inc = Incident(id=next_id(db, "INC"), deployment_id=dep.id, execution_id=exe.id, service=dep.service,
                   severity=health_result.health_metrics.get("severity") or "SEV2", status="OPEN",
                   title=f"Health Gate breach on {dep.version} at {dep.traffic_percent}% canary", data={})
    db.add(inc)
    db.flush()
    timeline = [{"ts": utcnow().isoformat(), "event": "Health Gate breach detected", "actor": "system-governance",
                 "evaluation_id": health_result.evaluation_id},
                {"ts": utcnow().isoformat(), "event": f"Incident {inc.id} opened", "actor": agent.id}]
    # authorized rollback (pre-authorized by policy health.breach_action)
    authorize(db, agent, "execute_authorized_rollback", entity_type="deployment", entity_id=dep.id,
              artifact_hash=dep.artifact_hash)
    rb = sre_agent.rollback(deployment_view(dep))
    dep.feature_flag, dep.traffic_percent, dep.status, dep.promotion_eligible = rb["feature_flag"], 0, "ROLLED_BACK", \
        False
    dep.steps = [*dep.steps, {**rb["step"], "ts": utcnow().isoformat(), "incident_id": inc.id}]
    timeline.append({"ts": utcnow().isoformat(), "event": "Authorized rollback executed: traffic 0%, flag disabled",
                     "actor": agent.id, "policy": "health.breach_action=ROLLBACK"})
    from app.simulation.telemetry import sample_logs
    logs = sample_logs(dep.health_profile, dep.version, seed)
    timeline.append({"ts": utcnow().isoformat(), "event": f"Captured {len(logs)} log lines and canary metrics",
                     "actor": agent.id})
    oncall = "user-jordan"
    notify(db, recipient_role="sre_oncall", recipient_id=oncall, subject=f"[{inc.severity}] {inc.title}",
           body=analysis["summary"], entity_type="incident", entity_id=inc.id)
    timeline.append({"ts": utcnow().isoformat(), "event": "On-call engineer paged (simulated)", "actor": agent.id})
    inc.status = "MITIGATED"
    inc.data = {"timeline": timeline, "violated_metrics": violated, "telemetry": health_result.health_metrics,
                "logs": logs, "summary": analysis["summary"], "recommendations": analysis["recommendations"],
                "rollback_evidence": {"deployment_id": dep.id, "feature_flag": dep.feature_flag,
                                      "traffic_percent": 0, "health_evaluation_id": health_result.evaluation_id},
                "change_categories": cats, "assignee": oncall, "remediation_owner": None, "root_cause": None,
                "corrective_actions": [], "notes": [], "artifact_hash": dep.artifact_hash,
                "deployment_version": dep.version}
    _audit_actor(db, agent, "open_incident", "incident", inc.id, artifact_hash=dep.artifact_hash,
                 details={"severity": inc.severity, "violated": [v["metric"] for v in violated]})
    _audit_actor(db, agent, "execute_authorized_rollback", "deployment", dep.id, artifact_hash=dep.artifact_hash)
    exe.status, exe.stage = "ROLLED_BACK", "operations"
    _event(exe, "incident", f"{agent.name} opened {inc.id} ({inc.severity}) and rolled back {dep.version}", agent.id,
           incident_id=inc.id)
    return inc
