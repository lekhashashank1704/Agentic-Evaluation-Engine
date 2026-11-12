"""Simulated, deterministic Coding / Fix Agent.

Behaviour is driven by the ticket's ``simulation`` controls so that demos are
reproducible, but the agent only *responds to structured feedback*: it maps each
feedback item to the part of its own code it believes is responsible and fixes a
limited number of items per iteration. Scores are then recomputed by independent
evaluators from real tool output.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

from app.agents.base import ArtifactDraft, CodingAgentAdapter
from app.schemas import FeedbackItem, Severity
from app.simulation import code_templates as T

SEVERITY_RANK = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3, Severity.INFO: 4}

# How the agent interprets feedback criteria (its "understanding" of its own code).
FEEDBACK_RULES: list[tuple[str, str]] = [
    (r"^test:test_ac[12]_", "D1"),
    (r"^test:test_ac3_", "D2"),
    (r"^test:test_ac4_", "D3"),
    (r"^test:test_reg_default_page_size", "D9"),
    (r"^security:SEC-SQLI", "D4"),
    (r"^security:SEC-SECRET", "D5"),
    (r"^security:SEC-DEP", "D11"),
    (r"^lint:", "D6"),
    (r"^pr_review:component:authentication", "D1"),
    (r"^pr_review:component:authorization", "D1"),
    (r"^pr_review:component:pagination", "D2"),
    (r"^pr_review:component:response_validation", "D3"),
    (r"^pr_review:component:documentation", "D7"),
    (r"^pr_review:unnecessary_change", "D8"),
    (r"^policy:protected_path", "D10"),
]

PROFILE_DEFECTS = {
    "clean": set(),
    "realistic": {"D1", "D2", "D3", "D4", "D6", "D7", "D8"},
    "critical_secret": {"D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"},
    "stagnation": {"D1", "D2", "D3", "D4", "D6", "D7", "D8"},
    "regression": {"D1", "D2", "D3", "D4", "D6", "D7", "D8"},
    "protected_path": {"D1", "D2", "D3", "D6", "D10"},
}

SCENARIO_FILES = {
    "low_risk": ["service/transaction_history.py", "docs/api/transaction_history.md", "service/api/routes.py"],
    "standard": [
        "service/transaction_history.py", "docs/api/transaction_history.md", "service/api/routes.py",
        "service/auth/scopes.py", "migrations/0042_transactions_customer_created_idx.sql", "requirements.txt",
        "config/feature_flags.yaml",
    ],
    "critical_auth_payment": [
        "service/transaction_history.py", "docs/api/transaction_history.md", "service/api/routes.py",
        "service/auth/scopes.py", "service/auth/token_validator.py", "service/payments/ledger_client.py",
        "migrations/0042_transactions_customer_created_idx.sql", "requirements.txt",
        "config/feature_flags.yaml", "helm/txn-history-api/values.yaml",
    ],
}


def _requirements(scenario: str, defects: set[str]) -> str:
    lines = T.REQUIREMENTS_BASE
    if scenario == "standard":
        lines += "python-dateutil==2.9.0\n"
    if scenario == "critical_auth_payment":
        lines += "python-dateutil==2.9.0\n"
        lines += "requests==2.31.0\n" if "D11" in defects else "requests==2.32.3\n"
    return lines


def render_files(scenario: str, defects: set[str], review_notes: list[str] | None = None) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in SCENARIO_FILES.get(scenario, SCENARIO_FILES["standard"]):
        if path == "service/transaction_history.py":
            files[path] = T.render_handler_module(defects, review_notes)
        elif path == "docs/api/transaction_history.md":
            if "D7" not in defects:
                files[path] = T.DOCS
        elif path == "service/api/routes.py":
            files[path] = T.ROUTES_NEW
        elif path == "service/auth/scopes.py":
            files[path] = T.SCOPES_NEW
        elif path == "service/auth/token_validator.py":
            files[path] = T.TOKEN_VALIDATOR_NEW
        elif path == "service/payments/ledger_client.py":
            files[path] = T.LEDGER_NEW
        elif path.startswith("migrations/"):
            files[path] = T.MIGRATION
        elif path == "config/feature_flags.yaml":
            files[path] = T.FEATURE_FLAGS
        elif path.startswith("helm/"):
            files[path] = T.HELM_VALUES
        elif path == "requirements.txt":
            files[path] = _requirements(scenario, defects)
    if "D8" in defects:
        files["service/utils/legacy_formatter.py"] = T.LEGACY_FORMATTER_CHANGED
    if "D10" in defects:
        files["governance/policy_overrides.yaml"] = T.POLICY_OVERRIDE
    return files


def unified_diff(files: dict[str, str]) -> str:
    chunks = []
    for path in sorted(files):
        before = T.BASE_REPO.get(path)
        a = before.splitlines(keepends=True) if before is not None else []
        b = files[path].splitlines(keepends=True)
        chunks.extend(
            difflib.unified_diff(a, b, fromfile=f"a/{path}" if before is not None else "/dev/null",
                                 tofile=f"b/{path}")
        )
    return "".join(c if c.endswith("\n") else c + "\n" for c in chunks)


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


class SimulatedCodingAgent(CodingAgentAdapter):
    agent_id = "agent-coding"
    model = "simulated-deterministic-v1"

    def _initial_defects(self, sim: dict[str, Any]) -> set[str]:
        defects = set(PROFILE_DEFECTS.get(sim.get("defect_profile", "realistic"), PROFILE_DEFECTS["realistic"]))
        if sim.get("scenario") == "critical_auth_payment" and sim.get("defect_profile") != "clean":
            defects.add("D11")
        return defects

    def _draft(self, ticket: dict[str, Any], defects: set[str], notes: list[str], prompt: str,
               iteration: int, actions: list[str]) -> ArtifactDraft:
        sim = ticket.get("simulation", {})
        scenario = sim.get("scenario", "standard")
        files = render_files(scenario, defects, notes)
        output_text = json.dumps(files)
        usage = {
            "model": self.model,
            "prompt_tokens": _tokens(prompt),
            "completion_tokens": _tokens(output_text),
            "simulated": True,
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        number = re.sub(r"\D", "", ticket.get("ticket_id", "")) or "1"
        pr = {
            "title": f"[{ticket.get('ticket_id')}] {ticket.get('title')}",
            "branch": f"agent/{ticket.get('ticket_id', 'ticket').lower()}-v{iteration}",
            "base": "main",
            "number": int(number),
            "description": ticket.get("description", ""),
            "impacted_files": sorted(files),
            "agent_actions": actions,
            "simulated": True,
        }
        manifest = {"claimed_by_agent": True, "files": sorted(files), "scenario": scenario}
        return ArtifactDraft(
            files=files, pr=pr, manifest=manifest, usage=usage,
            agent_state={"defects": sorted(defects), "review_notes": notes, "scenario": scenario,
                         "profile": sim.get("defect_profile", "realistic")},
        )

    def generate(self, ticket: dict[str, Any]) -> ArtifactDraft:
        sim = ticket.get("simulation", {})
        defects = self._initial_defects(sim)
        prompt = json.dumps({k: ticket.get(k) for k in ("description", "functional_requirements",
                                                         "acceptance_criteria", "security_constraints")})
        actions = ["Parsed ticket requirements", "Identified impacted files", "Generated implementation",
                   "Produced diff and opened simulated pull request"]
        return self._draft(ticket, defects, [], prompt, 1, actions)

    def map_feedback(self, feedback: list[FeedbackItem]) -> dict[str, Severity]:
        """Map feedback to the agent's own code regions, keeping the most severe signal per region."""
        mapped: dict[str, Severity] = {}
        for item in feedback:
            if item.defect_ref:
                target = item.defect_ref
            else:
                target = next((d for pat, d in FEEDBACK_RULES if re.search(pat, item.criterion)), None)
            if target is None:
                continue
            if target not in mapped or SEVERITY_RANK[item.severity] < SEVERITY_RANK[mapped[target]]:
                mapped[target] = item.severity
        return mapped

    def revise(self, ticket, previous: ArtifactDraft, feedback: list[FeedbackItem], iteration: int) -> ArtifactDraft:
        sim = ticket.get("simulation", {})
        profile = sim.get("defect_profile", "realistic")
        capacity = int(sim.get("fixes_per_iteration", 3))
        defects = set(previous.agent_state.get("defects", []))
        notes = list(previous.agent_state.get("review_notes", []))
        mapped = self.map_feedback(feedback)
        candidates = sorted((d for d in mapped if d in defects), key=lambda d: (SEVERITY_RANK[mapped[d]], d))
        actions: list[str] = []

        if profile == "stagnation":
            capacity = 0
            actions.append("Attempted fixes but could not resolve the reported failures")
        if profile == "regression" and iteration == 3:
            capacity = 0
            defects.add("D9")
            actions.append("Refactored pagination defaults (introduced a regression)")

        for d in candidates[:capacity]:
            defects.discard(d)
            actions.append(f"Fixed: {T.DEFECT_DESCRIPTIONS[d]}")

        human = [f.message for f in feedback if f.source == "human"]
        for comment in human:
            notes.append(comment[:120])
            actions.append("Addressed human review comment")

        prompt = json.dumps([f.model_dump(mode="json") for f in feedback]) + json.dumps(previous.files)
        return self._draft(ticket, defects, notes, prompt, iteration, actions or ["No changes produced"])
