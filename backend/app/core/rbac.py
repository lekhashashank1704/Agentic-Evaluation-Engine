"""Role-based access control and agent permission boundaries.

Identity in this prototype comes from the ``X-Actor-Id`` header (a stand-in for
SSO / OIDC). The important property is enforced server side: agents can never
perform human-only actions, whatever header or payload they send.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.common import audit
from app.db import get_session
from app.models import User

# Actions no agent may ever perform, regardless of configuration.
AGENT_FORBIDDEN_ACTIONS = {
    "approve",
    "reject",
    "request_changes",
    "merge_to_main",
    "modify_policy",
    "approve_policy_change",
    "bypass_evaluation",
    "complete_postmortem",
    "promote_agent",
}

SEED_USERS = [
    # humans
    {"id": "user-alex", "name": "Alex Rivera", "role": "engineer", "kind": "human",
     "permissions": ["create_ticket", "run_execution", "trigger_release", "comment"]},
    {"id": "user-ravi", "name": "Ravi Menon", "role": "reviewer", "kind": "human",
     "permissions": ["approve", "reject", "request_changes", "comment", "run_execution"]},
    {"id": "user-priya", "name": "Priya Shah", "role": "tech_lead", "kind": "human",
     "permissions": ["approve", "reject", "request_changes", "comment", "trigger_release", "run_execution",
                     "create_ticket", "promote_agent"]},
    {"id": "user-sam", "name": "Sam Okafor", "role": "security_lead", "kind": "human",
     "permissions": ["approve", "reject", "request_changes", "comment"]},
    {"id": "user-jordan", "name": "Jordan Lee", "role": "sre_oncall", "kind": "human",
     "permissions": ["manage_incident", "complete_postmortem", "comment", "authorize_rollback"]},
    {"id": "user-morgan", "name": "Morgan Chen", "role": "governance_admin", "kind": "human",
     "permissions": ["approve_policy_change", "modify_policy", "comment", "analyze_feedback", "promote_agent"]},
    # agents
    {"id": "agent-coding", "name": "Coding / Fix Agent", "role": "coding_agent", "kind": "agent",
     "permissions": ["generate_artifact", "revise_artifact", "open_pull_request"]},
    {"id": "agent-release", "name": "Release Agent", "role": "release_agent", "kind": "agent",
     "permissions": ["prepare_deployment", "deploy_canary", "promote_traffic", "toggle_feature_flag"]},
    {"id": "agent-sre", "name": "SRE Agent", "role": "sre_agent", "kind": "agent",
     "permissions": ["open_incident", "execute_authorized_rollback", "capture_telemetry", "recommend_remediation"]},
    # the engine itself
    {"id": "system-governance", "name": "Governance Engine", "role": "governance_engine", "kind": "system",
     "permissions": ["evaluate", "merge_to_main", "invalidate_approval", "open_approval", "analyze_feedback"]},
]


@dataclass
class Actor:
    id: str
    name: str
    role: str
    kind: str
    permissions: list[str]

    @property
    def is_human(self) -> bool:
        return self.kind == "human"


def to_actor(user: User) -> Actor:
    return Actor(user.id, user.name, user.role, user.kind, list(user.permissions or []))


def load_actor(db: Session, actor_id: str) -> Actor:
    user = db.get(User, actor_id)
    if user is None:
        raise HTTPException(status_code=401, detail=f"Unknown actor '{actor_id}'")
    return to_actor(user)


def current_actor(
    x_actor_id: str = Header("user-alex", alias="X-Actor-Id"), db: Session = Depends(get_session)
) -> Actor:
    return load_actor(db, x_actor_id)


class PermissionDenied(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=403, detail=detail)


def authorize(
    db: Session, actor: Actor, action: str, *, entity_type: str, entity_id: str,
    artifact_hash: str | None = None, require_human: bool = False, allowed_roles: list[str] | None = None,
) -> None:
    """Raise PermissionDenied (and audit the denial) unless ``actor`` may perform ``action``."""
    reason = None
    if actor.kind == "agent" and action in AGENT_FORBIDDEN_ACTIONS:
        reason = f"Agents may never perform '{action}' (agent boundary)."
    elif require_human and not actor.is_human:
        reason = f"'{action}' requires a human actor; {actor.kind} '{actor.id}' cannot impersonate a human."
    elif allowed_roles is not None and actor.role not in allowed_roles:
        reason = f"Role '{actor.role}' is not permitted to '{action}' (allowed: {', '.join(allowed_roles)})."
    elif action not in actor.permissions:
        reason = f"Actor '{actor.id}' lacks permission '{action}'."
    if reason:
        audit(db, actor_id=actor.id, actor_kind=actor.kind, actor_role=actor.role, action=action,
              entity_type=entity_type, entity_id=entity_id, outcome="DENIED", artifact_hash=artifact_hash,
              details={"reason": reason})
        db.commit()
        raise PermissionDenied(reason)
