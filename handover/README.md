# Handover: Agentic Evaluation Tool

## 1. Repo, branch, changes

| | |
|---|---|
| **Repository** | https://github.com/lekhashashank1704/Agentic-Evaluation-Engine |
| **Branch to run** | `docs/handover-eval-harness`, which contains everything below |
| **Pull requests** | #1 `feat/governance-engine-prototype` → `main` (the engine) · #2 `feat/eval-harness` → #1 (the eval harness) · #3 `docs/handover-eval-harness` → #2 (this handover and the rename). Merge them in order: #1, then #2, then #3 |

### What changed

**PR #1: governance engine** (the base system)
- A ticket-driven lifecycle: a simulated coding agent iterates against independent evaluators (pytest, security rules or Semgrep, Ruff and Radon, PR review, policy) until it succeeds or hits a stop condition (block, budget, timeout, iteration limit, stagnation, regression).
- A Risk Gate (LOW / MEDIUM / HIGH / CRITICAL) with configurable weights. Approvals are role-based and bound to the artifact's SHA-256, and agents can never approve.
- Canary release (5 → 25 → 50 → 100%) with a runtime Health Gate. A breach triggers automatic rollback, an incident and an on-call page. Postmortems feed an engine that proposes policy changes for a human to accept.
- A versioned policy store, a hash-chained audit log, and a React dashboard.

**PR #2: eval harness** (`backend/app/harness/`, `docs/EVAL_HARNESS.md`)
- Versioned golden datasets and graders for three suites, including an LLM-judge grader.
- Durable runs: per-case commits, pause/resume, budget caps, retries and output reuse.
- Re-grading of stored outputs, and run comparison that splits a score change into grader effect and agent effect.
- A shared model gateway: price list, metering for production and eval, and record/replay.
- An agent version registry with an eval-gated, human-approved promotion gate, which the production lifecycle follows.
- An Eval harness UI, API and walkthrough, plus 9 tests (28 in total).

**This branch: handover and rename**
- The app is renamed **Agentic Evaluation Tool**: sidebar heading, browser title, API title and README heading.
- This `handover/` folder: the run guide, the reports and a script to rebuild the reports.
- The screenshots are refreshed with the new name.

## 2. How to run it locally

See **[RUN_LOCALLY.md](RUN_LOCALLY.md)**, a no-steps-missed guide for macOS, Linux and Windows. It takes about 10 minutes and needs no cloud account or API keys.

## 3. Reports

| Version | Audience | Markdown | Word | PDF |
|---|---|---|---|---|
| Executive | Leadership and stakeholders: value and functional overview | [MD](reports/executive/Agentic_Evaluation_Tool_Executive_Report.md) | [DOCX](reports/executive/Agentic_Evaluation_Tool_Executive_Report.docx) | [PDF](reports/executive/Agentic_Evaluation_Tool_Executive_Report.pdf) |
| Technical | Engineering: system specs, architecture decisions, open action items | [MD](reports/technical/Agentic_Evaluation_Tool_Technical_Report.md) | [DOCX](reports/technical/Agentic_Evaluation_Tool_Technical_Report.docx) | [PDF](reports/technical/Agentic_Evaluation_Tool_Technical_Report.pdf) |

The Markdown files are the source. To rebuild the DOCX and PDF files after editing, run `./build_reports.sh`. It needs pandoc, LibreOffice and `pip install python-docx`.
