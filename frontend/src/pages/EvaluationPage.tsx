import { Link, useParams } from "react-router-dom";
import { Json } from "../api";
import { EvaluationView } from "../components/evaluation";
import { fmtTime, Loading, useApi } from "../components/ui";

export default function EvaluationPage() {
  const { id } = useParams();
  const { data, error } = useApi<Json>(`/evaluations/${id}`);
  if (!data) return <Loading error={error} />;
  return (
    <div>
      <div className="page-head">
        <div>
          <div className="small muted">
            {data.execution_id ? <Link to={`/executions/${data.execution_id}`}>{data.execution_id}</Link> : "Ad-hoc"} / {data.evaluation_id}
          </div>
          <h1>Evaluation {data.evaluation_id}{data.iteration ? ` · iteration ${data.iteration}` : ""}</h1>
          <p>{data.project_id} · {data.ticket_id} · evaluated {fmtTime(data.evaluated_at)}</p>
        </div>
      </div>
      <EvaluationView r={data} />
    </div>
  );
}
