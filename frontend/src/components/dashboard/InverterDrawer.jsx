import React, { useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchDashboardInverterDetail, createSession } from '../../services/api';

import Badge from '../common/Badge';
import Skeleton from '../common/Skeleton';
import ErrorCard from '../common/ErrorCard';

import {
  X,
  Zap,
  Activity,
  AlertTriangle,
  Bot,
  Gauge,
  TrendingDown,
  Calendar,
  Layers,
  ArrowRight,
} from 'lucide-react';

import {
  ResponsiveContainer,
  ComposedChart,
  LineChart,
  Line,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
} from 'recharts';

/**
 * Inverter detail side drawer (420px, right-aligned, closes on Esc or backdrop click).
 */
export default function InverterDrawer({
  inverterId,
  startDate,
  endDate,
  agg = 'day',
  onClose,
}) {
  const navigate = useNavigate();

  // Close on Escape key press
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Query inverter detail
  const {
    data,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['dashboard', 'inverter', { inverterId, start: startDate, end: endDate, agg }],
    queryFn: () => fetchDashboardInverterDetail({ inverterId, start: startDate, end: endDate, agg }),
    enabled: Boolean(inverterId),
  });

  const summary = data?.summary;
  const daily = data?.daily || [];
  const peerMap = useMemo(() => {
    const m = new Map();
    (data?.peer_median_pr || []).forEach((row) => {
      m.set(row.date, row.peer_median_pr);
    });
    return m;
  }, [data?.peer_median_pr]);

  // Merge peer median PR with daily telemetry
  const combinedDaily = useMemo(() => {
    return daily.map((row) => ({
      ...row,
      peer_median_pr: peerMap.get(row.date) ?? null,
      pr_pct: row.pr !== null ? Number((row.pr * 100).toFixed(1)) : null,
      peer_pr_pct:
        peerMap.get(row.date) !== undefined && peerMap.get(row.date) !== null
          ? Number((peerMap.get(row.date) * 100).toFixed(1))
          : null,
    }));
  }, [daily, peerMap]);

  // Find worst or first flagged date for intelligent AI prefill
  const worstFaultDate = useMemo(() => {
    const faulted = daily.find((r) => r.status_code === 2 || r.status_code === 3 || (r.pr !== null && r.pr < 0.2));
    if (faulted) return faulted.date;
    const flagged = daily.find((r) => r.yield_kwh <= 5 && r.pr === 0);
    return flagged ? flagged.date : null;
  }, [daily]);

  // Formulate AI prompt and always create a new chat session
  const handleAskAI = async () => {
    let question = `Explain why inverter ${inverterId} in ${summary?.block || 'Plant'} `;
    if (worstFaultDate) {
      // Format to e.g. "16 Sep 2026"
      try {
        const d = new Date(worstFaultDate);
        const formatted = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
        question += `failed on ${formatted}.`;
      } catch {
        question += `failed on ${worstFaultDate}.`;
      }
    } else if (summary?.flagged_days && summary.flagged_days > 0) {
      const daysText = summary.flagged_days === 1 ? '1 flagged day' : `${summary.flagged_days} flagged days`;
      question += `experienced ${daysText} between ${startDate} and ${endDate}.`;
    } else {
      question += `performance ratio deviated from its block peers during ${startDate} to ${endDate}.`;
    }

    try {
      const newSession = await createSession({ title: `Inverter ${inverterId} Analysis` });
      if (newSession?.id) {
        localStorage.setItem('solar_last_session_id', newSession.id);
        navigate(`/chat/${newSession.id}?prefill=${encodeURIComponent(question)}`);
        return;
      }
    } catch (err) {
      console.error('Failed to create new chat session from drawer:', err);
    }

    localStorage.removeItem('solar_last_session_id');
    navigate(`/chat?prefill=${encodeURIComponent(question)}`);
  };

  if (!inverterId) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-hidden">
      {/* Backdrop */}
      <div
        onClick={onClose}
        className="absolute inset-0 bg-slate-900/40 backdrop-blur-xs transition-opacity"
      />

      {/* Slide-out Drawer Panel */}
      <aside
        className="absolute inset-y-0 right-0 w-[420px] max-w-full bg-white border-l border-[#E2E8F0] shadow-2xl flex flex-col z-10"
        role="dialog"
        aria-modal="true"
        aria-label={`Inverter ${inverterId} details`}
      >
        {/* Header */}
        <div className="p-4 border-b border-[#E2E8F0] flex items-center justify-between bg-[#F8FAFC]">
          <div className="min-w-0 pr-2">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-bold text-[#0F172A] truncate">{inverterId}</h2>
              {summary?.block && <Badge variant="primary">{summary.block}</Badge>}
            </div>
            <p className="text-[11px] text-[#64748B] mt-0.5 truncate">
              {summary?.plant || 'Plant Asset'} • {startDate} to {endDate}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-[#64748B] hover:text-[#0F172A] hover:bg-[#E2E8F0] transition-colors"
            title="Close drawer (Esc)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Scrollable Body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-5">
          {isLoading ? (
            <div className="space-y-4">
              <Skeleton className="w-full h-24" />
              <Skeleton className="w-full h-40" />
              <Skeleton className="w-full h-16" />
              <Skeleton className="w-full h-36" />
            </div>
          ) : error ? (
            <ErrorCard
              title="Failed to load inverter telemetry"
              message={error.message}
              onRetry={refetch}
            />
          ) : (
            <>
              {/* Summary Block */}
              <div className="bg-[#F8FAFC] border border-[#E2E8F0] rounded-xl p-3.5 space-y-2.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-[#64748B]">Block Location</span>
                  <span className="font-semibold text-[#0F172A]">{summary?.block || '—'}</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-[#64748B]">Installed DC Rating</span>
                  <span className="font-semibold text-[#0F172A]">{summary?.rated_dc_kw} kW</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-[#64748B]">AC Output Capacity</span>
                  <span className="font-semibold text-[#0F172A]">{summary?.rated_ac_kw} kW</span>
                </div>
                <div className="flex items-center justify-between text-xs pt-1 border-t border-[#E2E8F0]">
                  <span className="text-[#64748B]">Flagged Days</span>
                  <Badge variant={summary?.flagged_days > 0 ? 'danger' : 'success'}>
                    {summary?.flagged_days === 1
                      ? '1 flagged day'
                      : `${summary?.flagged_days || 0} flagged days`}
                  </Badge>
                </div>
              </div>

              {/* Status Timeline */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-[#0F172A]">Operating Status Timeline</span>
                  <span className="text-[10px] text-[#64748B]">{daily.length} days</span>
                </div>
                <div className="p-2.5 bg-[#F8FAFC] border border-[#E2E8F0] rounded-lg">
                  <div className="flex flex-wrap gap-1.5 items-center">
                    {daily.map((day) => {
                      // Color mapping: normal = green-grey, warning = amber, fault = red
                      const sc = day.status_code;
                      let bg = 'bg-[#10B981]'; // normal green
                      if (sc === 2 || sc === 3 || sc === 4 || day.yield_kwh <= 5) {
                        bg = 'bg-[#DC2626]'; // fault red
                      } else if (sc !== 0 && sc !== 1 && sc !== null) {
                        bg = 'bg-[#D97706]'; // warning amber
                      }

                      return (
                        <div
                          key={day.date}
                          className={`w-3.5 h-3.5 rounded-xs ${bg} transition-transform hover:scale-125 cursor-pointer relative group`}
                          title={`${day.date}: ${day.status_label || `Status ${sc}`} (${day.yield_kwh} kWh)`}
                        >
                          {/* Hover Tooltip */}
                          <div className="hidden group-hover:block absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-36 p-1.5 bg-[#0F172A] text-white text-[10px] rounded shadow-lg pointer-events-none text-center">
                            <span className="font-bold block">{day.date}</span>
                            <span>{day.status_label || `Status ${sc}`}</span>
                            <span className="block text-slate-300">{day.yield_kwh} kWh</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  <div className="flex items-center justify-between text-[10px] text-[#64748B] mt-2 pt-2 border-t border-[#E2E8F0]">
                    <div className="flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-xs bg-[#10B981]" />
                      <span>Normal</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-xs bg-[#D97706]" />
                      <span>Warning</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-xs bg-[#DC2626]" />
                      <span>Fault / Zero-Gen</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Daily PR vs Block Peer Median PR Chart */}
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs font-semibold text-[#0F172A]">PR vs Block Peer Median</span>
                  <span className="text-[10px] text-[#64748B]">Dashed = Block median</span>
                </div>
                <div className="h-44 w-full bg-white border border-[#E2E8F0] rounded-lg p-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={combinedDaily} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 9, fill: '#64748B' }}
                        tickFormatter={(v) => v.slice(5)}
                      />
                      <YAxis
                        unit="%"
                        domain={[0, 100]}
                        tick={{ fontSize: 9, fill: '#64748B' }}
                      />
                      <Tooltip
                        content={({ active, payload, label }) => {
                          if (!active || !payload?.length) return null;
                          return (
                            <div className="bg-white border border-[#E2E8F0] shadow-md rounded p-1.5 text-[11px]">
                              <p className="font-semibold text-[#0F172A]">{label}</p>
                              {payload.map((p) => (
                                <p key={p.dataKey} style={{ color: p.color }}>
                                  {p.name}: {p.value}%
                                </p>
                              ))}
                            </div>
                          );
                        }}
                      />
                      <Line
                        type="monotone"
                        dataKey="pr_pct"
                        name="Inverter PR"
                        stroke="#0D9488"
                        strokeWidth={2}
                        dot={false}
                      />
                      <Line
                        type="monotone"
                        dataKey="peer_pr_pct"
                        name="Peer Median PR"
                        stroke="#64748B"
                        strokeDasharray="4 4"
                        strokeWidth={1.5}
                        dot={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Daily Generation (Yield kWh) Chart */}
              <div>
                <span className="text-xs font-semibold text-[#0F172A] block mb-1.5">
                  Daily Yield (kWh)
                </span>
                <div className="h-36 w-full bg-white border border-[#E2E8F0] rounded-lg p-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart data={combinedDaily} margin={{ top: 5, right: 10, left: -15, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 9, fill: '#64748B' }}
                        tickFormatter={(v) => v.slice(5)}
                      />
                      <YAxis tick={{ fontSize: 9, fill: '#2563EB' }} />
                      <Tooltip
                        content={({ active, payload, label }) => {
                          if (!active || !payload?.length) return null;
                          return (
                            <div className="bg-white border border-[#E2E8F0] shadow-md rounded p-1.5 text-[11px]">
                              <p className="font-semibold text-[#0F172A]">{label}</p>
                              <p className="text-[#2563EB]">Yield: {payload[0]?.value} kWh</p>
                            </div>
                          );
                        }}
                      />
                      <Bar dataKey="yield_kwh" name="Yield (kWh)" fill="#2563EB" radius={[2, 2, 0, 0]} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* DC Voltage & Current Chart */}
              <div>
                <span className="text-xs font-semibold text-[#0F172A] block mb-1.5">
                  DC Electrical Characteristics
                </span>
                <div className="h-40 w-full bg-white border border-[#E2E8F0] rounded-lg p-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={combinedDaily} margin={{ top: 5, right: 15, left: -20, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 9, fill: '#64748B' }}
                        tickFormatter={(v) => v.slice(5)}
                      />
                      <YAxis
                        yAxisId="volt"
                        unit="V"
                        tick={{ fontSize: 9, fill: '#2563EB' }}
                        stroke="#2563EB"
                      />
                      <YAxis
                        yAxisId="curr"
                        unit="A"
                        orientation="right"
                        tick={{ fontSize: 9, fill: '#D97706' }}
                        stroke="#D97706"
                      />
                      <Tooltip
                        content={({ active, payload, label }) => {
                          if (!active || !payload?.length) return null;
                          return (
                            <div className="bg-white border border-[#E2E8F0] shadow-md rounded p-1.5 text-[11px]">
                              <p className="font-semibold text-[#0F172A]">{label}</p>
                              {payload.map((p) => (
                                <p key={p.dataKey} style={{ color: p.color }}>
                                  {p.name}: {p.value} {p.unit}
                                </p>
                              ))}
                            </div>
                          );
                        }}
                      />
                      <Line
                        yAxisId="volt"
                        type="monotone"
                        dataKey="dc_voltage"
                        name="DC Voltage"
                        unit="V"
                        stroke="#2563EB"
                        strokeWidth={1.5}
                        dot={false}
                      />
                      <Line
                        yAxisId="curr"
                        type="monotone"
                        dataKey="dc_current"
                        name="DC Current"
                        unit="A"
                        stroke="#D97706"
                        strokeWidth={1.5}
                        dot={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Footer with Ask AI Button */}
        <div className="p-3.5 border-t border-[#E2E8F0] bg-white shrink-0">
          <button
            type="button"
            onClick={handleAskAI}
            className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl bg-[#2563EB] hover:bg-[#1D4ED8] text-white text-xs font-semibold shadow-sm transition-colors"
          >
            <Bot className="w-4 h-4" />
            <span>Ask AI about this inverter</span>
            <ArrowRight className="w-3.5 h-3.5 opacity-70" />
          </button>
        </div>
      </aside>
    </div>
  );
}
