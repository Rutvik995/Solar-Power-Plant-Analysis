import React, { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { useOutletContext, useParams, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  fetchSessions,
  createSession,
  fetchSessionMessages,
  startSessionChat,
  fetchRunStatus,
  stopRun,
  deleteMessagePair,
  editMessage,
  generateChatTitle,
} from '../services/api';
import Skeleton from '../components/common/Skeleton';
import PlotlyChart from '../components/common/PlotlyChart';
import ErrorCard from '../components/common/ErrorCard';

import {
  Send,
  Square,
  AlertTriangle,
  PanelRightClose,
  PanelRightOpen,
  ArrowRight,
  Pencil,
  Trash2,
  RotateCcw,
} from 'lucide-react';

const EXAMPLE_QUESTIONS = [
  'What was the PR of Plant 1 in September 2026?',
  'Find faulty inverters in Plant 1 and explain why.',
  'Compare soiling loss between plants.',
  'Which block in Plant 2 had the highest specific yield?',
  'Are there any zero-generation events under high irradiance?',
];

const DEFAULT_FOLLOW_UPS = [
  'Same for Plant 2',
  'Show as chart',
  'Find faulty inverters',
  'Compare with Plant 3',
];

const isNumericValue = (val) => {
  if (typeof val === 'number') return true;
  if (typeof val === 'string') {
    const clean = val.trim().replace(/[%$,]/g, '');
    return clean !== '' && !isNaN(Number(clean));
  }
  return false;
};

export default function ChatPage() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const outletContext = useOutletContext() || {};
  const mode = outletContext.mode || 'replay';

  const [messages, setMessages] = useState([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [input, setInput] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [activeRunId, setActiveRunId] = useState(null);
  const [liveRunData, setLiveRunData] = useState(null);
  const [lastQuery, setLastQuery] = useState('');
  const [selectedMessageId, setSelectedMessageId] = useState(null);
  const [isPanelOpen, setIsPanelOpen] = useState(true);
  const [generalError, setGeneralError] = useState(null);

  // Edit and delete state
  const [editingMessageId, setEditingMessageId] = useState(null);
  const [editQuery, setEditQuery] = useState('');
  const [confirmDeleteMessageId, setConfirmDeleteMessageId] = useState(null);

  const messagesEndRef = useRef(null);
  const pollIntervalRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, liveRunData, isRunning]);

  // Clean up polling interval on unmount
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, []);

  // Reload messages for a given session
  const reloadMessages = useCallback(async (sid) => {
    if (!sid) return;
    try {
      setLoadingMessages(true);
      const data = await fetchSessionMessages(sid);
      setMessages(data || []);
      setGeneralError(null);
    } catch (err) {
      if (err.status === 404) {
        localStorage.removeItem('solar_last_session_id');
        navigate('/');
      } else {
        setGeneralError(err.message || 'Failed to load session messages');
      }
    } finally {
      setLoadingMessages(false);
    }
  }, [navigate]);

  // Restore session on load or switch session
  useEffect(() => {
    let isMounted = true;

    async function initSession() {
      // Clear any running polling from previous session
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
      setIsRunning(false);
      setActiveRunId(null);
      setLiveRunData(null);
      setEditingMessageId(null);

      if (sessionId) {
        localStorage.setItem('solar_last_session_id', sessionId);
        await reloadMessages(sessionId);
      } else {
        // Root /chat or / route: restore last opened session or show empty state
        const lastId = localStorage.getItem('solar_last_session_id');
        if (lastId) {
          try {
            const allSessions = await fetchSessions();
            if (allSessions && allSessions.some((s) => s.id === lastId)) {
              if (isMounted) {
                navigate(`/chat/${lastId}`, { replace: true });
                return;
              }
            } else {
              localStorage.removeItem('solar_last_session_id');
            }
          } catch {
            localStorage.removeItem('solar_last_session_id');
          }
        }
        if (isMounted) {
          setMessages([]);
          setSelectedMessageId(null);
        }
      }
    }

    initSession();

    return () => {
      isMounted = false;
    };
  }, [sessionId, reloadMessages, navigate]);

  // Poll /api/runs/{run_id} every 1s
  const startPolling = useCallback((runId, targetSessionId) => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
    }

    pollIntervalRef.current = setInterval(async () => {
      try {
        const runData = await fetchRunStatus(runId);
        setLiveRunData(runData);

        if (runData.status === 'complete' || runData.status === 'stopped' || runData.status === 'error') {
          if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
          }
          setIsRunning(false);
          setActiveRunId(null);
          await reloadMessages(targetSessionId);
          queryClient.invalidateQueries({ queryKey: ['sessions'] });
        }
      } catch (err) {
        console.error('Polling run status error:', err);
      }
    }, 1000);
  }, [reloadMessages, queryClient]);

  // Stop active run (also bound to Esc key)
  const handleStop = useCallback(async () => {
    if (!activeRunId) return;
    try {
      await stopRun(activeRunId);
      const runData = await fetchRunStatus(activeRunId);
      setLiveRunData(runData);
      if (runData.status === 'stopped' || runData.status === 'complete') {
        if (pollIntervalRef.current) {
          clearInterval(pollIntervalRef.current);
          pollIntervalRef.current = null;
        }
        setIsRunning(false);
        setActiveRunId(null);
        if (sessionId) {
          await reloadMessages(sessionId);
        }
        queryClient.invalidateQueries({ queryKey: ['sessions'] });
      }
    } catch (err) {
      console.error('Failed to stop run:', err);
    }
  }, [activeRunId, sessionId, reloadMessages, queryClient]);

  // Bind Esc key to Stop while running
  useEffect(() => {
    const handleGlobalKeyDown = (e) => {
      if (e.key === 'Escape' && isRunning && activeRunId) {
        e.preventDefault();
        handleStop();
      }
    };
    window.addEventListener('keydown', handleGlobalKeyDown);
    return () => window.removeEventListener('keydown', handleGlobalKeyDown);
  }, [isRunning, activeRunId, handleStop]);

  // Submit query
  const handleSubmit = async (queryText) => {
    const q = (queryText || input).trim();
    if (!q || isRunning) return;

    setLastQuery(q);
    setInput('');
    setGeneralError(null);

    let currentSessionId = sessionId;
    if (!currentSessionId) {
      try {
        const smartTitle = generateChatTitle(q);
        const newSess = await createSession({ title: smartTitle });
        currentSessionId = newSess.id;
        localStorage.setItem('solar_last_session_id', currentSessionId);
        await queryClient.invalidateQueries({ queryKey: ['sessions'] });
        navigate(`/chat/${currentSessionId}`, { replace: true });
      } catch (err) {
        setGeneralError(err.message || 'Failed to create session');
        return;
      }
    }

    // Show user message immediately and working card
    const userMsg = {
      id: 'usr_opt_' + Date.now(),
      role: 'user',
      content: q,
      created_at: new Date().toISOString(),
    };
    const workingMsg = {
      id: 'asst_opt_' + Date.now(),
      role: 'assistant',
      status: 'running',
      content: '',
      payload: null,
      created_at: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMsg, workingMsg]);
    setIsRunning(true);
    setLiveRunData({ status: 'running', plan: [], llm_calls: { total: 0, budget: 12 } });

    try {
      const chatResp = await startSessionChat(currentSessionId, { query: q, mode });
      // Invalidate sessions immediately so auto-generated title is reflected in sidebar
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
      const runId = chatResp.run_id;
      setActiveRunId(runId);
      startPolling(runId, currentSessionId);
    } catch (err) {
      setIsRunning(false);
      setGeneralError(err.message || 'Failed to start analysis run');
      if (currentSessionId) {
        reloadMessages(currentSessionId);
      }
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  // Start editing a user message
  const handleStartEdit = (msg) => {
    if (isRunning) return;
    setEditingMessageId(msg.id);
    setEditQuery(msg.content);
  };

  // Save edited message and rerun
  const handleSaveAndRerun = async (msgId) => {
    const q = editQuery.trim();
    if (!q || isRunning || !sessionId) return;

    setEditingMessageId(null);
    setIsRunning(true);
    setGeneralError(null);

    // Truncate later messages in UI immediately
    const targetIdx = messages.findIndex((m) => m.id === msgId);
    const retained = messages.slice(0, targetIdx + 1).map((m) => {
      if (m.id === msgId) return { ...m, content: q };
      return m;
    });

    const workingMsg = {
      id: 'asst_edit_' + Date.now(),
      role: 'assistant',
      status: 'running',
      content: '',
      payload: null,
      created_at: new Date().toISOString(),
    };

    setMessages([...retained, workingMsg]);
    setLiveRunData({ status: 'running', plan: [], llm_calls: { total: 0, budget: 12 } });

    try {
      const editResp = await editMessage(msgId, { query: q, mode });
      const runId = editResp.run_id;
      setActiveRunId(runId);
      startPolling(runId, sessionId);
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    } catch (err) {
      setIsRunning(false);
      setGeneralError(err.message || 'Failed to edit message');
      reloadMessages(sessionId);
    }
  };

  // Confirm delete message pair
  const handleConfirmDeleteMessage = async () => {
    if (!confirmDeleteMessageId) return;
    try {
      await deleteMessagePair(confirmDeleteMessageId);
      setConfirmDeleteMessageId(null);
      if (sessionId) {
        await reloadMessages(sessionId);
      }
    } catch (err) {
      setGeneralError(err.message || 'Failed to delete message pair');
      setConfirmDeleteMessageId(null);
    }
  };

  // Retry from stopped or error card
  const handleRetry = (asstMsgId) => {
    const asstIdx = messages.findIndex((m) => m.id === asstMsgId);
    if (asstIdx > 0 && messages[asstIdx - 1]?.role === 'user') {
      handleSubmit(messages[asstIdx - 1].content);
    } else {
      handleSubmit(lastQuery);
    }
  };

  // Selected or active assistant message
  const activeMessage = useMemo(() => {
    if (selectedMessageId) {
      const found = messages.find((m) => m.id === selectedMessageId);
      if (found && found.role === 'assistant') return found;
    }
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant') {
        const p = messages[i].payload || messages[i].data;
        if (p?.plan || p?.llm_calls || messages[i].status === 'complete' || messages[i].status === 'stopped') {
          return messages[i];
        }
      }
    }
    return null;
  }, [messages, selectedMessageId]);

  // Active trace data (live or saved)
  const activeTrace = useMemo(() => {
    if (isRunning && liveRunData) {
      return {
        plan: liveRunData.plan || [],
        llm_calls: liveRunData.llm_calls || { total: 0, budget: 12, planner: 0, agent: 0, synthesizer: 0 },
        elapsed_ms: liveRunData.elapsed_ms || null,
      };
    }
    if (!activeMessage) return null;
    const p = activeMessage.payload || activeMessage.data || {};
    return {
      plan: p.plan || [],
      llm_calls: p.llm_calls || { total: 0, budget: 12, planner: 0, agent: 0, synthesizer: 0 },
      elapsed_ms: p.elapsed_ms || null,
    };
  }, [isRunning, liveRunData, activeMessage]);

  return (
    <div className="flex gap-5 h-[calc(100vh-6.5rem)] max-w-7xl mx-auto">
      {/* =========================================================================
          LEFT / MAIN COLUMN: Chat Stream & Input
      ========================================================================= */}
      <div className="flex-1 flex flex-col h-full bg-white border border-[#E2E8F0] rounded-lg overflow-hidden min-w-0">
        {/* Messages Stream */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5 bg-white">
          {generalError && (
            <ErrorCard
              title="Error"
              message={generalError}
              onRetry={() => setGeneralError(null)}
              className="mb-4"
            />
          )}

          {loadingMessages ? (
            <div className="space-y-4 p-4">
              <Skeleton className="w-1/3 h-4 bg-[#E2E8F0]" />
              <Skeleton className="w-full h-16 bg-[#E2E8F0]" />
              <Skeleton className="w-1/2 h-4 bg-[#E2E8F0]" />
            </div>
          ) : messages.length === 0 ? (
            /* Empty State */
            <div className="h-full flex flex-col items-center justify-center text-center p-6 max-w-xl mx-auto">
              <h2 className="text-xl font-bold text-[#0F172A] mb-2">
                Ask about your solar plants
              </h2>
              <p className="text-xs text-[#64748B] mb-6 leading-relaxed max-w-md">
                Query performance ratios, inverter abnormalities, soiling losses, and weather patterns. Responses are grounded directly against plant databases and operational telemetry.
              </p>

              <div className="w-full space-y-2 text-left">
                {EXAMPLE_QUESTIONS.map((q, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => handleSubmit(q)}
                    className="w-full p-3 rounded-lg bg-white hover:bg-[#F8FAFC] border border-[#E2E8F0] text-xs text-[#0F172A] hover:text-[#2563EB] transition-colors flex items-center justify-between group"
                  >
                    <span className="font-medium text-left mr-2 text-[#0F172A] group-hover:text-[#2563EB]">
                      {q}
                    </span>
                    <ArrowRight className="w-3.5 h-3.5 text-[#64748B] group-hover:text-[#2563EB] group-hover:translate-x-0.5 transition-transform shrink-0" />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((msg) => {
              const payload = msg.payload || msg.data || {};
              const isWorking = msg.status === 'running';
              const isStopped = msg.status === 'stopped';
              const isError = msg.status === 'error' || Boolean(payload.error);
              const isEditing = editingMessageId === msg.id;

              if (msg.role === 'user') {
                return (
                  <div key={msg.id} className="flex justify-end group relative">
                    {isEditing ? (
                      /* Edit Mode */
                      <div className="w-full max-w-xl bg-white border border-[#2563EB] rounded-lg p-3 space-y-2.5 shadow-sm">
                        <textarea
                          value={editQuery}
                          onChange={(e) => setEditQuery(e.target.value)}
                          rows={2}
                          className="w-full bg-[#F8FAFC] border border-[#E2E8F0] rounded p-2 text-sm text-[#0F172A] focus:outline-none focus:border-[#2563EB] resize-none"
                        />
                        <div className="text-xs text-[#D97706] bg-[#FFFBEB] border border-[#D97706]/30 rounded p-2 flex items-center gap-1.5">
                          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                          <span>Warning: Editing this question will delete all subsequent messages in this session.</span>
                        </div>
                        <div className="flex items-center justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => setEditingMessageId(null)}
                            className="px-3 py-1.5 text-xs font-medium rounded-lg border border-[#E2E8F0] hover:bg-[#F8FAFC] text-[#64748B] hover:text-[#0F172A] transition-colors"
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            disabled={!editQuery.trim() || isRunning}
                            onClick={() => handleSaveAndRerun(msg.id)}
                            className="px-3 py-1.5 text-xs font-medium rounded-lg bg-[#2563EB] hover:bg-[#1D4ED8] text-white transition-colors disabled:opacity-50"
                          >
                            Save & rerun
                          </button>
                        </div>
                      </div>
                    ) : (
                      /* Normal User Bubble */
                      <div className="relative max-w-xl">
                        <div className="bg-[#EFF6FF] border border-[#2563EB]/20 rounded-lg px-4 py-3">
                          <p className="text-sm text-[#0F172A] whitespace-pre-wrap leading-relaxed">
                            {msg.content}
                          </p>
                          <div className="flex justify-end mt-1 text-[11px] text-[#64748B]">
                            {msg.created_at
                              ? new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                              : ''}
                          </div>
                        </div>

                        {/* Hover Actions: Edit & Delete */}
                        <div className="absolute -top-3 right-2 opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1 bg-white border border-[#E2E8F0] rounded-lg p-0.5 shadow-sm">
                          <button
                            type="button"
                            onClick={() => handleStartEdit(msg)}
                            disabled={isRunning}
                            className="p-1 rounded hover:bg-[#F8FAFC] text-[#64748B] hover:text-[#2563EB] transition-colors disabled:opacity-40"
                            title="Edit question"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmDeleteMessageId(msg.id)}
                            disabled={isRunning}
                            className="p-1 rounded hover:bg-[#FEF2F2] text-[#64748B] hover:text-[#DC2626] transition-colors disabled:opacity-40"
                            title="Delete question & answer"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              }

              /* Assistant Message */
              return (
                <div key={msg.id} className="bg-white border border-[#E2E8F0] rounded-lg p-5 space-y-4">
                  {/* Running State */}
                  {isWorking ? (
                    <div className="space-y-3">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2 text-xs text-[#0F172A] font-medium">
                          <span className="w-2.5 h-2.5 rounded-full bg-[#2563EB] animate-ping" />
                          <span>Running multi-agent analysis...</span>
                        </div>
                        {liveRunData?.elapsed_ms && (
                          <span className="text-[11px] text-[#64748B] font-mono">
                            {liveRunData.elapsed_ms} ms
                          </span>
                        )}
                      </div>

                      {liveRunData?.plan && liveRunData.plan.length > 0 && (
                        <div className="space-y-1.5 pt-1">
                          <div className="text-[11px] font-semibold text-[#64748B]">Live Plan Steps</div>
                          <div className="space-y-1">
                            {liveRunData.plan.map((step, idx) => {
                              let dot = 'bg-[#94A3B8]';
                              if (step.status === 'done') dot = 'bg-[#16A34A]';
                              else if (step.status === 'running') dot = 'bg-[#D97706] animate-pulse';
                              else if (step.status === 'failed') dot = 'bg-[#DC2626]';

                              return (
                                <div
                                  key={idx}
                                  className="flex items-center justify-between p-2 rounded bg-[#F8FAFC] border border-[#E2E8F0] text-xs"
                                >
                                  <div className="flex items-center gap-2 min-w-0">
                                    <span className={`w-2 h-2 rounded-full shrink-0 ${dot}`} />
                                    <span className="font-semibold text-[#0F172A] capitalize shrink-0">
                                      {step.agent}
                                    </span>
                                    <span className="text-[#64748B] text-[11px] truncate">
                                      {step.task || step.tool}
                                    </span>
                                  </div>
                                  <span className="text-[10px] uppercase font-semibold text-[#64748B] shrink-0">
                                    {step.status}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      <div className="space-y-2 pt-2">
                        <Skeleton className="w-3/4 h-3.5 bg-[#E2E8F0]" />
                        <Skeleton className="w-full h-3.5 bg-[#E2E8F0]" />
                        <Skeleton className="w-2/3 h-3.5 bg-[#E2E8F0]" />
                      </div>
                    </div>
                  ) : isError ? (
                    /* Error State */
                    <ErrorCard
                      title={payload.error?.type || 'Execution Error'}
                      message={payload.error?.message || msg.content || 'An error occurred during query execution.'}
                      onRetry={() => handleRetry(msg.id)}
                    />
                  ) : (
                    <>
                      {/* Stopped Banner */}
                      {isStopped && (
                        <div className="flex items-center justify-between p-3 rounded-lg bg-[#FFFBEB] border border-[#D97706]/30 text-[#D97706] text-xs">
                          <div className="flex items-center gap-2">
                            <AlertTriangle className="w-4 h-4 shrink-0 text-[#D97706]" />
                            <span className="font-semibold text-[#B45309]">Stopped, partial results</span>
                          </div>
                          <button
                            type="button"
                            onClick={() => handleRetry(msg.id)}
                            className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-lg bg-white border border-[#D97706]/40 hover:bg-[#FFFBEB] text-[#B45309] transition-colors"
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                            <span>Retry</span>
                          </button>
                        </div>
                      )}

                      {/* 1. short_answer in semibold 16px */}
                      {(payload.short_answer || msg.content) && (
                        <div className="text-base font-semibold text-[#0F172A] leading-snug">
                          {payload.short_answer || msg.content}
                        </div>
                      )}

                      {/* 2. Tables */}
                      {payload.tables && payload.tables.length > 0 && (
                        <div className="space-y-3">
                          {payload.tables.map((table, tIdx) => (
                            <div
                              key={tIdx}
                              className="border border-[#E2E8F0] rounded-lg overflow-hidden bg-white"
                            >
                              {table.title && (
                                <div className="px-3 py-2 bg-[#F8FAFC] border-b border-[#E2E8F0] text-xs font-semibold text-[#0F172A]">
                                  {table.title}
                                </div>
                              )}
                              <div className="overflow-x-auto max-h-64">
                                <table className="w-full border-collapse text-xs">
                                  <thead className="bg-[#F8FAFC] border-b border-[#E2E8F0] text-[#64748B]">
                                    <tr>
                                      {table.columns.map((col, cIdx) => (
                                        <th
                                          key={cIdx}
                                          className="px-3 py-2 text-left font-medium whitespace-nowrap text-[#64748B]"
                                        >
                                          {col}
                                        </th>
                                      ))}
                                    </tr>
                                  </thead>
                                  <tbody className="divide-y divide-[#E2E8F0]">
                                    {table.rows.map((row, rIdx) => (
                                      <tr key={rIdx} className="hover:bg-[#F8FAFC] transition-colors">
                                        {row.map((val, vIdx) => {
                                          const isNum = isNumericValue(val);
                                          return (
                                            <td
                                              key={vIdx}
                                              className={`px-3 py-2 ${
                                                isNum
                                                  ? 'text-right font-mono tabular-nums text-[#0F172A]'
                                                  : 'text-left text-[#0F172A]'
                                              } whitespace-nowrap`}
                                            >
                                              {val !== null && val !== undefined ? String(val) : '—'}
                                            </td>
                                          );
                                        })}
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* 3. details_markdown */}
                      {payload.details_markdown ? (
                        <div className="prose prose-sm max-w-none text-[#0F172A] leading-relaxed text-sm">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {payload.details_markdown}
                          </ReactMarkdown>
                        </div>
                      ) : payload.answer_markdown ? (
                        <div className="prose prose-sm max-w-none text-[#0F172A] leading-relaxed text-sm">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {payload.answer_markdown}
                          </ReactMarkdown>
                        </div>
                      ) : null}

                      {/* 4. assumptions */}
                      {payload.assumptions && payload.assumptions.length > 0 && (
                        <div className="text-xs text-[#64748B] space-y-1 pt-1">
                          <span className="font-medium text-[#64748B]">Assumptions:</span>
                          <ul className="list-disc pl-4 space-y-0.5">
                            {payload.assumptions.map((a, aIdx) => (
                              <li key={aIdx}>{a}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {/* 5. warnings */}
                      {payload.warnings && payload.warnings.length > 0 && (
                        <div className="p-3 rounded-lg bg-[#FFFBEB] border border-[#D97706]/30 text-[#D97706] text-xs space-y-1">
                          <div className="font-semibold flex items-center gap-1.5">
                            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                            <span>Warnings:</span>
                          </div>
                          <ul className="list-disc pl-4 space-y-0.5 text-[#B45309]">
                            {payload.warnings.map((w, wIdx) => (
                              <li key={wIdx}>{w}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {/* Plotly Charts */}
                      {payload.charts && payload.charts.length > 0 && (
                        <div className="space-y-3 pt-1">
                          {payload.charts.map((chartSpec, cIdx) => (
                            <PlotlyChart key={cIdx} figureJson={chartSpec} />
                          ))}
                        </div>
                      )}

                      {/* Follow-up question chips */}
                      <div className="pt-2 flex flex-wrap items-center gap-1.5 border-t border-[#E2E8F0]">
                        <span className="text-xs text-[#64748B] font-medium mr-1">Follow up:</span>
                        {DEFAULT_FOLLOW_UPS.map((chip, fIdx) => (
                          <button
                            key={fIdx}
                            type="button"
                            onClick={() => handleSubmit(chip)}
                            className="text-xs px-2.5 py-1 rounded-lg bg-[#F8FAFC] hover:bg-[#EFF6FF] border border-[#E2E8F0] hover:border-[#2563EB] text-[#0F172A] hover:text-[#2563EB] transition-colors"
                          >
                            {chip}
                          </button>
                        ))}
                      </div>

                      {/* Footer with period, sources, inspect trace, time */}
                      <div className="pt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-[#64748B] border-t border-[#E2E8F0]">
                        <div className="flex flex-wrap items-center gap-3">
                          {payload.period && (
                            <span>
                              <strong className="text-[#0F172A] font-medium">Period analysed:</strong>{' '}
                              {payload.period.start} → {payload.period.end}
                            </span>
                          )}
                          {payload.sources && payload.sources.length > 0 && (
                            <div className="flex items-center gap-1.5">
                              <span>Sources:</span>
                              {payload.sources.map((src, sIdx) => (
                                <span
                                  key={sIdx}
                                  className="px-2 py-0.5 rounded bg-[#F1F5F9] text-[#64748B] text-[11px] border border-[#E2E8F0]"
                                >
                                  {src}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>

                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setSelectedMessageId(msg.id)}
                            className={`text-[11px] font-medium px-2 py-0.5 rounded transition-colors ${
                              activeMessage?.id === msg.id
                                ? 'bg-[#EFF6FF] text-[#2563EB] border border-[#2563EB]/30'
                                : 'text-[#64748B] hover:text-[#0F172A] hover:bg-[#F8FAFC] border border-transparent'
                            }`}
                          >
                            {activeMessage?.id === msg.id ? 'Viewing Trace' : 'Inspect Trace'}
                          </button>
                          <span className="text-[11px] text-[#64748B]">
                            {msg.created_at
                              ? new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                              : ''}
                          </span>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              );
            })
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input Bar */}
        <div className="p-3 border-t border-[#E2E8F0] bg-white">
          <div className="flex items-center gap-2">
            <textarea
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isRunning}
              placeholder={
                isRunning
                  ? 'Agent is processing query (press Esc or Stop button to halt)...'
                  : 'Ask a question about solar plant telemetry (Enter to send)...'
              }
              className="flex-1 bg-white border border-[#E2E8F0] rounded-lg px-3.5 py-2 text-sm text-[#0F172A] placeholder:text-[#64748B] focus:outline-none focus:border-[#2563EB] resize-none transition-colors disabled:bg-[#F8FAFC] disabled:cursor-not-allowed"
            />

            {isRunning ? (
              <button
                type="button"
                onClick={handleStop}
                className="px-4 py-2 rounded-lg border border-[#DC2626] text-[#DC2626] bg-white hover:bg-[#FEF2F2] font-medium text-sm inline-flex items-center gap-1.5 transition-colors"
                title="Stop generation (Esc)"
              >
                <Square className="w-3.5 h-3.5 fill-current" />
                <span>Stop</span>
              </button>
            ) : (
              <button
                type="button"
                onClick={() => handleSubmit()}
                disabled={!input.trim() || isRunning}
                className="px-4 py-2 rounded-lg bg-[#2563EB] hover:bg-[#1D4ED8] text-white font-medium text-sm inline-flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <Send className="w-4 h-4" />
                <span>Send</span>
              </button>
            )}
          </div>
        </div>
      </div>

      {/* =========================================================================
          RIGHT COLUMN: 320px Collapsible Trace Panel
      ========================================================================= */}
      {isPanelOpen ? (
        <aside className="w-[320px] min-w-[320px] max-w-[320px] flex flex-col h-full bg-white border border-[#E2E8F0] rounded-lg overflow-hidden shrink-0">
          {/* Panel Header */}
          <div className="px-4 py-3 border-b border-[#E2E8F0] flex items-center justify-between bg-white shrink-0">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-[#0F172A]">Execution Trace</h3>
              {activeMessage && (
                <span className="text-[10px] text-[#64748B] font-mono bg-[#F8FAFC] px-1.5 py-0.5 rounded border border-[#E2E8F0]">
                  {activeMessage.created_at
                    ? new Date(activeMessage.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                    : 'Saved'}
                </span>
              )}
            </div>
            <button
              type="button"
              onClick={() => setIsPanelOpen(false)}
              className="p-1 rounded hover:bg-[#F8FAFC] text-[#64748B] hover:text-[#0F172A] transition-colors"
              title="Collapse panel"
            >
              <PanelRightClose className="w-4 h-4" />
            </button>
          </div>

          {/* Panel Content */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-white">
            {activeTrace ? (
              <>
                {/* Budget Progress bar out of 12 */}
                <div className="space-y-1.5 bg-[#F8FAFC] border border-[#E2E8F0] rounded-lg p-3">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium text-[#0F172A]">LLM Call Budget</span>
                    <span className="font-mono text-xs font-semibold text-[#2563EB]">
                      {activeTrace.llm_calls?.total || 0} / {activeTrace.llm_calls?.budget || 12}
                    </span>
                  </div>

                  <div className="w-full bg-[#E2E8F0] rounded-full h-2 overflow-hidden">
                    <div
                      className={`h-full transition-all duration-300 ${
                        (activeTrace.llm_calls?.total || 0) >= (activeTrace.llm_calls?.budget || 12)
                          ? 'bg-[#DC2626]'
                          : (activeTrace.llm_calls?.total || 0) >= 9
                          ? 'bg-[#D97706]'
                          : 'bg-[#2563EB]'
                      }`}
                      style={{
                        width: `${Math.min(
                          100,
                          (((activeTrace.llm_calls?.total || 0) / (activeTrace.llm_calls?.budget || 12)) * 100)
                        )}%`,
                      }}
                    />
                  </div>

                  {/* Calls by role */}
                  <div className="grid grid-cols-3 gap-1.5 pt-2 text-center text-xs">
                    <div className="p-1.5 rounded bg-white border border-[#E2E8F0]">
                      <div className="text-[10px] text-[#64748B]">Planner</div>
                      <div className="font-mono font-semibold text-[#0F172A] mt-0.5">
                        {activeTrace.llm_calls?.planner || 0}
                      </div>
                    </div>
                    <div className="p-1.5 rounded bg-white border border-[#E2E8F0]">
                      <div className="text-[10px] text-[#64748B]">Agents</div>
                      <div className="font-mono font-semibold text-[#0F172A] mt-0.5">
                        {activeTrace.llm_calls?.agent || 0}
                      </div>
                    </div>
                    <div className="p-1.5 rounded bg-white border border-[#E2E8F0]">
                      <div className="text-[10px] text-[#64748B]">Synthesizer</div>
                      <div className="font-mono font-semibold text-[#0F172A] mt-0.5">
                        {activeTrace.llm_calls?.synthesizer || 0}
                      </div>
                    </div>
                  </div>

                  {/* Elapsed time */}
                  {activeTrace.elapsed_ms && (
                    <div className="flex items-center justify-between text-[11px] text-[#64748B] pt-2 border-t border-[#E2E8F0] mt-1">
                      <span>Elapsed time:</span>
                      <span className="font-mono font-medium text-[#0F172A]">
                        {activeTrace.elapsed_ms} ms
                      </span>
                    </div>
                  )}
                </div>

                {/* Plan steps list */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-semibold text-[#0F172A]">Plan Steps</h4>
                    <span className="text-[11px] text-[#64748B] font-mono">
                      {activeTrace.plan?.length || 0} steps
                    </span>
                  </div>

                  {activeTrace.plan && activeTrace.plan.length > 0 ? (
                    <div className="space-y-2">
                      {activeTrace.plan.map((step, sIdx) => {
                        let dotColor = 'bg-[#16A34A]';
                        if (step.status === 'running') dotColor = 'bg-[#D97706] animate-pulse';
                        else if (step.status === 'failed') dotColor = 'bg-[#DC2626]';
                        else if (step.status !== 'done') dotColor = 'bg-[#94A3B8]';

                        return (
                          <div
                            key={step.id || sIdx}
                            className="p-2.5 rounded-lg bg-[#F8FAFC] border border-[#E2E8F0] text-xs space-y-1"
                          >
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-1.5">
                                <span className={`w-2 h-2 rounded-full shrink-0 ${dotColor}`} />
                                <span className="font-semibold text-[#0F172A] capitalize">
                                  {step.agent}
                                </span>
                              </div>
                              <span className="text-[10px] uppercase font-semibold text-[#64748B]">
                                {step.status}
                              </span>
                            </div>

                            {step.task && (
                              <p className="text-[11px] text-[#64748B] leading-relaxed">
                                {step.task}
                              </p>
                            )}

                            <div className="flex items-center justify-between text-[11px] text-[#64748B] pt-1 border-t border-[#E2E8F0]/60">
                              <span>
                                Tool: <code className="text-[#2563EB] font-mono font-medium">{step.tool || step.agent}</code>
                              </span>
                              {step.depends_on && step.depends_on.length > 0 && (
                                <span className="text-[10px]">
                                  deps: [{step.depends_on.join(', ')}]
                                </span>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="py-6 text-center text-xs text-[#64748B]">
                      No plan steps recorded for this answer.
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="py-12 text-center text-xs text-[#64748B] space-y-2">
                <p className="font-medium text-[#0F172A]">No active trace</p>
                <p className="text-[11px]">
                  Submit a query to inspect orchestrator plan steps, tool executions, and LLM call counts.
                </p>
              </div>
            )}
          </div>
        </aside>
      ) : (
        /* Collapsed button to reopen the panel */
        <div className="flex flex-col items-center">
          <button
            type="button"
            onClick={() => setIsPanelOpen(true)}
            className="p-2 bg-white border border-[#E2E8F0] rounded-lg text-[#64748B] hover:text-[#0F172A] hover:border-[#2563EB] transition-colors"
            title="Expand trace panel"
          >
            <PanelRightOpen className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Delete Question/Answer Pair Confirmation Modal */}
      {confirmDeleteMessageId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-[1px] p-4">
          <div className="bg-white rounded-lg border border-[#E2E8F0] max-w-sm w-full p-5 space-y-4 shadow-sm animate-in fade-in zoom-in-95">
            <div className="space-y-1.5">
              <h4 className="text-sm font-semibold text-[#0F172A]">Delete question & answer?</h4>
              <p className="text-xs text-[#64748B] leading-relaxed">
                Are you sure you want to delete this question and its corresponding answer? This action cannot be undone.
              </p>
            </div>
            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setConfirmDeleteMessageId(null)}
                className="px-3 py-1.5 text-xs font-medium rounded-lg border border-[#E2E8F0] hover:bg-[#F8FAFC] text-[#64748B] hover:text-[#0F172A] transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmDeleteMessage}
                className="px-3 py-1.5 text-xs font-medium rounded-lg bg-[#DC2626] hover:bg-[#B91C1C] text-white transition-colors"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
