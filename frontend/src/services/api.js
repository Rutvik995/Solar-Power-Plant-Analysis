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
 * Fetch dashboard overview (fleet KPIs, plants table, daily series, data quality).
 */
export async function fetchDashboardOverview({ plantId, start, end, agg = 'day' } = {}) {
  const params = new URLSearchParams();
  if (plantId && plantId !== 'all') params.append('plant_id', plantId);
  if (start) params.append('start', start);
  if (end) params.append('end', end);
  if (agg) params.append('agg', agg);

  const qs = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE_URL}/dashboard/overview${qs}`);
  return handleResponse(res);
}

/**
 * Fetch per-plant dashboard detail (blocks, best/worst inverters, weather series, treemap).
 */
export async function fetchDashboardPlantDetail({ plantId, start, end, agg = 'day' } = {}) {
  if (!plantId || plantId === 'all') return null;
  const params = new URLSearchParams();
  if (start) params.append('start', start);
  if (end) params.append('end', end);
  if (agg) params.append('agg', agg);

  const qs = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE_URL}/dashboard/plant/${plantId}${qs}`);
  return handleResponse(res);
}

/**
 * Fetch plant PR heatmap matrix.
 */
export async function fetchDashboardHeatmapMatrix({ plantId, start, end } = {}) {
  if (!plantId || plantId === 'all') return null;
  const params = new URLSearchParams();
  if (start) params.append('start', start);
  if (end) params.append('end', end);

  const qs = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE_URL}/dashboard/plant/${plantId}/heatmap${qs}`);
  return handleResponse(res);
}

/**
 * Fetch per-inverter telemetry detail and block peer median.
 */
export async function fetchDashboardInverterDetail({ inverterId, start, end, agg = 'day' } = {}) {
  if (!inverterId) return null;
  const params = new URLSearchParams();
  if (start) params.append('start', start);
  if (end) params.append('end', end);
  if (agg) params.append('agg', agg);

  const qs = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${BASE_URL}/dashboard/inverter/${encodeURIComponent(inverterId)}${qs}`);
  return handleResponse(res);
}

/**
 * Fetch dashboard KPIs (legacy helper).
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

/**
 * Intelligently generate a concise, human-readable chat session title
 * based on the question (e.g. 'Plant 1 PR (Sep 2026)', 'Block 2 Faulty Inverters').
 */
export function generateChatTitle(query) {
  if (!query || typeof query !== 'string' || !query.trim()) {
    return 'New Chat';
  }

  const q = query.trim();
  const qLower = q.toLowerCase();

  if (['hi', 'hello', 'hey', 'help'].includes(qLower)) {
    return 'New Chat';
  }

  const isCompare = /\b(compare|comparison|versus|vs)\b/i.test(qLower);

  const plantMatches = [...qLower.matchAll(/\bplant\s*([0-9]+|[a-zA-Z]\b)/gi)];
  let plantStr = null;
  if (plantMatches.length >= 2) {
    plantStr = `Plant ${plantMatches[0][1].toUpperCase()} vs ${plantMatches[1][1].toUpperCase()}`;
  } else if (plantMatches.length === 1) {
    const val = plantMatches[0][1];
    plantStr = val.length === 1 && /[a-zA-Z]/.test(val)
      ? `Plant ${val.toUpperCase()}`
      : `Plant ${val.charAt(0).toUpperCase() + val.slice(1)}`;
  } else if (/\bplants?\b/i.test(qLower)) {
    plantStr = 'Plant';
  }

  const blockMatch = qLower.match(/\bblock\s*([0-9]+|[a-zA-Z]\b)/i);
  const blockStr = blockMatch ? `Block ${blockMatch[1].toUpperCase()}` : null;

  const invMatch = qLower.match(/\binverter\s*([0-9]+|[a-zA-Z0-9_-]+)?/i);
  let invStr = null;
  if (invMatch && invMatch[1] && !['s', 'in', 'of', 'for', 'the', 'with', 'had', 'all'].includes(invMatch[1].toLowerCase())) {
    invStr = `Inverter ${invMatch[1].toUpperCase()}`;
  } else if (qLower.includes('inverter')) {
    invStr = 'Inverters';
  }

  let dateStr = null;
  const monthsStr = 'january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec';
  const m1 = qLower.match(new RegExp(`\\b(${monthsStr})\\s+(\\d{1,2})(?:st|nd|rd|th)?(?:,?\\s*(\\d{4}))?\\b`, 'i'));
  const m2 = qLower.match(new RegExp(`\\b(\\d{1,2})(?:st|nd|rd|th)?\\s+(${monthsStr})(?:,?\\s*(\\d{4}))?\\b`, 'i'));
  const m3 = qLower.match(new RegExp(`\\b(${monthsStr})(?:\\s+(\\d{4}))?\\b`, 'i'));

  if (m1) {
    const mon = m1[1].slice(0, 3).charAt(0).toUpperCase() + m1[1].slice(1, 3);
    const day = m1[2];
    const yr = m1[3];
    dateStr = `${mon} ${day}` + (yr ? ` ${yr}` : '');
  } else if (m2) {
    const day = m2[1];
    const mon = m2[2].slice(0, 3).charAt(0).toUpperCase() + m2[2].slice(1, 3);
    const yr = m2[3];
    dateStr = `${mon} ${day}` + (yr ? ` ${yr}` : '');
  } else if (m3) {
    const mon = m3[1].slice(0, 3).charAt(0).toUpperCase() + m3[1].slice(1, 3);
    const yr = m3[2];
    dateStr = yr ? `${mon} ${yr}` : mon;
  } else if (qLower.includes('today')) {
    dateStr = 'Today';
  } else if (qLower.includes('yesterday')) {
    dateStr = 'Yesterday';
  } else if (qLower.includes('last month')) {
    dateStr = 'Last Month';
  } else if (qLower.includes('last week')) {
    dateStr = 'Last Week';
  }

  let topic = null;
  if (/\b(pr|performance\s*ratio)\b/i.test(qLower)) {
    topic = 'PR';
  } else if (/\bsoiling\s*loss\b/i.test(qLower)) {
    topic = 'Soiling Loss';
  } else if (/\b(soiling|dust|cleaning|clean)\b/i.test(qLower)) {
    topic = qLower.includes('clean') ? 'Soiling & Cleaning' : 'Soiling';
  } else if (/\b(zero[-\s]*gen|zero[-\s]*generation|offline|outage)\b/i.test(qLower)) {
    topic = 'Zero-Gen';
  } else if (/\b(trip|tripped)\b/i.test(qLower)) {
    topic = 'Trip';
  } else if (/\b(fault|faulty|alarm|breakdown|fail|failure|error)\b/i.test(qLower)) {
    topic = 'Faults';
  } else if (/\b(underperform|underperforming|underperformance|derating|low\s+gen)\b/i.test(qLower)) {
    topic = 'Underperforming';
  } else if (/\b(yield|generation|energy|kwh|mwh)\b/i.test(qLower)) {
    topic = 'Yield';
  } else if (/\b(irradiance|ghi|poa|weather|temperature)\b/i.test(qLower)) {
    topic = 'Irradiance';
  } else if (/\bstatus\b/i.test(qLower)) {
    topic = 'Status';
  }

  const parts = [];
  if (plantStr) parts.push(plantStr);
  if (blockStr) parts.push(blockStr);
  if (invStr) {
    if (invStr === 'Inverters' && topic === 'Faults') {
      parts.push('Faulty Inverters');
      topic = null;
    } else if (invStr === 'Inverters' && topic === 'Zero-Gen') {
      parts.push('Zero-Gen Inverters');
      topic = null;
    } else {
      parts.push(invStr);
    }
  }

  if (topic && !parts.includes(topic)) {
    parts.push(topic);
  }
  if (isCompare && !parts.join(' ').includes('vs')) {
    parts.push('Comparison');
  }
  if (dateStr) {
    parts.push(`(${dateStr})`);
  }

  if (parts.length > 0) {
    const candidate = parts.join(' ');
    if (candidate.length <= 35) return candidate;
    const partsNoDate = parts.filter((p) => !p.startsWith('('));
    const candidateShort = partsNoDate.join(' ');
    if (candidateShort.length <= 35) return candidateShort;
  }

  let clean = q
    .replace(
      /^(what\s+(is|was|were|are|rhe|the)\s+|show\s+(me\s+)?(all\s+)?|tell\s+me\s+(about\s+)?|can\s+you\s+(explain\s+)?|explain\s+(how\s+)?|give\s+me\s+|how\s+(much|many|is|are|does\s+the\s+system\s+|do\s+we\s+)\s*|why\s+(did|is|are)\s+|please\s+|find\s+(all\s+)?|calculate\s+|check\s+|which\s+|list\s+(all\s+)?)/i,
      ''
    )
    .replace(/^(the|a|an|all|any)\s+/i, '')
    .replace(/[?!.,;:]+$/, '')
    .trim();

  if (clean) {
    const words = clean.split(/\s+/).slice(0, 5);
    const acronyms = new Set(['pr', 'ghi', 'poa', 'dc', 'ac', 'kwh', 'mwh']);
    const formatted = words.map((w) =>
      acronyms.has(w.toLowerCase())
        ? w.toUpperCase()
        : w.charAt(0).toUpperCase() + w.slice(1)
    ).join(' ');
    return formatted.slice(0, 35);
  }

  return 'Chat Session';
}
