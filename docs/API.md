# REST API

Interactive OpenAPI docs are served at `http://localhost:8000/docs`. Send the acting identity in the `X-Actor-Id` header (seeded ids: `user-alex` engineer, `user-ravi` reviewer, `user-priya` tech_lead, `user-sam` security_lead, `user-jordan` sre_oncall, `user-morgan` governance_admin, `agent-coding`, `agent-release`, `agent-sre`).

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/dashboard` | KPIs, distributions, recent executions and evaluations |
| GET | `/api/users`, `/api/me` | Identities and roles |
| GET | `/api/tickets/template` | Example ticket (TICKET-104) |
| POST | `/api/executions` | Stage 1: create a ticket and lifecycle execution |
| POST | `/api/executions/{id}/run` | Stages 2–5: coding agent, evaluation loop, risk gate |
| POST | `/api/executions/{id}/rework` | Send human "request changes" feedback to the Fix Agent and re-evaluate |
| POST | `/api/executions/{id}/release` | Stage 7: release policy gate, then canary (`{"health_profile": "healthy" \| "unhealthy", "auto_promote": false}`) |
| GET | `/api/executions`, `/api/executions/{id}` | List and detail (timeline, artifacts, evaluations, approvals, deployments, incidents) |
| GET | `/api/executions/{id}/artifacts/{version}` | Files, diff, hash, PR metadata |
| GET | `/api/evaluations`, `/api/evaluations/{id}` | Full `EvaluationResult` |
| POST | `/api/evaluate/artifact` | Generic evaluation entry point any agent can call with `{files, ticket}` |
| GET | `/api/approvals`, `/api/approvals/{id}` | Approval queue and full review context |
| POST | `/api/approvals/{id}/decision` | `{"decision": "APPROVE" \| "REJECT" \| "REQUEST_CHANGES", "comment": ""}` |
| POST | `/api/approvals/{id}/comments` | Add a comment |
| GET | `/api/deployments`, `/api/deployments/{id}` | Deployments with health evaluations |
| POST | `/api/deployments/{id}/promote` | Next rollout step (only when the Health Gate allows it) |
| GET | `/api/incidents`, `/api/incidents/{id}` | Incident and postmortem |
| PATCH | `/api/incidents/{id}` | Assign, set status, root cause, corrective actions, notes (sre_oncall) |
| POST | `/api/incidents/{id}/postmortem` | Complete the postmortem, which triggers the Feedback Engine |
| GET | `/api/postmortems` | All postmortems |
| GET | `/api/policies`, `/api/policies/{version}` | Active policy and version history |
| PUT | `/api/policies/active` | Direct, versioned edit (governance_admin only) |
| GET | `/api/policy-proposals` | Feedback-engine proposals |
| POST | `/api/policy-proposals/{id}/decision` | Approve (creates a new version) or reject |
| POST | `/api/feedback/analyze` | Run the feedback analysis on demand |
| GET | `/api/audit`, `/api/audit/verify` | Audit entries and hash-chain verification |
| GET | `/api/notifications` | Simulated pages and emails |
| GET/POST | `/api/demo/scenarios`, `/api/demo/{name}` | Reproducible end-to-end scenarios |
| GET/POST | `/api/harness/...`, `/api/gateway/...` | Eval harness and model gateway: see [EVAL_HARNESS.md](EVAL_HARNESS.md#api) |
