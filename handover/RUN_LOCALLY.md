# Run it locally: the no-steps-missed guide

This guide takes you from nothing to a running app, with the demo data loaded, in about 10 minutes. It works offline after step 3 (installing dependencies). No cloud account, API key or database server is needed.

Each step says what to type and what you should see. If what you see is different, check **Troubleshooting** at the end.

---

## 0. What you need installed

| Tool | Version | Check with | Get it from |
|---|---|---|---|
| Git | any recent | `git --version` | https://git-scm.com/downloads |
| Python | **3.11, 3.12 or 3.13** | `python3 --version` (Windows: `py --version`) | https://www.python.org/downloads/ (Windows: tick **"Add python.exe to PATH"** in the installer) |
| Node.js | **18 or newer** (20 or 22 recommended) | `node --version` | https://nodejs.org (LTS) |
| npm | comes with Node.js | `npm --version` | |

You also need about 600 MB of free disk space (most of it is `node_modules`) and free ports **8000** and **5173**.

> **Windows users:** use **PowerShell**. Wherever this guide shows two variants, use the one labelled *Windows*.

---

## 1. Get the code

```bash
git clone https://github.com/lekhashashank1704/Agentic-Evaluation-Engine.git
cd Agentic-Evaluation-Engine
git checkout docs/handover-eval-harness
```

You should see `Switched to a new branch 'docs/handover-eval-harness'`.

This branch contains everything: the governance engine (PR #1), the eval harness (PR #2) and this handover package. Once those PRs are merged, `main` will have it all as well.

If GitHub asks you to sign in, use your GitHub account; the repository is private to your account.

---

## 2. Backend: create a Python environment and install it

Run these from the repository root.

**macOS / Linux**
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**Windows (PowerShell)**
```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

You should see your prompt start with `(.venv)` and the install end with `Successfully installed ... fastapi ... uvicorn ...`.

> If PowerShell says *"running scripts is disabled on this system"*, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, answer `Y`, and run the `Activate.ps1` line again.

**Optional:** `pip install semgrep` (macOS/Linux only) makes the Security Evaluator use Semgrep. Without it, a built-in engine applies the same rules. Nothing else changes.

---

## 3. Check the backend works (tests)

Still in `backend/` with `(.venv)` active:

```bash
python -m pytest -q
```

You should see **`28 passed`** after 1 to 3 minutes. The tests run the real lifecycle: the coding agent, the evaluators (pytest, Ruff, security rules), approvals, release, rollback, the feedback loop and the eval harness.

---

## 4. Frontend: install and build

Open the repository root in your terminal (`cd ..` from `backend/`), then:

```bash
cd frontend
npm ci
npm run build
```

You should see `added ... packages` and then `✓ built in ...s`. This produces `frontend/dist`, which the backend serves, so you only need one process to run the app.

> `npm ci` installs the exact versions in `package-lock.json`. If it fails with a lockfile error, use `npm install` instead.

After this step you can disconnect from the internet. Everything below runs offline.

---

## 5. Start the app

Go back to `backend/` with the virtual environment active.

**macOS / Linux**
```bash
cd ../backend
source .venv/bin/activate
uvicorn app.main:app --port 8000
```

**Windows (PowerShell)**
```powershell
cd ..\backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --port 8000
```

You should see `Application startup complete.` and `Uvicorn running on http://127.0.0.1:8000`. Leave this terminal open: closing it stops the app.

Now open **http://localhost:8000** in your browser. You should see the **Agentic Evaluation Tool** dashboard with a navy sidebar.

- API documentation (interactive): http://localhost:8000/docs
- On first start, the app creates `backend/governance.db` and loads seed data: users and roles, policy v1.0, one historical incident, the agent versions and the three eval suites.

---

## 6. Load the demo data

The dashboard starts mostly empty. Load the demo data in either of these ways.

**From the browser (no terminal needed)**
1. Go to **Executions** and click any scenario button, for example **"High risk change, Tech Lead approval, canary breach…"**. Each one takes 5 to 20 seconds.
2. Go to **Eval harness** and click **Run harness walkthrough**. This takes about 30 seconds.

**From a second terminal (loads all nine lifecycle scenarios)**

macOS / Linux:
```bash
./scripts/demo.sh
curl -X POST http://localhost:8000/api/harness/demo
```

Windows (PowerShell):
```powershell
foreach ($s in "breach_and_learn","healthy_release","critical_risk","security_block","stagnation","regression","budget_exceeded","protected_path","approval_invalidation") {
  Invoke-RestMethod -Method Post "http://localhost:8000/api/demo/$s" | Select-Object execution_id,status,decision
}
Invoke-RestMethod -Method Post http://localhost:8000/api/harness/demo
```

You should see nine lines such as `EXE-0001 ROLLED_BACK ...` and `EXE-0002 DEPLOYED ...`, then a JSON object listing run ids `RUN-0001` to `RUN-0010`.

---

## 7. A five-minute tour

The **Acting as** menu at the top of the page switches who you are. Permissions follow the role you pick.

| Who | Role | Can do |
|---|---|---|
| Alex Rivera | engineer | create tickets, run executions, trigger releases |
| Ravi Menon | reviewer | approve MEDIUM-risk changes |
| Priya Shah | tech_lead | approve HIGH risk, approve agent promotions |
| Sam Okafor | security_lead | second approver for CRITICAL risk |
| Jordan Lee | sre_oncall | manage incidents, complete postmortems |
| Morgan Chen | governance_admin | approve policy changes, approve agent promotions |
| Coding / Release / SRE Agent | agents | refused for every human-only action |

1. **Executions → open one → iterations.** The quality score climbs (for example 15% → 70% → 92% → 100%) as the agent fixes what the evaluators found.
2. **risk tab.** Shows why the change is HIGH or CRITICAL: each factor, its weight and the files that caused it.
3. **Approvals.** As *Alex* or an agent, try to approve: you are refused, and the refusal is audited. Switch to *Priya* and approve.
4. **Deployments / Incidents.** An unhealthy canary is rolled back automatically and an incident is opened. As *Jordan*, complete the postmortem.
5. **Policies & feedback.** As *Morgan*, approve the proposed policy change. This creates policy v1.1.
6. **Eval harness.** Compare `RUN-0001` with `RUN-0003`: tick both and click **Open comparison**. Then, as *Priya* or *Morgan*, open **agent versions** and approve `coding-agent@1.1`.
7. **Audit trail.** Every action is listed, including refused attempts, and the hash chain shows as verified.

---

## 8. Stop, reset, restart

- **Stop:** press `Ctrl + C` in the terminal running uvicorn.
- **Reset all data:** stop the app, delete `backend/governance.db`, and start it again (step 5). It reseeds automatically.
- **Restart later:** steps 5 and 6 only. You don't need to install again.

---

## 9. Optional: developer mode (live reload)

Use two terminals.

```bash
# terminal 1 (backend/, venv active)
uvicorn app.main:app --reload --port 8000
# terminal 2 (frontend/)
npm run dev        # open http://localhost:5173 ; /api is proxied to :8000
```

Lint: `cd backend && ruff check .`

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `python3: command not found` (macOS/Linux) | Python is not installed | Install Python 3.11+ from python.org, then open a new terminal |
| `py` or `python` is not recognised (Windows) | Python is not on PATH | Re-run the installer and tick "Add python.exe to PATH" |
| `ERROR: Package requires a different Python` | Python is older than 3.11 | Install 3.11+ and recreate `.venv` (delete the folder first) |
| `uvicorn: command not found` | The virtual environment is not active | `source .venv/bin/activate` (Windows: `.\.venv\Scripts\Activate.ps1`) |
| `[Errno 48/98] address already in use` | Port 8000 is taken | Stop the other process, or use `--port 8001` and open http://localhost:8001 |
| Browser shows `{"detail":"Not Found"}` at `/` | The frontend was not built | Run step 4, then restart the backend |
| Page loads but the lists are empty | No demo data yet | Run step 6 |
| A role is refused when approving an agent promotion | Your database was created before the eval harness existed | Stop the app, delete `backend/governance.db`, start again |
| `npm ci` fails with a lockfile error | npm version mismatch | Run `npm install` instead |
| Tests take a long time | The real pytest and Ruff run on generated code | This is expected: 1 to 3 minutes |

---

## What was verified

This exact sequence (a fresh clone, a new virtual environment, `pip install`, `pytest` with 28 passing, `npm ci`, `npm run build`, starting uvicorn, `scripts/demo.sh` and the harness walkthrough) was run end to end on a clean Linux machine with Python 3.13 and Node 22 before this package was published. The Windows commands follow the same steps but have not been run on a Windows machine.
