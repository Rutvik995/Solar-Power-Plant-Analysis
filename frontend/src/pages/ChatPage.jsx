import React, { useState, useRef, useEffect, useMemo } from 'react';
import { useOutletContext } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { sendChatQuery } from '../services/api';
import Skeleton from '../components/common/Skeleton';
import PlotlyChart from '../components/common/PlotlyChart';
import ErrorCard from '../components/common/ErrorCard';

import {
  Send,
  AlertTriangle,
  PanelRightClose,
  PanelRightOpen,
  ArrowRight,
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
  const outletContext = useOutletContext() || {};
  const mode = outletContext.mode || 'replay';
  const newChatTrigger = outletContext.newChatTrigger || 0;

  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [lastQuery, setLastQuery] = useState('');
  const [selectedMessageId, setSelectedMessageId] = useState(null);
  const [isPanelOpen, setIsPanelOpen] = useState(true);

  // Thread ID persisted in localStorage
  const [threadId, setThreadId] = useState(() => {
    let id = localStorage.getItem('solar_chat_thread_id');
    if (!id) {
      id = 'thread_' + Math.random().toString(36).substring(2, 10);
      localStorage.setItem('solar_chat_thread_id', id);
    }
    return id;
  });

  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading]);

  // Handle New Chat triggered from TopBar
  const prevTriggerRef = useRef(newChatTrigger);
  useEffect(() => {
    if (newChatTrigger !== prevTriggerRef.current) {
      prevTriggerRef.current = newChatTrigger;
      handleNewChat();
    }
  }, [newChatTrigger]);

  const handleNewChat = () => {
    const newId = 'thread_' + Math.random().toString(36).substring(2, 10);
    localStorage.setItem('solar_chat_thread_id', newId);
    setThreadId(newId);
    setMessages([]);
    setLastQuery('');
    setSelectedMessageId(null);
  };

  const handleSubmit = async (queryText) => {
    const q = (queryText || input).trim();
    if (!q || loading) return;

    setLastQuery(q);
    const userMessage = {
      id: 'usr_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
      role: 'user',
      content: q,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setLoading(true);

    try {
      const response = await sendChatQuery({
        query: q,
        threadId,
        mode,
      });

      // Store the trace on each assistant message
      const trace = {
        plan: response.plan || [],
        llm_calls: response.llm_calls || { total: 0, budget: 12, planner: 0, agent: 0, synthesizer: 0 },
        elapsed_ms: response.elapsed_ms || null,
      };

      const msgId = 'asst_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
      const assistantMessage = {
        id: msgId,
        role: 'assistant',
        queryAsked: q,
        data: response,
        trace,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };

      setMessages((prev) => [...prev, assistantMessage]);
      setSelectedMessageId(msgId);
    } catch (err) {
      const isBudget =
        err.status === 429 ||
        (err.message && err.message.includes('429')) ||
        (err.message && err.message.toLowerCase().includes('budget'));

      const msgId = 'err_' + Date.now();
      const assistantMessage = {
        id: msgId,
        role: 'assistant',
        queryAsked: q,
        error: err.data?.error || {
          code: err.status || 429,
          type: isBudget ? 'RateLimitOrBudgetExceeded' : 'ExecutionError',
          message: err.message || 'An error occurred during query execution.',
        },
        trace: {
          plan: [],
          llm_calls: { total: 0, budget: 12, planner: 0, agent: 0, synthesizer: 0 },
          elapsed_ms: null,
        },
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };

      setMessages((prev) => [...prev, assistantMessage]);
      setSelectedMessageId(msgId);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  // Find trace of the selected answer, defaulting to the latest assistant message
  const activeMessage = useMemo(() => {
    if (selectedMessageId) {
      const found = messages.find((m) => m.id === selectedMessageId);
      if (found && found.role === 'assistant') return found;
    }
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant' && messages[i].trace) {
        return messages[i];
      }
    }
    return null;
  }, [messages, selectedMessageId]);

  const activeTrace = activeMessage?.trace || null;

  return (
    <div className="flex gap-5 h-[calc(100vh-6.5rem)] max-w-7xl mx-auto">
      {/* =========================================================================
          LEFT / MAIN COLUMN: Chat Interface
      ========================================================================= */}
      <div className="flex-1 flex flex-col h-full bg-white border border-[#E2E8F0] rounded-lg overflow-hidden min-w-0">
        {/* Chat message stream */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5 bg-white">
          {messages.length === 0 ? (
            /* Empty State: Heading + 5 Example Chips */
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
            messages.map((msg) => (
              <div key={msg.id} className="space-y-3">
                {msg.role === 'user' ? (
                  /* User message: right-aligned, tint background, no avatar */
                  <div className="flex justify-end">
                    <div className="max-w-xl bg-[#EFF6FF] border border-[#2563EB]/20 rounded-lg px-4 py-3">
                      <p className="text-sm text-[#0F172A] whitespace-pre-wrap leading-relaxed">
                        {msg.content}
                      </p>
                      <div className="flex justify-end mt-1 text-[11px] text-[#64748B]">
                        {msg.timestamp}
                      </div>
                    </div>
                  </div>
                ) : (
                  /* Assistant message: white bordered card, no avatar */
                  <div className="bg-white border border-[#E2E8F0] rounded-lg p-5 space-y-4">
                    {msg.error ? (
                      <ErrorCard
                        title={msg.error.type || 'Execution Error'}
                        message={msg.error.message}
                        onRetry={() => handleSubmit(msg.queryAsked || lastQuery)}
                      />
                    ) : (
                      <>
                        {/* 1. short_answer in semibold 16px */}
                        {msg.data?.short_answer && (
                          <div className="text-base font-semibold text-[#0F172A] leading-snug">
                            {msg.data.short_answer}
                          </div>
                        )}

                        {/* 2. Each table from tables as a clean HTML table */}
                        {msg.data?.tables && msg.data.tables.length > 0 && (
                          <div className="space-y-3">
                            {msg.data.tables.map((table, tIdx) => (
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
                                        <tr
                                          key={rIdx}
                                          className="hover:bg-[#F8FAFC] transition-colors"
                                        >
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

                        {/* 3. details_markdown (rendered with remark-gfm) */}
                        {msg.data?.details_markdown ? (
                          <div className="prose prose-sm max-w-none text-[#0F172A] leading-relaxed text-sm">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                              {msg.data.details_markdown}
                            </ReactMarkdown>
                          </div>
                        ) : msg.data?.answer_markdown ? (
                          <div className="prose prose-sm max-w-none text-[#0F172A] leading-relaxed text-sm">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                              {msg.data.answer_markdown}
                            </ReactMarkdown>
                          </div>
                        ) : null}

                        {/* 4. assumptions in small muted text */}
                        {msg.data?.assumptions && msg.data.assumptions.length > 0 && (
                          <div className="text-xs text-[#64748B] space-y-1 pt-1">
                            <span className="font-medium text-[#64748B]">Assumptions:</span>
                            <ul className="list-disc pl-4 space-y-0.5">
                              {msg.data.assumptions.map((a, aIdx) => (
                                <li key={aIdx}>{a}</li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {/* 5. warnings in a warning-coloured banner (only if present) */}
                        {msg.data?.warnings && msg.data.warnings.length > 0 && (
                          <div className="p-3 rounded-lg bg-[#FFFBEB] border border-[#D97706]/30 text-[#D97706] text-xs space-y-1">
                            <div className="font-semibold flex items-center gap-1.5">
                              <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                              <span>Warnings:</span>
                            </div>
                            <ul className="list-disc pl-4 space-y-0.5 text-[#B45309]">
                              {msg.data.warnings.map((w, wIdx) => (
                                <li key={wIdx}>{w}</li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {/* Plotly charts on white cards */}
                        {msg.data?.charts && msg.data.charts.length > 0 && (
                          <div className="space-y-3 pt-1">
                            {msg.data.charts.map((chartSpec, cIdx) => (
                              <PlotlyChart key={cIdx} figureJson={chartSpec} />
                            ))}
                          </div>
                        )}

                        {/* Follow-up question chips */}
                        <div className="pt-2 flex flex-wrap items-center gap-1.5 border-t border-[#E2E8F0]">
                          <span className="text-xs text-[#64748B] font-medium mr-1">
                            Follow up:
                          </span>
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

                        {/* 6. Footer with "Period analysed" and source chips */}
                        <div className="pt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-[#64748B] border-t border-[#E2E8F0]">
                          <div className="flex flex-wrap items-center gap-3">
                            {msg.data?.period && (
                              <span>
                                <strong className="text-[#0F172A] font-medium">Period analysed:</strong>{' '}
                                {msg.data.period.start} → {msg.data.period.end}
                              </span>
                            )}
                            {msg.data?.sources && msg.data.sources.length > 0 && (
                              <div className="flex items-center gap-1.5">
                                <span>Sources:</span>
                                {msg.data.sources.map((src, sIdx) => (
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
                            <span className="text-[11px] text-[#64748B]">{msg.timestamp}</span>
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                )}
              </div>
            ))
          )}

          {/* Loading Skeleton */}
          {loading && (
            <div className="bg-white border border-[#E2E8F0] rounded-lg p-5 space-y-3">
              <div className="flex items-center gap-2 text-xs text-[#0F172A] font-medium">
                <span className="w-2 h-2 rounded-full bg-[#2563EB] animate-ping" />
                <span>Running multi-agent analysis...</span>
              </div>
              <Skeleton className="w-3/4 h-4 bg-[#E2E8F0]" />
              <Skeleton className="w-full h-4 bg-[#E2E8F0]" />
              <Skeleton className="w-2/3 h-4 bg-[#E2E8F0]" />
            </div>
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
              placeholder="Ask a question about solar plant telemetry (Enter to send)..."
              className="flex-1 bg-white border border-[#E2E8F0] rounded-lg px-3.5 py-2 text-sm text-[#0F172A] placeholder:text-[#64748B] focus:outline-none focus:border-[#2563EB] resize-none transition-colors"
            />
            <button
              type="button"
              onClick={() => handleSubmit()}
              disabled={!input.trim() || loading}
              className="px-4 py-2 rounded-lg bg-[#2563EB] hover:bg-[#1D4ED8] text-white font-medium text-sm inline-flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Send className="w-4 h-4" />
              <span>Send</span>
            </button>
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
                  {activeMessage.timestamp}
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
                {/* Progress bar out of 12 */}
                <div className="space-y-1.5 bg-[#F8FAFC] border border-[#E2E8F0] rounded-lg p-3">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium text-[#0F172A]">LLM Call Budget</span>
                    <span className="font-mono text-xs font-semibold text-[#2563EB]">
                      {activeTrace.llm_calls?.total || 0} / 12
                    </span>
                  </div>

                  <div className="w-full bg-[#E2E8F0] rounded-full h-2 overflow-hidden">
                    <div
                      className={`h-full transition-all duration-300 ${
                        (activeTrace.llm_calls?.total || 0) >= 12
                          ? 'bg-[#DC2626]'
                          : (activeTrace.llm_calls?.total || 0) >= 9
                          ? 'bg-[#D97706]'
                          : 'bg-[#2563EB]'
                      }`}
                      style={{
                        width: `${Math.min(
                          100,
                          (((activeTrace.llm_calls?.total || 0) / 12) * 100)
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

                {/* Plan steps as a vertical list with status dots & tool names */}
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
    </div>
  );
}
