# Architecture

## Principles

1. **Independent evaluation.** Agents produce artifacts and the engine evaluates them. No agent grades its own work, approves, merges, edits policy or bypasses evaluation. These are enforced server side (`app/core/rbac.py`, `AGENT_FORBIDDEN_ACTIONS`).
2. **Separate concerns, separate verdicts.** Quality, security, risk, policy, runtime health and human approval are each evaluated independently, and they are never blended into one arbitrary number.
3. **Mandatory gates beat scores.** A perfect quality score cannot override a critical security finding or a governance block.
4. **Everything is configurable and versioned.** Weights, bands, thresholds, loop limits, rollout steps and approval roles live in the policy document (`policy_versions` table) and are read through the `ThresholdManager`.
5. **Evidence or it did not happen.** Every check references evidence. Each evidence record states whether the tool `executed` and whether its data is `simulated`.
6. **Humans own critical decisions.** Approvals, postmortems and policy changes require a human with the right role, and approvals are bound to an artifact's SHA-256 hash.

## Component view

```mermaid
flowchart LR
  subgraph UI[React dashboard]
    D[Overview] --- E[Executions] --- A[Approvals] --- R[Deployments] --- I[Incidents] --- G[Policies] --- AU[Audit]
  end
  UI -->|REST + X-Actor-Id| API[FastAPI routes]
  API --> ORCH[Lifecycle orchestrator]
  ORCH --> CA[Coding / Fix Agent<br/>CodingAgentAdapter]
  ORCH --> RA[Release Agent]
  ORCH --> SA[SRE Agent]
  ORCH --> EE[Central Evaluation Engine]
  subgraph EE[Central Evaluation Engine]
    FT[Functional Test] --- SEC[Security] --- CQ[Code Quality] --- PR[PR Review]
    RK[Risk] --- PO[Policy] --- HE[Runtime Health]
    DE[Decision Engine] --- EC[Evidence Collector] --- TM[Threshold Manager] --- HM[History Manager]
  end
  ORCH --> FB[Feedback Engine]
  API --> RBAC[RBAC + agent boundaries]
  ORCH --> AUD[(Hash-chained audit log)]
  EE --> DB[(SQLite / PostgreSQL)]
```

## Lifecycle flow

```mermaid
sequenceDiagram
  participant Eng as Engineer
  participant O as Orchestrator
  participant C as Coding/Fix Agent
  participant E as Evaluation Engine
  participant TL as Tech Lead
  participant Rel as Release Agent
  participant SRE as SRE Agent
  participant OC as On-call
  participant GA as Governance admin
  Eng->>O: Ticket (stage 1)
  O->>C: generate (stage 2)
  loop until SUCCESS or a stop condition (stage 4)
    O->>E: evaluate_development (functional, security, quality, PR review, policy)
    E-->>O: decision RETRY + structured feedback
    O->>C: revise(feedback)
  end
  O->>E: evaluate_risk_gate (stage 5)
  E-->>O: HUMAN_APPROVAL_REQUIRED (roles from policy)
  TL->>O: approve (bound to sha256) (stage 6)
  O->>O: governance engine merges (agents cannot)
  Eng->>O: release
  O->>E: release policy gate (approvals bound to current hash?)
  O->>Rel: canary 5% (stage 7)
  O->>E: evaluate_runtime (stage 8)
  E-->>O: ROLLBACK (breach)
  O->>SRE: open incident, authorized rollback, capture evidence, page on-call (stage 9)
  OC->>O: root cause, corrective actions, postmortem (stage 10)
  O->>O: feedback engine -> PolicyProposal (stage 11)
  GA->>O: approve proposal -> policy v1.1
```

## Execution state machine

`CREATED → IN_PROGRESS → (BLOCKED | ESCALATED | AWAITING_APPROVAL | READY_FOR_RELEASE)`
`AWAITING_APPROVAL → (READY_FOR_RELEASE | CHANGES_REQUESTED → IN_PROGRESS | REJECTED)`
`READY_FOR_RELEASE → DEPLOYING → (DEPLOYED | ROLLED_BACK)`

## Data model

| Table | Purpose |
|---|---|
| `tickets` | Captured engineering intent (all Stage 1 fields) |
| `executions` | Lifecycle record: stage, status, loop state (tokens, cost, evaluations), timeline |
| `artifacts` | Every artifact version: files, unified diff, SHA-256, PR metadata, token usage |
| `evaluations` | Every `EvaluationResult` (quality, quality_and_risk, policy, runtime_health) |
| `approvals` | Hash-bound approvals with approver identity, role, timestamp, comments, invalidation reason |
| `deployments` | Version, feature flag, traffic, steps and their health evaluation ids |
| `incidents`, `postmortems` | Incident record, SRE analysis, telemetry, logs, timeline, postmortem fields, change categories |
| `policy_versions`, `policy_proposals` | Versioned policy documents and feedback-engine proposals with evidence |
| `audit_log` | Append-only entries; `entry_hash = sha256(prev_hash + entry)` |
| `notifications` | Simulated pager and email messages |
| `users` | Humans, agents and the governance engine, with roles and permissions |

## Security and governance model

- **Agent boundary.** Agents can never perform `approve`, `reject`, `request_changes`, `merge_to_main`, `modify_policy`, `approve_policy_change`, `bypass_evaluation` or `complete_postmortem`, regardless of their configured permissions.
- **No impersonation.** Human-only actions call `authorize(..., require_human=True)`. Every denial is written to the audit log.
- **Separation of duties.** The artifact author and the ticket owner cannot approve. Only the governance engine merges.
- **Hash binding.** An approval stores the artifact's SHA-256. Any new artifact version invalidates outstanding approvals for older hashes. The release gate re-checks that every required approval matches the current hash and that the latest evaluation is for that hash.
- **Protected paths.** Changes by an agent to `governance/`, `policies/` and similar paths trigger a mandatory BLOCK.
- **Governance-owned tests.** The acceptance suite is copied into the sandbox *after* the artifact files, so an artifact cannot overwrite it.

## Extension points

| Interface | Location | Replace with |
|---|---|---|
| `CodingAgentAdapter` | `app/agents/base.py` | LLM-backed agent (Claude via the Anthropic SDK, LangGraph graph) |
| `LLMClient`, `LLMJudge` | `app/agents/base.py` | Model client; LLM-as-a-judge notes in the PR Review Evaluator |
| `RepositoryAdapter` | `app/agents/base.py` | GitHub/GitLab: branches, PRs, protected merges |
| Security scanner | `app/evaluation/security.py` | Semgrep is used automatically when installed; add Bandit, pip-audit or Snyk |
| Telemetry source | `app/simulation/telemetry.py` | Prometheus, Datadog or CloudWatch queries returning the same metric keys |
| Release mechanics | `app/agents/ops_agents.py` | Argo Rollouts, Flagger, LaunchDarkly |

## Migrating to PostgreSQL

All models use portable types (`String`, `Integer`, `Float`, `DateTime(timezone=True)`, `JSON`). Install a driver (`pip install "psycopg[binary]"`) and set:

```bash
export DATABASE_URL=postgresql+psycopg://user:pass@host:5432/governance
```

For production, generate Alembic migrations from `app.models` and switch the JSON columns to `JSONB`.
