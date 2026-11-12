"""REST API for the eval harness, agent version registry and model gateway."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import Actor, current_actor
from app.db import get_session
from app.harness import demo as D
from app.harness import gateway as G
from app.harness import runner as R
from app.harness.graders import GRADERS
from app.harness.suites import latest_dataset, latest_grader
from app.models import AgentVersion, EvalDataset, EvalGrader, EvalRun, EvalSuite, ModelCall
from app.services.policies import active_policy

router = APIRouter(prefix="/api")


def row(obj) -> dict[str, Any]:
    return {c.key: getattr(obj, c.key) for c in obj.__table__.columns}


class RunCreate(BaseModel):
    suite_id: str
    agent_version: str | None = Field(None, description="Defaults to the ACTIVE version for the suite's target")
    dataset_version: str | None = None
    grader_version: str | None = None
    budget_usd: float | None = None
    max_cases: int | None = Field(None, description="Stop (PAUSED) after this many new cases; resume later")


class ResumeRequest(BaseModel):
    max_cases: int | None = None
    budget_usd: float | None = Field(None, description="Raise the budget of a BUDGET_EXCEEDED run")


class RegradeRequest(BaseModel):
    grader_version: str


class GraderCreate(BaseModel):
    graders: list[dict[str, Any]]
    notes: str = ""


class DatasetCreate(BaseModel):
    fixtures: list[dict[str, Any]]
    notes: str = ""


class PromotionRequest(BaseModel):
    run_id: str


class PromotionDecision(BaseModel):
    decision: str = Field(..., description="APPROVE | REJECT")
    comment: str = ""


def run_detail(db: Session, run: EvalRun) -> dict[str, Any]:
    return {**R.run_view(run), "cases": [row(c) for c in R.cases_of(db, run.id)]}


# ---------------------------------------------------------------------------- suites
@router.get("/harness/suites")
def suites(db: Session = Depends(get_session)):
    out = []
    for s in db.execute(select(EvalSuite).order_by(EvalSuite.id)).scalars():
        ds, gr = latest_dataset(db, s.id), latest_grader(db, s.id)
        last = db.execute(select(EvalRun).where(EvalRun.suite_id == s.id).order_by(EvalRun.created_at.desc())
                          ).scalars().first()
        out.append({**row(s), "latest_dataset": ds.version if ds else None, "cases": len(ds.fixtures) if ds else 0,
                    "latest_grader": gr.version if gr else None, "last_run": R.run_view(last) if last else None})
    return out


@router.get("/harness/suites/{suite_id}")
def suite(suite_id: str, db: Session = Depends(get_session)):
    s = db.get(EvalSuite, suite_id)
    if s is None:
        raise HTTPException(404, "Suite not found")
    datasets = db.execute(select(EvalDataset).where(EvalDataset.suite_id == suite_id).order_by(EvalDataset.id)).scalars()
    graders = db.execute(select(EvalGrader).where(EvalGrader.suite_id == suite_id).order_by(EvalGrader.id)).scalars()
    return {**row(s), "datasets": [row(d) for d in datasets], "graders": [row(g) for g in graders],
            "grader_types": sorted(GRADERS)}


@router.post("/harness/suites/{suite_id}/graders")
def add_grader(suite_id: str, req: GraderCreate, actor: Actor = Depends(current_actor),
               db: Session = Depends(get_session)):
    return row(R.add_grader_version(db, suite_id, {"graders": req.graders}, req.notes, actor))


@router.post("/harness/suites/{suite_id}/datasets")
def add_dataset(suite_id: str, req: DatasetCreate, actor: Actor = Depends(current_actor),
                db: Session = Depends(get_session)):
    return row(R.add_dataset_version(db, suite_id, req.fixtures, req.notes, actor))


# ---------------------------------------------------------------------------- runs
@router.get("/harness/runs")
def runs(suite_id: str | None = None, db: Session = Depends(get_session)):
    q = select(EvalRun).order_by(EvalRun.created_at.desc())
    if suite_id:
        q = q.where(EvalRun.suite_id == suite_id)
    return [R.run_view(r) for r in db.execute(q).scalars()]


@router.post("/harness/runs")
def create_run(req: RunCreate, actor: Actor = Depends(current_actor), db: Session = Depends(get_session)):
    s = db.get(EvalSuite, req.suite_id)
    if s is None:
        raise HTTPException(404, "Suite not found")
    agent = req.agent_version
    if agent is None:
        active = db.execute(select(AgentVersion).where(AgentVersion.target == s.target,
                                                       AgentVersion.status == "ACTIVE")).scalars().first()
        agent = active.id if active else ""
    run = R.create_run(db, suite_id=req.suite_id, agent_version=agent, actor=actor,
                       dataset_version=req.dataset_version, grader_version=req.grader_version, budget_usd=req.budget_usd)
    run = R.execute(db, run.id, max_cases=req.max_cases)
    return run_detail(db, run)


@router.get("/harness/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_session)):
    run = db.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run_detail(db, run)


@router.post("/harness/runs/{run_id}/resume")
def resume(run_id: str, req: ResumeRequest, db: Session = Depends(get_session)):
    run = db.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    if req.budget_usd is not None:
        run.budget_usd = req.budget_usd
        db.commit()
    return run_detail(db, R.execute(db, run_id, max_cases=req.max_cases))


@router.post("/harness/runs/{run_id}/pause")
def pause(run_id: str, db: Session = Depends(get_session)):
    if db.get(EvalRun, run_id) is None:
        raise HTTPException(404, "Run not found")
    return R.run_view(R.request_pause(db, run_id))


@router.post("/harness/runs/{run_id}/regrade")
def regrade(run_id: str, req: RegradeRequest, actor: Actor = Depends(current_actor),
            db: Session = Depends(get_session)):
    return run_detail(db, R.regrade(db, run_id, req.grader_version, actor))


@router.get("/harness/compare")
def compare(a: str, b: str, db: Session = Depends(get_session)):
    return R.compare(db, a, b)


# ---------------------------------------------------------------------------- agent versions
@router.get("/harness/agent-versions")
def agent_versions(db: Session = Depends(get_session)):
    return [row(a) for a in db.execute(select(AgentVersion).order_by(AgentVersion.target, AgentVersion.id)).scalars()]


@router.post("/harness/agent-versions/{version_id}/request-promotion")
def request_promotion(version_id: str, req: PromotionRequest, actor: Actor = Depends(current_actor),
                      db: Session = Depends(get_session)):
    return row(R.request_promotion(db, version_id, req.run_id, actor))


@router.post("/harness/agent-versions/{version_id}/decision")
def promotion_decision(version_id: str, req: PromotionDecision, actor: Actor = Depends(current_actor),
                       db: Session = Depends(get_session)):
    if req.decision not in ("APPROVE", "REJECT"):
        raise HTTPException(422, "decision must be APPROVE or REJECT")
    return row(R.decide_promotion(db, version_id, actor, req.decision, req.comment))


@router.post("/harness/demo")
def harness_demo(db: Session = Depends(get_session)):
    """Populate the harness with a reproducible walkthrough (about 30 seconds)."""
    return D.run_demo(db)


# ---------------------------------------------------------------------------- model gateway
@router.get("/gateway/summary")
def gateway_summary(db: Session = Depends(get_session)):
    return G.summary(db)


@router.get("/gateway/prices")
def gateway_prices(db: Session = Depends(get_session)):
    return active_policy(db)["gateway"]


@router.get("/gateway/calls")
def gateway_calls(caller: str | None = None, run_id: str | None = None, limit: int = 200,
                  db: Session = Depends(get_session)):
    q = select(ModelCall).order_by(ModelCall.id.desc()).limit(limit)
    if caller:
        q = q.where(ModelCall.caller == caller)
    if run_id:
        q = q.where(ModelCall.run_id == run_id)
    return [row(c) for c in db.execute(q).scalars()]
