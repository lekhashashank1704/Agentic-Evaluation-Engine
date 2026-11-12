import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Json } from "../api";
import { Badge, fmtTime, Loading, short, useApi } from "../components/ui";

export default function Audit() {
  const [params] = useSearchParams();
  const [entity, setEntity] = useState(params.get("entity") || "");
  const [onlyDenied, setOnlyDenied] = useState(false);
  const { data, error } = useApi<Json[]>(`/audit${entity ? `?entity_id=${encodeURIComponent(entity)}` : ""}`, [entity]);
  const chain = useApi<Json>("/audit/verify");
  const notes = useApi<Json[]>("/notifications");
  const rows = (data || []).filter((a) => !onlyDenied || a.outcome === "DENIED");
  return (
    <div>
      <div className="page-head">
        <div><h1>Audit trail</h1><p>Append-only, hash-chained record of every agent, human and engine action, including denied attempts.</p></div>
        {chain.data && <Badge v={chain.data.valid ? `CHAIN VALID · ${chain.data.entries} ENTRIES` : `CHAIN BROKEN AT ${chain.data.broken_at}`} tone={chain.data.valid ? "ok" : "bad"} />}
      </div>
      <div className="card">
        <div className="row" style={{ marginBottom: 10 }}>
          <input style={{ maxWidth: 320 }} placeholder="Filter by entity id (e.g. APR-0001, EXE-0001)" value={entity} onChange={(e) => setEntity(e.target.value)} />
          <label className="row small" style={{ width: "auto" }}><input type="checkbox" style={{ width: "auto" }} checked={onlyDenied} onChange={(e) => setOnlyDenied(e.target.checked)} /> Denied only</label>
        </div>
        {!data ? <Loading error={error} /> : (
          <div className="table-wrap"><table>
            <thead><tr><th>#</th><th>Time</th><th>Actor</th><th>Action</th><th>Outcome</th><th>Entity</th><th>Artifact hash</th><th>Details</th><th>Entry hash</th></tr></thead>
            <tbody>{rows.map((a) => (
              <tr key={a.id}><td className="small">{a.id}</td><td className="small">{fmtTime(a.ts)}</td>
                <td><b>{a.actor_id}</b><div className="small muted">{a.actor_kind} · {a.actor_role}</div></td>
                <td className="mono small">{a.action}</td><td><Badge v={a.outcome} /></td><td className="small">{a.entity_type}:{a.entity_id}</td>
                <td className="mono small">{short(a.artifact_hash, 10)}</td>
                <td className="small" style={{ maxWidth: 360 }}>{a.details.reason || a.details.comment || a.details.decision || Object.keys(a.details).slice(0, 4).join(", ")}</td>
                <td className="mono small">{short(a.entry_hash, 10)}</td></tr>))}</tbody>
          </table></div>
        )}
      </div>
      <div className="card">
        <h2>Notifications sent (simulated channels)</h2>
        {!notes.data ? <Loading /> : (
          <div className="table-wrap"><table><thead><tr><th>Time</th><th>To</th><th>Channel</th><th>Subject</th><th>Body</th></tr></thead>
            <tbody>{notes.data.map((n) => <tr key={n.id}><td className="small">{fmtTime(n.created_at)}</td><td>{n.recipient_id || n.recipient_role}</td><td className="small">{n.channel}</td>
              <td>{n.subject}</td><td className="small" style={{ maxWidth: 420 }}>{n.body}</td></tr>)}</tbody></table></div>
        )}
      </div>
    </div>
  );
}
