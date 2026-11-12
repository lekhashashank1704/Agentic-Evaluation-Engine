"""Seed data: users/agents, policy v1.0 and one historical incident with a completed postmortem."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.common import audit
from app.core.rbac import SEED_USERS
from app.models import Incident, Postmortem, User
from app.schemas import utcnow
from app.services.policies import active_policy


def seed(db: Session) -> None:
    if db.get(User, "user-alex") is not None:
        return
    for u in SEED_USERS:
        db.add(User(**u))
    active_policy(db)
    when = utcnow() - timedelta(days=41)
    db.add(Incident(
        id="INC-0000", deployment_id=None, execution_id=None, service="accounts-api", severity="SEV2",
        status="POSTMORTEM_COMPLETE", title="accounts-api latency spike after schema migration",
        created_at=when,
        data={"timeline": [{"ts": when.isoformat(), "event": "Historical incident (seed data)", "actor": "seed"}],
              "change_categories": ["db_schema_change"], "summary": "Seeded historical incident.",
              "violated_metrics": [], "logs": [], "recommendations": [], "seeded": True,
              "deployment_version": "accounts-api@3.4.0"}))
    db.add(Postmortem(
        id="PM-0000", incident_id="INC-0000", change_categories=["db_schema_change"], completed_by="user-jordan",
        completed_at=when + timedelta(days=2),
        data={"incident_id": "INC-0000", "service": "accounts-api", "deployment_version": "accounts-api@3.4.0",
              "root_cause": "A blocking ALTER TABLE ran during peak traffic and locked the accounts table.",
              "business_impact": "Account page unavailable for 14% of users for 22 minutes.",
              "technical_impact": "p99 latency 6.1s, error rate 7%.", "detection_method": "Customer reports",
              "recovery_actions": ["Killed migration", "Restored from replica"],
              "preventive_actions": ["Run schema migrations online and separately from code releases"],
              "lessons_learned": ["Schema changes need their own rollout plan"], "severity": "SEV2",
              "seeded": True}))
    audit(db, actor_id="system-governance", actor_kind="system", actor_role="governance_engine", action="seed",
          entity_type="system", entity_id="bootstrap", details={"users": len(SEED_USERS), "policy": "1.0"})
    db.flush()
