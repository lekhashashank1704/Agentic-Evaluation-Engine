import { Link } from "react-router-dom";
import { Json } from "../api";
import { Badge, fmtTime, Loading, short, Sim, useApi } from "../components/ui";

export default function Deployments() {
  const { data, error } = useApi<Json[]>("/deployments");
  return (
    <div>
      <div className="page-head">
        <div><h1>Deployments</h1><p>Stages 7 and 8: Release Agent canary rollouts gated by the Runtime Health Evaluator. <Sim label="Simulated environment" /></p></div>
      </div>
      <div className="card">
        {!data ? <Loading error={error} /> : data.length === 0 ? <div className="empty">No deployments yet.</div> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Deployment</th><th>Version</th><th>Execution</th><th>Feature flag</th><th className="num">Traffic</th><th>Status</th><th>Hash</th><th>Created</th></tr></thead>
            <tbody>{data.map((d) => (
              <tr key={d.id}><td><Link to={`/deployments/${d.id}`}>{d.id}</Link></td><td className="mono small">{d.version}</td>
                <td><Link to={`/executions/${d.execution_id}`}>{d.execution_id}</Link></td>
                <td className="small mono">{d.feature_flag.name} {d.feature_flag.enabled ? "on" : "off"}</td>
                <td className="num">{d.traffic_percent}%</td><td><Badge v={d.status} /></td><td className="mono small">{short(d.artifact_hash)}</td>
                <td className="small">{fmtTime(d.created_at)}</td></tr>))}</tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}
