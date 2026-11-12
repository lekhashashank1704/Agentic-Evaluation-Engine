"""Reproducible end-to-end demonstration scenarios.

Each scenario drives the real orchestrator through the same API-level functions the
UI uses, acting as the named seeded users. Every human decision made by a scenario
is recorded in the audit trail with ``via: demo-scenario``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.rbac import load_actor
from app.models import Approval
from app.orchestration import lifecycle as L
from app.schemas import PostmortemCreate, SimulationControls, TicketCreate
from app.services import incidents as I

CATALOG: dict[str, dict[str, Any]] = {
    "breach_and_learn": {
        "title": "High risk change, Tech Lead approval, canary breach, rollback and postmortem",
        "description": "TICKET-104 iterates to 100%, is classified HIGH risk, approved by the Tech Lead, breaches the "
                       "Health Gate at 5% canary, is rolled back by the SRE Agent, and the postmortem triggers a "
                       "policy proposal (second schema-change incident).",
    },
    "healthy_release": {
        "title": "Low risk change with automated progression and full healthy rollout",
        "description": "A small change is auto-merged after passing all gates and promoted 5% -> 25% -> 50% -> 100%.",
    },
    "critical_risk": {
        "title": "Authentication + payment change classified CRITICAL",
        "description": "Requires Tech Lead and Security Lead approval; stops at the approval queue.",
    },
    "security_block": {
        "title": "Hardcoded live credential triggers a mandatory block",
        "description": "Critical security stop condition halts the loop regardless of quality.",
    },
    "stagnation": {
        "title": "Fix Agent cannot improve: stagnation detected",
        "description": "Scores stop improving and the execution is escalated to a human.",
    },
    "regression": {
        "title": "Fix Agent introduces a regression, then recovers",
        "description": "Iteration 2 breaks existing behaviour; the regression is detected and fed back.",
    },
    "budget_exceeded": {
        "title": "Token budget exhausted",
        "description": "A tight budget stops the loop with BUDGET_EXCEEDED.",
    },
    "protected_path": {
        "title": "Agent tries to edit governance policy",
        "description": "Mandatory governance block: agents may not modify protected policy files.",
    },
    "approval_invalidation": {
        "title": "Request changes invalidates hash-bound approval",
        "description": "The Tech Lead requests changes; the new artifact hash invalidates the old approval and a new "
                       "approval is requested after re-evaluation.",
    },
}


def _ticket(n: int, **sim: Any) -> dict[str, Any]:
    t = TicketCreate(ticket_id=f"TICKET-{n}", simulation=SimulationControls(**sim))
    if sim.get("scenario") == "low_risk":
        t.criticality = "low"
        t.downstream_services = []
        t.title = "Transaction history API (internal pilot)"
    if sim.get("scenario") == "critical_auth_payment":
        t.title = "Transaction history API with payments issuer and token changes"
        t.criticality = "critical"
        t.description += " Also trust the payments identity provider and expose ledger history."
    return t.model_dump()


def _next_ticket_no(db: Session) -> int:
    from sqlalchemy import func, select

    from app.models import Ticket
    return 104 + (db.scalar(select(func.count()).select_from(Ticket)) or 0)


def run(db: Session, name: str) -> dict[str, Any]:
    alex, priya, sam, jordan = (load_actor(db, u) for u in ("user-alex", "user-priya", "user-sam", "user-jordan"))
    n = _next_ticket_no(db)
    steps: list[str] = []
    sims = {
        "breach_and_learn": {"scenario": "standard"},
        "healthy_release": {"scenario": "low_risk"},
        "critical_risk": {"scenario": "critical_auth_payment"},
        "security_block": {"scenario": "standard", "defect_profile": "critical_secret"},
        "stagnation": {"scenario": "standard", "defect_profile": "stagnation"},
        "regression": {"scenario": "standard", "defect_profile": "regression"},
        "budget_exceeded": {"scenario": "standard", "budget_usd_override": 0.05},
        "protected_path": {"scenario": "standard", "defect_profile": "protected_path"},
        "approval_invalidation": {"scenario": "standard"},
    }
    exe = L.create_execution(db, _ticket(n, **sims[name]), alex)
    steps.append(f"Created {exe.id} for TICKET-{n}")
    L.run_development(db, exe.id, alex)
    steps.append(f"Development loop finished: {exe.status} / {exe.last_decision}")

    def pending(role: str) -> Approval | None:
        from sqlalchemy import select
        return db.execute(select(Approval).where(Approval.execution_id == exe.id, Approval.status == "PENDING",
                                                 Approval.required_role == role)).scalar_one_or_none()

    if name == "breach_and_learn" and exe.status == "AWAITING_APPROVAL":
        ap = pending("tech_lead") or pending("reviewer")
        L.decide_approval(db, ap.id, priya, "APPROVE", "Reviewed diff, risk breakdown and tests. Approved for canary.")
        steps.append(f"{priya.name} approved {ap.id}")
        dep = L.start_release(db, exe.id, alex, "unhealthy", auto_promote=False)
        steps.append(f"Release {dep.id}: {dep.status}")
        from sqlalchemy import select

        from app.models import Incident
        inc = db.execute(select(Incident).where(Incident.execution_id == exe.id)).scalar_one_or_none()
        if inc:
            from app.schemas import IncidentUpdate
            I.update_incident(db, inc.id, jordan, IncidentUpdate(status="INVESTIGATING", remediation_owner="user-alex",
                                                                 note="Reviewed rollback evidence and canary logs."))
            I.complete_postmortem(db, inc.id, jordan, PostmortemCreate(
                root_cause="The new composite index was built inside the release, locking the transactions table "
                           "under canary load and exhausting the connection pool.",
                business_impact="5% of transaction-history requests failed or were slow for about 6 minutes.",
                technical_impact="Error rate 4-5%, p99 latency about 2.8 s at 5% canary.",
                recovery_actions=["SRE Agent executed authorized rollback", "Verified baseline metrics recovered"],
                preventive_actions=["Ship schema migrations separately and online", "Add load test for the endpoint"],
                lessons_learned=["Schema changes need their own rollout plan and stronger risk weighting"]))
            steps.append(f"Incident {inc.id} postmortem completed by {jordan.name}; feedback engine analysed it")
    elif name == "healthy_release" and exe.status == "READY_FOR_RELEASE":
        dep = L.start_release(db, exe.id, alex, "healthy", auto_promote=True)
        steps.append(f"Release {dep.id}: {dep.status} at {dep.traffic_percent}%")
    elif name == "approval_invalidation" and exe.status == "AWAITING_APPROVAL":
        ap = pending("tech_lead") or pending("reviewer")
        L.decide_approval(db, ap.id, priya, "REQUEST_CHANGES", "Please document the rate limits for this endpoint.")
        steps.append(f"{priya.name} requested changes on {ap.id} (bound to {ap.artifact_hash[:12]}...)")
        L.rework(db, exe.id, alex)
        steps.append(f"Fix Agent produced {exe.current_version}; re-evaluated -> {exe.status}")
    _ = sam
    db.flush()
    return {"scenario": name, "execution_id": exe.id, "status": exe.status, "decision": exe.last_decision,
            "steps": steps}
