"""Agent version registry: which configuration of each agent is ACTIVE in production."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.coding_agent import SimulatedCodingAgent
from app.models import AgentVersion

SEED_AGENT_VERSIONS: list[dict[str, Any]] = [
    {"id": "coding-agent@1.0", "target": "coding_agent", "status": "ACTIVE", "config": {"fixes_per_iteration": 2},
     "description": "Baseline: resolves the two most severe feedback items per iteration."},
    {"id": "coding-agent@1.1", "target": "coding_agent", "status": "CANDIDATE", "config": {"fixes_per_iteration": 3},
     "description": "Candidate: batches three fixes per iteration (fewer iterations, lower cost)."},
    {"id": "coding-agent@0.9-exp", "target": "coding_agent", "status": "CANDIDATE",
     "config": {"fixes_per_iteration": 1},
     "description": "Experimental: one fix per iteration. Expected to hit iteration limits."},
    {"id": "risk-evaluator@1.0", "target": "risk_evaluator", "status": "ACTIVE", "config": {"policy_overrides": {}},
     "description": "Risk evaluator using the active governance policy."},
    {"id": "risk-evaluator@1.1-db60", "target": "risk_evaluator", "status": "CANDIDATE",
     "config": {"policy_overrides": {"risk.factors.infrastructure_impact.db_schema_change": 60}},
     "description": "Policy what-if: schema-change factor raised to 60 (as proposed by the feedback engine)."},
    {"id": "sre-agent@1.0", "target": "sre_agent", "status": "ACTIVE", "config": {},
     "description": "Rule-based SRE incident summariser."},
]


def seed_agent_versions(db: Session) -> None:
    for av in SEED_AGENT_VERSIONS:
        if db.get(AgentVersion, av["id"]) is None:
            db.add(AgentVersion(**av, promotion={}))
    db.flush()


def active_version(db: Session, target: str) -> AgentVersion | None:
    return db.execute(select(AgentVersion).where(AgentVersion.target == target, AgentVersion.status == "ACTIVE")
                      ).scalars().first()


def coding_agent_for(av: AgentVersion | None) -> SimulatedCodingAgent:
    if av is None:
        return SimulatedCodingAgent()
    return SimulatedCodingAgent(version=av.id, config=av.config)


def active_coding_agent(db: Session) -> SimulatedCodingAgent:
    return coding_agent_for(active_version(db, "coding_agent"))
