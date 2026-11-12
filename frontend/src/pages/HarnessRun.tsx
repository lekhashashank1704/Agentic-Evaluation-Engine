import { Fragment, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Json } from "../api";
import { Badge, fmtTime, Kpi, Loading, Meter, pct, useApi, useToast } from "../components/ui";

const usd = (v?: number | null) => (v == null ? "—" : `$${v.toFixed(4)}`);

function outcome(o: Json) {
  if (!o) return "—";
  if (o.decision) return `${o.decision.replace(/_/g, " ")} · ${o.iterations} it · ${pct(o.final_quality)}`;
  if (o.risk_level) return `${o.risk_level} (${o.risk_score})`;
  if (o.summary) return o.summary.slice(0, 90) + (o.summary.length > 90 ? "…" : "");
  return "—";
}

export default function HarnessRun() {
  const { id } = useParams();
  const run = useApi<Json>(`/harness/runs/${id}`);
  const suite = useApi<Json>(run.data ? `/harness/suites/${run.data.suite_id}` : null, [run.data?.suite_id]);
  const [open, setOpen] = useState<string | null>(null);
  const [grader, setGrader] = useState("");
  const [budget, setBudget] = useState("");
  const [busy, setBusy] = useState(false);
  const { show, bump } = useToast();
  const nav = useNavigate();

  const call = async (path: string, body: Json, after?: (r: Json) => void) => {
    setBusy(true);
    try {
      const r = await api.post(path, body);
      bump();
      if (after) after(r);
      else run.reload();
    } catch (e) {
      show(String((e as Error).message), true);
    } finally {
      setBusy(false);
    }
  };

  if (!run.data) return <Loading error={run.error} />;
  const r = run.data;
  const s = r.summary;
  const resumable = ["PAUSED", "BUDGET_EXCEEDED", "PENDING", "RUNNING"].includes(r.status) && r.mode === "run";
  const graders = (suite.data?.graders || []).filter((g: Json) => g.version !== r.grader_version);

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="small muted"><Link to="/harness">Eval harness</Link> / {r.suite_id}</div>
          <h1>{r.id} <Badge v={r.status} /> <Badge v={r.mode.toUpperCase()} /></h1>
          <p>Agent <span className="mono">{r.agent_version}</span> · dataset {r.dataset_version} · grader {r.grader_version} · policy v{s.policy_version} · by {r.created_by} · {fmtTime(r.created_at)}
            {r.parent_run_id && <> · re-grade of <Link to={`/harness/runs/${r.parent_run_id}`}>{r.parent_run_id}</Link> (<Link to={`/harness/compare?a=${r.parent_run_id}&b=${r.id}`}>compare</Link>)</>}</p>
        </div>
      </div>
      <div className="grid g4" style={{ marginBottom: 16 }}>
        <Kpi label="Score" value={pct(s.score, 1)} sub={`threshold ${pct(s.pass_threshold)} · ${s.meets_threshold ? "meets" : "does not meet"}`} />
        <Kpi label="Progress" value={`${s.completed_cases ?? 0}/${s.total_cases}`} sub={`${s.errored_cases ?? 0} errored · ${s.reused_cases ?? 0} reused`} />
        <Kpi label="Spend" value={usd(r.spent_usd)} sub={`budget ${usd(r.budget_usd)} · ${r.tokens.toLocaleString()} tokens`} />
        <Kpi label="Case pass rate" value={pct(s.pass_rate)} sub="case passes at score ≥ 99%" />
      </div>
      <div className="card row" style={{ marginBottom: 16 }}>
        {resumable && <>
          <label className="field">Raise budget (USD)<input style={{ width: 110 }} type="number" step="0.1" value={budget} placeholder={String(r.budget_usd)} onChange={(e) => setBudget(e.target.value)} /></label>
          <button className="btn btn-primary" style={{ alignSelf: "flex-end" }} disabled={busy} onClick={() => call(`/harness/runs/${r.id}/resume`, { budget_usd: budget === "" ? null : Number(budget) })}>{busy ? "Running…" : "Resume run"}</button>
          <span className="small muted" style={{ alignSelf: "flex-end" }}>Finished cases are kept; only the remaining ones run.</span>
        </>}
        <span className="right" />
        {graders.length > 0 && <>
          <label className="field">Re-grade with<select value={grader} onChange={(e) => setGrader(e.target.value)}>
            <option value="">Choose grader…</option>{graders.map((g: Json) => <option key={g.version} value={g.version}>{g.version}</option>)}</select></label>
          <button className="btn" style={{ alignSelf: "flex-end" }} disabled={!grader || busy} onClick={() => call(`/harness/runs/${r.id}/regrade`, { grader_version: grader }, (n) => nav(`/harness/runs/${n.id}`))}>Re-grade stored outputs</button>
        </>}
      </div>
      <div className="card">
        <h2>Cases</h2>
        <div className="table-wrap"><table>
          <thead><tr><th>Case</th><th>Status</th><th>Outcome</th><th className="num">Score</th><th className="num">Attempts</th><th>Reused from</th><th className="num">Cost</th><th className="num">Tokens</th><th>Note</th></tr></thead>
          <tbody>{r.cases.map((c: Json) => (
            <Fragment key={c.case_id}>
              <tr onClick={() => setOpen(open === c.case_id ? null : c.case_id)} style={{ cursor: "pointer" }}>
                <td className="mono">{open === c.case_id ? "▾" : "▸"} {c.case_id}</td><td><Badge v={c.status} /></td><td className="small">{outcome(c.output)}</td>
                <td className="num"><b style={{ color: c.passed ? "var(--ok)" : "var(--bad)" }}>{pct(c.score, 1)}</b></td><td className="num">{c.attempts}</td>
                <td>{c.reused_from ? <Link to={`/harness/runs/${c.reused_from}`} onClick={(e) => e.stopPropagation()}>{c.reused_from}</Link> : "—"}</td>
                <td className="num">{usd(c.cost_usd)}</td><td className="num">{c.tokens.toLocaleString()}</td><td className="small muted">{c.error || ""}</td>
              </tr>
              {open === c.case_id && (
                <tr><td colSpan={9} style={{ background: "var(--surface-2)" }}>
                  <div className="grid g2">
                    <div><h3>Grades</h3>{(c.grades || []).map((g: Json, i: number) => (
                      <Meter key={i} label={<>{g.grader} <span className="muted">× {g.weight}</span>{g.simulated && <span className="badge b-sim" style={{ marginLeft: 6 }}>simulated judge</span>}</>} value={g.score} max={1} right={pct(g.score)} color={g.score >= 0.99 ? "var(--ok)" : g.score >= 0.5 ? "#d97706" : "var(--bad)"} />))}
                      <ul className="small">{(c.grades || []).map((g: Json, i: number) => <li key={i}><b>{g.grader}</b>: {g.reason}</li>)}</ul></div>
                    <div><h3>Output</h3>
                      {c.output?.trace && <table><thead><tr><th>Iteration</th><th className="num">Quality</th><th className="num">Failed</th><th>Decision</th></tr></thead>
                        <tbody>{c.output.trace.map((t: Json) => <tr key={t.iteration}><td>{t.iteration}</td><td className="num">{pct(t.quality)}</td><td className="num">{t.failed}</td><td><Badge v={t.decision} /></td></tr>)}</tbody></table>}
                      {c.output?.drivers && <ul className="small">{c.output.drivers.map((d: string) => <li key={d}>{d}</li>)}</ul>}
                      {c.output?.recommendations && <><p className="small">{c.output.summary}</p><ul className="small">{c.output.recommendations.map((d: string) => <li key={d}>{d}</li>)}</ul></>}
                      <div className="small muted mono">output key {c.output_key?.slice(0, 16)}…</div>
                    </div>
                  </div>
                </td></tr>
              )}
            </Fragment>))}</tbody>
        </table></div>
      </div>
    </div>
  );
}
