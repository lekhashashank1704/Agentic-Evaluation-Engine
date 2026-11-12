import { useState } from "react";
import { Link } from "react-router-dom";
import { Json } from "../api";
import { Badge, fmtTime, Loading, short, useApi } from "../components/ui";

export default function Approvals() {
  const [status, setStatus] = useState("PENDING");
  const { data, error } = useApi<Json[]>(`/approvals${status ? `?status=${status}` : ""}`, [status]);
  return (
    <div>
      <div className="page-head">
        <div><h1>Approval queue</h1><p>Stage 6: human governance. Each approval is bound to an exact artifact version and SHA-256 hash.</p></div>
        <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: 200 }} aria-label="Filter status">
          <option value="PENDING">Pending</option><option value="APPROVED">Approved</option><option value="CHANGES_REQUESTED">Changes requested</option>
          <option value="REJECTED">Rejected</option><option value="INVALIDATED">Invalidated</option><option value="">All</option>
        </select>
      </div>
      <div className="card">
        {!data ? <Loading error={error} /> : data.length === 0 ? <div className="empty">Nothing here.</div> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Approval</th><th>Change</th><th>Required role</th><th>Risk</th><th>Version / hash</th><th>Recommendation</th><th>Status</th><th>Requested</th></tr></thead>
            <tbody>{data.map((a) => (
              <tr key={a.id}>
                <td><Link to={`/approvals/${a.id}`}>{a.id}</Link></td>
                <td>{a.ticket_id} · {a.title}<div className="small muted">{a.execution_id}</div></td>
                <td><span className="badge b-primary">{a.required_role.replace(/_/g, " ")}</span></td>
                <td><Badge v={a.risk_level} /> {a.risk_score}</td>
                <td className="mono small">{a.artifact_version} · {short(a.artifact_hash)}</td>
                <td><Badge v={a.recommended_decision} tone="info" /></td>
                <td><Badge v={a.status} /></td>
                <td className="small">{fmtTime(a.created_at)}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}
