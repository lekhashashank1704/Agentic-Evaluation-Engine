"""Incident management (On-Call Engineer), postmortems and the Feedback Engine."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.common import audit, next_id, notify
from app.core.rbac import Actor, authorize
from app.models import Incident, PolicyProposal, Postmortem
from app.schemas import IncidentUpdate, PostmortemCreate, utcnow
from app.services.policies import active_policy, create_version, get_path

INCIDENT_STATUSES = ["OPEN", "MITIGATED", "INVESTIGATING", "RESOLVED", "POSTMORTEM_COMPLETE"]


def _a(db: Session, actor: Actor, action: str, etype: str, eid: str, **details: Any) -> None:
    audit(db, actor_id=actor.id, actor_kind=actor.kind, actor_role=actor.role, action=action, entity_type=etype,
          entity_id=eid, details=details)


def update_incident(db: Session, incident_id: str, actor: Actor, upd: IncidentUpdate) -> Incident:
    inc = db.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404, "Incident not found")
    authorize(db, actor, "manage_incident", entity_type="incident", entity_id=inc.id, require_human=True)
    data = dict(inc.data)
    events = []
    for field in ("assignee", "root_cause", "remediation_owner", "corrective_actions"):
        value = getattr(upd, field)
        if value is not None:
            data[field] = value
            events.append(f"{field} updated")
    if upd.note:
        data["notes"] = [*data.get("notes", []), {"author": actor.id, "ts": utcnow().isoformat(), "text": upd.note}]
        events.append("note added")
    if upd.status:
        if upd.status not in INCIDENT_STATUSES or upd.status == "POSTMORTEM_COMPLETE":
            raise HTTPException(422, "Invalid status (use the postmortem endpoint to complete)")
        inc.status = upd.status
        events.append(f"status -> {upd.status}")
    data["timeline"] = [*data.get("timeline", []), {"ts": utcnow().isoformat(), "event": "; ".join(events),
                                                    "actor": actor.id}]
    inc.data = data
    _a(db, actor, "update_incident", "incident", inc.id, changes=events)
    return inc


def complete_postmortem(db: Session, incident_id: str, actor: Actor, pm: PostmortemCreate) -> Postmortem:
    inc = db.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404, "Incident not found")
    authorize(db, actor, "complete_postmortem", entity_type="incident", entity_id=inc.id, require_human=True)
    cats = pm.change_categories if pm.change_categories is not None else inc.data.get("change_categories", [])
    record = Postmortem(
        id=next_id(db, "PM"), incident_id=inc.id, change_categories=cats, completed_by=actor.id,
        data={"incident_id": inc.id, "service": inc.service, "deployment_version": inc.data.get("deployment_version"),
              "timeline": inc.data.get("timeline", []), "root_cause": pm.root_cause,
              "business_impact": pm.business_impact, "technical_impact": pm.technical_impact,
              "detection_method": pm.detection_method, "recovery_actions": pm.recovery_actions,
              "preventive_actions": pm.preventive_actions, "lessons_learned": pm.lessons_learned,
              "severity": inc.severity})
    db.add(record)
    inc.status = "POSTMORTEM_COMPLETE"
    data = dict(inc.data)
    data["root_cause"] = pm.root_cause
    data["timeline"] = [*data.get("timeline", []), {"ts": utcnow().isoformat(), "event": f"Postmortem {record.id} "
                                                    "completed", "actor": actor.id}]
    inc.data = data
    _a(db, actor, "complete_postmortem", "incident", inc.id, postmortem=record.id, categories=cats)
    db.flush()
    analyze_feedback(db, triggered_by=actor.id)
    return record


# ---------------------------------------------------------------------------- Feedback Engine
def analyze_feedback(db: Session, triggered_by: str = "system-governance") -> dict[str, Any]:
    """Look for repeated incident causes and *propose* (never apply) policy changes."""
    policy = active_policy(db)
    fb = policy.get("feedback", {})
    min_n = fb.get("min_incidents_for_proposal", 2)
    since = utcnow() - timedelta(days=fb.get("lookback_days", 180))
    pms = db.execute(select(Postmortem)).scalars().all()
    by_cat: dict[str, list[Postmortem]] = {}
    for pm in pms:
        completed = pm.completed_at if pm.completed_at.tzinfo else pm.completed_at.replace(tzinfo=since.tzinfo)
        if completed < since:
            continue
        for c in pm.change_categories or []:
            by_cat.setdefault(c, []).append(pm)
    observations, created = [], []
    pending = {p.data.get("path") for p in db.execute(select(PolicyProposal).where(
        PolicyProposal.status == "PENDING")).scalars()}
    for cat, items in sorted(by_cat.items()):
        path = fb.get("category_to_policy_path", {}).get(cat)
        obs = {"category": cat, "incidents": [p.incident_id for p in items], "count": len(items), "path": path,
               "threshold": min_n}
        if len(items) < min_n or not path:
            obs["status"] = "insufficient_evidence" if path else "no_policy_mapping"
            observations.append(obs)
            continue
        if path in pending:
            obs["status"] = "proposal_pending"
            observations.append(obs)
            continue
        current = get_path(policy, path)
        proposed = min(100, current + fb.get("factor_increase_points", 15))
        if proposed == current:
            continue
        prop = PolicyProposal(
            id=next_id(db, "POL"), status="PENDING", base_version=policy["version"],
            title=f"Increase risk points for {cat.replace('_', ' ')} ({current} -> {proposed})",
            data={"category": cat, "path": path, "current_value": current, "proposed_value": proposed,
                  "rationale": f"{len(items)} incidents in the last {fb.get('lookback_days', 180)} days were linked "
                               f"to {cat.replace('_', ' ')}s (threshold {min_n}). Raising the factor makes similar "
                               "changes more likely to require human approval.",
                  "evidence": [{"incident_id": p.incident_id, "postmortem_id": p.id, "service": p.data.get("service"),
                                "root_cause": p.data.get("root_cause"), "severity": p.data.get("severity"),
                                "completed_at": p.completed_at.isoformat()} for p in items],
                  "generated_by": "feedback-engine", "auto_applied": False})
        db.add(prop)
        db.flush()
        created.append(prop.id)
        obs["status"] = "proposal_created"
        obs["proposal_id"] = prop.id
        observations.append(obs)
        audit(db, actor_id="system-governance", actor_kind="system", actor_role="governance_engine",
              action="propose_policy_change", entity_type="policy_proposal", entity_id=prop.id,
              details={"path": path, "from": current, "to": proposed, "triggered_by": triggered_by})
        notify(db, recipient_role="governance_admin", subject=f"Policy change proposed: {prop.title}",
               body=prop.data["rationale"], entity_type="policy_proposal", entity_id=prop.id, channel="simulated-email")
    return {"observations": observations, "created_proposals": created, "policy_version": policy["version"]}


def decide_proposal(db: Session, proposal_id: str, actor: Actor, decision: str, comment: str) -> PolicyProposal:
    prop = db.get(PolicyProposal, proposal_id)
    if prop is None:
        raise HTTPException(404, "Proposal not found")
    policy = active_policy(db)
    authorize(db, actor, "approve_policy_change", entity_type="policy_proposal", entity_id=prop.id,
              require_human=True, allowed_roles=policy["rbac"]["policy_admin_roles"])
    if prop.status != "PENDING":
        raise HTTPException(409, f"Proposal is {prop.status}")
    if decision not in ("APPROVE", "REJECT"):
        raise HTTPException(422, "decision must be APPROVE or REJECT")
    prop.status = "APPROVED" if decision == "APPROVE" else "REJECTED"
    prop.decided_by, prop.decided_at, prop.decision_comment = actor.id, utcnow(), comment
    if decision == "APPROVE":
        d = prop.data
        pv = create_version(db, changes=[{"path": d["path"], "old_value": d["current_value"],
                                          "new_value": d["proposed_value"]}],
                            created_by=actor.id, summary=prop.title, proposal_id=prop.id)
        prop.data = {**d, "resulting_version": pv.version}
    _a(db, actor, f"{decision.lower()}_policy_change", "policy_proposal", prop.id, comment=comment)
    return prop
