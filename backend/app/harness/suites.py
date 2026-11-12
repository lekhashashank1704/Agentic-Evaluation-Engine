"""Seed eval suites: versioned golden datasets (fixtures) and versioned graders.

A new evaluation needs only two things: a fixture list and a grader spec.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EvalDataset, EvalGrader, EvalSuite


def content_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def _c(case_id, scenario, profile, decision, max_iter, min_q=0.95, **extra):
    return {"case_id": case_id, "input": {"scenario": scenario, "defect_profile": profile, **extra},
            "expected": {"decision": decision, "max_iterations": max_iter, "min_quality": min_q}}


CODING_FIXTURES = [
    _c("std-realistic", "standard", "realistic", "SUCCESS", 4),
    _c("low-realistic", "low_risk", "realistic", "SUCCESS", 4),
    _c("crit-realistic", "critical_auth_payment", "realistic", "SUCCESS", 5),
    _c("std-clean", "standard", "clean", "SUCCESS", 1),
    _c("low-clean", "low_risk", "clean", "SUCCESS", 1),
    _c("std-secret", "standard", "critical_secret", "BLOCK", 1),
    _c("std-protected", "standard", "protected_path", "BLOCK", 1),
    _c("std-stagnation", "standard", "stagnation", "STAGNATION_DETECTED", 3),
    _c("std-regression", "standard", "regression", "SUCCESS", 5),
    {**_c("std-flaky-infra", "standard", "realistic", "SUCCESS", 4), "transient_failures": 1},
]

CODING_GRADERS = {
    "v1": {"notes": "Initial grader: rewards SUCCESS and high quality on every case.",
           "graders": [{"type": "decision_is_success", "weight": 0.5},
                       {"type": "final_quality", "weight": 0.3, "apply_to_all": True},
                       {"type": "iterations_within", "weight": 0.2}]},
    "v2": {"notes": "Refined after inspecting outputs: correct BLOCK/STAGNATION outcomes are the desired behaviour, "
                    "and quality only matters where SUCCESS is expected.",
           "graders": [{"type": "expected_decision", "weight": 0.5},
                       {"type": "final_quality", "weight": 0.3, "apply_to_all": False},
                       {"type": "iterations_within", "weight": 0.2}]},
}


def _r(case_id, scenario, criticality, level, downstream=("mobile-bff", "support-portal"), defects=()):
    return {"case_id": case_id, "input": {"scenario": scenario, "criticality": criticality,
                                          "downstream_services": list(downstream), "defects": list(defects)},
            "expected": {"risk_level": level}}


# Labels assigned by (simulated) human reviewers of past changes.
RISK_FIXTURES = [
    _r("low-internal", "low_risk", "low", "LOW", downstream=()),
    _r("low-medium-crit", "low_risk", "medium", "LOW", downstream=()),
    _r("std-high", "standard", "high", "HIGH"),
    _r("std-medium", "standard", "medium", "MEDIUM"),
    _r("std-critical-app", "standard", "critical", "HIGH"),
    _r("crit-critical", "critical_auth_payment", "critical", "CRITICAL"),
    _r("crit-high", "critical_auth_payment", "high", "CRITICAL"),
    _r("std-low-crit-schema", "standard", "low", "MEDIUM", downstream=()),
]

RISK_GRADERS = {
    "v1": {"notes": "Exact band match only.", "graders": [{"type": "exact_risk_level", "weight": 1.0}]},
    "v2": {"notes": "Partial credit for an adjacent band; an under-estimate is penalised more than an over-estimate.",
           "graders": [{"type": "risk_level_distance", "weight": 1.0}]},
}


def _s(case_id, violated, categories, keywords):
    return {"case_id": case_id, "input": {"violated": violated, "categories": categories},
            "expected": {"keywords": keywords}}


def _v(metric, label, value, limit, unit, op="<="):
    return {"metric": metric, "label": label, "value": value, "limit": limit, "unit": unit, "operator": op,
            "baseline": None}


SRE_FIXTURES = [
    _s("schema-latency", [_v("p99_latency_ms", "p99 latency", 2900, 1500, "ms")], ["db_schema_change"],
       ["rolled back", "migration", "query plan", "load test"]),
    _s("errors-payment", [_v("error_rate_pct", "Error rate", 4.8, 1.0, "%")], ["payment_change"],
       ["rolled back", "5xx", "ledger", "timeouts"]),
    _s("errors-auth", [_v("error_rate_pct", "Error rate", 3.1, 1.0, "%")], ["auth_change"],
       ["rolled back", "5xx", "token", "issuer"]),
    _s("cpu-only", [_v("cpu_utilization_pct", "CPU utilization", 91, 80, "%")], ["api_change"],
       ["rolled back", "cpu", "profile", "load test"]),
]

SRE_GRADERS = {
    "v1": {"notes": "Keyword recall against the expected remediation points.",
           "graders": [{"type": "keyword_recall", "weight": 1.0}]},
    "v2": {"notes": "Adds a rubric-based LLM judge (via the model gateway) for actionability and specificity.",
           "graders": [{"type": "keyword_recall", "weight": 0.6},
                       {"type": "llm_judge", "weight": 0.4,
                        "rubric": ["names the change that caused the breach", "gives a concrete next step",
                                   "keeps the human in control of remediation"]}]},
}

SUITES = [
    {"id": "coding-agent-core", "name": "Coding agent: core tickets", "target": "coding_agent", "pass_threshold": 0.85,
     "description": "Runs the production coding agent through the production evaluation engine on 10 tickets.",
     "fixtures": CODING_FIXTURES, "graders": CODING_GRADERS},
    {"id": "risk-calibration", "name": "Risk evaluator calibration", "target": "risk_evaluator", "pass_threshold": 0.8,
     "description": "Compares the Risk Gate's classification with human-labelled changes.",
     "fixtures": RISK_FIXTURES, "graders": RISK_GRADERS},
    {"id": "sre-incident-summaries", "name": "SRE agent: incident summaries", "target": "sre_agent",
     "pass_threshold": 0.75, "description": "Grades the SRE agent's incident summary and recommendations.",
     "fixtures": SRE_FIXTURES, "graders": SRE_GRADERS},
]


def seed_suites(db: Session) -> None:
    for s in SUITES:
        if db.get(EvalSuite, s["id"]) is not None:
            continue
        db.add(EvalSuite(id=s["id"], name=s["name"], target=s["target"], description=s["description"],
                         pass_threshold=s["pass_threshold"]))
        db.add(EvalDataset(suite_id=s["id"], version="v1", fixtures=s["fixtures"], sha256=content_hash(s["fixtures"]),
                           notes="Initial golden dataset", created_by="seed"))
        for ver, g in s["graders"].items():
            spec = {"graders": g["graders"]}
            db.add(EvalGrader(suite_id=s["id"], version=ver, spec=spec, sha256=content_hash(spec), notes=g["notes"],
                              created_by="seed"))
    db.flush()


def latest_dataset(db: Session, suite_id: str) -> EvalDataset | None:
    return db.execute(select(EvalDataset).where(EvalDataset.suite_id == suite_id).order_by(EvalDataset.id.desc())
                      ).scalars().first()


def latest_grader(db: Session, suite_id: str) -> EvalGrader | None:
    return db.execute(select(EvalGrader).where(EvalGrader.suite_id == suite_id).order_by(EvalGrader.id.desc())
                      ).scalars().first()
