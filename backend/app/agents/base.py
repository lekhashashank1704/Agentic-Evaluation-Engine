"""Adapter interfaces so real LLMs, agent frameworks and repositories can be plugged in later.

The engine only depends on these protocols. The prototype ships deterministic
simulated implementations; a LangGraph / Claude-backed implementation would
implement ``CodingAgentAdapter`` and a GitHub/GitLab implementation would
implement ``RepositoryAdapter`` without any change to the evaluation engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.schemas import FeedbackItem


@dataclass
class ArtifactDraft:
    files: dict[str, str]
    pr: dict[str, Any]
    agent_state: dict[str, Any]
    usage: dict[str, Any]
    manifest: dict[str, Any] = field(default_factory=dict)  # agent's own claims; evaluators re-derive


class CodingAgentAdapter(ABC):
    """Generates and revises code artifacts. Has no access to approvals, merges or policies."""

    agent_id: str = "agent-coding"

    @abstractmethod
    def generate(self, ticket: dict[str, Any]) -> ArtifactDraft: ...

    @abstractmethod
    def revise(self, ticket: dict[str, Any], previous: ArtifactDraft, feedback: list[FeedbackItem],
               iteration: int) -> ArtifactDraft: ...


class LLMClient(ABC):
    """Minimal text-completion interface (e.g. Anthropic Messages API)."""

    @abstractmethod
    def complete(self, system: str, prompt: str, max_tokens: int = 4000) -> tuple[str, dict[str, int]]: ...


class LLMCodingAgent(CodingAgentAdapter):
    """Placeholder showing how a real LLM-backed agent plugs in.

    Implementations should: build a prompt from the ticket and structured feedback,
    ask the model for a JSON patch of files, and report real token usage. Not enabled
    in the offline prototype.
    """

    def __init__(self, client: LLMClient):
        self.client = client

    def generate(self, ticket):  # pragma: no cover - integration point
        raise NotImplementedError("Configure an LLMClient implementation to enable real code generation.")

    def revise(self, ticket, previous, feedback, iteration):  # pragma: no cover
        raise NotImplementedError


class LLMJudge(ABC):
    """Optional LLM-as-a-judge used by the PR Review Evaluator for qualitative review."""

    @abstractmethod
    def review(self, ticket: dict[str, Any], diff: str) -> dict[str, Any]: ...


class RepositoryAdapter(ABC):
    @abstractmethod
    def open_pull_request(self, artifact_id: str, branch: str, title: str, body: str) -> dict[str, Any]: ...

    @abstractmethod
    def merge(self, artifact_id: str, artifact_hash: str, merged_by: str) -> dict[str, Any]: ...


class InMemoryRepository(RepositoryAdapter):
    """Simulated repository. Merges are only callable by the governance engine (see orchestrator)."""

    def __init__(self):
        self.prs: dict[str, dict[str, Any]] = {}

    def open_pull_request(self, artifact_id, branch, title, body):
        pr = {"artifact_id": artifact_id, "branch": branch, "base": "main", "title": title, "body": body,
              "state": "open", "simulated": True}
        self.prs[artifact_id] = pr
        return pr

    def merge(self, artifact_id, artifact_hash, merged_by):
        pr = self.prs.setdefault(artifact_id, {"artifact_id": artifact_id})
        pr.update({"state": "merged", "merged_hash": artifact_hash, "merged_by": merged_by, "simulated": True})
        return pr
