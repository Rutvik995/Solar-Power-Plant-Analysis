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
 * Submit chat query to multi-agent LangGraph graph.
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
