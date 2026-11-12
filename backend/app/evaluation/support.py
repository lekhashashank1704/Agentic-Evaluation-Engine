"""Evidence Collector, Threshold Manager and objective-function scoring framework."""

from __future__ import annotations

import copy
import itertools
from typing import Any

from app.schemas import CheckStatus, Evidence, MetricScore, ScoreBreakdown


class EvidenceCollector:
    """Creates uniquely identified evidence records. Evidence is never synthesised for tools
    that did not run: ``executed`` reflects real execution and ``simulated`` flags synthetic data."""

    def __init__(self, prefix: str = "EV"):
        self.prefix = prefix
        self._seq = itertools.count(1)
        self.items: list[Evidence] = []

    def add(self, *, type: str, source: str, executed: bool, simulated: bool, summary: str,
            data: dict[str, Any] | None = None) -> Evidence:
        ev = Evidence(evidence_id=f"{self.prefix}-{next(self._seq):03d}", type=type, source=source,
                      executed=executed, simulated=simulated, summary=summary, data=data or {})
        self.items.append(ev)
        return ev


class ThresholdManager:
    """Single access point for every configurable threshold, weight and limit."""

    def __init__(self, policy: dict[str, Any]):
        self.policy = policy

    @property
    def version(self) -> str:
        return self.policy.get("version", "unknown")

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.policy
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def loop_limits(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        limits = copy.deepcopy(self.get("loop", {}))
        overrides = overrides or {}
        if overrides.get("budget_usd_override") is not None:
            limits["budget_usd"] = overrides["budget_usd_override"]
        if overrides.get("max_iterations_override") is not None:
            limits["max_iterations"] = overrides["max_iterations_override"]
        return limits

    def risk_level(self, score: float) -> str:
        rounded = round(score)
        for level in self.get("risk.levels", []):
            if level["min"] <= rounded <= level["max"]:
                return level["name"]
        return "CRITICAL" if rounded > 100 else "LOW"


class ObjectiveFunction:
    """Weighted objective function: score = sum(metric_value * weight)."""

    def __init__(self, name: str, spec: dict[str, dict[str, Any]], threshold: float):
        self.name = name
        self.spec = spec
        self.threshold = threshold

    def evaluate(self, values: dict[str, float], evidence: dict[str, list[str]],
                 explanations: dict[str, str]) -> ScoreBreakdown:
        metrics: list[MetricScore] = []
        total = 0.0
        for metric, cfg in self.spec.items():
            value = max(0.0, min(1.0, float(values.get(metric, 0.0))))
            contribution = value * cfg["weight"]
            total += contribution
            metrics.append(MetricScore(
                metric=metric, definition=cfg.get("definition", ""), formula=cfg.get("formula", ""),
                weight=cfg["weight"], data_source=cfg.get("data_source", ""), threshold=cfg.get("threshold"),
                value=round(value, 4), weighted_contribution=round(contribution, 4),
                result=CheckStatus.PASSED if value >= cfg.get("threshold", 1.0) else CheckStatus.FAILED,
                evidence_ids=evidence.get(metric, []), explanation=explanations.get(metric, ""),
            ))
        score = round(total, 4)
        return ScoreBreakdown(objective=self.name, score=score, threshold=self.threshold,
                              passed=score >= self.threshold, metrics=metrics)
