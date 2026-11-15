// Every call to the FastAPI backend goes through this file, so components never deal with URLs,
// status codes, or JSON parsing. They get data back, or an ApiError with a message to show.

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
export const AIRFLOW_UI_URL = import.meta.env.VITE_AIRFLOW_UI_URL ?? "http://localhost:8080";

export class ApiError extends Error {
  constructor(status, message, errors = []) {
    super(message);
    this.status = status; // HTTP status, or 0 if the API couldn't be reached at all
    this.errors = errors; // spec validation errors: [{ path, message, hint }]
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err; // we cancelled it on purpose; not a real failure
    throw new ApiError(0, `Can't reach the API at ${API_URL}. Is it running?`);
  }

  const body = await response.json().catch(() => null);

  // fetch() does NOT throw on 400/500 -- it only throws on network failure. Check response.ok ourselves.
  if (!response.ok) {
    const errors = body?.errors ?? [];
    let message;
    if (errors.length) {
      message = `The spec has ${errors.length} problem${errors.length === 1 ? "" : "s"}.`; // our 400
    } else if (typeof body?.detail === "string") {
      message = body.detail; // FastAPI HTTPException, e.g. 409 "already exists"
    } else {
      message = `Request failed (HTTP ${response.status}).`;
    }
    throw new ApiError(response.status, message, errors);
  }
  return body;
}

export function listWorkflows(signal) {
  return request("/workflows", { signal });
}

export function submitWorkflow({ specYaml, target, overwrite }) {
  return request("/workflows", {
    method: "POST",
    body: JSON.stringify({ spec_yaml: specYaml, target, overwrite }),
  });
}
