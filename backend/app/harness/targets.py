"""Target adapters: run the *production* agent code on one fixture.

The harness keeps its own orchestration and result store, but the agents and the
evaluation engine are the same modules production uses.
"""

from __future__ import annotations

import copy
import time
from typing import Any

from sqlalchemy.orm import Session

from app.agents.coding_agent import render_files, unified_diff
from app.agents.ops_agents import SREAgent
from app.core.common import artifact_hash
from app.evaluation.engine import EvaluationEngine
from app.evaluation.risk import evaluate_risk
from app.evaluation.security import evaluate_security
from app.evaluation.support import EvidenceCollector, ThresholdManager
from app.harness.gateway import ModelGateway
from app.harness.registry import coding_agent_for
from app.models import AgentVersion
from app.schemas import Decision, SimulationControls, TicketCreate
from app.services.policies import set_path


class TransientError(RuntimeError):
    """Simulated infrastructure hiccup (e.g. sandbox start-up). Retried by the runner."""


def run_coding_case(db: Session, policy: dict, av: AgentVersion, fixture: dict, run_id: str,
                    gateway: ModelGateway) -> dict[str, Any]:
    inp = fixture["input"]
    ticket = TicketCreate(ticket_id=f"EVAL-{fixture['case_id']}", simulation=SimulationControls(
        scenario=inp["scenario"], defect_profile=inp["defect_profile"])).model_dump()
    if inp["scenario"] == "low_risk":
        ticket.update(criticality="low", downstream_services=[])
    agent = coding_agent_for(av)
    engine = EvaluationEngine(db, policy, persist=False)
    limits = ThresholdManager(policy).loop_limits()
    tokens = cost = 0.0
    t0 = time.time()

    def charge(draft):
        nonlocal tokens, cost
        u = draft.usage
        call = gateway.meter(caller="eval", agent=av.id, purpose="coding", model=u["model"],
                             prompt_tokens=u["prompt_tokens"], completion_tokens=u["completion_tokens"], run_id=run_id)
        tokens += u["total_tokens"]
        cost += call.cost_usd

    draft = agent.generate(ticket)
    charge(draft)
    history: list[dict] = []
    trace = []
    for iteration in range(1, 50):
        sha = artifact_hash(draft.files, draft.manifest)
        ctx = {"project_id": "EVAL", "ticket_id": ticket["ticket_id"], "artifact_id": "EVAL-PR",
               "artifact_version": f"v{iteration}", "artifact_hash": sha, "execution_id": None, "author_kind": "agent"}
        loop = {"iteration": iteration, "tokens": tokens, "cost_usd": cost, "elapsed_s": time.time() - t0}
        result, feedback = engine.evaluate_development(ctx, draft.files, unified_diff(draft.files), draft.manifest,
                                                       ticket, iteration, history, loop, limits)
        history.append(result.model_dump(mode="json"))
        trace.append({"iteration": iteration, "quality": result.quality_score, "decision": result.decision.value,
                      "failed": len(result.failed_criteria)})
        if result.decision != Decision.RETRY:
            break
        draft = agent.revise(ticket, draft, feedback, iteration + 1)
        charge(draft)
    return {"decision": result.decision.value, "iterations": iteration, "final_quality": result.quality_score,
            "trace": trace, "tokens": int(tokens), "cost_usd": round(cost, 6),
            "_usage": {"tokens": int(tokens), "cost_usd": cost}}


def run_risk_case(db: Session, policy: dict, av: AgentVersion, fixture: dict, run_id: str,
                  gateway: ModelGateway) -> dict[str, Any]:
    pol = copy.deepcopy(policy)
    for path, value in (av.config.get("policy_overrides") or {}).items():
        set_path(pol, path, value)
    tm = ThresholdManager(pol)
    inp = fixture["input"]
    files = render_files(inp["scenario"], set(inp.get("defects", [])))
    ticket = {**TicketCreate().model_dump(), "criticality": inp["criticality"],
              "downstream_services": inp["downstream_services"]}
    ev = EvidenceCollector()
    sec = evaluate_security(files, ev, tm)
    _, rb = evaluate_risk(files, unified_diff(files), ticket, sec.findings, [], [], True, ev, tm)
    return {"risk_score": rb.risk_score, "risk_level": rb.risk_level.value, "drivers": rb.primary_drivers,
            "_usage": {"tokens": 0, "cost_usd": 0.0}}


def run_sre_case(db: Session, policy: dict, av: AgentVersion, fixture: dict, run_id: str,
                 gateway: ModelGateway) -> dict[str, Any]:
    inp = fixture["input"]
    out = SREAgent().summarize(inp["violated"], "svc@eval", 5, inp["categories"])
    return {**out, "_usage": {"tokens": 0, "cost_usd": 0.0}}


TARGETS = {"coding_agent": run_coding_case, "risk_evaluator": run_risk_case, "sre_agent": run_sre_case}
