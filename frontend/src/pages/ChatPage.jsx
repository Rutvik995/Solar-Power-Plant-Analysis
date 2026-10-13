import React, { useState, useRef, useEffect, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import { sendChatQuery } from '../services/api';
import Card from '../components/common/Card';
import Badge from '../components/common/Badge';
import Skeleton from '../components/common/Skeleton';
import PlotlyChart from '../components/common/PlotlyChart';
import ErrorCard from '../components/common/ErrorCard';

import {
  Send,
  Sparkles,
  Bot,
  User,
  Cpu,
  Layers,
  Table as TableIcon,
  BarChart3,
  AlertTriangle,
  RotateCcw,
  Clock,
  CheckCircle2,
  AlertCircle,
  HelpCircle,
  ArrowRight,
  Database,
  Workflow,
  ChevronDown,
  ChevronUp,
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

export default function ChatPage() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [mode, setMode] = useState('replay'); // 'replay' | 'live'
  const [loading, setLoading] = useState(false);
  const [lastQuery, setLastQuery] = useState('');
  const [expandedTables, setExpandedTables] = useState({});

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

  const handleNewChat = () => {
    const newId = 'thread_' + Math.random().toString(36).substring(2, 10);
    localStorage.setItem('solar_chat_thread_id', newId);
    setThreadId(newId);
    setMessages([]);
    setLastQuery('');
  };

  const handleSubmit = async (queryText) => {
    const q = (queryText || input).trim();
    if (!q || loading) return;

    setLastQuery(q);
    const userMessage = {
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

      const assistantMessage = {
        role: 'assistant',
        queryAsked: q,
        data: response,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err) {
      const isBudget = err.status === 429 || (err.message && err.message.includes('429')) || (err.message && err.message.toLowerCase().includes('budget'));
      const assistantMessage = {
        role: 'assistant',
        queryAsked: q,
        error: err.data?.error || {
          code: err.status || 429,
          type: isBudget ? 'RateLimitOrBudgetExceeded' : 'ExecutionError',
          message: err.message || 'An error occurred during query execution.',
        },
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };

      setMessages((prev) => [...prev, assistantMessage]);
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

  // Extract latest execution result for the right-hand "How it worked" panel
  const latestExecution = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant' && messages[i].data) {
        return messages[i].data;
      }
    }
    return null;
  }, [messages]);

  const toggleTableExpand = (msgIdx, tableIdx) => {
    const key = `${msgIdx}_${tableIdx}`;
    setExpandedTables((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 h-[calc(100vh-6.5rem)] max-w-7xl mx-auto">
      {/* =========================================================================
          LEFT COLUMN: Chat Interface (~65% width)
      ========================================================================= */}
      <div className="lg:col-span-8 flex flex-col h-full bg-surface border border-border rounded-xl shadow-sm overflow-hidden">
        {/* Top Session Bar */}
        <div className="px-5 py-3 border-b border-border flex items-center justify-between bg-card/40 shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="p-1.5 rounded-lg bg-primary/15 text-primary border border-primary/30">
              <Sparkles className="w-4 h-4" />
            </div>
            <div>
              <span className="text-xs font-semibold text-text">Session:</span>
              <span className="ml-1.5 font-mono text-[11px] text-muted bg-surface px-2 py-0.5 rounded border border-border">
                {threadId}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2.5">
            {/* Mode switch */}
            <div className="flex items-center bg-surface border border-border rounded-lg p-0.5 text-xs font-medium">
              <button
                onClick={() => setMode('replay')}
                className={`px-2.5 py-1 rounded-md transition-colors ${
                  mode === 'replay'
                    ? 'bg-primary text-bg font-semibold shadow-sm'
                    : 'text-muted hover:text-text'
                }`}
                title="Uses recorded offline golden responses"
              >
                Replay Mode
              </button>
              <button
                onClick={() => setMode('live')}
                className={`px-2.5 py-1 rounded-md transition-colors ${
                  mode === 'live'
                    ? 'bg-teal text-bg font-semibold shadow-sm'
                    : 'text-muted hover:text-text'
                }`}
                title="Executes live LLM calls via API"
              >
                Live API
              </button>
            </div>

            {/* New Chat Button */}
            <button
              onClick={handleNewChat}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-card border border-border hover:border-primary text-text hover:text-primary transition-all"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>New chat</span>
            </button>
          </div>
        </div>

        {/* Message Stream */}
        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {messages.length === 0 ? (
            /* Empty State: 5 Example Question Chips */
            <div className="h-full flex flex-col items-center justify-center text-center p-6 max-w-xl mx-auto">
              <div className="w-14 h-14 rounded-2xl bg-card border border-border flex items-center justify-center text-primary mb-3 shadow-md">
                <Bot className="w-7 h-7" />
              </div>
              <h3 className="text-base font-bold text-text mb-1">Solar Plant Operations Assistant</h3>
              <p className="text-xs text-muted mb-6 leading-relaxed">
                Ask natural language questions about generation, inverter faults, soiling, and weather impact. Numbers come directly from database tools and an automated grounding check flags mismatches.
              </p>

              <div className="w-full space-y-2 text-left">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-muted px-1">
                  Suggested Questions:
                </p>
                <div className="space-y-1.5">
                  {EXAMPLE_QUESTIONS.map((q, idx) => (
                    <button
                      key={idx}
                      onClick={() => handleSubmit(q)}
                      className="w-full p-2.5 rounded-lg bg-card hover:bg-card/80 border border-border text-xs text-text hover:text-primary transition-all flex items-center justify-between text-left group"
                    >
                      <span className="flex items-center gap-2">
                        <HelpCircle className="w-3.5 h-3.5 text-primary shrink-0" />
                        <span>{q}</span>
                      </span>
                      <ArrowRight className="w-3.5 h-3.5 text-muted group-hover:text-primary group-hover:translate-x-0.5 transition-all" />
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            messages.map((msg, index) => (
              <div key={index} className="space-y-3">
                {msg.role === 'user' ? (
                  /* User Bubble */
                  <div className="flex items-start gap-3 justify-end">
                    <div className="max-w-xl bg-primary/10 border border-primary/20 rounded-2xl rounded-tr-sm px-4 py-3 shadow-sm">
                      <p className="text-sm text-text leading-relaxed whitespace-pre-wrap">{msg.content}</p>
                      <div className="flex justify-end mt-1 text-[10px] text-muted">{msg.timestamp}</div>
                    </div>
                    <div className="w-8 h-8 rounded-lg bg-card border border-border flex items-center justify-center text-primary shrink-0 mt-0.5">
                      <User className="w-4 h-4" />
                    </div>
                  </div>
                ) : (
                  /* Assistant Bubble */
                  <div className="flex items-start gap-3">
                    <div className="w-8 h-8 rounded-lg bg-card border border-border flex items-center justify-center text-teal shrink-0 mt-0.5">
                      <Bot className="w-4 h-4" />
                    </div>

                    <div className="flex-1 bg-card border border-border rounded-2xl rounded-tl-sm p-5 space-y-4 shadow-sm">
                      {msg.error ? (
                        /* Error Card with Retry Button for 429 and Budget Errors */
                        <ErrorCard
                          title={msg.error.type || 'Execution Error'}
                          message={msg.error.message}
                          onRetry={() => handleSubmit(msg.queryAsked || lastQuery)}
                        />
                      ) : (
                        <>
                          {/* Yellow Warnings Banner if warnings exist */}
                          {msg.data?.warnings && msg.data.warnings.length > 0 && (
                            <div className="p-3 rounded-lg bg-warning/15 border border-warning/30 text-warning text-xs space-y-1">
                              <div className="font-semibold flex items-center gap-1.5">
                                <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                                <span>Grounding & Calibration Warnings:</span>
                              </div>
                              <ul className="list-disc pl-5 text-muted space-y-0.5">
                                {msg.data.warnings.map((w, wIdx) => (
                                  <li key={wIdx}>{w}</li>
                                ))}
                              </ul>
                            </div>
                          )}

                          {/* Markdown Narrative */}
                          <div className="prose prose-invert prose-sm max-w-none text-text leading-relaxed text-sm">
                            <ReactMarkdown>{msg.data?.answer_markdown || 'No answer generated.'}</ReactMarkdown>
                          </div>

                          {/* Embedded Data Tables directly under answer */}
                          {msg.data?.tables && msg.data.tables.length > 0 && (
                            <div className="space-y-2 pt-2 border-t border-border/40">
                              <span className="text-xs font-semibold text-muted uppercase tracking-wider flex items-center gap-1.5">
                                <TableIcon className="w-3.5 h-3.5 text-teal" />
                                Telemetry Tables ({msg.data.tables.length})
                              </span>

                              {msg.data.tables.map((table, tIdx) => {
                                const isExpanded = !!expandedTables[`${index}_${tIdx}`];
                                return (
                                  <div key={tIdx} className="bg-surface border border-border rounded-lg overflow-hidden text-xs">
                                    <button
                                      onClick={() => toggleTableExpand(index, tIdx)}
                                      className="w-full px-3 py-2 flex items-center justify-between text-left hover:bg-card/50 transition-colors font-medium text-text"
                                    >
                                      <span>{table.title}</span>
                                      {isExpanded ? (
                                        <ChevronUp className="w-3.5 h-3.5 text-muted" />
                                      ) : (
                                        <ChevronDown className="w-3.5 h-3.5 text-muted" />
                                      )}
                                    </button>

                                    {isExpanded && (
                                      <div className="overflow-x-auto border-t border-border/40 p-2 max-h-56">
                                        <table className="w-full text-left">
                                          <thead>
                                            <tr className="border-b border-border/80 text-muted">
                                              {table.columns.map((col, cIdx) => (
                                                <th key={cIdx} className="pb-1.5 px-2 font-medium whitespace-nowrap">
                                                  {col}
                                                </th>
                                              ))}
                                            </tr>
                                          </thead>
                                          <tbody className="divide-y divide-border/20">
                                            {table.rows.map((row, rIdx) => (
                                              <tr key={rIdx} className="hover:bg-card/40">
                                                {row.map((val, vIdx) => (
                                                  <td key={vIdx} className="py-1.5 px-2 font-mono text-muted whitespace-nowrap">
                                                    {val !== null ? String(val) : '—'}
                                                  </td>
                                                ))}
                                              </tr>
                                            ))}
                                          </tbody>
                                        </table>
                                      </div>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          )}

                          {/* Embedded Visualizations directly under answer */}
                          {msg.data?.charts && msg.data.charts.length > 0 && (
                            <div className="space-y-3 pt-2 border-t border-border/40">
                              <span className="text-xs font-semibold text-muted uppercase tracking-wider flex items-center gap-1.5">
                                <BarChart3 className="w-3.5 h-3.5 text-primary" />
                                Visualizations
                              </span>
                              {msg.data.charts.map((chartSpec, cIdx) => (
                                <div key={cIdx} className="bg-surface border border-border rounded-xl p-3">
                                  <PlotlyChart figureJson={chartSpec} />
                                </div>
                              ))}
                            </div>
                          )}

                          {/* Follow-up question chips under each answer */}
                          <div className="pt-2 border-t border-border/40 space-y-1.5">
                            <span className="text-[11px] font-semibold text-muted uppercase tracking-wider">
                              Follow-up questions:
                            </span>
                            <div className="flex flex-wrap gap-1.5">
                              {DEFAULT_FOLLOW_UPS.map((chip, fIdx) => (
                                <button
                                  key={fIdx}
                                  onClick={() => handleSubmit(chip)}
                                  className="text-xs px-2.5 py-1 rounded-md bg-surface hover:bg-surface/80 border border-border text-muted hover:text-primary transition-all text-left"
                                >
                                  {chip}
                                </button>
                              ))}
                            </div>
                          </div>

                          {/* "Period analysed / Sources" footer */}
                          <div className="pt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted border-t border-border/40">
                            <div className="flex items-center gap-3">
                              {msg.data?.period && (
                                <span>
                                  <strong>Period analysed:</strong> {msg.data.period.start} → {msg.data.period.end}
                                </span>
                              )}
                              {msg.data?.sources && msg.data.sources.length > 0 && (
                                <span>
                                  <strong>Sources:</strong> {msg.data.sources.join(', ')}
                                </span>
                              )}
                            </div>
                            <span>{msg.timestamp}</span>
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))
          )}

          {/* Loading Skeleton */}
          {loading && (
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-lg bg-card border border-border flex items-center justify-center text-teal shrink-0">
                <Bot className="w-4 h-4 animate-spin" />
              </div>
              <div className="flex-1 bg-card border border-border rounded-2xl rounded-tl-sm p-5 space-y-3">
                <div className="flex items-center gap-2 text-xs text-text font-medium">
                  <span className="w-2 h-2 rounded-full bg-primary animate-ping" />
                  <span>Planning and running multi-agent tools...</span>
                </div>
                <Skeleton className="w-3/4 h-4" />
                <Skeleton className="w-full h-4" />
                <Skeleton className="w-2/3 h-4" />
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input Bar */}
        <div className="p-3 border-t border-border bg-card/50">
          <div className="flex items-center gap-2">
            <textarea
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask a question about solar plant telemetry (press Enter to send)..."
              className="flex-1 bg-surface border border-border rounded-lg px-3.5 py-2.5 text-xs sm:text-sm text-text placeholder:text-muted/60 focus:outline-none focus:border-primary resize-none transition-colors"
            />
            <button
              onClick={() => handleSubmit()}
              disabled={!input.trim() || loading}
              className="px-4 py-2.5 rounded-lg bg-primary hover:bg-primary/90 text-bg font-semibold text-xs sm:text-sm inline-flex items-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed transition-all shadow-sm"
            >
              <Send className="w-4 h-4" />
              <span>Send</span>
            </button>
          </div>
        </div>
      </div>

      {/* =========================================================================
          RIGHT COLUMN: Always-Visible "How it worked" Panel (~35% width)
      ========================================================================= */}
      <div className="lg:col-span-4 flex flex-col h-full bg-surface border border-border rounded-xl shadow-sm overflow-hidden">
        {/* Panel Header */}
        <div className="px-5 py-3 border-b border-border flex items-center justify-between bg-card/40 shrink-0">
          <div className="flex items-center gap-2">
            <Cpu className="w-4 h-4 text-violet" />
            <h3 className="text-sm font-bold text-text">How it worked</h3>
          </div>
          <Badge variant="violet">LangGraph Trace</Badge>
        </div>

        {/* Panel Content (Scrollable) */}
        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {latestExecution ? (
            <>
              {/* LLM Call Budget Progress Bar */}
              <div className="bg-card border border-border rounded-xl p-4 space-y-3">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-semibold text-text">LLM Call Budget</span>
                  <span className="font-mono text-xs font-bold text-primary">
                    {latestExecution.llm_calls?.total || 0} / {latestExecution.llm_calls?.budget || 12}
                  </span>
                </div>

                {/* Progress Bar out of 12 */}
                <div className="w-full bg-surface rounded-full h-2 overflow-hidden border border-border/80">
                  <div
                    className={`h-full transition-all duration-500 ${
                      (latestExecution.llm_calls?.total || 0) >= 12
                        ? 'bg-danger'
                        : (latestExecution.llm_calls?.total || 0) >= 9
                        ? 'bg-warning'
                        : 'bg-primary'
                    }`}
                    style={{
                      width: `${Math.min(
                        100,
                        ((latestExecution.llm_calls?.total || 0) /
                          (latestExecution.llm_calls?.budget || 12)) *
                          100
                      )}%`,
                    }}
                  />
                </div>

                {/* Role Breakdown & Timings */}
                <div className="grid grid-cols-3 gap-2 pt-1 text-center text-[11px]">
                  <div className="p-2 rounded-lg bg-surface border border-border/60">
                    <div className="text-muted text-[10px]">Planner</div>
                    <div className="font-mono font-bold text-text mt-0.5">
                      {latestExecution.llm_calls?.planner || 0}
                    </div>
                  </div>
                  <div className="p-2 rounded-lg bg-surface border border-border/60">
                    <div className="text-muted text-[10px]">Agents</div>
                    <div className="font-mono font-bold text-text mt-0.5">
                      {latestExecution.llm_calls?.agent || 0}
                    </div>
                  </div>
                  <div className="p-2 rounded-lg bg-surface border border-border/60">
                    <div className="text-muted text-[10px]">Synthesizer</div>
                    <div className="font-mono font-bold text-text mt-0.5">
                      {latestExecution.llm_calls?.synthesizer || 0}
                    </div>
                  </div>
                </div>

                {latestExecution.elapsed_ms && (
                  <div className="flex items-center justify-between text-[11px] text-muted pt-1 border-t border-border/40">
                    <span className="flex items-center gap-1">
                      <Clock className="w-3 h-3 text-muted" /> Elapsed Time:
                    </span>
                    <span className="font-mono font-medium text-text">
                      {latestExecution.elapsed_ms} ms
                    </span>
                  </div>
                )}
              </div>

              {/* Plan Steps (DAG Tasks) */}
              <div className="bg-card border border-border rounded-xl p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <h4 className="text-xs font-semibold text-text flex items-center gap-1.5">
                    <Layers className="w-3.5 h-3.5 text-teal" />
                    <span>DAG Plan Steps</span>
                  </h4>
                  <span className="text-[11px] text-muted">
                    {latestExecution.plan?.length || 0} steps
                  </span>
                </div>

                {latestExecution.plan && latestExecution.plan.length > 0 ? (
                  <div className="space-y-2">
                    {latestExecution.plan.map((step) => {
                      let statusVariant = 'muted';
                      if (step.status === 'done') statusVariant = 'success';
                      if (step.status === 'failed') statusVariant = 'danger';
                      if (step.status === 'running') statusVariant = 'warning';

                      return (
                        <div
                          key={step.id}
                          className="p-2.5 rounded-lg bg-surface border border-border/70 space-y-1 text-xs"
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <span className="font-mono font-bold text-primary bg-primary/10 px-1.5 py-0.5 rounded text-[11px]">
                                {step.id}
                              </span>
                              <span className="font-semibold text-text uppercase text-[11px]">
                                {step.agent}
                              </span>
                            </div>
                            <Badge variant={statusVariant}>{step.status}</Badge>
                          </div>

                          <div className="text-[11px] text-muted flex items-center justify-between pt-0.5">
                            <span>
                              Tool: <code className="text-teal font-mono">{step.tool || 'auto'}</code>
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
                  <div className="py-4 text-center text-xs text-muted">
                    No task steps recorded for this query.
                  </div>
                )}
              </div>
            </>
          ) : (
            /* Empty State for "How it worked" Panel */
            <div className="h-full flex flex-col items-center justify-center text-center p-6 space-y-4">
              <div className="w-12 h-12 rounded-xl bg-card border border-border flex items-center justify-center text-muted">
                <Workflow className="w-6 h-6 text-muted/60" />
              </div>
              <div>
                <h4 className="text-xs font-semibold text-text mb-1">No Active Execution Trace</h4>
                <p className="text-[11px] text-muted leading-relaxed">
                  Submit a query to inspect the Orchestrator DAG plan, specialist tools invoked by agents, per-role LLM call counts, and execution latency.
                </p>
              </div>
              <div className="p-3 rounded-lg bg-card border border-border/60 text-left text-[11px] space-y-2 w-full">
                <div className="font-semibold text-text text-xs">Pipeline Lifecycle:</div>
                <div className="text-muted space-y-1">
                  <div>1. Orchestrator plans DAG task dependencies</div>
                  <div>2. Specialist Agents execute deterministic tools</div>
                  <div>3. Validator checks numbers & criteria</div>
                  <div>4. Synthesizer drafts grounded narrative</div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
