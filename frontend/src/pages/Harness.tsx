import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Json } from "../api";
import { Badge, fmtTime, Kpi, Loading, pct, Sim, useApi, useToast } from "../components/ui";

const usd = (v?: number | null, d = 4) => (v == null ? "—" : `$${v.toFixed(d)}`);

export default function Harness() {
  const suites = useApi<Json[]>("/harness/suites");
  const runs = useApi<Json[]>("/harness/runs");
  const versions = useApi<Json[]>("/harness/agent-versions");
  const gw = useApi<Json>("/gateway/summary");
  const [tab, setTab] = useState("suites & runs");
  const [busy, setBusy] = useState(false);
  const { show, bump } = useToast();
  const reloadAll = () => {
    suites.reload();
    runs.reload();
    versions.reload();
    gw.reload();
    bump();
  };

  const walkthrough = async () => {
    setBusy(true);
    try {
      await api.post("/harness/demo");
      show("Walkthrough complete: runs, re-grades, a paused run and a pending promotion were created");
      reloadAll();
    } catch (e) {
      show(String((e as Error).message), true);
    } finally {
      setBusy(false);
    }
  };

  if (!suites.data || !runs.data || !versions.data || !gw.data) return <Loading error={suites.error || runs.error} />;
  const cost = (caller: string) => gw.data.by_caller_model.filter((r: Json) => r.caller === caller).reduce((s: number, r: Json) => s + r.cost_usd, 0);
  const pending = versions.data.filter((v) => v.status === "PROMOTION_PENDING").length;

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Eval harness</h1>
          <p>One harness for every agent: versioned golden datasets and graders, durable runs, re-grading without re-running, and an eval-gated promotion of agent versions.</p>
        </div>
        <div className="row"><Sim label="Simulated models" /><button className="btn btn-primary" disabled={busy} onClick={walkthrough}>{busy ? "Running walkthrough… (~30s)" : "Run harness walkthrough"}</button></div>
      </div>
      <div className="grid g4" style={{ marginBottom: 16 }}>
        <Kpi label="Eval suites" value={suites.data.length} sub={`${suites.data.reduce((s, x) => s + x.cases, 0)} golden cases`} />
        <Kpi label="Eval runs" value={runs.data.length} sub={`${runs.data.filter((r) => r.mode === "regrade").length} re-grades`} />
        <Kpi label="Model spend" value={usd(cost("production") + cost("eval"), 3)} sub={`production ${usd(cost("production"), 3)} · eval ${usd(cost("eval"), 3)}`} />
        <Kpi label="Pending promotions" value={pending} sub="need tech_lead or governance_admin" />
      </div>
      <div className="tabs">{["suites & runs", "agent versions", "grader history", "model gateway"].map((t) => (
        <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}{t === "agent versions" && pending > 0 && <span className="count">{pending}</span>}</button>))}</div>
      {tab === "suites & runs" && <SuitesTab suites={suites.data} runs={runs.data} versions={versions.data} onChange={reloadAll} />}
      {tab === "agent versions" && <VersionsTab versions={versions.data} runs={runs.data} onChange={reloadAll} />}
      {tab === "grader history" && <GraderHistory suites={suites.data} />}
      {tab === "model gateway" && <GatewayTab summary={gw.data} />}
    </div>
  );
}

function SuitesTab({ suites, runs, versions, onChange }: { suites: Json[]; runs: Json[]; versions: Json[]; onChange: () => void }) {
  const [picked, setPicked] = useState<string[]>([]);
  const nav = useNavigate();
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p.slice(-1), id]));
  return (
    <div className="stack">
      {picked.length === 2 && (
        <div className="card row"><b>Compare</b> {picked[0]} → {picked[1]}<button className="btn btn-primary right" onClick={() => nav(`/harness/compare?a=${picked[0]}&b=${picked[1]}`)}>Open comparison</button></div>
      )}
      {suites.map((s) => (
        <div key={s.id} className="card">
          <div className="row">
            <h2 style={{ margin: 0 }}>{s.name}</h2>
            <span className="badge b-neutral">target: {s.target}</span>
            <span className="badge b-primary">dataset {s.latest_dataset} · {s.cases} cases</span>
            <span className="badge b-primary">grader {s.latest_grader}</span>
            <span className="right small muted">pass threshold {pct(s.pass_threshold)}</span>
          </div>
          <p className="small muted">{s.description}</p>
          <Launcher suite={s} versions={versions.filter((v) => v.target === s.target)} onDone={onChange} />
          <RunsTable runs={runs.filter((r) => r.suite_id === s.id)} picked={picked} toggle={toggle} />
        </div>
      ))}
    </div>
  );
}

function Launcher({ suite, versions, onDone }: { suite: Json; versions: Json[]; onDone: () => void }) {
  const detail = useApi<Json>(`/harness/suites/${suite.id}`);
  const active = versions.find((v) => v.status === "ACTIVE");
  const [agent, setAgent] = useState("");
  const [grader, setGrader] = useState("");
  const [maxCases, setMaxCases] = useState("");
  const [budget, setBudget] = useState("");
  const [busy, setBusy] = useState(false);
  const { show } = useToast();
  const nav = useNavigate();
  useEffect(() => setAgent(active?.id || ""), [active?.id]);
  useEffect(() => setGrader(suite.latest_grader || ""), [suite.latest_grader]);
  const go = async () => {
    setBusy(true);
    try {
      const r = await api.post("/harness/runs", {
        suite_id: suite.id, agent_version: agent || null, grader_version: grader || null,
        max_cases: maxCases === "" ? null : Number(maxCases), budget_usd: budget === "" ? null : Number(budget),
      });
      show(`${r.id}: ${r.status}, score ${pct(r.summary.score)}`);
      onDone();
      nav(`/harness/runs/${r.id}`);
    } catch (e) {
      show(String((e as Error).message), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="row" style={{ margin: "10px 0" }}>
      <label className="field">Agent version<select value={agent} onChange={(e) => setAgent(e.target.value)}>
        {versions.map((v) => <option key={v.id} value={v.id}>{v.id} ({v.status.toLowerCase()})</option>)}</select></label>
      <label className="field">Grader<select value={grader} onChange={(e) => setGrader(e.target.value)}>
        {(detail.data?.graders || []).map((g: Json) => <option key={g.version} value={g.version}>{g.version}</option>)}</select></label>
      <label className="field">Max cases<input style={{ width: 90 }} type="number" min={1} placeholder="all" value={maxCases} onChange={(e) => setMaxCases(e.target.value)} /></label>
      <label className="field">Budget (USD)<input style={{ width: 100 }} type="number" step="0.05" placeholder="policy" value={budget} onChange={(e) => setBudget(e.target.value)} /></label>
      <button className="btn btn-primary" style={{ alignSelf: "flex-end" }} disabled={busy} onClick={go}>{busy ? "Running…" : "Start run"}</button>
    </div>
  );
}

export function RunsTable({ runs, picked, toggle }: { runs: Json[]; picked?: string[]; toggle?: (id: string) => void }) {
  if (runs.length === 0) return <div className="empty">No runs yet.</div>;
  return (
    <div className="table-wrap"><table>
      <thead><tr>{toggle && <th />}<th>Run</th><th>Agent version</th><th>Grader</th><th>Mode</th><th>Status</th><th className="num">Cases</th><th className="num">Score</th><th className="num">Pass rate</th><th className="num">Spent</th><th className="num">Reused</th><th>Created</th></tr></thead>
      <tbody>{runs.map((r) => (
        <tr key={r.id}>
          {toggle && <td><input type="checkbox" aria-label={`Select ${r.id} to compare`} checked={picked?.includes(r.id)} onChange={() => toggle(r.id)} /></td>}
          <td><Link to={`/harness/runs/${r.id}`}>{r.id}</Link>{r.parent_run_id && <div className="small muted">of {r.parent_run_id}</div>}</td>
          <td className="mono">{r.agent_version}</td><td>{r.grader_version}</td><td><Badge v={r.mode.toUpperCase()} /></td><td><Badge v={r.status} /></td>
          <td className="num">{r.summary.completed_cases ?? 0}/{r.summary.total_cases}</td>
          <td className="num"><b style={{ color: r.summary.meets_threshold ? "var(--ok)" : undefined }}>{pct(r.summary.score, 1)}</b></td>
          <td className="num">{pct(r.summary.pass_rate)}</td><td className="num">{usd(r.spent_usd)}</td><td className="num">{r.summary.reused_cases ?? 0}</td>
          <td className="small">{fmtTime(r.created_at)}</td>
        </tr>))}</tbody>
    </table></div>
  );
}

function VersionsTab({ versions, runs, onChange }: { versions: Json[]; runs: Json[]; onChange: () => void }) {
  const [runFor, setRunFor] = useState<Record<string, string>>({});
  const [comment, setComment] = useState("");
  const { show } = useToast();
  const act = async (path: string, body: Json, msg: string) => {
    try {
      const r = await api.post(path, body);
      show(`${msg}: ${r.id} is now ${r.status.replace(/_/g, " ")}`);
      onChange();
    } catch (e) {
      show(String((e as Error).message), true);
    }
  };
  return (
    <div className="stack">
      <div className="sim-note">Agent versions are promoted like code. A candidate needs a complete run on the latest dataset and grader that meets the suite threshold with no case regressions against the active version, then a <b>human</b> tech_lead or governance_admin (not the requester) approves it. Production immediately uses the newly active version.</div>
      {versions.map((v) => {
        const eligible = runs.filter((r) => r.agent_version === v.id && r.status === "COMPLETED");
        const p = v.promotion || {};
        return (
          <div key={v.id} className="card">
            <div className="row"><h2 style={{ margin: 0 }} className="mono">{v.id}</h2><Badge v={v.status} /><span className="badge b-neutral">{v.target}</span>
              <span className="right mono small">{JSON.stringify(v.config)}</span></div>
            <p className="small muted">{v.description}</p>
            {p.checks && (
              <div className="table-wrap"><table><thead><tr><th>Promotion gate check</th><th>Result</th><th>Detail</th></tr></thead>
                <tbody>{p.checks.map((c: Json) => <tr key={c.check}><td>{c.check}</td><td><Badge v={c.passed ? "PASSED" : "FAILED"} /></td><td className="small">{c.detail}</td></tr>)}</tbody></table>
                <div className="small muted" style={{ marginTop: 6 }}>Requested by {p.requested_by} with <Link to={`/harness/runs/${p.eval_run_id}`}>{p.eval_run_id}</Link>
                  {p.baseline_run_id && <> vs baseline <Link to={`/harness/compare?a=${p.baseline_run_id}&b=${p.eval_run_id}`}>{p.baseline_run_id}</Link></>}
                  {p.decided_by && <> · {p.decision} by {p.decided_by} ({p.decided_role}) {p.comment && `: "${p.comment}"`}</>}</div></div>
            )}
            {(v.status === "CANDIDATE" || v.status === "REJECTED") && (
              <div className="row" style={{ marginTop: 10 }}>
                <select style={{ width: "auto", minWidth: 320 }} value={runFor[v.id] || ""} onChange={(e) => setRunFor({ ...runFor, [v.id]: e.target.value })} aria-label="Evidence run">
                  <option value="">Choose a completed eval run…</option>
                  {eligible.map((r) => <option key={r.id} value={r.id}>{r.id} · grader {r.grader_version} · {pct(r.summary.score, 1)}</option>)}
                </select>
                <button className="btn" disabled={!runFor[v.id]} onClick={() => act(`/harness/agent-versions/${v.id}/request-promotion`, { run_id: runFor[v.id] }, "Promotion requested")}>Request promotion</button>
              </div>
            )}
            {v.status === "PROMOTION_PENDING" && (
              <div className="row" style={{ marginTop: 10 }}>
                <input style={{ flex: 1, minWidth: 200 }} placeholder="Decision comment" value={comment} onChange={(e) => setComment(e.target.value)} />
                <button className="btn btn-ok" onClick={() => act(`/harness/agent-versions/${v.id}/decision`, { decision: "APPROVE", comment }, "Approved")}>Approve and activate</button>
                <button className="btn btn-bad" onClick={() => act(`/harness/agent-versions/${v.id}/decision`, { decision: "REJECT", comment }, "Rejected")}>Reject</button>
                <span className="small muted">Requires tech_lead or governance_admin</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function GraderHistory({ suites }: { suites: Json[] }) {
  const [sid, setSid] = useState(suites[0]?.id);
  const d = useApi<Json>(sid ? `/harness/suites/${sid}` : null);
  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <select value={sid} onChange={(e) => setSid(e.target.value)} aria-label="Suite">{suites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
        <span className="small muted">Graders are versioned with the experiment: change a grader, re-grade stored outputs, and compare.</span>
      </div>
      {!d.data ? <Loading error={d.error} /> : (
        <div className="grid g2">
          <div className="card"><h2>Grader versions</h2>
            {d.data.graders.map((g: Json) => (
              <div key={g.version} style={{ marginBottom: 14 }}>
                <div className="row"><b>{g.version}</b><span className="mono small muted">sha256 {g.sha256.slice(0, 12)}…</span><span className="right small muted">{g.created_by}</span></div>
                <p className="small" style={{ margin: "4px 0" }}>{g.notes}</p>
                <table><tbody>{g.spec.graders.map((x: Json, i: number) => <tr key={i}><td className="mono">{x.type}</td><td className="num">weight {x.weight}</td>
                  <td className="small muted">{x.rubric ? `rubric: ${x.rubric.join("; ")}` : x.apply_to_all !== undefined ? `apply_to_all: ${x.apply_to_all}` : ""}</td></tr>)}</tbody></table>
              </div>))}
          </div>
          <div className="card"><h2>Golden datasets</h2>
            {d.data.datasets.map((ds: Json) => (
              <div key={ds.version}>
                <div className="row"><b>{ds.version}</b><span className="mono small muted">sha256 {ds.sha256.slice(0, 12)}…</span><span className="right small muted">{ds.notes}</span></div>
                <div className="table-wrap"><table><thead><tr><th>Case</th><th>Input</th><th>Expected</th></tr></thead>
                  <tbody>{ds.fixtures.map((f: Json) => <tr key={f.case_id}><td className="mono">{f.case_id}</td>
                    <td className="small mono">{Object.entries(f.input).filter(([k]) => k !== "violated").map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") || "-" : v}`).join(" ")}</td>
                    <td className="small mono">{Object.entries(f.expected).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(", ") : v}`).join(" ")}</td></tr>)}</tbody></table></div>
              </div>))}
          </div>
        </div>
      )}
    </div>
  );
}

function GatewayTab({ summary }: { summary: Json }) {
  const prices = useApi<Json>("/gateway/prices");
  const calls = useApi<Json[]>("/gateway/calls?limit=15");
  return (
    <div className="stack">
      <div className="sim-note">Every model call from production agents and from eval runs (including the LLM judge) goes through one gateway: it applies the price list, attributes cost to the caller, and replays identical requests from its cache so re-runs are free and reproducible.</div>
      <div className="grid g2">
        <div className="card"><h2>Spend by caller and model</h2>
          <table><thead><tr><th>Caller</th><th>Model</th><th className="num">Calls</th><th className="num">Cache hits</th><th className="num">Tokens</th><th className="num">Cost</th></tr></thead>
            <tbody>{summary.by_caller_model.map((r: Json) => <tr key={r.caller + r.model}><td><Badge v={r.caller.toUpperCase()} tone={r.caller === "production" ? "primary" : "info"} /></td><td className="mono">{r.model}</td>
              <td className="num">{r.calls}</td><td className="num">{r.cache_hits}</td><td className="num">{r.tokens.toLocaleString()}</td><td className="num">{usd(r.cost_usd)}</td></tr>)}
              <tr><td colSpan={5}><b>Total</b></td><td className="num"><b>{usd(summary.total_cost_usd)}</b></td></tr></tbody></table>
        </div>
        <div className="card"><h2>Price list (policy v-controlled)</h2>
          {!prices.data ? <Loading /> : <table><thead><tr><th>Model</th><th className="num">Input / 1k</th><th className="num">Output / 1k</th></tr></thead>
            <tbody>{Object.entries(prices.data.prices_per_1k_tokens_usd).map(([m, p]: [string, Json]) => <tr key={m}><td className="mono">{m}</td><td className="num">${p.input}</td><td className="num">${p.output}</td></tr>)}</tbody></table>}
          {prices.data && <p className="small muted">Record/replay: <b>{prices.data.replay_enabled ? "enabled" : "disabled"}</b></p>}
        </div>
      </div>
      <div className="card"><h2>Recent calls</h2>
        {!calls.data ? <Loading /> : <div className="table-wrap"><table><thead><tr><th>Time</th><th>Caller</th><th>Agent</th><th>Purpose</th><th>Model</th><th>Run / execution</th><th className="num">Tokens</th><th>Cache</th><th className="num">Cost</th></tr></thead>
          <tbody>{calls.data.map((c) => <tr key={c.id}><td className="small">{fmtTime(c.created_at)}</td><td>{c.caller}</td><td className="mono small">{c.agent}</td><td>{c.purpose}</td><td className="mono small">{c.model}</td>
            <td>{c.run_id ? <Link to={`/harness/runs/${c.run_id}`}>{c.run_id}</Link> : c.execution_id ? <Link to={`/executions/${c.execution_id}`}>{c.execution_id}</Link> : "—"}</td>
            <td className="num">{(c.prompt_tokens + c.completion_tokens).toLocaleString()}</td><td>{c.cache_hit ? <Badge v="REPLAYED" tone="ok" /> : "—"}</td><td className="num">{usd(c.cost_usd)}</td></tr>)}</tbody></table></div>}
      </div>
    </div>
  );
}
