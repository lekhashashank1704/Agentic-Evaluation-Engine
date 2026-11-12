# Evaluation framework

All values below are the defaults in policy **v1.0** (`backend/app/governance/default_policy.py`). They are read at runtime from the active policy version and can only change through a versioned policy update.

## 1. Standard result

Every evaluation returns an `EvaluationResult` (`app/schemas.py`) with the required fields (`evaluation_id`, `project_id`, `ticket_id`, `artifact_id`, `artifact_version`, `artifact_hash`, `lifecycle_stage`, `evaluation_type`, `quality_score`, `quality_threshold`, `risk_score`, `risk_level`, `evaluation_status`, `decision`, `checks`, `failed_criteria`, `evidence`, `explanation`, `evaluated_at`, `policy_version`). It also has extensions: `iteration`, `score_breakdown`, `risk_breakdown`, `evaluator_reports`, `iteration_comparison` and `health_metrics`.

Evidence records carry `executed` (the tool ran during this evaluation) and `simulated` (the data is synthetic).

## 2. Evaluators

| Evaluator | Tooling | Output |
|---|---|---|
| Functional Test | pytest runs the governance-owned acceptance suite in an isolated sandbox, and JUnit XML is parsed | pass rate; per-criterion status (AC-1…AC-4, REG); regression failures |
| Security | Semgrep if installed, otherwise built-in AST/regex rules: hardcoded secrets, SQL injection, unauthenticated handlers, sensitive logging, weakened token validation; offline advisory DB for dependencies | findings by severity; blocking findings |
| Code Quality | Ruff (E, F, W, N, B, C901), Radon complexity and maintainability index, duplicate-block detector | lint findings; complexity; hygiene score |
| PR Review | AST checks for the required components derived from the ticket; approved-design scope check; documentation check; optional LLM judge (off by default) | requirement coverage; missing components; unnecessary changes |
| Risk | Blast-radius analysis of files, diff, dependencies, schema, infrastructure, criticality, downstream services, findings and incident history | `RiskBreakdown` with factors |
| Policy | Mandatory binary gates | pass/fail per gate, with BLOCK/RETRY semantics |
| Runtime Health | Telemetry against thresholds, with canary vs baseline | violated metrics; severity |

## 3. Development quality objective (weighted)

```
Quality = 0.70 × test_pass_rate + 0.20 × requirement_coverage + 0.10 × code_hygiene
threshold = 0.95
```

| Metric | Definition | Formula | Source |
|---|---|---|---|
| test_pass_rate | Share of governance-owned tests that passed | passed / executed | functional_test_evaluator |
| requirement_coverage | Share of required ticket components detected | implemented / required | pr_review_evaluator |
| code_hygiene | Lint and maintainability | max(0, 1 − 0.1·lint − 0.15·complex_fns − 0.05·dups) | code_quality_evaluator |

**Mandatory development gates** (binary, independent of the score):

| Gate | Rule | On failure |
|---|---|---|
| PG-001 | No CRITICAL security findings | BLOCK |
| PG-002 | HIGH findings ≤ 0 | RETRY |
| PG-003 | All acceptance and regression tests pass (branch protection) | RETRY |
| PG-004 | Agent did not modify protected governance paths | BLOCK |
| PG-005 | Recomputed SHA-256 equals the recorded artifact hash | BLOCK |
| PG-006 | Separation of duties (informational) | — |

## 4. Iteration control

| Guard | Default | Decision |
|---|---|---|
| Max iterations per run | 5 | `MAX_ITERATIONS_REACHED` |
| Stagnation | improvement < 2 pts for 2 consecutive iterations | `STAGNATION_DETECTED` |
| Regression | score drop or newly failing criterion; 2 in a row escalates | `RETRY` (flagged) → `ESCALATE` |
| Budget | $2.00 or 250k tokens (at $0.012 per 1k tokens) | `BUDGET_EXCEEDED` |
| Timeout | 300 s | `ESCALATE` |
| Critical security stop | on | `BLOCK` |

The History Manager reports the previous score, current score, threshold, improvement (points and relative), resolved criteria, new failures and remaining issues for every iteration.

**Decision precedence:** mandatory blocks → SUCCESS (threshold met and gates pass) → budget → timeout → max iterations → stagnation → regression streak → RETRY.

## 5. Risk Gate

```
Risk = Σ dimension_score(0–100) × weight
```

| Dimension | Weight | Factors (points; each dimension is capped at 100) |
|---|---:|---|
| Code change impact | 20% | 6 per file (max 30); lines > 50/150/400 → 10/20/30; API change 25; change outside approved design 10 |
| Dependency impact | 15% | new dependency 20; version change 10; known-vulnerable 40; 8 per downstream service (max 40) |
| Security exposure | 25% | auth module 45; payment module 35; customer financial data 15; open findings (severity points) |
| Business criticality | 20% | low 20, medium 45, high 70, critical 90; 10 per related incident in 180 days (max 30) |
| Infrastructure impact | 20% | DB schema change 45; infrastructure/pipeline 40; runtime config 15 |

| Band | Range | Decision | Required approvals |
|---|---|---|---|
| LOW | 0–30 | CONTINUE (mandatory validation still applies) | — |
| MEDIUM | 31–60 | HUMAN_APPROVAL_REQUIRED + extended regression | reviewer acknowledgement |
| HIGH | 61–80 | HUMAN_APPROVAL_REQUIRED | tech_lead |
| CRITICAL | 81–100 | HUMAN_APPROVAL_REQUIRED (automatic progression blocked) + security review | tech_lead **and** security_lead |

**Overrides:** a critical security finding or a protected-path change by an agent forces `BLOCK`, whatever the aggregate score. The breakdown lists the primary drivers and every factor's points, reason and evidence files.

## 6. Health Gate

| Metric | Threshold |
|---|---|
| Error rate | ≤ 1.0% |
| p95 latency | ≤ 800 ms |
| p99 latency | ≤ 1500 ms |
| CPU | ≤ 80% |
| Memory | ≤ 85% |
| Availability | ≥ 99.9% |
| SLO compliance (success and ≤ 1 s) | ≥ 99.0% |
| Business transaction success | ≥ 98.5% |

Rollout steps are 5 → 25 → 50 → 100%. A breach at any step returns `ROLLBACK`. The SRE Agent then disables the feature flag (traffic 0%), opens an incident (SEV1 if availability, business success or error rate is violated), captures logs and metrics, writes a summary and recommendations, and pages on-call. Percentiles are computed from generated request samples.

## 7. Feedback Engine

After each postmortem, and on demand, the engine counts postmortems by change category within 180 days. When a category reaches **2** incidents, it creates a `PolicyProposal` that raises the mapped risk factor by 15 points. The proposal includes its rationale and evidence (incidents, postmortems, root causes). It is never applied automatically. A `governance_admin` must approve it, which creates a new policy version and records the change history.
