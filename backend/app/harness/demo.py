"""Reproducible eval-harness walkthrough, acting as the seeded users.

1. Coding suite: the ACTIVE agent is graded with the naive v1 grader, then the stored outputs are
   re-graded with v2 (no agent work repeated). The comparison attributes the whole change to the grader.
2. Candidate coding-agent@1.1 is evaluated on v2 and its promotion is requested; it waits for a human.
3. The experimental 0.9 build is started with a case cap and left PAUSED, ready to resume.
4. Risk calibration: v1 vs v2 grader, then the db60 policy what-if as a candidate risk evaluator.
5. SRE summaries: keyword recall (v1), then the LLM judge (v2) via the model gateway; a re-grade
   replays the judge from the gateway cache at zero cost.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.rbac import load_actor
from app.harness import runner as R
from app.harness.registry import active_version
from app.models import EvalSuite


def run_demo(db: Session) -> dict[str, Any]:
    alex = load_actor(db, "user-alex")
    out: dict[str, Any] = {}

    def full(suite: str, agent: str | None, grader: str) -> str:
        agent = agent or active_version(db, db.get(EvalSuite, suite).target).id
        run = R.create_run(db, suite_id=suite, agent_version=agent, actor=alex, grader_version=grader)
        return R.execute(db, run.id).id

    base_v1 = full("coding-agent-core", None, "v1")
    base_v2 = R.regrade(db, base_v1, "v2", alex).id
    cand = full("coding-agent-core", "coding-agent@1.1", "v2")
    R.request_promotion(db, "coding-agent@1.1", cand, alex)
    db.commit()
    paused = R.create_run(db, suite_id="coding-agent-core", agent_version="coding-agent@0.9-exp", actor=alex)
    R.execute(db, paused.id, max_cases=3)
    out["coding"] = {"active_v1": base_v1, "active_v2_regrade": base_v2, "candidate_1_1": cand,
                     "experimental_paused": paused.id}

    risk_v1 = full("risk-calibration", None, "v1")
    risk_v2 = R.regrade(db, risk_v1, "v2", alex).id
    risk_db60 = full("risk-calibration", "risk-evaluator@1.1-db60", "v2")
    out["risk"] = {"active_v1": risk_v1, "active_v2_regrade": risk_v2, "candidate_db60": risk_db60}

    sre_v1 = full("sre-incident-summaries", None, "v1")
    sre_v2 = R.regrade(db, sre_v1, "v2", alex).id
    sre_replay = R.regrade(db, sre_v1, "v2", alex).id
    out["sre"] = {"active_v1": sre_v1, "llm_judge_v2": sre_v2, "replayed_v2": sre_replay}
    db.commit()
    return out
