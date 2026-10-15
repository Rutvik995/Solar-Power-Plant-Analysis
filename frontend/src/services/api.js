/**
 * api.js — Client API service for Solar Power Plant Analysis backend.
 * All requests route through Vite's dev proxy to http://localhost:8000.
 */

const BASE_URL = '/api';

async function handleResponse(res) {
  if (!res.ok) {
    let errorData = null;
    try {
      errorData = await res.json();
    } catch {
      // ignore
    }
    const message =
      errorData?.error?.message ||
      errorData?.detail ||
      `HTTP Error ${res.status}: ${res.statusText}`;
    const error = new Error(message);
    error.status = res.status;
    error.data = errorData;
    throw error;
  }
  return res.json();
}

/**
 * Fetch system metadata (plants, blocks, min/max log_date, provider, mode).
 */
export async function fetchMeta() {
  const res = await fetch(`${BASE_URL}/meta`);
  return handleResponse(res);
}

/**
 * Fetch dashboard KPIs (yield, PR, soiling loss %, fault days, previous period, sparkline).
 */
export async function fetchDashboardKpis({ plantId, start, end } = {}) {
  const params = new URLSearchParams();
  if (plantId && plantId !== 'all') params.append('plant_id', plantId);
  if (start) params.append('start', start);
  if (end) params.append('end', end);

  const qs = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE_URL}/dashboard/kpis${qs}`);
  return handleResponse(res);
}

/**
 * Fetch daily telemetry breakdown (date, yield, irradiance).
 */
export async function fetchDashboardDaily({ plantId, start, end } = {}) {
  const params = new URLSearchParams();
  if (plantId && plantId !== 'all') params.append('plant_id', plantId);
  if (start) params.append('start', start);
  if (end) params.append('end', end);

  const qs = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE_URL}/dashboard/daily${qs}`);
  return handleResponse(res);
}

/**
 * Fetch block-level PR metrics.
 */
export async function fetchDashboardBlocks({ plantId, start, end } = {}) {
  const params = new URLSearchParams();
  if (plantId && plantId !== 'all') params.append('plant_id', plantId);
  if (start) params.append('start', start);
  if (end) params.append('end', end);

  const qs = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE_URL}/dashboard/blocks${qs}`);
  return handleResponse(res);
}

/**
 * Fetch inverter daily PR heatmap.
 */
export async function fetchDashboardHeatmap({ plantId, start, end } = {}) {
  const params = new URLSearchParams();
  if (plantId && plantId !== 'all') params.append('plant_id', plantId);
  if (start) params.append('start', start);
  if (end) params.append('end', end);

  const qs = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE_URL}/dashboard/heatmap${qs}`);
  return handleResponse(res);
}

/**
 * Fetch soiling ratio time series by plant.
 */
export async function fetchDashboardSoiling({ plantId, start, end } = {}) {
  const params = new URLSearchParams();
  if (plantId && plantId !== 'all') params.append('plant_id', plantId);
  if (start) params.append('start', start);
  if (end) params.append('end', end);

  const qs = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE_URL}/dashboard/soiling${qs}`);
  return handleResponse(res);
}

/**
 * Fetch flagged inverters (faults, zero-gen, underperformance).
 */
export async function fetchDashboardFlagged({ plantId, start, end } = {}) {
  const params = new URLSearchParams();
  if (plantId && plantId !== 'all') params.append('plant_id', plantId);
  if (start) params.append('start', start);
  if (end) params.append('end', end);

  const qs = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE_URL}/dashboard/flagged${qs}`);
  return handleResponse(res);
}

/**
 * Submit chat query to multi-agent LangGraph graph (legacy/direct).
 */
export async function sendChatQuery({ query, threadId, mode = 'replay' }) {
  const res = await fetch(`${BASE_URL}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      query,
      thread_id: threadId,
      mode,
    }),
  });
  return handleResponse(res);
}

/**
 * Fetch all chat sessions (newest first).
 */
export async function fetchSessions() {
  const res = await fetch(`${BASE_URL}/sessions`);
  return handleResponse(res);
}

/**
 * Create a new chat session.
 */
export async function createSession({ title = 'New Chat' } = {}) {
  const res = await fetch(`${BASE_URL}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
  return handleResponse(res);
}

/**
 * Rename an existing chat session.
 */
export async function renameSession(sessionId, title) {
  const res = await fetch(`${BASE_URL}/sessions/${sessionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
  return handleResponse(res);
}

/**
 * Delete a chat session and all its messages.
 */
export async function deleteSession(sessionId) {
  const res = await fetch(`${BASE_URL}/sessions/${sessionId}`, {
    method: 'DELETE',
  });
  return handleResponse(res);
}

/**
 * Fetch all messages for a session.
 */
export async function fetchSessionMessages(sessionId) {
  const res = await fetch(`${BASE_URL}/sessions/${sessionId}/messages`);
  return handleResponse(res);
}

/**
 * Start asynchronous chat run for a session.
 */
export async function startSessionChat(sessionId, { query, mode = 'replay' }) {
  const res = await fetch(`${BASE_URL}/sessions/${sessionId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, mode }),
  });
  return handleResponse(res);
}

/**
 * Poll live status, plan steps, and payload for a run.
 */
export async function fetchRunStatus(runId) {
  const res = await fetch(`${BASE_URL}/runs/${runId}`);
  return handleResponse(res);
}

/**
 * Stop an in-flight run.
 */
export async function stopRun(runId) {
  const res = await fetch(`${BASE_URL}/runs/${runId}/stop`, {
    method: 'POST',
  });
  return handleResponse(res);
}

/**
 * Delete a user question and its paired assistant answer.
 */
export async function deleteMessagePair(messageId) {
  const res = await fetch(`${BASE_URL}/messages/${messageId}`, {
    method: 'DELETE',
  });
  return handleResponse(res);
}

/**
 * Edit a user message, truncate subsequent messages, and start a new run.
 */
export async function editMessage(messageId, { query, mode = 'replay' }) {
  const res = await fetch(`${BASE_URL}/messages/${messageId}/edit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, mode }),
  });
  return handleResponse(res);
}

/**
 * Format relative time (e.g., 'just now', '5m ago', '2h ago', 'yesterday', '3d ago').
 */
export function formatRelativeTime(dateString) {
  if (!dateString) return '';
  const date = new Date(dateString);
  const now = new Date();
  const diffInSeconds = Math.floor((now - date) / 1000);

  if (diffInSeconds < 30) return 'just now';
  if (diffInSeconds < 60) return `${diffInSeconds}s ago`;
  const diffInMinutes = Math.floor(diffInSeconds / 60);
  if (diffInMinutes < 60) return `${diffInMinutes}m ago`;
  const diffInHours = Math.floor(diffInMinutes / 60);
  if (diffInHours < 24) return `${diffInHours}h ago`;
  const diffInDays = Math.floor(diffInHours / 24);
  if (diffInDays === 1) return 'yesterday';
  if (diffInDays < 7) return `${diffInDays}d ago`;
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
}
