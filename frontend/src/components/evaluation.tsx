import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Json } from "../api";
import { Badge, DecisionCard, Meter, pct, riskColor, Sim } from "./ui";

export function ScoreBreakdown({ sb }: { sb: Json }) {
  if (!sb) return null;
  return (
    <div className="card">
      <div className="row" style={{ marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>Quality objective: {sb.objective.replace(/_/g, " ")}</h2>
        <span className="right"><Badge v={sb.passed ? "PASSED" : "FAILED"} /></span>
      </div>
      <div className="row small muted" style={{ marginBottom: 10 }}>
        Score {pct(sb.score, 1)} · threshold {pct(sb.threshold)} · weighted objective function
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Metric</th><th>Definition and formula</th><th className="num">Weight</th><th className="num">Value</th>
              <th className="num">Contribution</th><th>Data source</th><th>Evidence</th></tr>
          </thead>
          <tbody>
            {sb.metrics.map((m: Json) => (
              <tr key={m.metric}>
                <td><b>{m.metric.replace(/_/g, " ")}</b></td>
                <td className="small">{m.definition}<div className="mono muted">{m.formula}</div>
                  <div className="muted">{m.explanation}</div></td>
                <td className="num">{pct(m.weight)}</td>
                <td className="num">{pct(m.value, 1)}</td>
                <td className="num">{pct(m.weighted_contribution, 1)}</td>
                <td className="small">{m.data_source}</td>
                <td className="small mono">{m.evidence_ids.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function ChecksTable({ checks, filter }: { checks: Json[]; filter?: (c: Json) => boolean }) {
  const [onlyFailed, setOnlyFailed] = useState(false);
  const rows = checks.filter((c) => (filter ? filter(c) : true)).filter((c) => !onlyFailed || c.status !== "PASSED");
  return (
    <div>
      <label className="row small" style={{ marginBottom: 6, width: "auto" }}>
        <input type="checkbox" style={{ width: "auto" }} checked={onlyFailed} onChange={(e) => setOnlyFailed(e.target.checked)} />
        Only failed and warning checks
      </label>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Check</th><th>Category</th><th>Status</th><th>Severity</th><th>Mandatory</th><th>Detail</th><th>Evidence</th></tr></thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.check_id}>
                <td><b>{c.name}</b><div className="small mono muted">{c.criterion_id || c.check_id}</div></td>
                <td className="small">{c.category}</td>
                <td><Badge v={c.status} /></td>
                <td><Badge v={c.severity} /></td>
                <td className="small">{c.mandatory ? "Yes" : "No"}</td>
                <td className="small" style={{ maxWidth: 360 }}>{c.message}</td>
                <td className="small mono">{(c.evidence_ids || []).join(", ")}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={7} className="muted">No checks.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function EvidenceList({ evidence }: { evidence: Json[] }) {
  return (
    <div className="stack">
      {evidence.map((e) => (
        <details key={e.evidence_id} className="card" style={{ padding: "10px 14px" }}>
          <summary>
            <span className="mono">{e.evidence_id}</span> · {e.source}{" "}
            <Badge v={e.executed ? "EXECUTED" : "NOT EXECUTED"} tone={e.executed ? "ok" : "neutral"} />{" "}
            {e.simulated && <Sim label="Synthetic data" />}
            <div className="small muted" style={{ fontWeight: 400 }}>{e.summary}</div>
          </summary>
          <pre className="json">{JSON.stringify(e.data, null, 2)}</pre>
        </details>
      ))}
    </div>
  );
}

export function EvaluatorReports({ reports }: { reports: Json[] }) {
  const [sel, setSel] = useState(reports[0]?.evaluator);
  const r = reports.find((x) => x.evaluator === sel) || reports[0];
  if (!r) return null;
  return (
    <div>
      <div className="tabs">
        {reports.map((x) => (
          <button key={x.evaluator} className={x.evaluator === r.evaluator ? "active" : ""} onClick={() => setSel(x.evaluator)}>
            {x.evaluator.replace(/_/g, " ")} {x.passed ? "✓" : "✗"}
          </button>
        ))}
      </div>
      <div className="grid g-2-1">
        <div>
          <ChecksTable checks={r.checks} />
          {r.findings?.length > 0 && (
            <div className="card" style={{ marginTop: 16 }}>
              <h3>Findings</h3>
              <div className="table-wrap"><table>
                <thead><tr><th>Rule</th><th>Title</th><th>Severity</th><th>Location</th><th>Tool</th><th>Remediation</th></tr></thead>
                <tbody>{r.findings.map((f: Json) => (
                  <tr key={f.finding_id}><td className="mono small">{f.rule_id}</td><td>{f.title}</td><td><Badge v={f.severity} /></td>
                    <td className="mono small">{f.file}{f.line ? `:${f.line}` : ""}</td><td className="small">{f.tool}</td>
                    <td className="small">{f.remediation}</td></tr>))}</tbody>
              </table></div>
            </div>
          )}
        </div>
        <div className="stack">
          <div className="card">
            <h3>{r.evaluator.replace(/_/g, " ")} report</h3>
            <dl className="kv">
              <dt>Status</dt><dd><Badge v={r.status} /></dd>
              <dt>Passed</dt><dd><Badge v={r.passed ? "PASSED" : "FAILED"} /></dd>
              <dt>Score</dt><dd>{r.score === null || r.score === undefined ? "—" : r.evaluator === "risk" ? r.score : pct(r.score, 1)}</dd>
              <dt>Duration</dt><dd>{r.duration_ms} ms</dd>
            </dl>
            <ul className="small" style={{ paddingLeft: 18 }}>{r.explanation.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
          </div>
          <EvidenceList evidence={r.evidence} />
        </div>
      </div>
    </div>
  );
}

export function RiskPanel({ rb }: { rb: Json }) {
  if (!rb) return <div className="empty">Risk has not been evaluated yet.</div>;
  const data = rb.dimensions.map((d: Json) => ({ name: d.dimension.replace(/_/g, " "), score: d.score, weighted: d.weighted_score, weight: d.weight }));
  return (
    <div className="stack">
      <div className="grid g3">
        <div className="card kpi">
          <div className="label">Risk score</div>
          <div className="value" style={{ color: riskColor(rb.risk_level) }}>{rb.risk_score}<span className="small muted">/100</span></div>
          <Badge v={rb.risk_level} />
        </div>
        <div className="card">
          <h3>Required approvals</h3>
          {rb.required_approvals.length ? rb.required_approvals.map((r: string) => <span key={r} className="badge b-primary" style={{ marginRight: 6 }}>{r.replace(/_/g, " ")}</span>)
            : <span className="muted">None: eligible for automated progression</span>}
          <h3 style={{ marginTop: 12 }}>Additional validation</h3>
          {rb.additional_validation.length ? rb.additional_validation.join(", ") : <span className="muted">None</span>}
        </div>
        <div className="card">
          <h3>Mandatory overrides</h3>
          {rb.overrides.length ? <ul className="small" style={{ paddingLeft: 18, margin: 0 }}>{rb.overrides.map((o: string) => <li key={o} style={{ color: "var(--bad)" }}>{o}</li>)}</ul>
            : <span className="muted">None. Aggregate score applies.</span>}
        </div>
      </div>
      <div className="grid g2">
        <div className="card">
          <h2>Dimension scores (0–100) and weighted contribution</h2>
          <div style={{ height: 260 }}>
            <ResponsiveContainer>
              <BarChart data={data} layout="vertical" margin={{ left: 40, right: 16 }}>
                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" domain={[0, 100]} />
                <YAxis type="category" dataKey="name" width={130} tick={{ fontSize: 12 }} />
                <Tooltip />
                <Bar dataKey="score" name="Dimension score" fill="#93c5fd" radius={[0, 4, 4, 0]} isAnimationActive={false} />
                <Bar dataKey="weighted" name="Weighted contribution" radius={[0, 4, 4, 0]} isAnimationActive={false}>
                  {data.map((_: Json, i: number) => <Cell key={i} fill="#1d4ed8" />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="card">
          <h2>Why this classification</h2>
          <ol className="small" style={{ paddingLeft: 18 }}>{rb.primary_drivers.map((d: string) => <li key={d}>{d}</li>)}</ol>
          {rb.dimensions.map((d: Json) => (
            <Meter key={d.dimension} label={`${d.dimension.replace(/_/g, " ")} (${pct(d.weight)})`} value={d.score}
              right={`${d.score} → ${d.weighted_score}`} color={riskColor(d.score > 80 ? "CRITICAL" : d.score > 60 ? "HIGH" : d.score > 30 ? "MEDIUM" : "LOW")} />
          ))}
        </div>
      </div>
      <div className="card">
        <h2>Contributing factors</h2>
        <div className="table-wrap"><table>
          <thead><tr><th>Dimension</th><th>Factor</th><th className="num">Points</th><th>Reason</th><th>Evidence</th></tr></thead>
          <tbody>{rb.dimensions.flatMap((d: Json) => d.factors.map((f: Json) => (
            <tr key={d.dimension + f.factor}><td className="small">{d.dimension.replace(/_/g, " ")}</td><td className="mono small">{f.factor}</td>
              <td className="num">{f.points}</td><td>{f.reason}</td><td className="small mono">{f.evidence.join(", ")}</td></tr>)))}</tbody>
        </table></div>
      </div>
    </div>
  );
}

export function IterationChart({ evals }: { evals: Json[] }) {
  const data = evals.map((e) => ({ name: `#${e.iteration} ${e.artifact_version}`, score: Math.round((e.quality_score || 0) * 1000) / 10,
    threshold: Math.round((e.quality_threshold || 0) * 100) }));
  const th = data[0]?.threshold ?? 95;
  return (
    <div style={{ height: 240 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" tick={{ fontSize: 12 }} />
          <YAxis domain={[0, 100]} unit="%" />
          <Tooltip />
          <ReferenceLine y={th} stroke="#b91c1c" strokeDasharray="5 4" label={{ value: `threshold ${th}%`, position: "insideTopRight", fill: "#b91c1c", fontSize: 11 }} />
          <Line type="monotone" dataKey="score" name="Quality score" stroke="#1d4ed8" strokeWidth={2.5} dot={{ r: 4 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function EvaluationView({ r }: { r: Json }) {
  const [tab, setTab] = useState("summary");
  const tabs = ["summary", "evaluators", "checks", "evidence", "json"];
  if (r.risk_breakdown) tabs.splice(1, 0, "risk");
  return (
    <div>
      <div className="grid g4" style={{ marginBottom: 16 }}>
        <div className="card kpi"><div className="label">Decision</div><div style={{ marginTop: 6 }}><Badge v={r.decision} /></div>
          <div className="sub">{r.evaluation_type} · {r.lifecycle_stage}</div></div>
        <div className="card kpi"><div className="label">Quality score</div><div className="value">{pct(r.quality_score, 1)}</div>
          <div className="sub">threshold {pct(r.quality_threshold)}</div></div>
        <div className="card kpi"><div className="label">Risk score</div><div className="value" style={{ color: riskColor(r.risk_level) }}>{r.risk_score ?? "—"}</div>
          <div className="sub">{r.risk_level || "not part of this evaluation"}</div></div>
        <div className="card kpi"><div className="label">Artifact</div><div style={{ fontWeight: 700, marginTop: 4 }}>{r.artifact_id} {r.artifact_version}</div>
          <div className="sub mono">sha256 {r.artifact_hash.slice(0, 16)}…</div><div className="sub">policy v{r.policy_version}</div></div>
      </div>
      <div className="tabs">{tabs.map((t) => <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>)}</div>
      {tab === "summary" && (
        <div className="stack">
          <DecisionCard decision={r.decision} reasons={r.explanation} />
          {r.iteration_comparison && r.iteration_comparison.previous_score !== null && (
            <div className="card">
              <h3>Compared with previous iteration</h3>
              <dl className="kv">
                <dt>Previous → current</dt><dd>{pct(r.iteration_comparison.previous_score, 1)} → {pct(r.iteration_comparison.current_score, 1)}
                  {" "}({r.iteration_comparison.improvement_pct > 0 ? "+" : ""}{r.iteration_comparison.improvement_pct} pts)</dd>
                <dt>Resolved</dt><dd className="small">{r.iteration_comparison.resolved_criteria.join(", ") || "—"}</dd>
                <dt>New failures</dt><dd className="small">{r.iteration_comparison.new_failures.join(", ") || "—"}</dd>
                <dt>Remaining</dt><dd className="small">{r.iteration_comparison.remaining_issues.join(", ") || "—"}</dd>
              </dl>
            </div>
          )}
          {r.score_breakdown && <ScoreBreakdown sb={r.score_breakdown} />}
          {r.failed_criteria?.length > 0 && <div className="card"><h3>Failed criteria</h3><div className="row">{r.failed_criteria.map((f: string) => <span key={f} className="badge b-bad">{f}</span>)}</div></div>}
        </div>
      )}
      {tab === "risk" && <RiskPanel rb={r.risk_breakdown} />}
      {tab === "evaluators" && <EvaluatorReports reports={r.evaluator_reports} />}
      {tab === "checks" && <ChecksTable checks={r.checks} />}
      {tab === "evidence" && <EvidenceList evidence={r.evidence} />}
      {tab === "json" && <pre className="json">{JSON.stringify(r, null, 2)}</pre>}
    </div>
  );
}
