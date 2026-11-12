"""Pydantic schemas shared by the API, the evaluation engine and the agents."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- enums
class Decision(str, Enum):
    CONTINUE = "CONTINUE"
    SUCCESS = "SUCCESS"
    FAIL = "FAIL"
    RETRY = "RETRY"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    BLOCK = "BLOCK"
    ROLLBACK = "ROLLBACK"
    ESCALATE = "ESCALATE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    MAX_ITERATIONS_REACHED = "MAX_ITERATIONS_REACHED"
    STAGNATION_DETECTED = "STAGNATION_DETECTED"


class CheckStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    WARNING = "WARNING"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ActorKind(str, Enum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"


# --------------------------------------------------------------------------- tickets
class SimulationControls(BaseModel):
    """Controls for the deterministic simulated agents. Clearly not part of a real ticket."""

    scenario: str = Field("standard", description="standard | critical_auth_payment | low_risk")
    defect_profile: str = Field(
        "realistic",
        description="realistic | clean | critical_secret | stagnation | regression | protected_path",
    )
    fixes_per_iteration: int | None = Field(None, description="None = use the active agent version's config")
    seed: int = 42
    budget_usd_override: float | None = None
    max_iterations_override: int | None = None


class TicketCreate(BaseModel):
    project_id: str = "PAY-001"
    ticket_id: str = "TICKET-104"
    title: str = "Customer transaction history API"
    business_objective: str = (
        "Give customers and support agents a paginated, secure view of historical transactions."
    )
    description: str = (
        "Implement a new customer transaction history API endpoint with authentication, "
        "pagination and response validation."
    )
    functional_requirements: list[str] = Field(
        default_factory=lambda: [
            "Expose GET /customers/{customer_id}/transactions",
            "Require a valid bearer token",
            "Customers may only read their own transactions",
            "Support page and page_size query parameters (max page_size 100)",
            "Validate every response item against the transaction schema",
        ]
    )
    non_functional_requirements: list[str] = Field(
        default_factory=lambda: ["p99 latency below 1.5 s", "Error rate below 1%", "Document the endpoint"]
    )
    acceptance_criteria: list[dict[str, str]] = Field(
        default_factory=lambda: [
            {"id": "AC-1", "text": "Requests without a valid bearer token return 401."},
            {"id": "AC-2", "text": "A customer requesting another customer's history receives 403."},
            {"id": "AC-3", "text": "Pagination returns newest-first pages, enforces page_size <= 100 and rejects invalid pages."},
            {"id": "AC-4", "text": "Every item matches the transaction schema; invalid customer ids return 400."},
            {"id": "REG", "text": "Existing behaviour (empty history, default page size) is preserved."},
        ]
    )
    security_constraints: list[str] = Field(
        default_factory=lambda: [
            "No hardcoded credentials",
            "Parameterised SQL only",
            "No PII in logs",
        ]
    )
    affected_application: str = "txn-history-api"
    criticality: str = Field("high", description="low | medium | high | critical")
    engineering_owner: str = "user-alex"
    expected_outcome: str = "Endpoint deployed to production behind a feature flag with canary validation."
    downstream_services: list[str] = Field(default_factory=lambda: ["mobile-bff", "support-portal"])
    simulation: SimulationControls = Field(default_factory=SimulationControls)


# --------------------------------------------------------------------------- evaluation
class Evidence(BaseModel):
    evidence_id: str
    type: str  # test_report | static_analysis | diff | telemetry | dependency_scan | approval | policy
    source: str  # tool that produced the evidence
    executed: bool  # the tool actually ran during this evaluation
    simulated: bool  # the underlying data is synthetic (e.g. telemetry, advisory DB)
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=utcnow)


class Check(BaseModel):
    check_id: str
    name: str
    category: str
    status: CheckStatus
    severity: Severity = Severity.INFO
    mandatory: bool = False
    message: str = ""
    criterion_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    finding_id: str
    rule_id: str
    title: str
    severity: Severity
    file: str | None = None
    line: int | None = None
    snippet: str | None = None
    category: str = "general"
    tool: str = ""
    remediation: str = ""


class MetricScore(BaseModel):
    metric: str
    definition: str
    formula: str
    weight: float
    data_source: str
    threshold: float | None = None
    value: float
    weighted_contribution: float
    result: CheckStatus
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str = ""


class ScoreBreakdown(BaseModel):
    objective: str
    score: float
    threshold: float
    passed: bool
    metrics: list[MetricScore]


class RiskFactor(BaseModel):
    dimension: str
    factor: str
    points: float
    reason: str
    evidence: list[str] = Field(default_factory=list)


class RiskDimension(BaseModel):
    dimension: str
    score: float  # 0-100
    weight: float
    weighted_score: float
    factors: list[RiskFactor]


class RiskBreakdown(BaseModel):
    risk_score: float
    risk_level: RiskLevel
    dimensions: list[RiskDimension]
    overrides: list[str] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    additional_validation: list[str] = Field(default_factory=list)
    primary_drivers: list[str] = Field(default_factory=list)


class EvaluatorReport(BaseModel):
    evaluator: str
    status: str  # COMPLETED | ERROR | SKIPPED
    score: float | None = None
    passed: bool
    checks: list[Check] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    explanation: list[str] = Field(default_factory=list)
    duration_ms: int = 0


class EvaluationResult(BaseModel):
    evaluation_id: str
    project_id: str
    ticket_id: str
    artifact_id: str
    artifact_version: str
    artifact_hash: str
    lifecycle_stage: str
    evaluation_type: str
    quality_score: float | None = None
    quality_threshold: float | None = None
    risk_score: float | None = None
    risk_level: RiskLevel | None = None
    evaluation_status: str = "COMPLETED"
    decision: Decision
    checks: list[Check] = Field(default_factory=list)
    failed_criteria: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    explanation: list[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=utcnow)
    policy_version: str
    # extensions beyond the minimum schema
    iteration: int | None = None
    execution_id: str | None = None
    score_breakdown: ScoreBreakdown | None = None
    risk_breakdown: RiskBreakdown | None = None
    evaluator_reports: list[EvaluatorReport] = Field(default_factory=list)
    iteration_comparison: dict[str, Any] | None = None
    health_metrics: dict[str, Any] | None = None


# --------------------------------------------------------------------------- feedback
class FeedbackItem(BaseModel):
    feedback_id: str
    source: str  # evaluator or human
    criterion: str
    severity: Severity
    message: str
    remediation: str = ""
    defect_ref: str | None = None  # deterministic link for the simulated fix agent


# --------------------------------------------------------------------------- requests
class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(..., description="APPROVE | REJECT | REQUEST_CHANGES")
    comment: str = ""


class CommentRequest(BaseModel):
    comment: str


class ReleaseRequest(BaseModel):
    health_profile: str = Field("healthy", description="healthy | unhealthy")
    auto_promote: bool = False


class IncidentUpdate(BaseModel):
    assignee: str | None = None
    status: str | None = None
    root_cause: str | None = None
    remediation_owner: str | None = None
    corrective_actions: list[str] | None = None
    note: str | None = None


class PostmortemCreate(BaseModel):
    root_cause: str
    business_impact: str
    technical_impact: str
    detection_method: str = "Automated Health Gate (canary)"
    recovery_actions: list[str]
    preventive_actions: list[str]
    lessons_learned: list[str]
    change_categories: list[str] | None = None


class ProposalDecision(BaseModel):
    decision: str = Field(..., description="APPROVE | REJECT")
    comment: str = ""
