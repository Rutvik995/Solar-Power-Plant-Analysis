import React, { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { useOutletContext, useParams, useNavigate, useSearchParams } from 'react-router-dom';
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
import GlossaryText from '../components/common/GlossaryText';

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
  Info,
  Play,
  ChevronDown,
  Code2,
  Calendar,
} from 'lucide-react';

const EXAMPLE_QUESTIONS = [
  'What was the PR of Plant 1 in September 2026?',
  'Find faulty inverters in Plant 1 and explain why.',
  'Compare soiling loss between plants.',
  'Which block in Plant 2 had the highest specific yield?',
  'Are there any zero-generation events under high irradiance?',
];

const isNumericValue = (val) => {
  if (typeof val === 'number') return true;
  if (typeof val === 'string') {
    const clean = val.trim().replace(/[%$,]/g, '');
    return clean !== '' && !isNaN(Number(clean));
  }
  return false;
};

const isMainColumn = (col) => {
  if (!col) return false;
  const c = String(col).toLowerCase();
  return (
    c.includes('performance ratio') ||
    c === 'pr' ||
    c.includes('peer performance ratio') ||
    c.includes('energy produced') ||
    c.includes('yield') ||
    c.includes('soiling loss') ||
    c.includes('loss (%)') ||
    c.includes('capacity (mwp)') ||
    c.includes('rated dc')
  );
};

export default function ChatPage() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const outletContext = useOutletContext() || {};
  const mode = outletContext.mode || 'live';
  const setMode = outletContext.setMode;

  const [messages, setMessages] = useState([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [input, setInput] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [activeRunId, setActiveRunId] = useState(null);
  const [liveRunData, setLiveRunData] = useState(null);
  const [lastQuery, setLastQuery] = useState('');
  const [selectedMessageId, setSelectedMessageId] = useState(null);
  const [isPanelOpen, setIsPanelOpen] = useState(true);
  const [isTechDetailsOpen, setIsTechDetailsOpen] = useState(false);
  const [generalError, setGeneralError] = useState(null);
  const [expandedBlocks, setExpandedBlocks] = useState({});

  // Edit and delete state
  const [editingMessageId, setEditingMessageId] = useState(null);
  const [editQuery, setEditQuery] = useState('');
  const [confirmDeleteMessageId, setConfirmDeleteMessageId] = useState(null);

  const messagesEndRef = useRef(null);
  const pollIntervalRef = useRef(null);
  const [searchParams] = useSearchParams();

  // Populate prompt input from query parameter ?prefill=... without auto-sending
  useEffect(() => {
    const prefillQuery = searchParams.get('prefill');
    if (prefillQuery) {
      setInput(prefillQuery);
    }
  }, [searchParams]);

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
      if (sessionId) {
        localStorage.setItem('solar_last_session_id', sessionId);
        await reloadMessages(sessionId);
      } else {
        const lastSid = localStorage.getItem('solar_last_session_id');
        if (lastSid) {
          try {
            const sessions = await fetchSessions();
            if (sessions.some((s) => s.id === lastSid)) {
              if (isMounted) {
                navigate(`/chat/${lastSid}`, { replace: true });
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
  const handleSubmit = async (queryText, modeOverride) => {
    const q = (queryText || input).trim();
    if (!q || isRunning) return;

    const effectiveMode = modeOverride || mode;
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
      const chatResp = await startSessionChat(currentSessionId, { query: q, mode: effectiveMode });
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

  // Switch to Live mode and retry query
  const handleSwitchToLiveAndRetry = (msg) => {
    if (setMode) setMode('live');
    const msgIdx = messages.findIndex((m) => m.id === msg.id);
    let userQuery = lastQuery;
    if (msgIdx > 0 && messages[msgIdx - 1]?.role === 'user') {
      userQuery = messages[msgIdx - 1].content;
    }
    handleSubmit(userQuery, 'live');
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
      id: 'asst_opt_' + Date.now(),
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
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
      const runId = editResp.run_id;
      setActiveRunId(runId);
      startPolling(runId, sessionId);
    } catch (err) {
      setIsRunning(false);
      setGeneralError(err.message || 'Failed to update message');
      reloadMessages(sessionId);
    }
  };

  // Delete message pair (user message + its assistant response)
  const handleDeletePair = async (msgId) => {
    if (isRunning || !sessionId) return;
    setConfirmDeleteMessageId(null);

    // Optimistically remove from UI
    const targetIdx = messages.findIndex((m) => m.id === msgId);
    if (targetIdx === -1) return;

    const idsToRemove = new Set([msgId]);
    if (messages[targetIdx].role === 'user' && targetIdx + 1 < messages.length && messages[targetIdx + 1].role === 'assistant') {
      idsToRemove.add(messages[targetIdx + 1].id);
    } else if (messages[targetIdx].role === 'assistant' && targetIdx - 1 >= 0 && messages[targetIdx - 1].role === 'user') {
      idsToRemove.add(messages[targetIdx - 1].id);
    }

    const previousMessages = [...messages];
    setMessages((prev) => prev.filter((m) => !idsToRemove.has(m.id)));

    try {
      await deleteMessagePair(msgId);
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    } catch (err) {
      setMessages(previousMessages); // Rollback on failure
      setGeneralError(err.message || 'Failed to delete message');
    }
  };

  // Retry stopped/failed run
  const handleRetry = (msgId) => {
    if (isRunning) return;
    // Find preceding user question
    const idx = messages.findIndex((m) => m.id === msgId);
    if (idx > 0 && messages[idx - 1]?.role === 'user') {
      handleSubmit(messages[idx - 1].content);
    } else if (lastQuery) {
      handleSubmit(lastQuery);
    }
  };

  // Active message selection for trace inspection
  const activeMessage = useMemo(() => {
    if (selectedMessageId) {
      const found = messages.find((m) => m.id === selectedMessageId);
      if (found) return found;
    }
    // Default to the last assistant message
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant' && messages[i].payload) {
        return messages[i];
      }
    }
    return null;
  }, [messages, selectedMessageId]);

  // Active trace data (live or saved)
  const activeTrace = useMemo(() => {
    if (isRunning && liveRunData) {
      return liveRunData;
    }
    if (activeMessage && activeMessage.payload) {
      return activeMessage.payload;
    }
    return null;
  }, [isRunning, liveRunData, activeMessage]);

  return (
    <div className="flex h-[calc(100vh-3.5rem-3rem)] gap-4">
      {/* =========================================================================
          LEFT / CENTER: Chat Thread
      ========================================================================= */}
      <div className="flex-1 flex flex-col bg-white border border-[#E2E8F0] rounded-lg overflow-hidden min-w-0 shadow-none">
        {/* General Error banner */}
        {generalError && (
          <div className="p-3 bg-[#FEF2F2] border-b border-[#FCA5A5] text-[#DC2626] text-xs flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 shrink-0 text-[#DC2626]" />
              <span>{generalError}</span>
            </div>
            <button
              type="button"
              onClick={() => setGeneralError(null)}
              className="text-[#DC2626] font-semibold text-xs hover:underline"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Message Thread Scroll Area */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-white">
          {loadingMessages ? (
            <div className="space-y-4 py-6">
              <div className="flex justify-end">
                <Skeleton className="w-1/3 h-10 rounded-xl" />
              </div>
              <div className="flex justify-start">
                <Skeleton className="w-2/3 h-32 rounded-xl" />
              </div>
            </div>
          ) : messages.length === 0 ? (
            /* Empty State: Example Questions */
            <div className="py-12 flex flex-col items-center justify-center text-center max-w-lg mx-auto">
              <div className="w-12 h-12 rounded-full bg-[#EFF6FF] border border-[#2563EB]/20 flex items-center justify-center text-[#2563EB] mb-4 shadow-none">
                <Send className="w-5 h-5 ml-0.5" />
              </div>
              <h3 className="text-base font-semibold text-[#0F172A] mb-1">
                Solar Fleet Intelligence Agent
              </h3>
              <p className="text-xs text-[#64748B] mb-6 max-w-sm">
                Ask natural questions about plant performance ratio, fault diagnosis, peer anomalies, or soiling loss.
              </p>

              <div className="w-full space-y-2">
                <div className="text-[11px] font-semibold text-[#64748B] uppercase tracking-wider text-left pl-1">
                  Suggested Questions
                </div>
                {EXAMPLE_QUESTIONS.map((q, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => handleSubmit(q)}
                    className="w-full text-left p-3 rounded-lg border border-[#E2E8F0] bg-[#F8FAFC] hover:bg-[#EFF6FF] hover:border-[#2563EB] text-xs text-[#0F172A] hover:text-[#2563EB] transition-colors flex items-center justify-between group shadow-none"
                  >
                    <span>{q}</span>
                    <ArrowRight className="w-3.5 h-3.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0 text-[#2563EB]" />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            /* Render Messages */
            messages.map((msg, idx) => {
              if (msg.role === 'user') {
                const isEditing = editingMessageId === msg.id;

                return (
                  <div key={msg.id || idx} className="flex flex-col items-end group">
                    {isEditing ? (
                      /* Inline Edit Form */
                      <div className="w-full max-w-xl bg-white border border-[#2563EB] rounded-xl p-3 shadow-sm space-y-2">
                        <textarea
                          rows={2}
                          value={editQuery}
                          onChange={(e) => setEditQuery(e.target.value)}
                          className="w-full text-sm text-[#0F172A] p-2 bg-[#F8FAFC] border border-[#E2E8F0] rounded-lg focus:outline-none focus:border-[#2563EB] resize-none"
                        />
                        <div className="flex items-center justify-end gap-2 text-xs">
                          <button
                            type="button"
                            onClick={() => setEditingMessageId(null)}
                            className="px-2.5 py-1 rounded text-[#64748B] hover:text-[#0F172A]"
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            onClick={() => handleSaveAndRerun(msg.id)}
                            disabled={!editQuery.trim()}
                            className="px-3 py-1 bg-[#2563EB] text-white rounded font-medium hover:bg-[#1D4ED8] disabled:opacity-50"
                          >
                            Save & Rerun
                          </button>
                        </div>
                      </div>
                    ) : (
                      /* User Bubble */
                      <div className="flex items-center gap-1.5 max-w-[85%]">
                        {/* Hover Action Menu */}
                        <div className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1">
                          <button
                            type="button"
                            onClick={() => handleStartEdit(msg)}
                            disabled={isRunning}
                            className="p-1 rounded hover:bg-[#F1F5F9] text-[#64748B] hover:text-[#0F172A] transition-colors disabled:opacity-40"
                            title="Edit message"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmDeleteMessageId(msg.id)}
                            disabled={isRunning}
                            className="p-1 rounded hover:bg-[#FEE2E2] text-[#64748B] hover:text-[#DC2626] transition-colors disabled:opacity-40"
                            title="Delete question & answer"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>

                        <div className="bg-[#2563EB] text-white text-sm px-4 py-2.5 rounded-2xl rounded-tr-sm shadow-none leading-relaxed">
                          {msg.content}
                        </div>
                      </div>
                    )}

                    {/* Delete Confirmation Modal inline */}
                    {confirmDeleteMessageId === msg.id && (
                      <div className="mt-1.5 p-2 bg-[#FFFBEB] border border-[#D97706]/30 rounded-lg text-xs flex items-center gap-2">
                        <span className="text-[#B45309]">Delete question and its response?</span>
                        <button
                          type="button"
                          onClick={() => handleDeletePair(msg.id)}
                          className="font-semibold text-[#DC2626] hover:underline"
                        >
                          Confirm
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirmDeleteMessageId(null)}
                          className="text-[#64748B] hover:underline"
                        >
                          Cancel
                        </button>
                      </div>
                    )}
                  </div>
                );
              }

              // Assistant message
              const isWorking = msg.status === 'running';
              const isStopped = msg.status === 'stopped';
              const isError = msg.status === 'error';
              const payload = msg.payload || {};

              return (
                <div
                  key={msg.id || idx}
                  className="bg-white border border-[#E2E8F0] rounded-xl p-4 shadow-none space-y-3.5 w-full max-w-full min-w-0 overflow-hidden"
                >
                  {isWorking ? (
                    /* Working Card with Live Plan Steps & Skeletons */
                    <div className="space-y-3">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full bg-[#2563EB] animate-ping" />
                          <span className="text-xs font-semibold text-[#2563EB]">
                            Agent working on plan...
                          </span>
                        </div>
                        <span className="text-xs text-[#64748B] font-mono">
                          {liveRunData?.llm_calls?.total || 0} / 12 calls
                        </span>
                      </div>

                      {liveRunData?.plan && liveRunData.plan.length > 0 && (
                        <div className="space-y-1.5 pt-1">
                          <div className="text-[11px] font-semibold text-[#64748B]">Live Plan Steps</div>
                          <div className="space-y-1">
                            {liveRunData.plan.map((step, sIdx) => {
                              let dot = 'bg-[#94A3B8]';
                              if (step.status === 'done') dot = 'bg-[#16A34A]';
                              else if (step.status === 'running') dot = 'bg-[#D97706] animate-pulse';
                              else if (step.status === 'failed') dot = 'bg-[#DC2626]';

                              return (
                                <div
                                  key={sIdx}
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
                  ) : (() => {
                    // Check for Replay cache miss error_code='not_recorded'
                    const isNotRecorded =
                      payload.error_code === 'not_recorded' ||
                      payload.error?.error_code === 'not_recorded';

                    if (isNotRecorded) {
                      return (
                        <div className="p-4 rounded-xl border border-[#E2E8F0] bg-white shadow-sm space-y-3">
                          <div className="flex items-center gap-2.5 text-[#0F172A]">
                            <Info className="w-5 h-5 text-[#2563EB] shrink-0" />
                            <span className="font-semibold text-sm">
                              This question hasn’t been recorded. Switch to Live to ask it
                            </span>
                          </div>
                          <button
                            type="button"
                            onClick={() => handleSwitchToLiveAndRetry(msg)}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-[#2563EB] text-white hover:bg-[#1D4ED8] transition-colors shadow-sm"
                          >
                            <Play className="w-3.5 h-3.5 fill-current" />
                            <span>Switch to Live and retry</span>
                          </button>
                        </div>
                      );
                    }

                    if (isError) {
                      return (
                        <ErrorCard
                          title={payload.error?.type || 'Execution Error'}
                          message={payload.error?.message || msg.content || 'An error occurred during query execution.'}
                          onRetry={() => handleRetry(msg.id)}
                        />
                      );
                    }

                    const headlineText = payload.headline || payload.short_answer || msg.content || '';
                    const explanationText = payload.explanation || payload.details_markdown || '';
                    const notesList = (payload.notes && payload.notes.length > 0) ? payload.notes : (payload.assumptions || []);

                    return (
                      <div className="space-y-3.5">
                        {/* Stopped Banner if stopped */}
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

                        {/* 1. headline (bold, 18px, dark text) */}
                        {headlineText && (
                          <h3 className="text-[18px] font-bold text-[#0F172A] leading-snug">
                            <GlossaryText text={headlineText} glossary={payload.glossary} />
                          </h3>
                        )}

                        {/* 2. explanation (muted, normal weight) */}
                        {explanationText && (
                          <p className="text-sm font-normal text-[#64748B] leading-relaxed">
                            <GlossaryText text={explanationText} glossary={payload.glossary} />
                          </p>
                        )}

                        {/* 3. the table(s): grey header, right-aligned numbers with tabular-nums, hover row, main number column in bold, wide tables stay in card with scroll fallback */}
                        {payload.tables && payload.tables.length > 0 && (() => {
                          const isOverview =
                            payload.intent === 'overview' ||
                            payload.tables.some(
                              (t) => t.type === 'blocks' || (t.title && t.title.toLowerCase().includes('block'))
                            );

                          const renderSingleTable = (table, key) => {
                            const mainColIdx = table.columns?.findIndex((c) => isMainColumn(c));
                            return (
                              <div
                                key={key}
                                className="border border-[#E2E8F0] rounded-lg overflow-hidden bg-white shadow-sm w-full max-w-full min-w-0"
                              >
                                {table.title && (
                                  <div className="px-3.5 py-2 bg-[#F8FAFC] border-b border-[#E2E8F0] text-xs font-semibold text-[#0F172A] flex items-center justify-between">
                                    <span>{table.title}</span>
                                    {table.rows && (
                                      <span className="text-[11px] font-normal text-[#64748B]">
                                        {table.rows.length} {table.rows.length === 1 ? 'row' : 'rows'}
                                      </span>
                                    )}
                                  </div>
                                )}
                                <div className="overflow-x-auto max-w-full">
                                  <table className="w-full border-collapse text-xs">
                                    <thead className="bg-[#F1F5F9] border-b border-[#E2E8F0] text-[#475569]">
                                      <tr>
                                        {table.columns.map((col, cIdx) => (
                                          <th
                                            key={cIdx}
                                            className="px-3.5 py-2.5 text-left font-semibold whitespace-nowrap text-[#475569]"
                                          >
                                            <GlossaryText text={col} glossary={payload.glossary} />
                                          </th>
                                        ))}
                                      </tr>
                                    </thead>
                                    <tbody className="divide-y divide-[#E2E8F0]">
                                      {table.rows.map((row, rIdx) => (
                                        <tr key={rIdx} className="hover:bg-[#F8FAFC] transition-colors">
                                          {row.map((val, vIdx) => {
                                            const isNum = isNumericValue(val);
                                            const isMain = vIdx === mainColIdx;
                                            return (
                                              <td
                                                key={vIdx}
                                                className={`px-3.5 py-2.5 ${
                                                  isNum
                                                    ? 'text-right font-mono tabular-nums text-[#0F172A]'
                                                    : 'text-left text-[#0F172A]'
                                                } ${isMain ? 'font-bold' : ''} whitespace-nowrap`}
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
                                {table.notes && table.notes.length > 0 && (
                                  <div className="px-3.5 py-1.5 bg-[#F8FAFC] border-t border-[#E2E8F0] text-[11px] text-[#64748B]">
                                    {table.notes.join(' ')}
                                  </div>
                                )}
                              </div>
                            );
                          };

                          if (isOverview) {
                            const plantsTable = payload.tables.find(
                              (t) => t.type === 'plants' || (t.title && t.title.toLowerCase().includes('plant'))
                            );
                            const blocksTable = payload.tables.find(
                              (t) => t.type === 'blocks' || (t.title && t.title.toLowerCase().includes('block'))
                            );
                            const otherTables = payload.tables.filter(
                              (t) => t !== plantsTable && t !== blocksTable
                            );
                            const blockCount = blocksTable?.rows?.length || 0;
                            const isBlocksOpen = Boolean(expandedBlocks[msg.id]);

                            return (
                              <div className="space-y-3 max-w-full min-w-0">
                                {/* 1. Plants table shown first */}
                                {plantsTable && renderSingleTable(plantsTable, 'plants')}

                                {/* 2. Blocks table in a collapsed "Show blocks (N)" section */}
                                {blocksTable && (
                                  <div className="border border-[#E2E8F0] rounded-lg overflow-hidden bg-white shadow-sm w-full max-w-full min-w-0">
                                    <button
                                      type="button"
                                      onClick={() => {
                                        setExpandedBlocks((prev) => ({
                                          ...prev,
                                          [msg.id]: !prev[msg.id],
                                        }));
                                      }}
                                      className="w-full px-3.5 py-2.5 bg-[#F8FAFC] hover:bg-[#F1F5F9] transition-colors flex items-center justify-between text-xs font-semibold text-[#0F172A]"
                                    >
                                      <span className="flex items-center gap-1.5">
                                        <ChevronDown
                                          className={`w-3.5 h-3.5 text-[#64748B] transition-transform duration-200 ${
                                            isBlocksOpen ? 'rotate-180' : ''
                                          }`}
                                        />
                                        <span>
                                          {isBlocksOpen
                                            ? `Hide blocks (${blockCount})`
                                            : `Show blocks (${blockCount})`}
                                        </span>
                                      </span>
                                      <span className="text-[11px] font-normal text-[#64748B]">
                                        {isBlocksOpen ? 'Click to collapse' : 'Click to expand'}
                                      </span>
                                    </button>
                                    {isBlocksOpen && (
                                      <div className="border-t border-[#E2E8F0]">
                                        {renderSingleTable(blocksTable, 'blocks')}
                                      </div>
                                    )}
                                  </div>
                                )}

                                {/* Any other analysis tables */}
                                {otherTables.map((t, oIdx) => renderSingleTable(t, `other-${oIdx}`))}
                              </div>
                            );
                          }

                          return (
                            <div className="space-y-3 max-w-full min-w-0">
                              {payload.tables.map((table, tIdx) => renderSingleTable(table, tIdx))}
                            </div>
                          );
                        })()}

                        {/* Plotly Charts if present */}
                        {payload.charts && payload.charts.length > 0 && (
                          <div className="space-y-3 pt-1">
                            {payload.charts.map((chartSpec, cIdx) => (
                              <PlotlyChart key={cIdx} figureJson={chartSpec} />
                            ))}
                          </div>
                        )}

                        {/* 4. key_points as bullets */}
                        {payload.key_points && payload.key_points.length > 0 && (
                          <ul className="list-disc pl-5 space-y-1.5 text-sm text-[#0F172A]">
                            {payload.key_points.map((kp, kIdx) => (
                              <li key={kIdx} className="leading-relaxed">
                                <GlossaryText text={kp} glossary={payload.glossary} />
                              </li>
                            ))}
                          </ul>
                        )}

                        {/* 5. notes in small muted text under a “Good to know” label */}
                        {notesList && notesList.length > 0 && (
                          <div className="pt-2 text-xs text-[#64748B] space-y-1 border-t border-[#F1F5F9]">
                            <span className="font-semibold text-[#64748B] uppercase tracking-wider text-[11px]">
                              Good to know
                            </span>
                            <ul className="list-disc pl-4 space-y-1 text-[#64748B]">
                              {notesList.map((note, nIdx) => (
                                <li key={nIdx}>
                                  <GlossaryText text={note} glossary={payload.glossary} />
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {/* 6. a warnings banner only if present */}
                        {payload.warnings && payload.warnings.length > 0 && (
                          <div className="p-3 rounded-lg bg-[#FFFBEB] border border-[#D97706]/30 text-[#D97706] text-xs space-y-1">
                            <div className="font-semibold flex items-center gap-1.5 text-[#B45309]">
                              <AlertTriangle className="w-3.5 h-3.5 shrink-0 text-[#D97706]" />
                              <span>Warnings:</span>
                            </div>
                            <ul className="list-disc pl-4 space-y-0.5 text-[#B45309]">
                              {payload.warnings.map((w, wIdx) => (
                                <li key={wIdx}>
                                  <GlossaryText text={w} glossary={payload.glossary} />
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {/* Clickable suggestion chips from response (replaces static chips) */}
                        {payload.suggestions && payload.suggestions.length > 0 && (
                          <div className="pt-2 flex flex-wrap items-center gap-1.5 border-t border-[#E2E8F0]">
                            <span className="text-xs text-[#64748B] font-medium mr-1">
                              {payload.is_out_of_scope ? 'Suggested:' : 'Follow up:'}
                            </span>
                            {payload.suggestions.map((chip, fIdx) => (
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
                        )}

                        {/* 7. Footer line with period, inspect trace, time */}
                        <div className="pt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-[#64748B] border-t border-[#E2E8F0]">
                          <div className="flex items-center gap-2">
                            {payload.period && (
                              <span className="flex items-center gap-1.5">
                                <Calendar className="w-3.5 h-3.5 text-[#94A3B8]" />
                                <span>
                                  <strong className="text-[#0F172A] font-medium">Period analysed:</strong>{' '}
                                  {payload.period.start} → {payload.period.end}
                                </span>
                              </span>
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
                      </div>
                    );
                  })()}
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
        <aside className="w-[320px] min-w-[320px] max-w-[320px] flex flex-col h-full bg-white border border-[#E2E8F0] rounded-lg overflow-hidden shrink-0 shadow-none">
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
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="py-4 text-center text-xs text-[#64748B]">
                      No plan steps recorded for this answer.
                    </div>
                  )}
                </div>

                {/* Collapsible Technical Details Section */}
                <div className="border border-[#E2E8F0] rounded-lg overflow-hidden bg-white">
                  <button
                    type="button"
                    onClick={() => setIsTechDetailsOpen(!isTechDetailsOpen)}
                    className="w-full px-3 py-2 bg-[#F8FAFC] flex items-center justify-between text-xs font-semibold text-[#0F172A] hover:bg-[#F1F5F9] transition-colors"
                  >
                    <span className="flex items-center gap-1.5">
                      <Code2 className="w-3.5 h-3.5 text-[#64748B]" />
                      <span>Technical details</span>
                    </span>
                    <ChevronDown
                      className={`w-3.5 h-3.5 text-[#64748B] transition-transform duration-200 ${
                        isTechDetailsOpen ? 'rotate-180' : ''
                      }`}
                    />
                  </button>

                  {isTechDetailsOpen && (
                    <div className="p-3 space-y-3 text-xs bg-white border-t border-[#E2E8F0]">
                      {/* Task IDs & Tools */}
                      {activeTrace.plan && activeTrace.plan.length > 0 && (
                        <div className="space-y-1">
                          <span className="text-[10px] font-semibold text-[#64748B] uppercase tracking-wider">
                            Tasks & Tools
                          </span>
                          <div className="space-y-1">
                            {activeTrace.plan.map((step, idx) => (
                              <div
                                key={idx}
                                className="flex items-center justify-between font-mono text-[11px] p-1.5 rounded bg-[#F8FAFC] border border-[#E2E8F0]/60"
                              >
                                <span className="text-[#0F172A] font-medium">{step.id || `Step ${idx + 1}`}</span>
                                <span className="text-[#2563EB]">{step.tool || step.agent}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Row Counts */}
                      {activeTrace.trace?.row_counts &&
                        Object.keys(activeTrace.trace.row_counts).length > 0 && (
                          <div className="space-y-1">
                            <span className="text-[10px] font-semibold text-[#64748B] uppercase tracking-wider">
                              Row Counts
                            </span>
                            <div className="space-y-1">
                              {Object.entries(activeTrace.trace.row_counts).map(([tid, count]) => (
                                <div
                                  key={tid}
                                  className="flex items-center justify-between font-mono text-[11px] p-1.5 rounded bg-[#F8FAFC] border border-[#E2E8F0]/60"
                                >
                                  <span className="text-[#64748B]">Task {tid}</span>
                                  <span className="font-semibold text-[#0F172A]">{count} rows</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                      {/* Data References */}
                      {activeTrace.trace?.data_refs &&
                        Object.keys(activeTrace.trace.data_refs).length > 0 && (
                          <div className="space-y-1">
                            <span className="text-[10px] font-semibold text-[#64748B] uppercase tracking-wider">
                              Data References
                            </span>
                            <div className="space-y-1">
                              {Object.entries(activeTrace.trace.data_refs).map(([tid, ref]) => (
                                <div
                                  key={tid}
                                  className="flex items-center justify-between font-mono text-[11px] p-1.5 rounded bg-[#F8FAFC] border border-[#E2E8F0]/60"
                                >
                                  <span className="text-[#64748B]">Task {tid}</span>
                                  <span className="text-[#2563EB] truncate max-w-[130px]" title={ref}>
                                    {ref}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                      {/* Executed Tools */}
                      {activeTrace.sources && activeTrace.sources.length > 0 && (
                        <div className="space-y-1">
                          <span className="text-[10px] font-semibold text-[#64748B] uppercase tracking-wider">
                            Tools Executed
                          </span>
                          <div className="flex flex-wrap gap-1">
                            {activeTrace.sources.map((src, sIdx) => (
                              <span
                                key={sIdx}
                                className="px-1.5 py-0.5 rounded bg-[#F1F5F9] text-[#64748B] font-mono text-[10px] border border-[#E2E8F0]"
                              >
                                {src}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
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
            className="p-2 bg-white border border-[#E2E8F0] rounded-lg text-[#64748B] hover:text-[#0F172A] hover:border-[#2563EB] transition-colors shadow-none"
            title="Expand trace panel"
          >
            <PanelRightOpen className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  );
}
