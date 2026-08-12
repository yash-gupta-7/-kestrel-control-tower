// Thin fetch wrapper. Base URL is injected at build time (see vite.config.js
// / docker-compose.yml); defaults to localhost:8000 for local dev against
// `uvicorn backend.app.main:app`.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

export async function apiGet(path) {
  const url = `${API_BASE_URL}${path}`;
  let res;
  try {
    res = await fetch(url);
  } catch (e) {
    throw new ApiError(
      "Could not reach the Kestrel Control Tower API. Is the backend running?",
      0,
      String(e),
    );
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    // no body / not JSON
  }
  if (!res.ok) {
    const detail = body?.detail ?? res.statusText;
    throw new ApiError(
      typeof detail === "string" ? detail : JSON.stringify(detail),
      res.status,
      detail,
    );
  }
  return body;
}

export async function apiPost(path, payload) {
  const url = `${API_BASE_URL}${path}`;
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    throw new ApiError(
      "Could not reach the Kestrel Control Tower API. Is the backend running?",
      0,
      String(e),
    );
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    // no body / not JSON
  }
  if (!res.ok) {
    const detail = body?.detail ?? res.statusText;
    throw new ApiError(
      typeof detail === "string" ? detail : JSON.stringify(detail),
      res.status,
      detail,
    );
  }
  return body;
}

export function buildQuery(params) {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") usp.set(k, v);
  }
  const qs = usp.toString();
  return qs ? `?${qs}` : "";
}
