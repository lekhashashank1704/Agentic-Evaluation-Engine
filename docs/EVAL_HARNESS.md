# Eval harness

The governance engine evaluates **one change** as it moves through the lifecycle. The eval harness evaluates **an agent**: it runs a version of an agent over a fixed golden dataset, grades the outputs, and keeps the results so that versions, graders and datasets can be compared over time. Every agent in the system (Coding/Fix agent, Risk evaluator, SRE agent) is evaluated through the same harness, and a new evaluation needs only two things: a fixture list and a grader spec.

The harness runs the **production code**. Its targets call the same `SimulatedCodingAgent`, `EvaluationEngine`, `evaluate_risk` and `SREAgent` that the lifecycle uses. Only orchestration and result storage are harness-owned (the engine is used with `persist=False`, so eval runs never write lifecycle evaluations).

```
              ┌──────────── versioned ─────────────┐
 EvalSuite ── EvalDataset vN (fixtures, sha256)     │
     │        EvalGrader  vN (grader spec, sha256)  │
     │                                              │
 AgentVersion (config, status) ──► EvalRun ──► EvalCaseResult per case
                                     │            (output, grades, score, cost, attempts, reused_from)
                                     ├── regrade ─► new EvalRun from stored outputs (no agent work)
                                     └── compare ─► per-case deltas + grader vs agent attribution
 ModelGateway: every model call (production + eval + LLM judge) → price list, ModelCall log, replay cache
```

## 1. Fixtures and graders

- **Datasets** (`EvalDataset`) are immutable, versioned lists of fixtures `{case_id, input, expected}` with a content hash. Add a version with `POST /api/harness/suites/{id}/datasets`.
- **Graders** (`EvalGrader`) are versioned specs: a weighted list of grader types from the registry in `app/harness/graders.py`. Add a version with `POST /api/harness/suites/{id}/graders`. Unknown grader types are refused.
- The case score is the weighted mean of its graders. A case **passes** at score ≥ 0.99; the run score is the mean case score, compared with the suite's `pass_threshold`.

| Grader | Used by | What it measures |
|---|---|---|
| `decision_is_success` | coding v1 | 1 if the loop ended in SUCCESS (naive: penalises correct BLOCKs) |
| `expected_decision` | coding v2 | 1 if the loop's decision equals the labelled one (BLOCK, STAGNATION_DETECTED, …) |
| `final_quality` | coding | final quality ÷ target; with `apply_to_all: false` only where SUCCESS is expected |
| `iterations_within` | coding | 1 within the iteration limit, decaying linearly beyond it |
| `exact_risk_level` | risk v1 | exact band match against the human label |
| `risk_level_distance` | risk v2 | 1 exact, 0.6 one band over, 0.4 one band under (under-estimates are worse) |
| `keyword_recall` | SRE | share of expected remediation points found in the summary |
| `llm_judge` | SRE v2 | rubric-based judge called **through the model gateway** (simulated, deterministic) |

Seeded suites:

| Suite | Target | Cases | Threshold | Grader story |
|---|---|---|---|---|
| `coding-agent-core` | coding_agent | 10 (incl. secret, protected path, stagnation, regression, flaky infra) | 85% | v1 rewards SUCCESS everywhere and scores the active agent 77.6%; v2 rewards the *correct* outcome and scores the same outputs 100% |
| `risk-calibration` | risk_evaluator | 8 human-labelled changes | 80% | v1 exact match 75%; v2 partial credit 85%; the `db60` policy what-if candidate scores 92.5% |
| `sre-incident-summaries` | sre_agent | 4 incidents | 75% | v1 keyword recall; v2 adds the LLM judge |

## 2. Durable runs

`POST /api/harness/runs` creates a run and executes it. Execution (`app/harness/runner.py`) is durable:

- **Per-case persistence.** Each case result is committed as soon as it completes. A run that is paused, interrupted or out of budget keeps everything it finished, and `POST /runs/{id}/resume` runs only the remaining cases.
- **Pause / case cap.** `max_cases` stops the run as `PAUSED` after that many new cases; `POST /runs/{id}/pause` asks a running run to stop at the next case boundary.
- **Budget cap.** Each run has `budget_usd` (default from `policy.harness.default_budget_usd`). When spend reaches it the run stops as `BUDGET_EXCEEDED`; resume with a higher `budget_usd`.
- **Retries.** Transient infrastructure errors (`TransientError`, simulated by the `std-flaky-infra` fixture) are retried up to `policy.harness.max_retries`; the case records its attempts and "recovered after 1 retry".
- **Output reuse.** Each case output is keyed by `sha256(fixture input + agent version + agent config + suite)`. If any earlier run already produced that output, it is reused (`reused_from`) at zero cost. Re-running the active agent with a new grader costs nothing.

## 3. The grader is part of the experiment

A score is a function of (dataset, agent, grader). Changing the grader changes the score without any change in the agent, so the harness treats grader versions as first-class:

- **Re-grade** (`POST /runs/{id}/regrade {"grader_version"}`) re-scores the stored outputs of a run with another grader version. No agent work is repeated; only the LLM judge is called, and the gateway replays it from cache when the request is identical.
- **Compare** (`GET /api/harness/compare?a=&b=`) shows per-case deltas, newly failing and fixed cases, which of dataset/grader/agent differ, agent cost and average iterations, and an **attribution** of the score change:
  - only the grader differs → the whole change is the grader effect;
  - only the agent differs → the whole change is the agent effect;
  - both differ → B's outputs are re-scored with A's grader; `agent_effect = score(B outputs, A grader) − score(A)` and `grader_effect = score(B) − score(B outputs, A grader)`;
  - different datasets → flagged as not comparable.

Example from the walkthrough: active agent with grader v1 (77.6%) vs candidate 1.1 with grader v2 (100%): +22.4 pts, **all of it grader effect**, agent effect 0.0, while agent cost falls from $0.605 to $0.491 and average iterations from 2.8 to 2.3.

## 4. Shared model gateway

`app/harness/gateway.py` is the single door to (simulated) hosted models for production and eval alike.

- **Price list** in the governed policy (`policy.gateway.prices_per_1k_tokens_usd`), so pricing changes are versioned and approved like any other policy change.
- **Metering.** Every call is logged as a `ModelCall` with caller (`production` or `eval`), agent, purpose, model, tokens, cost, and the eval run or execution it belongs to. The lifecycle's coding agent and the harness are metered by the same code.
- **Record/replay.** `complete()` hashes (model, prompt) and serves identical requests from `GatewayCache` (cost 0, `cache_hit` true). This makes LLM-judge verdicts reproducible and re-grades free.

`GET /api/gateway/summary`, `/api/gateway/calls`, `/api/gateway/prices`.

## 5. Agent versions and the promotion gate

`AgentVersion` records which configuration of each agent is `ACTIVE` in production; the lifecycle loads the active coding agent version for every execution and records it in the execution's loop state. Candidates are promoted like code:

1. A human requests promotion with an evidence run (`POST /api/harness/agent-versions/{id}/request-promotion {"run_id"}`). Agents cannot request promotion.
2. The **eval gate** checks: the run evaluates this version; it is complete; it uses the latest dataset **and** the latest grader; its score meets the suite threshold; and it has no more than `policy.harness.promotion_max_case_regressions` (default 0) newly failing cases against the latest comparable run of the active version. If there is no baseline run, the gate fails and says so. A passing gate sets `PROMOTION_PENDING`; a failing one leaves the version `CANDIDATE` with the failed checks recorded.
3. A human with a role in `policy.harness.promotion_approval_roles` (tech_lead, governance_admin) and the `promote_agent` permission approves or rejects. The requester cannot approve their own promotion (separation of duties). `promote_agent` is an agent-forbidden action.
4. On approval the version becomes `ACTIVE`, the previous one `RETIRED`, and production uses it from the next execution. Every step is in the hash-chained audit log.

## Walkthrough

`POST /api/harness/demo` (or **Run harness walkthrough** on the Eval harness page) takes about 30 seconds and creates: the active coding agent graded with v1 and re-graded with v2; candidate 1.1 run on v2 with its promotion requested and waiting for approval; the experimental 0.9 build paused after 3 cases (resume it from its run page); risk calibration with v1, v2 and the db60 candidate; and SRE summaries with keyword recall, the LLM judge, and a replayed judge re-grade at $0.

Then, as *Priya (tech lead)* or *Morgan (governance admin)*, open **Eval harness → agent versions** and approve `coding-agent@1.1`. New executions now run with three fixes per iteration.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/harness/suites`, `/api/harness/suites/{id}` | Suites with dataset and grader versions |
| POST | `/api/harness/suites/{id}/graders` | New grader version `{graders: [...], notes}` |
| POST | `/api/harness/suites/{id}/datasets` | New dataset version `{fixtures: [...], notes}` |
| GET/POST | `/api/harness/runs` | List runs; create and execute `{suite_id, agent_version?, grader_version?, dataset_version?, budget_usd?, max_cases?}` |
| GET | `/api/harness/runs/{id}` | Run with per-case results |
| POST | `/api/harness/runs/{id}/resume`, `/pause` | Resume (optionally `{budget_usd, max_cases}`) or request a pause |
| POST | `/api/harness/runs/{id}/regrade` | Re-score stored outputs `{grader_version}` |
| GET | `/api/harness/compare?a=&b=` | Per-case deltas and attribution |
| GET | `/api/harness/agent-versions` | Versions, status and promotion record |
| POST | `/api/harness/agent-versions/{id}/request-promotion`, `/decision` | Promotion gate and human decision |
| GET | `/api/gateway/summary`, `/calls`, `/prices` | Model spend by caller/model, call log, price list |
| POST | `/api/harness/demo` | Walkthrough |

## Limitations

- Runs execute synchronously inside the request; durability comes from per-case commits. A production deployment would run cases on a worker queue (the runner's `execute` is already resumable and idempotent per case).
- The LLM judge and the coding agent's model are deterministic simulations; the gateway's interface (`complete`, `meter`) is where a real provider client would plug in.
- Existing databases created before this change keep their seeded users' permissions; delete `backend/governance.db` to reseed with `promote_agent`.
