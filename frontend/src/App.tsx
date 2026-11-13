import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, getActor, Json, setActor } from "./api";
import { useToast } from "./components/ui";
import Overview from "./pages/Overview";
import Executions from "./pages/Executions";
import ExecutionDetail from "./pages/ExecutionDetail";
import EvaluationPage from "./pages/EvaluationPage";
import Approvals from "./pages/Approvals";
import ApprovalReview from "./pages/ApprovalReview";
import Deployments from "./pages/Deployments";
import DeploymentDetail from "./pages/DeploymentDetail";
import Incidents from "./pages/Incidents";
import IncidentDetail from "./pages/IncidentDetail";
import Governance from "./pages/Governance";
import Audit from "./pages/Audit";
import Harness from "./pages/Harness";
import HarnessRun from "./pages/HarnessRun";
import HarnessCompare from "./pages/HarnessCompare";

export default function App() {
  const [users, setUsers] = useState<Json[]>([]);
  const [actor, setActorState] = useState(getActor());
  const [counts, setCounts] = useState<Json>({});
  const { version, bump } = useToast();

  useEffect(() => {
    api.get("/users").then(setUsers).catch(() => setUsers([]));
  }, []);
  useEffect(() => {
    api.get("/dashboard").then((d) => setCounts(d.counts)).catch(() => {});
  }, [version, actor]);

  const me = users.find((u) => u.id === actor);
  const change = (id: string) => {
    setActor(id);
    setActorState(id);
    bump();
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">A</div>
          <div>
            <div style={{ fontWeight: 700 }}>Agentic Evaluation Tool</div>
            <small>SDLC governance &amp; evals</small>
          </div>
        </div>
        <nav className="nav">
          <div className="nav-section">Lifecycle</div>
          <NavLink to="/" end>Overview</NavLink>
          <NavLink to="/executions">Executions</NavLink>
          <NavLink to="/approvals">
            Approvals {counts.pending_approvals > 0 && <span className="count">{counts.pending_approvals}</span>}
          </NavLink>
          <NavLink to="/deployments">Deployments</NavLink>
          <NavLink to="/incidents">
            Incidents {counts.open_incidents > 0 && <span className="count">{counts.open_incidents}</span>}
          </NavLink>
          <div className="nav-section">Governance</div>
          <NavLink to="/governance">
            Policies & feedback {counts.pending_proposals > 0 && <span className="count">{counts.pending_proposals}</span>}
          </NavLink>
          <NavLink to="/audit">Audit trail</NavLink>
          <div className="nav-section">Agent quality</div>
          <NavLink to="/harness">Eval harness</NavLink>
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="small muted">Acting as</div>
          <select value={actor} onChange={(e) => change(e.target.value)} aria-label="Acting as">
            {["human", "agent", "system"].map((kind) => (
              <optgroup key={kind} label={kind === "human" ? "People" : kind === "agent" ? "Agents" : "System"}>
                {users.filter((u) => u.kind === kind).map((u) => (
                  <option key={u.id} value={u.id}>{u.name} · {u.role.replace(/_/g, " ")}</option>
                ))}
              </optgroup>
            ))}
          </select>
          {me && me.kind !== "human" && <span className="badge b-warn">Non-human identity: human-only actions are refused</span>}
          <div className="spacer" />
          <span className="small muted">Identity via X-Actor-Id header (SSO stand-in)</span>
        </header>
        <main className="content" key={actor}>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/executions" element={<Executions />} />
            <Route path="/executions/:id" element={<ExecutionDetail />} />
            <Route path="/evaluations/:id" element={<EvaluationPage />} />
            <Route path="/approvals" element={<Approvals />} />
            <Route path="/approvals/:id" element={<ApprovalReview />} />
            <Route path="/deployments" element={<Deployments />} />
            <Route path="/deployments/:id" element={<DeploymentDetail />} />
            <Route path="/incidents" element={<Incidents />} />
            <Route path="/incidents/:id" element={<IncidentDetail />} />
            <Route path="/governance" element={<Governance />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/harness" element={<Harness />} />
            <Route path="/harness/runs/:id" element={<HarnessRun />} />
            <Route path="/harness/compare" element={<HarnessCompare />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
