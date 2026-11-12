import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, Json } from "../api";
import { ChecksTable } from "../components/evaluation";
import { Badge, DecisionCard, fmtTime, Loading, Sim, useApi, useToast } from "../components/ui";

export default function DeploymentDetail() {
  const { id } = useParams();
  const { data, error, reload } = useApi<Json>(`/deployments/${id}`);
  const policy = useApi<Json>("/policies");
  const [sel, setSel] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const { show, bump } = useToast();
  if (!data) return <Loading error={error} />;
  const d = data.deployment;
  const evals: Json[] = data.health_evaluations;
  const ev = evals[sel ?? evals.length - 1];
  const thresholds = policy.data?.active.policy.health.thresholds || {};
  const steps: number[] = policy.data?.active.policy.health.rollout_steps || [5, 25, 50, 100];

  const promote = async () => {
    setBusy(true);
    try {
      await api.post(`/deployments/${id}/promote`);
      show("Promoted to next rollout step");
      reload();
      bump();
    } catch (e) {
      show(String((e as Error).message), true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="small muted"><Link to="/deployments">Deployments</Link> / {d.id}</div>
          <h1>{d.version}</h1>
          <p>Execution <Link to={`/executions/${d.execution_id}`}>{d.execution_id}</Link> · feature flag <span className="mono">{d.feature_flag.name}</span> · <Sim label="Simulated environment and telemetry" /></p>
        </div>
        <div className="row"><Badge v={d.status} />
          {d.promotion_eligible && <button className="btn btn-primary" disabled={busy} onClick={promote}>Promote to next step</button>}</div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Rollout policy</h2>
        <div className="stages">
          {steps.map((s, i) => {
            const step = d.steps.find((x: Json) => x.traffic_percent === s && x.evaluation_id);
            const state = step ? (step.decision === "ROLLBACK" ? "failed" : "done") : d.traffic_percent === 0 && d.status === "ROLLED_BACK" ? "" : "";
            return (
              <div key={s} className={`stage ${state}`}>
                <div className="dot">{state === "done" ? "✓" : state === "failed" ? "!" : i + 1}</div>
                <div className="name">{s}% traffic</div><div className="sub">{step ? step.decision.replace(/_/g, " ") : "not reached"}</div>
              </div>
            );
          })}
        </div>
        <div className="small muted" style={{ marginTop: 8 }}>The Release Agent cannot move to the next step until the Health Gate returns CONTINUE. A breach triggers the configured rollback workflow.</div>
      </div>

      {evals.length > 0 && ev && (
        <>
          <div className="row" style={{ marginBottom: 10 }}>
            {evals.map((e, i) => (
              <button key={e.evaluation_id} className={`btn btn-sm ${ev === e ? "btn-primary" : ""}`} onClick={() => setSel(i)}>{e.health_metrics.traffic_percent}% · {e.decision}</button>
            ))}
          </div>
          <div className="grid g-2-1">
            <div className="stack">
              <DecisionCard decision={ev.decision === "ROLLBACK" ? "HEALTH_BREACH" : ev.decision} title={`Health Gate · ${ev.evaluation_id} · ${ev.health_metrics.traffic_percent}% canary`} reasons={ev.explanation.slice(0, 1).concat(ev.decision === "ROLLBACK" ? ["Rollout stopped; rollback executed; incident opened; on-call paged."] : [])} />
              <div className="card">
                <h2>Canary latency and errors over the observation window</h2>
                <div style={{ height: 260 }}>
                  <ResponsiveContainer>
                    <LineChart data={ev.health_metrics.series}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="t" unit="s" />
                      <YAxis yAxisId="ms" unit="ms" />
                      <YAxis yAxisId="pct" orientation="right" unit="%" />
                      <Tooltip /><Legend />
                      <ReferenceLine yAxisId="ms" y={thresholds.p99_latency_ms?.max} stroke="#b91c1c" strokeDasharray="5 4" />
                      <Line yAxisId="ms" dataKey="p95_latency_ms" name="p95 ms" stroke="#60a5fa" dot={false} />
                      <Line yAxisId="ms" dataKey="p99_latency_ms" name="p99 ms" stroke="#1d4ed8" strokeWidth={2} dot={false} />
                      <Line yAxisId="pct" dataKey="error_rate_pct" name="error %" stroke="#dc2626" dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
              <div className="card"><h2>Health checks</h2><ChecksTable checks={ev.checks} /></div>
            </div>
            <div className="card">
              <h2>Canary vs baseline</h2>
              <table><thead><tr><th>Metric</th><th className="num">Canary</th><th className="num">Baseline</th><th className="num">Limit</th></tr></thead>
                <tbody>{Object.entries(thresholds).map(([k, t]: [string, Json]) => {
                  const v = ev.health_metrics.canary[k];
                  const bad = t.max !== undefined ? v > t.max : v < t.min;
                  return (
                    <tr key={k}><td className="small">{t.label}</td><td className="num" style={{ color: bad ? "var(--bad)" : undefined, fontWeight: bad ? 700 : 400 }}>{v}{t.unit}</td>
                      <td className="num small">{ev.health_metrics.baseline[k]}{t.unit}</td><td className="num small">{t.max !== undefined ? `≤ ${t.max}` : `≥ ${t.min}`}{t.unit}</td></tr>
                  );
                })}</tbody></table>
              <div className="small muted" style={{ marginTop: 8 }}>{ev.health_metrics.canary.requests} synthetic requests; percentiles computed from samples.</div>
            </div>
          </div>
        </>
      )}

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Deployment steps</h2>
        <ul className="timeline">{d.steps.map((s: Json, i: number) => (
          <li key={i}><span className="small muted">{fmtTime(s.ts)}</span><span><Badge v={s.action.toUpperCase()} tone="primary" /></span>
            <span>{s.detail} {s.decision && <Badge v={s.decision} />} {s.incident_id && <Link to={`/incidents/${s.incident_id}`}>{s.incident_id}</Link>}</span></li>))}</ul>
      </div>
    </div>
  );
}
