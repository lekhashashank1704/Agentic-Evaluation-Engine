"""Release Agent and SRE Agent (simulated deployment environment)."""

from __future__ import annotations

from typing import Any

from app.simulation import telemetry


class ReleaseAgent:
    """Prepares deployments and moves traffic. It can never promote past a failed Health Gate:
    the orchestrator only calls ``set_traffic`` after the engine returns CONTINUE/SUCCESS."""

    agent_id = "agent-release"

    def prepare(self, service: str, artifact_version: str, artifact_hash: str, build_no: int) -> dict[str, Any]:
        version = f"{service}@1.{build_no}.0+{artifact_hash[:8]}"
        flag = {"name": f"ff_{service.replace('-', '_')}_{artifact_version}", "enabled": False, "rollout_percent": 0,
                "simulated": True}
        return {"version": version, "feature_flag": flag,
                "steps": [{"action": "prepare", "detail": f"Built {version}; feature flag {flag['name']} created "
                                                         "(disabled)", "simulated": True}]}

    def set_traffic(self, deployment: dict[str, Any], percent: int) -> dict[str, Any]:
        flag = dict(deployment["feature_flag"])
        flag.update({"enabled": percent > 0, "rollout_percent": percent})
        return {"feature_flag": flag, "traffic_percent": percent,
                "step": {"action": "canary" if percent < 100 else "promote_full", "traffic_percent": percent,
                         "detail": f"Routed {percent}% of traffic to the new version via feature flag",
                         "simulated": True}}

    def observe(self, profile: str, traffic: int, samples: int, seed: int) -> tuple[dict, dict]:
        canary = telemetry.generate(profile, samples, seed, traffic)
        baseline = telemetry.generate("baseline", samples, seed, 100 - traffic)
        return canary, baseline


class SREAgent:
    """Handles Health Gate breaches: incident, authorized rollback, evidence capture, recommendations.
    Root cause and corrective actions remain owned by the human On-Call Engineer."""

    agent_id = "agent-sre"

    def rollback(self, deployment: dict[str, Any]) -> dict[str, Any]:
        flag = dict(deployment["feature_flag"])
        flag.update({"enabled": False, "rollout_percent": 0})
        return {"feature_flag": flag, "traffic_percent": 0,
                "step": {"action": "rollback", "traffic_percent": 0, "simulated": True,
                         "detail": "Feature flag disabled; 100% of traffic restored to the stable version"}}

    def summarize(self, violated: list[dict[str, Any]], version: str, traffic: int,
                  categories: list[str]) -> dict[str, Any]:
        lines = [f"{v['label']} {v['value']}{v['unit']} (limit {v['operator']} {v['limit']}{v['unit']}, "
                 f"baseline {v['baseline']}{v['unit']})" for v in violated]
        recs = ["Keep the release rolled back until the root cause is confirmed by the on-call engineer."]
        if "db_schema_change" in categories:
            recs.append("Check whether the index migration ran online; schedule it separately from the code "
                        "release and rehearse it against production-sized data.")
        if any(v["metric"].startswith("p9") for v in violated):
            recs.append("Profile the new query path under load; compare query plans before and after the change.")
        if any(v["metric"] == "error_rate_pct" for v in violated):
            recs.append("Inspect 5xx logs for upstream timeouts and connection-pool exhaustion.")
        if "payment_change" in categories:
            recs.append("Verify ledger-service timeouts and retry budgets.")
        recs.append("Add a pre-production load test gate for this endpoint.")
        return {
            "summary": f"Canary {version} at {traffic}% traffic breached the Health Gate: " + "; ".join(lines) + ".",
            "recommendations": recs,
            "generated_by": self.agent_id,
            "requires_human_confirmation": True,
        }
