"""ORM models. JSON columns keep the prototype flexible while staying PostgreSQL-compatible."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.schemas import utcnow


class Counter(Base):
    __tablename__ = "counters"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))  # human | agent | system
    permissions: Mapped[list] = mapped_column(JSON, default=list)


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Execution(Base):
    __tablename__ = "executions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ticket_pk: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    ticket_id: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[str] = mapped_column(String(64))
    artifact_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(48))
    stage: Mapped[str] = mapped_column(String(48))
    current_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_decision: Mapped[str | None] = mapped_column(String(48), nullable=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    loop_state: Mapped[dict] = mapped_column(JSON, default=dict)
    timeline: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"), index=True)
    artifact_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(16))
    sha256: Mapped[str] = mapped_column(String(64))
    author_id: Mapped[str] = mapped_column(String(64))
    pr: Mapped[dict] = mapped_column(JSON)  # title, description, branch, impacted files
    files: Mapped[dict] = mapped_column(JSON)  # path -> content
    diff: Mapped[str] = mapped_column(Text)
    change_manifest: Mapped[dict] = mapped_column(JSON)  # dependencies, schema changes, etc.
    agent_state: Mapped[dict] = mapped_column(JSON)  # simulation bookkeeping (never shown to evaluators)
    usage: Mapped[dict] = mapped_column(JSON)  # tokens / cost
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Evaluation(Base):
    __tablename__ = "evaluations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    execution_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    artifact_version: Mapped[str] = mapped_column(String(16))
    artifact_hash: Mapped[str] = mapped_column(String(64))
    evaluation_type: Mapped[str] = mapped_column(String(48))
    lifecycle_stage: Mapped[str] = mapped_column(String(48))
    iteration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision: Mapped[str] = mapped_column(String(48))
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(32), index=True)
    artifact_version: Mapped[str] = mapped_column(String(16))
    artifact_hash: Mapped[str] = mapped_column(String(64))
    required_role: Mapped[str] = mapped_column(String(48))
    risk_level: Mapped[str] = mapped_column(String(16))
    risk_score: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32))  # PENDING|APPROVED|REJECTED|CHANGES_REQUESTED|INVALIDATED
    recommended_decision: Mapped[str] = mapped_column(String(32))
    recommendation_reasons: Mapped[list] = mapped_column(JSON, default=list)
    approver_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approver_role: Mapped[str | None] = mapped_column(String(48), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comments: Mapped[list] = mapped_column(JSON, default=list)
    invalidated_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    evaluation_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Deployment(Base):
    __tablename__ = "deployments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(32), index=True)
    service: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    artifact_hash: Mapped[str] = mapped_column(String(64))
    feature_flag: Mapped[dict] = mapped_column(JSON)
    traffic_percent: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32))
    health_profile: Mapped[str] = mapped_column(String(32))
    steps: Mapped[list] = mapped_column(JSON, default=list)
    promotion_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    deployment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    execution_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    service: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(256))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Postmortem(Base):
    __tablename__ = "postmortems"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(32), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    change_categories: Mapped[list] = mapped_column(JSON, default=list)
    completed_by: Mapped[str] = mapped_column(String(64))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    version: Mapped[str] = mapped_column(String(16), primary_key=True)
    policy: Mapped[dict] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(64))
    change_summary: Mapped[str] = mapped_column(Text, default="")
    changes: Mapped[list] = mapped_column(JSON, default=list)
    proposal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PolicyProposal(Base):
    __tablename__ = "policy_proposals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))  # PENDING|APPROVED|REJECTED
    title: Mapped[str] = mapped_column(String(256))
    data: Mapped[dict] = mapped_column(JSON)
    base_version: Mapped[str] = mapped_column(String(16))
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEntry(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_id: Mapped[str] = mapped_column(String(64))
    actor_kind: Mapped[str] = mapped_column(String(16))
    actor_role: Mapped[str] = mapped_column(String(48))
    action: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16))  # ALLOWED | DENIED | INFO
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    artifact_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64))


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipient_role: Mapped[str] = mapped_column(String(48))
    recipient_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(32))  # simulated pager / email
    subject: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
