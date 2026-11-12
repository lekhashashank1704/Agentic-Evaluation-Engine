"""Shared model gateway.

The single door to (simulated) hosted models for both the production lifecycle
and eval runs. It meters every call against a price list, attributes cost to the
caller (production vs eval), and offers record/replay: identical requests are
served from the cache, which makes reruns free and results reproducible.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import GatewayCache, ModelCall


def _hash(model: str, prompt: str) -> str:
    return hashlib.sha256(f"{model}\n{prompt}".encode()).hexdigest()


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


class ModelGateway:
    def __init__(self, db: Session, policy: dict[str, Any]):
        self.db = db
        cfg = policy.get("gateway", {})
        self.prices = cfg.get("prices_per_1k_tokens_usd", {})
        self.replay = cfg.get("replay_enabled", True)

    def price(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        p = self.prices.get(model, {"input": 0.0, "output": 0.0})
        return round(prompt_tokens / 1000 * p["input"] + completion_tokens / 1000 * p["output"], 6)

    def meter(self, *, caller: str, agent: str, purpose: str, model: str, prompt_tokens: int,
              completion_tokens: int, run_id: str | None = None, execution_id: str | None = None,
              request_hash: str = "", cache_hit: bool = False) -> ModelCall:
        """Record a call whose output was produced by the agent's own (simulated) model client."""
        call = ModelCall(caller=caller, agent=agent, purpose=purpose, model=model, prompt_tokens=prompt_tokens,
                         completion_tokens=completion_tokens,
                         cost_usd=0.0 if cache_hit else self.price(model, prompt_tokens, completion_tokens),
                         cache_hit=cache_hit, request_hash=request_hash or "-", run_id=run_id,
                         execution_id=execution_id, simulated=True)
        self.db.add(call)
        self.db.flush()
        return call

    def complete(self, *, caller: str, agent: str, purpose: str, model: str, prompt: str,
                 responder: Callable[[str], dict[str, Any]], run_id: str | None = None) -> tuple[dict, ModelCall]:
        """Call a model through the gateway, using record/replay when enabled."""
        h = _hash(model, prompt)
        cached = self.db.get(GatewayCache, h) if self.replay else None
        if cached is not None:
            response = cached.response
            hit = True
        else:
            response = responder(prompt)
            hit = False
            if self.replay:
                self.db.add(GatewayCache(request_hash=h, model=model, response=response))
        call = self.meter(caller=caller, agent=agent, purpose=purpose, model=model, prompt_tokens=_tokens(prompt),
                          completion_tokens=_tokens(json.dumps(response)), run_id=run_id, request_hash=h,
                          cache_hit=hit)
        return response, call


def summary(db: Session) -> dict[str, Any]:
    rows = db.execute(select(ModelCall.caller, ModelCall.model, func.count(), func.sum(ModelCall.cost_usd),
                             func.sum(ModelCall.prompt_tokens + ModelCall.completion_tokens),
                             func.sum(case((ModelCall.cache_hit.is_(True), 1), else_=0)))
                      .group_by(ModelCall.caller, ModelCall.model)).all()
    out = [{"caller": c, "model": m, "calls": n, "cost_usd": round(cost or 0, 6), "tokens": int(t or 0),
            "cache_hits": int(hits or 0)} for c, m, n, cost, t, hits in rows]
    return {"by_caller_model": out,
            "total_cost_usd": round(sum(r["cost_usd"] for r in out), 6),
            "total_calls": sum(r["calls"] for r in out)}
