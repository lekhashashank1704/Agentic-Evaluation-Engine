"""REST API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.common import verify_audit_chain
from app.core.rbac import Actor, authorize, current_actor, load_actor
from app.db import get_session
from app.evaluation.engine import EvaluationEngine
from app.models import (
    Approval,
    Artifact,
    AuditEntry,
    Deployment,
    Evaluation,
    Execution,
    Incident,
    Notification,
    PolicyProposal,
    PolicyVersion,
    Postmortem,
    Ticket,
    User,
)
from app.orchestration import lifecycle as L
from app.schemas import (
    ApprovalDecisionRequest,
    CommentRequest,
    IncidentUpdate,
    PostmortemCreate,
    ProposalDecision,
    ReleaseRequest,
    TicketCreate,
)
from app.services import incidents as I
from app.services.policies import active_policy
from app.simulation import scenarios

router = APIRouter(prefix="/api")


def row(obj) -> dict[str, Any]:
    return {c.key: getattr(obj, c.key) for c in obj.__table__.columns}


def exe_summary(e: Execution) -> dict[str, Any]:
    d = row(e)
    d.pop("timeline", None)
    d["loop_state"] = {k: v for k, v in (e.loop_state or {}).items() if k != "usage_log"}
    return d


def eval_summary(ev: Evaluation) -> dict[str, Any]:
    r = ev.result
    return {"evaluation_id": ev.id, "execution_id": ev.execution_id, "evaluation_type": ev.evaluation_type,
            "lifecycle_stage": ev.lifecycle_stage, "iteration": ev.iteration, "artifact_version": ev.artifact_version,
            "artifact_hash": ev.artifact_hash, "decision": ev.decision, "quality_score": ev.quality_score,
            "quality_threshold": r.get("quality_threshold"), "risk_score": ev.risk_score,
            "risk_level": r.get("risk_level"), "failed_criteria": r.get("failed_criteria", []),
            "iteration_comparison": r.get("iteration_comparison"), "evaluated_at": r.get("evaluated_at"),
            "policy_version": r.get("policy_version"), "explanation": r.get("explanation", [])[:4]}


# ---------------------------------------------------------------------------- identity
@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/users")
def users(db: Session = Depends(get_session)):
    return [row(u) for u in db.execute(select(User)).scalars()]


@router.get("/me")
def me(actor: Actor = Depends(current_actor)):
    return actor.__dict__


# ---------------------------------------------------------------------------- dashboard
@router.get("/dashboard")
def dashboard(db: Session = Depends(get_session)):
    exes = db.execute(select(Execution).order_by(Execution.created_at.desc())).scalars().all()
    evals = db.execute(select(Evaluation).order_by(Evaluation.created_at.desc())).scalars().all()
    decisions: dict[str, int] = {}
    for ev in evals:
        decisions[ev.decision] = decisions.get(ev.decision, 0) + 1
    risk_dist: dict[str, int] = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
    for e in exes:
        if e.risk_level:
            risk_dist[e.risk_level] = risk_dist.get(e.risk_level, 0) + 1
    status: dict[str, int] = {}
    for e in exes:
        status[e.status] = status.get(e.status, 0) + 1
    return {
        "executions": [exe_summary(e) for e in exes[:10]],
        "counts": {
            "executions": len(exes), "evaluations": len(evals),
            "pending_approvals": db.scalar(select(func.count()).select_from(Approval).where(
                Approval.status == "PENDING")),
            "open_incidents": db.scalar(select(func.count()).select_from(Incident).where(
                Incident.status.in_(["OPEN", "MITIGATED", "INVESTIGATING", "RESOLVED"]))),
            "pending_proposals": db.scalar(select(func.count()).select_from(PolicyProposal).where(
                PolicyProposal.status == "PENDING")),
            "deployments": db.scalar(select(func.count()).select_from(Deployment)),
        },
        "decisions": decisions, "risk_distribution": risk_dist, "status_distribution": status,
        "recent_evaluations": [eval_summary(e) for e in evals[:12]],
        "policy_version": active_policy(db)["version"],
    }


# ---------------------------------------------------------------------------- executions
@router.post("/executions")
def create_execution(ticket: TicketCreate, actor: Actor = Depends(current_actor), db: Session = Depends(get_session)):
    exe = L.create_execution(db, ticket.model_dump(), actor)
    return exe_summary(exe)


@router.get("/executions")
def list_executions(db: Session = Depends(get_session)):
    return [exe_summary(e) for e in db.execute(select(Execution).order_by(Execution.created_at.desc())).scalars()]


@router.get("/executions/{exe_id}")
def execution_detail(exe_id: str, db: Session = Depends(get_session)):
    exe = L.get_execution(db, exe_id)
    arts = db.execute(select(Artifact).where(Artifact.execution_id == exe_id).order_by(Artifact.id)).scalars().all()
    evals = db.execute(select(Evaluation).where(Evaluation.execution_id == exe_id).order_by(Evaluation.created_at)
                       ).scalars().all()
    return {
        "execution": {**exe_summary(exe), "loop_state": exe.loop_state, "timeline": exe.timeline},
        "ticket": L.ticket_of(db, exe),
        "artifacts": [{"version": a.version, "sha256": a.sha256, "author_id": a.author_id, "pr": a.pr,
                       "usage": a.usage, "created_at": a.created_at, "files": sorted(a.files)} for a in arts],
        "evaluations": [eval_summary(e) for e in evals],
        "approvals": [row(a) for a in db.execute(select(Approval).where(Approval.execution_id == exe_id)).scalars()],
        "deployments": [row(d) for d in db.execute(select(Deployment).where(Deployment.execution_id == exe_id))
                        .scalars()],
        "incidents": [row(i) for i in db.execute(select(Incident).where(Incident.execution_id == exe_id)).scalars()],
    }


@router.get("/executions/{exe_id}/artifacts/{version}")
def artifact_detail(exe_id: str, version: str, db: Session = Depends(get_session)):
    art = db.execute(select(Artifact).where(Artifact.execution_id == exe_id, Artifact.version == version)
                     ).scalar_one_or_none()
    if art is None:
        raise HTTPException(404, "Artifact not found")
    d = row(art)
    d.pop("agent_state")  # simulation bookkeeping is never exposed to reviewers or evaluators
    return d


@router.post("/executions/{exe_id}/run")
def run_execution(exe_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_session)):
    return exe_summary(L.run_development(db, exe_id, actor))


@router.post("/executions/{exe_id}/rework")
def rework(exe_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_session)):
    return exe_summary(L.rework(db, exe_id, actor))


@router.post("/executions/{exe_id}/release")
def release(exe_id: str, req: ReleaseRequest, actor: Actor = Depends(current_actor),
            db: Session = Depends(get_session)):
    return row(L.start_release(db, exe_id, actor, req.health_profile, req.auto_promote))


# ---------------------------------------------------------------------------- evaluations
@router.get("/evaluations")
def list_evaluations(execution_id: str | None = None, evaluation_type: str | None = None,
                     db: Session = Depends(get_session)):
    q = select(Evaluation).order_by(Evaluation.created_at.desc())
    if execution_id:
        q = q.where(Evaluation.execution_id == execution_id)
    if evaluation_type:
        q = q.where(Evaluation.evaluation_type == evaluation_type)
    return [eval_summary(e) for e in db.execute(q.limit(200)).scalars()]


@router.get("/evaluations/{eval_id}")
def evaluation_detail(eval_id: str, db: Session = Depends(get_session)):
    ev = db.get(Evaluation, eval_id)
    if ev is None:
        raise HTTPException(404, "Evaluation not found")
    return ev.result


@router.post("/evaluate/artifact")
def evaluate_artifact(payload: dict[str, Any], actor: Actor = Depends(current_actor),
                      db: Session = Depends(get_session)):
    """Generic entry point any lifecycle agent can call: evaluate an arbitrary set of files for a ticket."""
    # any authenticated actor (human or agent) may request an evaluation; evaluation never grants permissions
    from app.agents.coding_agent import unified_diff
    from app.core.common import artifact_hash
    from app.evaluation.support import ThresholdManager
    files = payload.get("files", {})
    ticket = TicketCreate(**payload.get("ticket", {})).model_dump()
    policy = active_policy(db)
    sha = artifact_hash(files, {})
    ctx = {"project_id": ticket["project_id"], "ticket_id": ticket["ticket_id"],
           "artifact_id": payload.get("artifact_id", "ADHOC"), "artifact_version": payload.get("version", "v1"),
           "artifact_hash": sha, "execution_id": None, "author_kind": actor.kind}
    engine = EvaluationEngine(db, policy)
    limits = ThresholdManager(policy).loop_limits()
    result, feedback = engine.evaluate_development(ctx, files, unified_diff(files), {}, ticket, 1, [],
                                                   {"iteration": 1, "tokens": 0, "cost_usd": 0, "elapsed_s": 0},
                                                   limits)
    return {"result": result.model_dump(mode="json"), "feedback": [f.model_dump(mode="json") for f in feedback]}


# ---------------------------------------------------------------------------- approvals
@router.get("/approvals")
def list_approvals(status: str | None = None, db: Session = Depends(get_session)):
    q = select(Approval).order_by(Approval.created_at.desc())
    if status:
        q = q.where(Approval.status == status)
    out = []
    for a in db.execute(q).scalars():
        exe = db.get(Execution, a.execution_id)
        out.append({**row(a), "ticket_id": exe.ticket_id, "artifact_id": exe.artifact_id,
                    "title": L.ticket_of(db, exe).get("title")})
    return out


@router.get("/approvals/{approval_id}")
def approval_detail(approval_id: str, db: Session = Depends(get_session)):
    ap = db.get(Approval, approval_id)
    if ap is None:
        raise HTTPException(404, "Approval not found")
    exe = L.get_execution(db, ap.execution_id)
    art = db.execute(select(Artifact).where(Artifact.execution_id == exe.id, Artifact.version == ap.artifact_version)
                     ).scalar_one()
    risk_eval = db.get(Evaluation, ap.evaluation_id).result
    qevals = [db.get(Evaluation, i).result for i in (exe.loop_state or {}).get("quality_evaluations", [])]
    final_q = next((q for q in reversed(qevals) if q["artifact_hash"] == ap.artifact_hash), qevals[-1] if qevals else None)
    current = L.current_artifact(db, exe)
    return {
        "approval": row(ap), "execution": exe_summary(exe), "ticket": L.ticket_of(db, exe),
        "pr": art.pr, "diff": art.diff, "files": sorted(art.files), "artifact_hash": art.sha256,
        "current_artifact_hash": current.sha256 if current else None,
        "hash_matches_current": current is not None and current.sha256 == ap.artifact_hash,
        "quality": final_q, "risk": risk_eval,
        "history": [{k: q.get(k) for k in ("evaluation_id", "iteration", "artifact_version", "quality_score",
                                           "decision", "failed_criteria", "iteration_comparison")} for q in qevals],
        "siblings": [row(a) for a in db.execute(select(Approval).where(Approval.execution_id == exe.id)).scalars()],
    }


@router.post("/approvals/{approval_id}/decision")
def decide(approval_id: str, req: ApprovalDecisionRequest, actor: Actor = Depends(current_actor),
           db: Session = Depends(get_session)):
    return row(L.decide_approval(db, approval_id, actor, req.decision, req.comment))


@router.post("/approvals/{approval_id}/comments")
def comment(approval_id: str, req: CommentRequest, actor: Actor = Depends(current_actor),
            db: Session = Depends(get_session)):
    return row(L.add_comment(db, approval_id, actor, req.comment))


# ---------------------------------------------------------------------------- deployments
@router.get("/deployments")
def list_deployments(db: Session = Depends(get_session)):
    return [row(d) for d in db.execute(select(Deployment).order_by(Deployment.created_at.desc())).scalars()]


@router.get("/deployments/{dep_id}")
def deployment_detail(dep_id: str, db: Session = Depends(get_session)):
    dep = db.get(Deployment, dep_id)
    if dep is None:
        raise HTTPException(404, "Deployment not found")
    health = [db.get(Evaluation, s["evaluation_id"]).result for s in dep.steps if s.get("evaluation_id")]
    return {"deployment": row(dep), "health_evaluations": health}


@router.post("/deployments/{dep_id}/promote")
def promote(dep_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_session)):
    return row(L.promote(db, dep_id, actor))


# ---------------------------------------------------------------------------- incidents
@router.get("/incidents")
def list_incidents(db: Session = Depends(get_session)):
    return [row(i) for i in db.execute(select(Incident).order_by(Incident.created_at.desc())).scalars()]


@router.get("/incidents/{incident_id}")
def incident_detail(incident_id: str, db: Session = Depends(get_session)):
    inc = db.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404, "Incident not found")
    pm = db.execute(select(Postmortem).where(Postmortem.incident_id == incident_id)).scalar_one_or_none()
    return {"incident": row(inc), "postmortem": row(pm) if pm else None}


@router.patch("/incidents/{incident_id}")
def update_incident(incident_id: str, upd: IncidentUpdate, actor: Actor = Depends(current_actor),
                    db: Session = Depends(get_session)):
    return row(I.update_incident(db, incident_id, actor, upd))


@router.post("/incidents/{incident_id}/postmortem")
def postmortem(incident_id: str, pm: PostmortemCreate, actor: Actor = Depends(current_actor),
               db: Session = Depends(get_session)):
    return row(I.complete_postmortem(db, incident_id, actor, pm))


@router.get("/postmortems")
def postmortems(db: Session = Depends(get_session)):
    return [row(p) for p in db.execute(select(Postmortem).order_by(Postmortem.completed_at.desc())).scalars()]


# ---------------------------------------------------------------------------- policy & feedback
@router.get("/policies")
def policies(db: Session = Depends(get_session)):
    active_policy(db)
    versions = db.execute(select(PolicyVersion).order_by(PolicyVersion.created_at.desc())).scalars().all()
    return {"active": next(row(v) for v in versions if v.active),
            "versions": [{k: v for k, v in row(p).items() if k != "policy"} for p in versions]}


@router.get("/policies/{version}")
def policy_version(version: str, db: Session = Depends(get_session)):
    pv = db.get(PolicyVersion, version)
    if pv is None:
        raise HTTPException(404, "Policy version not found")
    return row(pv)


@router.put("/policies/active")
def edit_policy_directly(payload: dict[str, Any], actor: Actor = Depends(current_actor),
                         db: Session = Depends(get_session)):
    """Direct policy edits are only for governance admins and are themselves versioned."""
    policy = active_policy(db)
    authorize(db, actor, "modify_policy", entity_type="policy", entity_id=policy["version"], require_human=True,
              allowed_roles=policy["rbac"]["policy_admin_roles"])
    from app.services.policies import create_version, get_path
    changes = [{"path": c["path"], "old_value": get_path(policy, c["path"]), "new_value": c["value"]}
               for c in payload.get("changes", [])]
    pv = create_version(db, changes=changes, created_by=actor.id, summary=payload.get("summary", "Manual edit"))
    from app.core.common import audit
    audit(db, actor_id=actor.id, actor_kind=actor.kind, actor_role=actor.role, action="modify_policy",
          entity_type="policy", entity_id=pv.version, details={"changes": changes})
    return {k: v for k, v in row(pv).items() if k != "policy"}


@router.get("/policy-proposals")
def proposals(db: Session = Depends(get_session)):
    return [row(p) for p in db.execute(select(PolicyProposal).order_by(PolicyProposal.created_at.desc())).scalars()]


@router.post("/policy-proposals/{proposal_id}/decision")
def decide_proposal(proposal_id: str, req: ProposalDecision, actor: Actor = Depends(current_actor),
                    db: Session = Depends(get_session)):
    return row(I.decide_proposal(db, proposal_id, actor, req.decision, req.comment))


@router.post("/feedback/analyze")
def analyze(actor: Actor = Depends(current_actor), db: Session = Depends(get_session)):
    authorize(db, actor, "analyze_feedback", entity_type="feedback", entity_id="analysis")
    return I.analyze_feedback(db, triggered_by=actor.id)


# ---------------------------------------------------------------------------- audit & notifications
@router.get("/audit")
def audit_log(entity_id: str | None = None, actor_id: str | None = None, limit: int = 300,
              db: Session = Depends(get_session)):
    q = select(AuditEntry).order_by(AuditEntry.id.desc())
    if entity_id:
        q = q.where(AuditEntry.entity_id.contains(entity_id))
    if actor_id:
        q = q.where(AuditEntry.actor_id == actor_id)
    return [row(a) for a in db.execute(q.limit(limit)).scalars()]


@router.get("/audit/verify")
def audit_verify(db: Session = Depends(get_session)):
    return verify_audit_chain(db)


@router.get("/notifications")
def notifications(db: Session = Depends(get_session)):
    return [row(n) for n in db.execute(select(Notification).order_by(Notification.id.desc()).limit(100)).scalars()]


# ---------------------------------------------------------------------------- demo
@router.get("/demo/scenarios")
def demo_scenarios():
    return scenarios.CATALOG


@router.post("/demo/{scenario}")
def run_demo(scenario: str, db: Session = Depends(get_session)):
    if scenario not in scenarios.CATALOG:
        raise HTTPException(404, f"Unknown scenario {scenario}")
    return scenarios.run(db, scenario)


@router.get("/tickets/template")
def ticket_template():
    return TicketCreate().model_dump()


__all__ = ["router", "Ticket", "load_actor"]
