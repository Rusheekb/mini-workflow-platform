import { AIRFLOW_UI_URL } from "../api";

function StatusBadge({ run }) {
  if (!run) return <span className="badge none">no runs yet</span>;
  const state = run.state ?? "unknown";
  return <span className={`badge ${state}`}>{state}</span>;
}

function formatDate(iso) {
  return iso ? new Date(iso).toLocaleString() : "";
}

export default function WorkflowList({ workflows, airflowAvailable, loading, error, onRefresh }) {
  return (
    <section className="card">
      <div className="card-header">
        <h2>Workflows</h2>
        <button type="button" className="secondary" onClick={onRefresh}>
          Refresh
        </button>
      </div>

      {/* Every async UI needs loading, error, empty, and data states. */}
      {loading && <p className="muted">Loading…</p>}

      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}

      {!airflowAvailable && (
        <p className="alert warning" role="status">
          Airflow is unreachable, so run status isn't shown. Workflows are still listed.
        </p>
      )}

      {!loading && !error && workflows.length === 0 && (
        <p className="muted">No workflows yet. Submit one on the left.</p>
      )}

      {workflows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Workflow</th>
              <th>Schedule</th>
              <th>Tasks</th>
              <th>Latest run</th>
            </tr>
          </thead>
          <tbody>
            {workflows.map((wf) => (
              // workflow_name is unique (the API enforces it), so it's the perfect key.
              <tr key={wf.workflow_name}>
                <td>
                  <a href={`${AIRFLOW_UI_URL}/dags/${wf.workflow_name}`} target="_blank" rel="noreferrer">
                    {wf.workflow_name}
                  </a>
                  <div className="muted small">owner: {wf.owner}</div>
                </td>
                <td>
                  <code>{wf.schedule}</code>
                </td>
                <td>{wf.task_count}</td>
                <td>
                  <StatusBadge run={wf.latest_run} />
                  <div className="muted small">{formatDate(wf.latest_run?.start_date)}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
