import { useState } from "react";
import { submitWorkflow } from "../api";

const EXAMPLE_SPEC = `workflow_name: my_first_workflow
schedule: "@daily"
owner: rusheek
retries: 2
tags: [demo]
tasks:
  - id: extract
    image: mwp-tasks:0.1.0
    command: ["python", "extract.py"]
    memory_limit: 512Mi
  - id: transform
    image: mwp-tasks:0.1.0
    command: ["python", "transform.py"]
    depends_on: [extract]
  - id: load
    image: mwp-tasks:0.1.0
    command: ["python", "load.py"]
    depends_on: [transform]
`;

export default function SubmitForm({ onCreated }) {
  // Controlled inputs: React state is the single source of truth for what's in each field.
  const [specYaml, setSpecYaml] = useState(EXAMPLE_SPEC);
  const [target, setTarget] = useState("local");
  const [overwrite, setOverwrite] = useState(false);
  // One status object instead of several booleans, so impossible combos (loading AND error) can't happen.
  const [status, setStatus] = useState({ kind: "idle" }); // idle | submitting | success | error

  async function handleSubmit(event) {
    event.preventDefault(); // stop the browser's default full-page form submit
    setStatus({ kind: "submitting" });
    try {
      const result = await submitWorkflow({ specYaml, target, overwrite });
      setStatus({ kind: "success", message: `Created ${result.workflow_name}. ${result.message}` });
      onCreated();
    } catch (err) {
      setStatus({ kind: "error", message: err.message, errors: err.errors ?? [] });
    }
  }

  const submitting = status.kind === "submitting";

  return (
    <section className="card">
      <h2>Submit a workflow</h2>
      <form onSubmit={handleSubmit}>
        <label htmlFor="spec">Spec (YAML)</label>
        <textarea
          id="spec"
          value={specYaml}
          onChange={(e) => setSpecYaml(e.target.value)}
          rows={20}
          spellCheck={false}
        />

        <div className="form-row">
          <label>
            Target{" "}
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="local">local (BashOperator)</option>
              <option value="kubernetes">kubernetes (pods)</option>
            </select>
          </label>
          <label className="checkbox">
            <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
            Overwrite if it exists
          </label>
          <button type="submit" disabled={submitting}>
            {submitting ? "Submitting…" : "Submit"}
          </button>
        </div>
      </form>

      {status.kind === "success" && (
        <p className="alert success" role="status">
          {status.message}
        </p>
      )}

      {status.kind === "error" && (
        <div className="alert error" role="alert">
          <p>
            <strong>{status.message}</strong>
          </p>
          {status.errors.length > 0 && (
            <ul className="error-list">
              {/* key: lets React track each item between renders. Use something stable and unique
                  from the data -- not the array index, which shifts when items are added/removed. */}
              {status.errors.map((e) => (
                <li key={`${e.path}|${e.message}`}>
                  <code>{e.path}</code> {e.message}
                  <div className="hint">Fix: {e.hint}</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
