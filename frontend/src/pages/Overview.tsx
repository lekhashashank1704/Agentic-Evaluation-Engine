import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Json } from "../api";
import { Badge, fmtTime, Kpi, Loading, pct, riskColor, useApi } from "../components/ui";

const DECISION_COLORS: Record<string, string> = {
  SUCCESS: "#15803d", CONTINUE: "#22c55e", RETRY: "#f59e0b", HUMAN_APPROVAL_REQUIRED: "#d97706", BLOCK: "#b91c1c",
  ROLLBACK: "#dc2626", STAGNATION_DETECTED: "#7f1d1d", BUDGET_EXCEEDED: "#991b1b", ESCALATE: "#9f1239",
  MAX_ITERATIONS_REACHED: "#be123c",
};

export default function Overview() {
  const { data, error } = useApi<Json>("/dashboard");
  if (!data) return <Loading error={error} />;
  const risk = Object.entries(data.risk_distribution).map(([k, v]) => ({ name: k, value: v as number }));
  const decisions = Object.entries(data.decisions).map(([k, v]) => ({ name: k.replace(/_/g, " "), key: k, value: v as number }));
  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Governance overview</h1>
          <p>Quality, risk, security, policy and runtime health are evaluated separately. Active policy v{data.policy_version}.</p>
        </div>
        <Link className="btn btn-primary" to="/executions">New execution or demo</Link>
      </div>
      <div className="grid g4">
        <Kpi label="Lifecycle executions" value={data.counts.executions} sub={`${data.counts.evaluations} evaluations recorded`} />
        <Kpi label="Pending approvals" value={data.counts.pending_approvals} sub={<Link to="/approvals">Open approval queue</Link>} />
        <Kpi label="Open incidents" value={data.counts.open_incidents} sub={<Link to="/incidents">Incident management</Link>} />
        <Kpi label="Policy proposals" value={data.counts.pending_proposals} sub={<Link to="/governance">Feedback engine</Link>} />
      </div>
      <div className="grid g2" style={{ marginTop: 16 }}>
        <div className="card">
          <h2>Risk classification of executions</h2>
          <div style={{ height: 220 }}>
            <ResponsiveContainer>
              <BarChart data={risk}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="name" /><YAxis allowDecimals={false} /><Tooltip />
                <Bar dataKey="value" name="Executions" radius={[4, 4, 0, 0]}>
                  {risk.map((r) => <Cell key={r.name} fill={riskColor(r.name)} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="card">
          <h2>Decision engine outcomes</h2>
          <div style={{ height: 220 }}>
            <ResponsiveContainer>
              <BarChart data={decisions} layout="vertical" margin={{ left: 60 }}>
                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" allowDecimals={false} /><YAxis type="category" dataKey="name" width={150} tick={{ fontSize: 11 }} /><Tooltip />
                <Bar dataKey="value" name="Evaluations" radius={[0, 4, 4, 0]}>
                  {decisions.map((d) => <Cell key={d.key} fill={DECISION_COLORS[d.key] || "#1d4ed8"} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2>Recent executions</h2>
        {data.executions.length === 0 ? (
          <div className="empty">No executions yet. Go to <Link to="/executions">Executions</Link> to create a ticket or run a demo scenario.</div>
        ) : (
          <div className="table-wrap"><table>
            <thead><tr><th>Execution</th><th>Ticket</th><th>Stage</th><th>Status</th><th className="num">Quality</th><th>Risk</th><th>Last decision</th><th>Updated</th></tr></thead>
            <tbody>{data.executions.map((e: Json) => (
              <tr key={e.id}>
                <td><Link to={`/executions/${e.id}`}>{e.id}</Link></td><td>{e.ticket_id} · {e.artifact_id} {e.current_version}</td>
                <td className="small">{e.stage}</td><td><Badge v={e.status} /></td><td className="num">{pct(e.quality_score)}</td>
                <td>{e.risk_level ? <><Badge v={e.risk_level} /> <span className="small">{e.risk_score}</span></> : "—"}</td>
                <td><Badge v={e.last_decision} /></td><td className="small">{fmtTime(e.updated_at)}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </div>
      <div className="card">
        <h2>Latest evaluations</h2>
        <div className="table-wrap"><table>
          <thead><tr><th>Evaluation</th><th>Type</th><th>Artifact</th><th className="num">Quality</th><th className="num">Risk</th><th>Decision</th><th>Policy</th></tr></thead>
          <tbody>{data.recent_evaluations.map((e: Json) => (
            <tr key={e.evaluation_id}><td><Link to={`/evaluations/${e.evaluation_id}`}>{e.evaluation_id}</Link></td>
              <td className="small">{e.evaluation_type}{e.iteration ? ` · iter ${e.iteration}` : ""}</td>
              <td className="small">{e.execution_id} {e.artifact_version}</td><td className="num">{pct(e.quality_score)}</td>
              <td className="num">{e.risk_score ?? "—"}</td><td><Badge v={e.decision} /></td><td className="small">v{e.policy_version}</td></tr>))}</tbody>
        </table></div>
      </div>
    </div>
  );
}
