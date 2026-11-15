import { useCallback, useEffect, useState } from "react";
import { listWorkflows } from "./api";
import SubmitForm from "./components/SubmitForm";
import WorkflowList from "./components/WorkflowList";

const REFRESH_MS = 15_000;

export default function App() {
  // State: data that, when it changes, makes React re-render the page.
  const [workflows, setWorkflows] = useState([]);
  const [airflowAvailable, setAirflowAvailable] = useState(true);
  const [loading, setLoading] = useState(true); // true only until the first response
  const [error, setError] = useState(null);

  // useCallback keeps the same function between renders, so the effect below doesn't restart every render.
  const loadWorkflows = useCallback(async (signal) => {
    try {
      const body = await listWorkflows(signal);
      setWorkflows(body.workflows);
      setAirflowAvailable(body.airflow_available);
      setError(null);
    } catch (err) {
      if (err.name === "AbortError") return; // cancelled because the page went away: ignore
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // Effect: code that talks to the outside world. Runs after the first render: load now, then every 15s.
  useEffect(() => {
    const controller = new AbortController();
    const firstLoad = setTimeout(() => loadWorkflows(controller.signal), 0);
    const timer = setInterval(() => loadWorkflows(controller.signal), REFRESH_MS);
    // Cleanup: runs when the component unmounts. Stop the timers and cancel in-flight requests,
    // otherwise they'd keep running (and setting state) after the page is gone.
    return () => {
      clearTimeout(firstLoad);
      clearInterval(timer);
      controller.abort();
    };
  }, [loadWorkflows]);

  return (
    <main className="page">
      <header className="page-header">
        <h1>Mini Workflow Platform</h1>
        <p className="muted">Submit a YAML workflow spec. It's validated, turned into an Airflow DAG, and run.</p>
      </header>

      <div className="columns">
        {/* Props: data passed from parent to child. The child calls onCreated to ask the parent to refresh. */}
        <SubmitForm onCreated={() => loadWorkflows()} />
        <WorkflowList
          workflows={workflows}
          airflowAvailable={airflowAvailable}
          loading={loading}
          error={error}
          onRefresh={() => loadWorkflows()}
        />
      </div>
    </main>
  );
}
