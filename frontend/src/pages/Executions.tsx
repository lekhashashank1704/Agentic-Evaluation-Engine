import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Json } from "../api";
import { Badge, fmtTime, Loading, pct, useApi, useToast } from "../components/ui";

const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

export default function Executions() {
  const { data, error, reload } = useApi<Json[]>("/executions");
  const scenarios = useApi<Json>("/demo/scenarios");
  const [tpl, setTpl] = useState<Json | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const { show, bump } = useToast();
  const nav = useNavigate();

  useEffect(() => {
    api.get("/tickets/template").then(setTpl);
  }, []);

  const runDemo = async (name: string) => {
    setBusy(name);
    try {
      const r = await api.post(`/demo/${name}`);
      show(`${r.execution_id}: ${r.status} (${r.decision})`);
      bump();
      nav(`/executions/${r.execution_id}`);
    } catch (e) {
      show(String((e as Error).message), true);
    } finally {
      setBusy(null);
    }
  };

  const submit = async (run: boolean) => {
    if (!tpl) return;
    setBusy("form");
    try {
      const exe = await api.post("/executions", tpl);
      if (run) await api.post(`/executions/${exe.id}/run`);
      show(`Created ${exe.id}${run ? " and ran the development loop" : ""}`);
      bump();
      nav(`/executions/${exe.id}`);
    } catch (e) {
      show(String((e as Error).message), true);
    } finally {
      setBusy(null);
    }
  };

  const set = (k: string, v: unknown) => setTpl({ ...tpl, [k]: v });
  const setSim = (k: string, v: unknown) => setTpl({ ...tpl, simulation: { ...tpl!.simulation, [k]: v } });

  return (
    <div>
      <div className="page-head">
        <div><h1>Lifecycle executions</h1><p>Stage 1: an engineer provides intent. The engine creates a lifecycle execution record.</p></div>
        <button className="btn btn-primary" onClick={() => setShowForm(!showForm)}>{showForm ? "Hide ticket form" : "New engineering ticket"}</button>
      </div>

      {showForm && tpl && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>Engineering ticket</h2>
          <div className="grid g3">
            <label className="field">Project ID<input value={tpl.project_id} onChange={(e) => set("project_id", e.target.value)} /></label>
            <label className="field">Ticket ID<input value={tpl.ticket_id} onChange={(e) => set("ticket_id", e.target.value)} /></label>
            <label className="field">Title<input value={tpl.title} onChange={(e) => set("title", e.target.value)} /></label>
            <label className="field">Affected application<input value={tpl.affected_application} onChange={(e) => set("affected_application", e.target.value)} /></label>
            <label className="field">Criticality<select value={tpl.criticality} onChange={(e) => set("criticality", e.target.value)}>
              {["low", "medium", "high", "critical"].map((c) => <option key={c}>{c}</option>)}</select></label>
            <label className="field">Engineering owner<input value={tpl.engineering_owner} onChange={(e) => set("engineering_owner", e.target.value)} /></label>
          </div>
          <div className="grid g2" style={{ marginTop: 12 }}>
            <label className="field">Description<textarea value={tpl.description} onChange={(e) => set("description", e.target.value)} /></label>
            <label className="field">Business objective<textarea value={tpl.business_objective} onChange={(e) => set("business_objective", e.target.value)} /></label>
            <label className="field">Functional requirements (one per line)<textarea value={tpl.functional_requirements.join("\n")} onChange={(e) => set("functional_requirements", lines(e.target.value))} /></label>
            <label className="field">Non-functional requirements<textarea value={tpl.non_functional_requirements.join("\n")} onChange={(e) => set("non_functional_requirements", lines(e.target.value))} /></label>
            <label className="field">Security constraints<textarea value={tpl.security_constraints.join("\n")} onChange={(e) => set("security_constraints", lines(e.target.value))} /></label>
            <label className="field">Downstream services<textarea value={tpl.downstream_services.join("\n")} onChange={(e) => set("downstream_services", lines(e.target.value))} /></label>
          </div>
          <div className="card" style={{ marginTop: 12, background: "var(--surface-2)" }}>
            <h3>Acceptance criteria</h3>
            <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{tpl.acceptance_criteria.map((a: Json) => <li key={a.id}><b>{a.id}</b> {a.text}</li>)}</ul>
            <div className="small muted" style={{ marginTop: 6 }}>Verified by the governance-owned acceptance suite. The coding agent cannot modify it.</div>
          </div>
          <div className="sim-note" style={{ marginTop: 12 }}>
            <b>Simulation controls</b> drive the deterministic simulated coding agent so demos are reproducible. Scores are still computed from real pytest, Ruff and security-rule output.
            <div className="grid g4" style={{ marginTop: 8 }}>
              <label className="field">Change scope<select value={tpl.simulation.scenario} onChange={(e) => setSim("scenario", e.target.value)}>
                <option value="low_risk">low_risk</option><option value="standard">standard</option><option value="critical_auth_payment">critical_auth_payment</option></select></label>
              <label className="field">Defect profile<select value={tpl.simulation.defect_profile} onChange={(e) => setSim("defect_profile", e.target.value)}>
                {["realistic", "clean", "critical_secret", "stagnation", "regression", "protected_path"].map((p) => <option key={p}>{p}</option>)}</select></label>
              <label className="field">Fixes per iteration<input type="number" min={0} max={8} placeholder="active agent version" value={tpl.simulation.fixes_per_iteration ?? ""} onChange={(e) => setSim("fixes_per_iteration", e.target.value === "" ? null : Number(e.target.value))} /></label>
              <label className="field">Budget override (USD)<input type="number" step="0.01" value={tpl.simulation.budget_usd_override ?? ""} onChange={(e) => setSim("budget_usd_override", e.target.value === "" ? null : Number(e.target.value))} /></label>
            </div>
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            <button className="btn btn-primary" disabled={busy !== null} onClick={() => submit(true)}>{busy === "form" ? "Running…" : "Create and run coding agent"}</button>
            <button className="btn" disabled={busy !== null} onClick={() => submit(false)}>Create only</button>
          </div>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Demonstration scenarios</h2>
        <p className="small muted" style={{ marginTop: -6 }}>Each scenario drives the real orchestrator end to end. Human decisions are made as seeded users and recorded in the audit trail.</p>
        {!scenarios.data ? <Loading /> : (
          <div className="grid g3">
            {Object.entries(scenarios.data).map(([k, v]: [string, Json]) => (
              <div key={k} className="card" style={{ display: "flex", flexDirection: "column" }}>
                <b>{v.title}</b>
                <div className="small muted" style={{ flex: 1, margin: "6px 0 10px" }}>{v.description}</div>
                <button className="btn btn-sm" disabled={busy !== null} onClick={() => runDemo(k)}>{busy === k ? "Running…" : "Run scenario"}</button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <div className="row"><h2 style={{ margin: 0 }}>All executions</h2><button className="btn btn-sm right" onClick={reload}>Refresh</button></div>
        {!data ? <Loading error={error} /> : data.length === 0 ? <div className="empty" style={{ marginTop: 12 }}>No executions yet.</div> : (
          <div className="table-wrap" style={{ marginTop: 10 }}><table>
            <thead><tr><th>Execution</th><th>Ticket</th><th>Artifact</th><th>Stage</th><th>Status</th><th className="num">Quality</th><th>Risk</th><th>Decision</th><th className="num">Tokens / cost</th><th>Created</th></tr></thead>
            <tbody>{data.map((e) => (
              <tr key={e.id}><td><Link to={`/executions/${e.id}`}>{e.id}</Link></td><td>{e.ticket_id}</td><td>{e.artifact_id} {e.current_version}</td>
                <td className="small">{e.stage}</td><td><Badge v={e.status} /></td><td className="num">{pct(e.quality_score)}</td>
                <td>{e.risk_level ? <><Badge v={e.risk_level} /> {e.risk_score}</> : "—"}</td><td><Badge v={e.last_decision} /></td>
                <td className="num small">{e.loop_state?.tokens ?? 0} / ${(e.loop_state?.cost_usd ?? 0).toFixed(3)}</td><td className="small">{fmtTime(e.created_at)}</td></tr>))}</tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}
