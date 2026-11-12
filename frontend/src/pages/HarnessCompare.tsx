import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Link, useSearchParams } from "react-router-dom";
import { Json } from "../api";
import { Badge, Kpi, Loading, pct, useApi } from "../components/ui";

const signed = (v?: number | null) => (v == null ? "—" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)} pts`);

export default function HarnessCompare() {
  const [q] = useSearchParams();
  const a = q.get("a"), b = q.get("b");
  const { data, error } = useApi<Json>(a && b ? `/harness/compare?a=${a}&b=${b}` : null, [a, b]);
  if (!data) return <Loading error={error} />;
  const at = data.attribution;
  const chart = data.cases.map((c: Json) => ({ case: c.case_id, A: c.a_score, B: c.b_score }));
  const label = (r: Json) => `${r.id} · ${r.agent_version} · grader ${r.grader_version}`;
  const ef = data.efficiency;

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="small muted"><Link to="/harness">Eval harness</Link> / compare</div>
          <h1>Compare runs</h1>
          <p>A: <Link to={`/harness/runs/${data.a.id}`}>{label(data.a)}</Link><br />B: <Link to={`/harness/runs/${data.b.id}`}>{label(data.b)}</Link></p>
        </div>
        <div className="row">{Object.entries(data.differs).map(([k, v]) => <Badge key={k} v={`${k} ${v ? "differs" : "same"}`} tone={v ? "warn" : "neutral"} />)}</div>
      </div>
      <div className="grid g4" style={{ marginBottom: 16 }}>
        <Kpi label="Score change" value={signed(at.total_delta)} sub={`${pct(data.a.summary.score, 1)} → ${pct(data.b.summary.score, 1)}`} />
        <Kpi label="Grader effect" value={signed(at.grader_effect)} sub="change caused by how outputs are scored" />
        <Kpi label="Agent effect" value={signed(at.agent_effect)} sub="change caused by different agent outputs" />
        <Kpi label="Agent cost" value={`$${ef.a.agent_cost_usd.toFixed(3)} → $${ef.b.agent_cost_usd.toFixed(3)}`}
          sub={ef.a.avg_iterations != null ? `avg iterations ${ef.a.avg_iterations} → ${ef.b.avg_iterations}` : `${ef.a.agent_tokens} → ${ef.b.agent_tokens} tokens`} />
      </div>
      {(at.method || at.note) && <div className="sim-note" style={{ marginBottom: 16 }}>{at.method ? <>Attribution: {at.method}. The agent effect is measured under A's grader; the grader effect is the rest.</> : at.note}</div>}
      <div className="grid g2">
        <div className="card"><h2>Per-case scores</h2>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={chart} margin={{ left: -10, bottom: 50 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="case" angle={-35} textAnchor="end" interval={0} fontSize={11} />
              <YAxis domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} fontSize={11} />
              <Tooltip formatter={(v: number) => pct(v, 1)} />
              <Legend verticalAlign="top" />
              <Bar dataKey="A" fill="#94a3b8" isAnimationActive={false} />
              <Bar dataKey="B" fill="#1d4ed8" isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="card"><h2>What changed</h2>
          <dl className="kv">
            <dt>Newly failing</dt><dd>{data.new_failures.length ? data.new_failures.join(", ") : <span className="muted">none</span>}</dd>
            <dt>Fixed</dt><dd>{data.fixed.length ? data.fixed.join(", ") : <span className="muted">none</span>}</dd>
            <dt>Outputs changed</dt><dd>{data.cases.filter((c: Json) => c.output_changed).length} of {data.cases.length} cases</dd>
          </dl>
        </div>
      </div>
      <div className="card" style={{ marginTop: 16 }}><h2>Cases</h2>
        <div className="table-wrap"><table>
          <thead><tr><th>Case</th><th>A outcome</th><th>B outcome</th><th className="num">A</th><th className="num">B</th><th className="num">Delta</th><th>Output</th></tr></thead>
          <tbody>{data.cases.map((c: Json) => (
            <tr key={c.case_id}><td className="mono">{c.case_id}</td><td><Badge v={c.a_decision} /></td><td><Badge v={c.b_decision} /></td>
              <td className="num">{pct(c.a_score, 1)}</td><td className="num">{pct(c.b_score, 1)}</td>
              <td className="num" style={{ color: c.delta > 0 ? "var(--ok)" : c.delta < 0 ? "var(--bad)" : undefined }}><b>{signed(c.delta)}</b></td>
              <td>{c.output_changed ? <Badge v="CHANGED" tone="info" /> : <span className="muted small">same output</span>}</td></tr>))}</tbody>
        </table></div>
      </div>
    </div>
  );
}
