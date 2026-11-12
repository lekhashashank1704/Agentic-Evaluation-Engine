import { Fragment, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Json } from "../api";
import { Badge, DecisionCard, fmtTime, Loading, Sim, useApi, useToast } from "../components/ui";

const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

export default function IncidentDetail() {
  const { id } = useParams();
  const { data, error, reload } = useApi<Json>(`/incidents/${id}`);
  const users = useApi<Json[]>("/users");
  const { show, bump } = useToast();
  const [note, setNote] = useState("");
  const [rootCause, setRootCause] = useState("");
  const [actions, setActions] = useState("");
  const [pm, setPm] = useState({
    root_cause: "", business_impact: "", technical_impact: "", detection_method: "Automated Health Gate (canary)",
    recovery_actions: "SRE Agent executed authorized rollback\nVerified baseline metrics recovered",
    preventive_actions: "", lessons_learned: "",
  });

  if (!data) return <Loading error={error} />;
  const inc = data.incident;
  const d = inc.data;
  const humans = (users.data || []).filter((u) => u.kind === "human");

  const call = async (fn: () => Promise<Json>, ok: string) => {
    try {
      await fn();
      show(ok);
      reload();
      bump();
    } catch (e) {
      show(String((e as Error).message), true);
    }
  };
  const update = (body: Json, ok: string) => call(() => api.patch(`/incidents/${id}`, body), ok);

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="small muted"><Link to="/incidents">Incidents</Link> / {inc.id}</div>
          <h1>{inc.title}</h1>
          <p>{inc.service} · {d.deployment_version} · opened {fmtTime(inc.created_at)}
            {inc.execution_id && <> · <Link to={`/executions/${inc.execution_id}`}>{inc.execution_id}</Link></>}
            {inc.deployment_id && <> · <Link to={`/deployments/${inc.deployment_id}`}>{inc.deployment_id}</Link></>}</p>
        </div>
        <div className="row"><Badge v={inc.severity} /><Badge v={inc.status} /></div>
      </div>

      <div className="grid g-2-1">
        <div className="stack">
          <DecisionCard decision={inc.status === "POSTMORTEM_COMPLETE" ? "POSTMORTEM_COMPLETE" : "ROLLBACK"} title="SRE Agent summary (requires human confirmation)" reasons={[d.summary]} />
          <div className="card">
            <h2>Violated metrics</h2>
            {d.violated_metrics.length === 0 ? <div className="muted">None recorded.</div> : (
              <table><thead><tr><th>Metric</th><th className="num">Canary</th><th className="num">Limit</th><th className="num">Baseline</th></tr></thead>
                <tbody>{d.violated_metrics.map((v: Json) => <tr key={v.metric}><td>{v.label}</td><td className="num" style={{ color: "var(--bad)", fontWeight: 700 }}>{v.value}{v.unit}</td>
                  <td className="num">{v.operator} {v.limit}{v.unit}</td><td className="num">{v.baseline}{v.unit}</td></tr>)}</tbody></table>
            )}
          </div>
          <div className="card">
            <h2>Recommended remediation (SRE Agent)</h2>
            <ol className="small" style={{ paddingLeft: 18 }}>{d.recommendations.map((r: string) => <li key={r}>{r}</li>)}</ol>
          </div>
          <div className="card">
            <h2>Rollback evidence</h2>
            {d.rollback_evidence ? <dl className="kv">
              <dt>Deployment</dt><dd>{d.rollback_evidence.deployment_id}</dd><dt>Traffic now</dt><dd>{d.rollback_evidence.traffic_percent}%</dd>
              <dt>Feature flag</dt><dd className="mono small">{d.rollback_evidence.feature_flag.name}: {d.rollback_evidence.feature_flag.enabled ? "enabled" : "disabled"}</dd>
              <dt>Health evaluation</dt><dd><Link to={`/evaluations/${d.rollback_evidence.health_evaluation_id}`}>{d.rollback_evidence.health_evaluation_id}</Link></dd>
            </dl> : <div className="muted">Not applicable.</div>}
          </div>
          <div className="card">
            <h2>Captured logs <Sim label="Synthetic logs" /></h2>
            <pre className="json">{d.logs.map((l: Json) => `${l.level.padEnd(5)} ${l.message}`).join("\n") || "—"}</pre>
          </div>
          <div className="card">
            <h2>Timeline</h2>
            <ul className="timeline">{d.timeline.map((t: Json, i: number) => <li key={i}><span className="small muted">{fmtTime(t.ts)}</span><span className="small">{t.actor}</span><span>{t.event}</span></li>)}</ul>
          </div>
          {data.postmortem && (
            <div className="card">
              <h2>Postmortem {data.postmortem.id}</h2>
              <dl className="kv">
                {(["incident_id", "deployment_version", "root_cause", "business_impact", "technical_impact", "detection_method"] as const).map((k) => (
                  <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{data.postmortem.data[k]}</dd></Fragment>
                ))}
                <dt>Recovery actions</dt><dd><ul className="small">{data.postmortem.data.recovery_actions.map((x: string) => <li key={x}>{x}</li>)}</ul></dd>
                <dt>Preventive actions</dt><dd><ul className="small">{data.postmortem.data.preventive_actions.map((x: string) => <li key={x}>{x}</li>)}</ul></dd>
                <dt>Lessons learned</dt><dd><ul className="small">{data.postmortem.data.lessons_learned.map((x: string) => <li key={x}>{x}</li>)}</ul></dd>
                <dt>Change categories</dt><dd>{data.postmortem.change_categories.join(", ")}</dd>
              </dl>
              <Link to="/governance">See resulting policy proposals</Link>
            </div>
          )}
        </div>

        <div className="stack">
          <div className="card">
            <h2>On-call actions</h2>
            <p className="small muted" style={{ marginTop: -6 }}>Requires the sre_oncall role (switch identity in the top bar).</p>
            <dl className="kv"><dt>Assignee</dt><dd>{d.assignee || "—"}</dd><dt>Remediation owner</dt><dd>{d.remediation_owner || "—"}</dd>
              <dt>Root cause</dt><dd>{d.root_cause || "—"}</dd><dt>Corrective actions</dt><dd>{(d.corrective_actions || []).join("; ") || "—"}</dd></dl>
            <div className="stack" style={{ marginTop: 12 }}>
              <label className="field">Assign remediation to
                <select value={d.remediation_owner || ""} onChange={(e) => update({ remediation_owner: e.target.value }, "Remediation assigned")}>
                  <option value="">Select…</option>{humans.map((u) => <option key={u.id} value={u.id}>{u.name} ({u.role})</option>)}
                </select></label>
              <div className="row">
                {["INVESTIGATING", "RESOLVED"].map((s) => <button key={s} className="btn btn-sm" onClick={() => update({ status: s }, `Status ${s}`)}>Mark {s.toLowerCase()}</button>)}
              </div>
              <label className="field">Root cause<textarea value={rootCause} onChange={(e) => setRootCause(e.target.value)} /></label>
              <label className="field">Corrective actions (one per line)<textarea value={actions} onChange={(e) => setActions(e.target.value)} /></label>
              <label className="field">Note<input value={note} onChange={(e) => setNote(e.target.value)} /></label>
              <button className="btn btn-primary" onClick={() => update({
                root_cause: rootCause || undefined, corrective_actions: actions ? lines(actions) : undefined, note: note || undefined,
              }, "Incident updated")}>Save</button>
            </div>
          </div>
          {!data.postmortem && (
            <div className="card">
              <h2>Complete postmortem</h2>
              <div className="stack">
                {(["root_cause", "business_impact", "technical_impact", "detection_method"] as const).map((k) => (
                  <label key={k} className="field">{k.replace(/_/g, " ")}<textarea value={pm[k]} onChange={(e) => setPm({ ...pm, [k]: e.target.value })} /></label>
                ))}
                {(["recovery_actions", "preventive_actions", "lessons_learned"] as const).map((k) => (
                  <label key={k} className="field">{k.replace(/_/g, " ")} (one per line)<textarea value={pm[k]} onChange={(e) => setPm({ ...pm, [k]: e.target.value })} /></label>
                ))}
                <div className="small muted">Change categories from the risk analysis: {(d.change_categories || []).join(", ") || "none"}</div>
                <button className="btn btn-primary" disabled={!pm.root_cause} onClick={() => call(() => api.post(`/incidents/${id}/postmortem`, {
                  ...pm, recovery_actions: lines(pm.recovery_actions), preventive_actions: lines(pm.preventive_actions), lessons_learned: lines(pm.lessons_learned),
                }), "Postmortem completed; feedback engine ran")}>Complete postmortem</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
