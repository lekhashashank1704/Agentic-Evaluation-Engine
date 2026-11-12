import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Json } from "../api";
import { IterationChart, RiskPanel } from "../components/evaluation";
import { Badge, DecisionCard, DiffView, fmtTime, Loading, pct, short, Sim, useApi, useToast } from "../components/ui";

const STAGES = [
  { key: "intent", name: "Intent", sub: "Ticket" },
  { key: "coding", name: "Coding agent", sub: "PR + diff" },
  { key: "development", name: "Evaluation loop", sub: "Review agents" },
  { key: "risk_gate", name: "Risk gate", sub: "Blast radius" },
  { key: "approval", name: "Human approval", sub: "Hash-bound" },
  { key: "release", name: "Canary release", sub: "Release agent" },
  { key: "operations", name: "Health gate", sub: "Runtime" },
];

function stageState(exe: Json, idx: number): string {
  const order = STAGES.map((s) => s.key);
  let cur = order.indexOf(exe.stage === "development" && exe.current_version ? "development" : exe.stage);
  if (cur < 0) cur = 0;
  const failed = ["BLOCKED", "ESCALATED", "REJECTED", "ROLLED_BACK"].includes(exe.status);
  if (exe.status === "DEPLOYED") return "done";
  if (idx < cur) return "done";
  if (idx === cur) return failed ? "failed" : "current";
  return "";
}

export default function ExecutionDetail() {
  const { id } = useParams();
  const { data, error, reload } = useApi<Json>(`/executions/${id}`);
  const [tab, setTab] = useState("overview");
  const [busy, setBusy] = useState(false);
  const [profile, setProfile] = useState("healthy");
  const { show, bump } = useToast();

  const act = async (fn: () => Promise<Json>, ok: string) => {
    setBusy(true);
    try {
      await fn();
      show(ok);
      reload();
      bump();
    } catch (e) {
      show(String((e as Error).message), true);
    } finally {
      setBusy(false);
    }
  };

  if (!data) return <Loading error={error} />;
  const exe = data.execution;
  const ticket = data.ticket;
  const qEvals = data.evaluations.filter((e: Json) => e.evaluation_type === "quality");
  const last = data.evaluations[data.evaluations.length - 1];
  const tabs = ["overview", "iterations", "risk", "code", "approvals", "release", "ticket"];

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="small muted"><Link to="/executions">Executions</Link> / {exe.id}</div>
          <h1>{ticket.ticket_id}: {ticket.title}</h1>
          <p>{ticket.project_id} · {ticket.affected_application} · criticality {ticket.criticality} · owner {ticket.engineering_owner}</p>
        </div>
        <div className="row">
          <Badge v={exe.status} />
          {exe.status === "CREATED" && <button className="btn btn-primary" disabled={busy} onClick={() => act(() => api.post(`/executions/${id}/run`), "Development loop completed")}>Run coding agent</button>}
          {exe.status === "CHANGES_REQUESTED" && <button className="btn btn-warn" disabled={busy} onClick={() => act(() => api.post(`/executions/${id}/rework`), "Fix agent revised the artifact")}>Send review feedback to Fix Agent</button>}
          {exe.status === "READY_FOR_RELEASE" && (
            <>
              <select value={profile} onChange={(e) => setProfile(e.target.value)} style={{ width: 200 }} aria-label="Synthetic health profile">
                <option value="healthy">Healthy telemetry (synthetic)</option><option value="unhealthy">Unhealthy telemetry (synthetic)</option>
              </select>
              <button className="btn btn-primary" disabled={busy} onClick={() => act(() => api.post(`/executions/${id}/release`, { health_profile: profile }), "Canary deployed to 5%")}>Release canary (5%)</button>
            </>
          )}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="stages">
          {STAGES.map((s, i) => (
            <div key={s.key} className={`stage ${stageState(exe, i)}`}>
              <div className="dot">{stageState(exe, i) === "done" ? "✓" : stageState(exe, i) === "failed" ? "!" : i + 1}</div>
              <div className="name">{s.name}</div><div className="sub">{s.sub}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="tabs">{tabs.map((t) => <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>)}</div>

      {tab === "overview" && (
        <div className="grid g-2-1">
          <div className="stack">
            {last && <DecisionCard decision={last.decision} title={`Latest decision · ${last.evaluation_id} (${last.evaluation_type})`} reasons={last.explanation} />}
            <div className="card">
              <h2>Lifecycle timeline</h2>
              <ul className="timeline">
                {exe.timeline.map((t: Json, i: number) => (
                  <li key={i}><span className="small muted">{fmtTime(t.ts)}</span><span><Badge v={t.stage.toUpperCase()} tone="primary" /></span>
                    <span>{t.message}{t.evaluation_id && <> · <Link to={`/evaluations/${t.evaluation_id}`}>{t.evaluation_id}</Link></>}
                      {t.actions && <div className="small muted">{t.actions.join(" · ")}</div>}
                      <div className="small muted">by {t.actor}</div></span></li>
                ))}
              </ul>
            </div>
          </div>
          <div className="stack">
            <div className="card">
              <h2>Execution</h2>
              <dl className="kv">
                <dt>Artifact</dt><dd>{exe.artifact_id} {exe.current_version}</dd>
                <dt>Quality</dt><dd>{pct(exe.quality_score, 1)}</dd>
                <dt>Risk</dt><dd>{exe.risk_level ? <><Badge v={exe.risk_level} /> {exe.risk_score}/100</> : "—"}</dd>
                <dt>Iterations</dt><dd>{qEvals.length}</dd>
                <dt>Tokens used</dt><dd>{exe.loop_state?.tokens ?? 0} <Sim label="Simulated usage" /></dd>
                <dt>Cost</dt><dd>${(exe.loop_state?.cost_usd ?? 0).toFixed(4)}</dd>
                <dt>Change categories</dt><dd>{(exe.loop_state?.change_categories || []).join(", ") || "—"}</dd>
              </dl>
            </div>
            <div className="card">
              <h2>Agent boundaries</h2>
              <ul className="small" style={{ paddingLeft: 18, margin: 0 }}>
                <li>The coding agent cannot approve, merge, edit policy, or bypass evaluation.</li>
                <li>Merges are performed only by the governance engine after all gates pass.</li>
                <li>Approvals are bound to the artifact's SHA-256 hash and are invalidated when it changes.</li>
                <li>The release agent cannot promote past a failed Health Gate.</li>
              </ul>
            </div>
          </div>
        </div>
      )}

      {tab === "iterations" && (
        <div className="stack">
          <div className="card"><h2>Quality score by iteration</h2>{qEvals.length ? <IterationChart evals={qEvals} /> : <div className="empty">No iterations yet.</div>}</div>
          <div className="card">
            <h2>Iteration comparison</h2>
            <div className="table-wrap"><table>
              <thead><tr><th>Iter</th><th>Version</th><th className="num">Previous</th><th className="num">Current</th><th className="num">Threshold</th><th className="num">Improvement</th><th>Resolved</th><th>New failures</th><th>Remaining</th><th>Decision</th></tr></thead>
              <tbody>{qEvals.map((e: Json) => {
                const c = e.iteration_comparison || {};
                return (
                  <tr key={e.evaluation_id}>
                    <td><Link to={`/evaluations/${e.evaluation_id}`}>#{e.iteration}</Link></td><td>{e.artifact_version}</td>
                    <td className="num">{pct(c.previous_score, 1)}</td><td className="num"><b>{pct(e.quality_score, 1)}</b></td>
                    <td className="num">{pct(e.quality_threshold)}</td>
                    <td className="num" style={{ color: c.improvement < 0 ? "var(--bad)" : "var(--ok)" }}>{c.improvement_pct === null || c.improvement_pct === undefined ? "—" : `${c.improvement_pct > 0 ? "+" : ""}${c.improvement_pct} pts`}</td>
                    <td className="small">{(c.resolved_criteria || []).length}</td>
                    <td className="small" style={{ color: (c.new_failures || []).length ? "var(--bad)" : undefined }}>{(c.new_failures || []).join(", ") || "—"}</td>
                    <td className="small">{(c.remaining_issues || []).length}</td>
                    <td><Badge v={e.decision} /></td>
                  </tr>
                );
              })}</tbody>
            </table></div>
            <div className="small muted" style={{ marginTop: 8 }}>Open an iteration to see per-evaluator reports, checks, structured feedback criteria and evidence.</div>
          </div>
        </div>
      )}

      {tab === "risk" && <RiskTab evalId={exe.loop_state?.risk_evaluation} />}
      {tab === "code" && <CodeTab exeId={exe.id} artifacts={data.artifacts} />}

      {tab === "approvals" && (
        <div className="card">
          <h2>Approvals</h2>
          {data.approvals.length === 0 ? <div className="empty">No approvals requested.</div> : (
            <div className="table-wrap"><table>
              <thead><tr><th>Approval</th><th>Role</th><th>Version</th><th>Hash</th><th>Status</th><th>Approver</th><th>Decided</th><th>Note</th></tr></thead>
              <tbody>{data.approvals.map((a: Json) => (
                <tr key={a.id}><td><Link to={`/approvals/${a.id}`}>{a.id}</Link></td><td>{a.required_role}</td><td>{a.artifact_version}</td>
                  <td className="mono small">{short(a.artifact_hash)}</td><td><Badge v={a.status} /></td><td>{a.approver_id || "—"}</td>
                  <td className="small">{fmtTime(a.decided_at)}</td><td className="small">{a.invalidated_reason}</td></tr>))}</tbody>
            </table></div>
          )}
        </div>
      )}

      {tab === "release" && (
        <div className="stack">
          {data.deployments.length === 0 && <div className="empty">Not deployed yet.</div>}
          {data.deployments.map((d: Json) => (
            <div key={d.id} className="card">
              <div className="row"><h2 style={{ margin: 0 }}><Link to={`/deployments/${d.id}`}>{d.id}</Link> · {d.version}</h2><Badge v={d.status} /><Sim label="Simulated environment" />
                <span className="right small">traffic {d.traffic_percent}%</span></div>
              <ul className="timeline" style={{ marginTop: 10 }}>{d.steps.map((s: Json, i: number) => (
                <li key={i}><span className="small muted">{fmtTime(s.ts)}</span><span><Badge v={s.action.toUpperCase()} tone="primary" /></span>
                  <span>{s.detail} {s.decision && <Badge v={s.decision} />} {s.evaluation_id && <Link to={`/evaluations/${s.evaluation_id}`}>{s.evaluation_id}</Link>}</span></li>))}</ul>
            </div>
          ))}
          {data.incidents.map((i: Json) => (
            <DecisionCard key={i.id} decision="ROLLBACK" title={`Incident ${i.id} · ${i.severity} · ${i.status}`} reasons={[i.data.summary]} />
          ))}
          {data.incidents.length > 0 && <Link className="btn" to={`/incidents/${data.incidents[0].id}`}>Open incident</Link>}
        </div>
      )}

      {tab === "ticket" && (
        <div className="grid g2">
          <div className="card"><h2>Intent</h2><dl className="kv">
            <dt>Business objective</dt><dd>{ticket.business_objective}</dd><dt>Description</dt><dd>{ticket.description}</dd>
            <dt>Expected outcome</dt><dd>{ticket.expected_outcome}</dd><dt>Downstream</dt><dd>{ticket.downstream_services.join(", ")}</dd></dl></div>
          <div className="card"><h2>Requirements</h2>
            <h3>Functional</h3><ul className="small">{ticket.functional_requirements.map((r: string) => <li key={r}>{r}</li>)}</ul>
            <h3>Non-functional</h3><ul className="small">{ticket.non_functional_requirements.map((r: string) => <li key={r}>{r}</li>)}</ul>
            <h3>Security constraints</h3><ul className="small">{ticket.security_constraints.map((r: string) => <li key={r}>{r}</li>)}</ul>
            <h3>Acceptance criteria</h3><ul className="small">{ticket.acceptance_criteria.map((a: Json) => <li key={a.id}><b>{a.id}</b> {a.text}</li>)}</ul></div>
        </div>
      )}
    </div>
  );
}

function RiskTab({ evalId }: { evalId?: string }) {
  const { data, error } = useApi<Json>(evalId ? `/evaluations/${evalId}` : null);
  if (!evalId) return <div className="empty">The risk gate runs once the quality loop succeeds.</div>;
  if (!data) return <Loading error={error} />;
  return (
    <div className="stack">
      <DecisionCard decision={data.decision} title={`Risk gate · ${data.evaluation_id}`} reasons={data.explanation.slice(0, 3)} />
      <RiskPanel rb={data.risk_breakdown} />
    </div>
  );
}

function CodeTab({ exeId, artifacts }: { exeId: string; artifacts: Json[] }) {
  const [ver, setVer] = useState(artifacts[artifacts.length - 1]?.version);
  const [art, setArt] = useState<Json | null>(null);
  useEffect(() => {
    if (ver) api.get(`/executions/${exeId}/artifacts/${ver}`).then(setArt);
  }, [exeId, ver]);
  if (!artifacts.length) return <div className="empty">No artifact yet.</div>;
  return (
    <div className="stack">
      <div className="card">
        <div className="row">
          <h2 style={{ margin: 0 }}>Simulated pull request</h2><Sim label="Simulated coding agent" />
          <select className="right" style={{ width: 220 }} value={ver} onChange={(e) => setVer(e.target.value)} aria-label="Artifact version">
            {artifacts.map((a) => <option key={a.version} value={a.version}>{a.version} · {a.sha256.slice(0, 10)}</option>)}
          </select>
        </div>
        {art && (
          <dl className="kv" style={{ marginTop: 12 }}>
            <dt>Title</dt><dd>{art.pr.title}</dd><dt>Branch</dt><dd className="mono">{art.pr.branch} → {art.pr.base}</dd>
            <dt>Author</dt><dd>{art.author_id} (agent)</dd><dt>SHA-256</dt><dd className="mono">{art.sha256}</dd>
            <dt>Impacted files</dt><dd className="mono small">{art.pr.impacted_files.join(", ")}</dd>
            <dt>Agent actions</dt><dd className="small">{art.pr.agent_actions.join(" · ")}</dd>
            <dt>Token usage</dt><dd className="small">{art.usage.total_tokens} tokens ({art.usage.model})</dd>
          </dl>
        )}
      </div>
      {art && <div className="card"><h2>Code diff</h2><DiffView diff={art.diff} /></div>}
    </div>
  );
}
