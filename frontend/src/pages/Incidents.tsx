import { Link } from "react-router-dom";
import { Json } from "../api";
import { Badge, fmtTime, Loading, useApi } from "../components/ui";

export default function Incidents() {
  const { data, error } = useApi<Json[]>("/incidents");
  const pms = useApi<Json[]>("/postmortems");
  return (
    <div>
      <div className="page-head"><div><h1>Incidents</h1><p>Stages 9 and 10: SRE Agent response and On-Call Engineer ownership.</p></div></div>
      <div className="card">
        {!data ? <Loading error={error} /> : data.length === 0 ? <div className="empty">No incidents.</div> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Incident</th><th>Title</th><th>Service</th><th>Severity</th><th>Status</th><th>Assignee</th><th>Opened</th></tr></thead>
            <tbody>{data.map((i) => (
              <tr key={i.id}><td><Link to={`/incidents/${i.id}`}>{i.id}</Link></td><td>{i.title}{i.data.seeded && <span className="badge b-neutral" style={{ marginLeft: 6 }}>historical seed</span>}</td>
                <td>{i.service}</td><td><Badge v={i.severity} /></td><td><Badge v={i.status} /></td><td>{i.data.assignee || "—"}</td><td className="small">{fmtTime(i.created_at)}</td></tr>))}</tbody>
          </table></div>
        )}
      </div>
      <div className="card">
        <h2>Postmortems</h2>
        {!pms.data ? <Loading /> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Postmortem</th><th>Incident</th><th>Service</th><th>Root cause</th><th>Change categories</th><th>Completed</th></tr></thead>
            <tbody>{pms.data.map((p) => (
              <tr key={p.id}><td>{p.id}</td><td><Link to={`/incidents/${p.incident_id}`}>{p.incident_id}</Link></td><td>{p.data.service}</td>
                <td className="small">{p.data.root_cause}</td><td className="small">{p.change_categories.join(", ")}</td><td className="small">{fmtTime(p.completed_at)} by {p.completed_by}</td></tr>))}</tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}
