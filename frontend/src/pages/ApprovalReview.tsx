import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, getActor, Json } from "../api";
import { ChecksTable, IterationChart, RiskPanel, ScoreBreakdown } from "../components/evaluation";
import { Badge, DecisionCard, DiffView, fmtTime, Loading, pct, short, useApi, useToast } from "../components/ui";

export default function ApprovalReview() {
  const { id } = useParams();
  const { data, error, reload } = useApi<Json>(`/approvals/${id}`);
  const [comment, setComment] = useState("");
  const [tab, setTab] = useState("summary");
  const [busy, setBusy] = useState(false);
  const { show, bump } = useToast();

  const send = async (path: string, body: Json, ok: string) => {
    setBusy(true);
    try {
      await api.post(path, body);
      show(ok);
      setComment("");
      reload();
      bump();
    } catch (e) {
      show(String((e as Error).message), true);
    } finally {
      setBusy(false);
    }
  };

  if (!data) return <Loading error={error} />;
  const ap = data.approval;
  const q = data.quality;
  const sec = q?.evaluator_reports?.find((r: Json) => r.evaluator === "security");
  const fn = q?.evaluator_reports?.find((r: Json) => r.evaluator === "functional_test");
  const pending = ap.status === "PENDING";

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="small muted"><Link to="/approvals">Approvals</Link> / {ap.id}</div>
          <h1>Review {data.ticket.ticket_id}: {data.ticket.title}</h1>
          <p>Required role <b>{ap.required_role.replace(/_/g, " ")}</b> · artifact {ap.artifact_version} · sha256 <span className="mono">{short(ap.artifact_hash, 16)}</span> · requested {fmtTime(ap.created_at)}</p>
        </div>
        <Badge v={ap.status} />
      </div>

      {!data.hash_matches_current && (
        <DecisionCard decision="INVALIDATED" title="Artifact changed" reasons={[`The current artifact hash (${short(data.current_artifact_hash)}) differs from the approved hash. Re-evaluation is required.`]} />
      )}

      <div className="grid g4" style={{ margin: "16px 0" }}>
        <div className="card kpi"><div className="label">Quality score</div><div className="value">{pct(q?.quality_score, 1)}</div><div className="sub">threshold {pct(q?.quality_threshold)}</div></div>
        <div className="card kpi"><div className="label">Risk score</div><div className="value">{ap.risk_score}</div><Badge v={ap.risk_level} /></div>
        <div className="card kpi"><div className="label">Security findings</div><div className="value">{sec?.findings?.length ?? 0}</div>
          <div className="sub">{Object.entries(sec?.metrics?.counts || {}).filter(([, v]) => (v as number) > 0).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(", ") || "none"}</div></div>
        <div className="card kpi"><div className="label">Tests</div><div className="value">{fn?.metrics?.tests_passed}/{fn?.metrics?.tests_executed}</div><div className="sub">executed by pytest</div></div>
      </div>

      <div className="grid g-2-1">
        <div>
          <div className="tabs">{["summary", "diff", "risk", "quality", "security", "tests", "history"].map((t) => <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>)}</div>
          {tab === "summary" && (
            <div className="stack">
              <DecisionCard decision={ap.recommended_decision} title="Engine recommendation (advisory; the decision is yours)" reasons={ap.recommendation_reasons} />
              <div className="card"><h2>Pull request</h2><dl className="kv">
                <dt>Title</dt><dd>{data.pr.title}</dd><dt>Branch</dt><dd className="mono">{data.pr.branch} → {data.pr.base}</dd>
                <dt>Files</dt><dd className="mono small">{data.files.join(", ")}</dd><dt>Agent actions</dt><dd className="small">{data.pr.agent_actions.join(" · ")}</dd></dl></div>
              <div className="card"><h2>Ticket</h2><p style={{ marginTop: 0 }}>{data.ticket.description}</p>
                <ul className="small">{data.ticket.acceptance_criteria.map((a: Json) => <li key={a.id}><b>{a.id}</b> {a.text}</li>)}</ul></div>
            </div>
          )}
          {tab === "diff" && <DiffView diff={data.diff} />}
          {tab === "risk" && <RiskPanel rb={data.risk.risk_breakdown} />}
          {tab === "quality" && q && <ScoreBreakdown sb={q.score_breakdown} />}
          {tab === "security" && sec && <ChecksTable checks={sec.checks} />}
          {tab === "tests" && fn && <ChecksTable checks={fn.checks} />}
          {tab === "history" && (
            <div className="card"><h2>Evaluation history</h2><IterationChart evals={data.history.map((h: Json) => ({ ...h, quality_threshold: q?.quality_threshold }))} />
              <table><thead><tr><th>Evaluation</th><th>Version</th><th className="num">Quality</th><th>Decision</th><th className="num">Remaining issues</th></tr></thead>
                <tbody>{data.history.map((h: Json) => <tr key={h.evaluation_id}><td><Link to={`/evaluations/${h.evaluation_id}`}>{h.evaluation_id}</Link></td>
                  <td>{h.artifact_version}</td><td className="num">{pct(h.quality_score, 1)}</td><td><Badge v={h.decision} /></td><td className="num">{h.failed_criteria.length}</td></tr>)}</tbody></table></div>
          )}
        </div>
        <div className="stack">
          <div className="card">
            <h2>Decision</h2>
            <p className="small muted" style={{ marginTop: -6 }}>Acting as <b>{getActor()}</b>. Only a human with the <b>{ap.required_role}</b> role{ap.required_role === "reviewer" ? " (or tech lead)" : ""} can decide. Agents are refused server side.</p>
            <label className="field">Comment<textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Reasoning, conditions or requested changes" /></label>
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn btn-ok" disabled={!pending || busy} onClick={() => send(`/approvals/${id}/decision`, { decision: "APPROVE", comment }, "Approved")}>Approve</button>
              <button className="btn btn-warn" disabled={!pending || busy} onClick={() => send(`/approvals/${id}/decision`, { decision: "REQUEST_CHANGES", comment }, "Changes requested")}>Request changes</button>
              <button className="btn btn-bad" disabled={!pending || busy} onClick={() => send(`/approvals/${id}/decision`, { decision: "REJECT", comment }, "Rejected")}>Reject</button>
              <button className="btn" disabled={!comment || busy} onClick={() => send(`/approvals/${id}/comments`, { comment }, "Comment added")}>Comment</button>
            </div>
          </div>
          <div className="card">
            <h2>Approvals for this execution</h2>
            {data.siblings.map((s: Json) => (
              <div key={s.id} className="row small" style={{ padding: "4px 0" }}>
                <Link to={`/approvals/${s.id}`}>{s.id}</Link> {s.required_role} · {s.artifact_version} <Badge v={s.status} />
                {s.approver_id && <span className="muted">by {s.approver_id}</span>}
              </div>
            ))}
          </div>
          <div className="card">
            <h2>Comments and audit</h2>
            {ap.comments.length === 0 && <div className="muted small">No comments yet.</div>}
            {ap.comments.map((c: Json, i: number) => (
              <div key={i} className="small" style={{ borderBottom: "1px dashed var(--border)", padding: "6px 0" }}>
                <b>{c.author}</b> ({c.role}) · {fmtTime(c.ts)} {c.kind === "decision" && <Badge v="DECISION" tone="primary" />}<div>{c.text}</div>
              </div>
            ))}
            {ap.approver_id && <div className="small" style={{ marginTop: 8 }}>Decided by <b>{ap.approver_id}</b> ({ap.approver_role}) at {fmtTime(ap.decided_at)} for hash <span className="mono">{short(ap.artifact_hash, 16)}</span></div>}
            <Link className="small" to={`/audit?entity=${ap.id}`}>View audit entries</Link>
          </div>
        </div>
      </div>
    </div>
  );
}
