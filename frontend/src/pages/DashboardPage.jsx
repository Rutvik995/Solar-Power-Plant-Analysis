import React, { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  fetchMeta,
  fetchDashboardKpis,
  fetchDashboardDaily,
  fetchDashboardBlocks,
  fetchDashboardFlagged,
  fetchDashboardSoiling,
} from '../services/api';

import Card from '../components/common/Card';
import KpiCard from '../components/common/KpiCard';
import Badge from '../components/common/Badge';
import Skeleton from '../components/common/Skeleton';
import ErrorCard from '../components/common/ErrorCard';

import {
  Zap,
  Sun,
  ShieldAlert,
  Activity,
  Layers,
  Filter,
  RefreshCw,
  AlertTriangle,
  Calendar,
  CheckCircle2,
} from 'lucide-react';

import {
  ResponsiveContainer,
  ComposedChart,
  BarChart,
  LineChart,
  Area,
  Bar,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
  ReferenceLine,
} from 'recharts';

export default function DashboardPage() {
  // Filters state
  const [selectedPlant, setSelectedPlant] = useState('all');
  const [startDate, setStartDate] = useState('2026-08-01');
  const [endDate, setEndDate] = useState('2026-09-26');

  // Metadata query
  const {
    data: meta,
    isLoading: metaLoading,
    error: metaError,
    refetch: refetchMeta,
  } = useQuery({
    queryKey: ['meta'],
    queryFn: fetchMeta,
  });

  const queryParams = useMemo(
    () => ({
      plantId: selectedPlant,
      start: startDate,
      end: endDate,
    }),
    [selectedPlant, startDate, endDate]
  );

  // Dashboard Queries
  const {
    data: kpis,
    isLoading: kpisLoading,
    error: kpisError,
    refetch: refetchKpis,
  } = useQuery({
    queryKey: ['dashboard', 'kpis', queryParams],
    queryFn: () => fetchDashboardKpis(queryParams),
  });

  const {
    data: daily,
    isLoading: dailyLoading,
    error: dailyError,
  } = useQuery({
    queryKey: ['dashboard', 'daily', queryParams],
    queryFn: () => fetchDashboardDaily(queryParams),
  });

  const {
    data: blocks,
    isLoading: blocksLoading,
    error: blocksError,
  } = useQuery({
    queryKey: ['dashboard', 'blocks', queryParams],
    queryFn: () => fetchDashboardBlocks(queryParams),
  });

  const {
    data: flagged,
    isLoading: flaggedLoading,
    error: flaggedError,
  } = useQuery({
    queryKey: ['dashboard', 'flagged', queryParams],
    queryFn: () => fetchDashboardFlagged(queryParams),
  });

  const {
    data: soiling,
    isLoading: soilingLoading,
  } = useQuery({
    queryKey: ['dashboard', 'soiling', queryParams],
    queryFn: () => fetchDashboardSoiling(queryParams),
  });

  // Quick Date Presets
  const handleDatePreset = (preset) => {
    if (preset === 'sept') {
      setStartDate('2026-09-01');
      setEndDate('2026-09-26');
    } else if (preset === 'aug') {
      setStartDate('2026-08-01');
      setEndDate('2026-08-31');
    } else {
      setStartDate(meta?.min_log_date || '2026-08-01');
      setEndDate(meta?.max_log_date || '2026-09-26');
    }
  };

  // Sparkline data mappings
  const yieldSparkline = useMemo(() => {
    if (!kpis?.sparkline) return [];
    return kpis.sparkline.map((item) => ({ value: item.yield }));
  }, [kpis]);

  const prSparkline = useMemo(() => {
    if (!kpis?.sparkline) return [];
    return kpis.sparkline.map((item) => ({ value: item.pr ? item.pr * 100 : 0 }));
  }, [kpis]);

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* Filters & Control Bar */}
      <div className="bg-surface border border-border rounded-xl p-4 flex flex-col md:flex-row items-stretch md:items-center justify-between gap-4 shadow-sm">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <Filter className="w-4 h-4 text-muted" />
            <span className="text-xs font-semibold uppercase text-muted">Facility:</span>
            <select
              value={selectedPlant}
              onChange={(e) => setSelectedPlant(e.target.value)}
              className="bg-card border border-border text-text text-xs rounded-lg px-3 py-1.5 focus:outline-none focus:border-primary transition-colors cursor-pointer"
            >
              <option value="all">All Solar Plants (5)</option>
              {meta?.plants?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.capacity_mwp} MWp)
                </option>
              ))}
            </select>
          </div>

          <div className="h-4 w-[1px] bg-border hidden sm:block" />

          {/* Date Range Inputs */}
          <div className="flex items-center gap-2 text-xs">
            <Calendar className="w-4 h-4 text-muted" />
            <input
              type="date"
              value={startDate}
              min={meta?.min_log_date}
              max={endDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="bg-card border border-border text-text px-2.5 py-1 rounded-lg text-xs focus:outline-none focus:border-primary"
            />
            <span className="text-muted">to</span>
            <input
              type="date"
              value={endDate}
              min={startDate}
              max={meta?.max_log_date}
              onChange={(e) => setEndDate(e.target.value)}
              className="bg-card border border-border text-text px-2.5 py-1 rounded-lg text-xs focus:outline-none focus:border-primary"
            />
          </div>
        </div>

        {/* Date Presets & Refresh */}
        <div className="flex items-center gap-2 justify-end">
          <button
            onClick={() => handleDatePreset('all')}
            className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${
              startDate === '2026-08-01' && endDate === '2026-09-26'
                ? 'bg-primary/15 text-primary border-primary/30'
                : 'bg-card text-muted border-border hover:text-text'
            }`}
          >
            All 57 Days
          </button>
          <button
            onClick={() => handleDatePreset('sept')}
            className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${
              startDate === '2026-09-01'
                ? 'bg-primary/15 text-primary border-primary/30'
                : 'bg-card text-muted border-border hover:text-text'
            }`}
          >
            Sept 2026
          </button>
          <button
            onClick={() => handleDatePreset('aug')}
            className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${
              endDate === '2026-08-31'
                ? 'bg-primary/15 text-primary border-primary/30'
                : 'bg-card text-muted border-border hover:text-text'
            }`}
          >
            Aug 2026
          </button>
          <button
            onClick={() => refetchKpis()}
            title="Refresh Telemetry"
            className="p-1.5 rounded-lg bg-card border border-border text-muted hover:text-text hover:border-primary transition-all"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${kpisLoading ? 'animate-spin text-primary' : ''}`} />
          </button>
        </div>
      </div>

      {kpisError && (
        <ErrorCard
          title="Failed to load portfolio KPIs"
          message={kpisError.message}
          onRetry={() => refetchKpis()}
        />
      )}

      {/* KPI Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard
          title="Total Energy Yield"
          value={
            kpis?.yield !== undefined
              ? (kpis.yield >= 1000000
                  ? `${(kpis.yield / 1000000).toFixed(2)} MWh`
                  : `${(kpis.yield / 1000).toFixed(1)} MWh`)
              : '—'
          }
          previousValue={kpis?.previous_period?.yield}
          sparklineData={yieldSparkline}
          icon={Zap}
          deltaIsGood={true}
          loading={kpisLoading}
        />

        <KpiCard
          title="Performance Ratio (PR)"
          value={kpis?.pr ? `${(kpis.pr * 100).toFixed(1)}%` : '—'}
          previousValue={kpis?.previous_period?.pr ? kpis.previous_period.pr * 100 : null}
          sparklineData={prSparkline}
          icon={Activity}
          deltaIsGood={true}
          loading={kpisLoading}
        />

        <KpiCard
          title="Average Soiling Loss"
          value={kpis?.soiling_loss_pct !== null && kpis?.soiling_loss_pct !== undefined ? `${kpis.soiling_loss_pct}%` : '—'}
          previousValue={kpis?.previous_period?.soiling_loss_pct}
          icon={Sun}
          deltaIsGood={false}
          loading={kpisLoading}
        />

        <KpiCard
          title="Hardware Fault Days"
          value={kpis?.fault_days !== undefined ? kpis.fault_days : '0'}
          unit="days"
          previousValue={kpis?.previous_period?.fault_days}
          icon={ShieldAlert}
          deltaIsGood={false}
          loading={kpisLoading}
        />
      </div>

      {/* Charts Section */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Daily Generation vs Insolation (2 Columns) */}
        <Card
          title="Daily Generation Yield & Solar Insolation"
          subtitle="Compares AC electrical output (kWh) with total solar radiation H (kWh/m²)"
          className="lg:col-span-2"
          action={
            <div className="flex items-center gap-2">
              <Badge variant="teal">Dual-Axis Telemetry</Badge>
            </div>
          }
        >
          {dailyLoading ? (
            <div className="h-72 flex items-center justify-center">
              <Skeleton className="w-full h-full rounded-lg" />
            </div>
          ) : dailyError ? (
            <ErrorCard message={dailyError.message} />
          ) : daily && daily.length > 0 ? (
            <div className="h-72 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={daily} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                  <defs>
                    <linearGradient id="yieldGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#F5B301" stopOpacity={0.4} />
                      <stop offset="95%" stopColor="#F5B301" stopOpacity={0.0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#24324F" opacity={0.6} />
                  <XAxis
                    dataKey="date"
                    stroke="#8FA1BF"
                    fontSize={11}
                    tickFormatter={(str) => str.slice(5)}
                  />
                  <YAxis
                    yAxisId="left"
                    stroke="#F5B301"
                    fontSize={11}
                    tickFormatter={(val) => `${(val / 1000).toFixed(0)}k`}
                  />
                  <YAxis
                    yAxisId="right"
                    orientation="right"
                    stroke="#2DD4BF"
                    fontSize={11}
                    unit=" H"
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#16213A',
                      borderColor: '#24324F',
                      borderRadius: '8px',
                      color: '#E6ECF5',
                      fontSize: '12px',
                    }}
                    labelStyle={{ color: '#8FA1BF', marginBottom: '4px' }}
                    formatter={(val, name) => [
                      name === 'yield' ? `${Number(val).toLocaleString()} kWh` : `${val} kWh/m²`,
                      name === 'yield' ? 'AC Generation' : 'Solar Radiation',
                    ]}
                  />
                  <Legend
                    verticalAlign="top"
                    align="right"
                    iconSize={10}
                    wrapperStyle={{ fontSize: '11px', paddingBottom: '10px' }}
                  />
                  <Area
                    yAxisId="left"
                    type="monotone"
                    dataKey="yield"
                    name="yield"
                    stroke="#F5B301"
                    strokeWidth={2}
                    fillOpacity={1}
                    fill="url(#yieldGradient)"
                  />
                  <Line
                    yAxisId="right"
                    type="monotone"
                    dataKey="irradiance"
                    name="irradiance"
                    stroke="#2DD4BF"
                    strokeWidth={2}
                    dot={false}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="h-72 flex items-center justify-center text-muted text-xs">
              No daily telemetry available for selected timeframe.
            </div>
          )}
        </Card>

        {/* Block PR Ranking (1 Column) */}
        <Card
          title="Block PR Efficiency"
          subtitle="Aggregated PR across inverter blocks"
          action={<Badge variant="violet">BR-04 Validated</Badge>}
        >
          {blocksLoading ? (
            <div className="h-72 flex items-center justify-center">
              <Skeleton className="w-full h-full rounded-lg" />
            </div>
          ) : blocksError ? (
            <ErrorCard message={blocksError.message} />
          ) : blocks && blocks.length > 0 ? (
            <div className="h-72 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={blocks.slice(0, 8)}
                  layout="vertical"
                  margin={{ top: 10, right: 20, left: 20, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#24324F" opacity={0.6} horizontal={false} />
                  <XAxis
                    type="number"
                    domain={[0.7, 1.0]}
                    stroke="#8FA1BF"
                    fontSize={11}
                    tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
                  />
                  <YAxis
                    dataKey="block_name"
                    type="category"
                    stroke="#8FA1BF"
                    fontSize={11}
                    width={70}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#16213A',
                      borderColor: '#24324F',
                      borderRadius: '8px',
                      color: '#E6ECF5',
                      fontSize: '12px',
                    }}
                    formatter={(val) => [`${(Number(val) * 100).toFixed(2)}%`, 'PR']}
                  />
                  <ReferenceLine x={0.8} stroke="#EF4444" strokeDasharray="3 3" label={{ value: 'Target 80%', fill: '#EF4444', fontSize: 10 }} />
                  <Bar
                    dataKey="performance_ratio"
                    fill="#A78BFA"
                    radius={[0, 4, 4, 0]}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="h-72 flex items-center justify-center text-muted text-xs">
              No block telemetry found.
            </div>
          )}
        </Card>
      </div>

      {/* Flagged Inverters & Alarms Table */}
      <Card
        title="Hardware Health & Flagged Inverters"
        subtitle="Automatic detection of zero generation, status code alarms, and peer underperformance"
        action={
          <Badge variant={flagged && flagged.length > 0 ? 'warning' : 'success'}>
            {flagged?.length || 0} Telemetry Flags
          </Badge>
        }
      >
        {flaggedLoading ? (
          <div className="space-y-2 py-4">
            <Skeleton className="w-full h-8" />
            <Skeleton className="w-full h-8" />
            <Skeleton className="w-full h-8" />
          </div>
        ) : flaggedError ? (
          <ErrorCard message={flaggedError.message} />
        ) : flagged && flagged.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-border text-muted font-medium">
                  <th className="pb-3 px-3">Date</th>
                  <th className="pb-3 px-3">Inverter</th>
                  <th className="pb-3 px-3">Block</th>
                  <th className="pb-3 px-3">Issue Type</th>
                  <th className="pb-3 px-3">Status Code</th>
                  <th className="pb-3 px-3 text-right">Performance Ratio</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {flagged.map((item, idx) => {
                  let badgeVariant = 'warning';
                  if (item.type === 'Fault' || item.type === 'Offline') badgeVariant = 'danger';
                  if (item.type === 'Zero Generation') badgeVariant = 'warning';
                  if (item.type === 'Warning') badgeVariant = 'primary';
                  if (item.type === 'Underperformance') badgeVariant = 'violet';

                  return (
                    <tr key={idx} className="hover:bg-surface/50 transition-colors">
                      <td className="py-3 px-3 font-mono text-muted">{item.date}</td>
                      <td className="py-3 px-3 font-medium text-text">{item.inverter}</td>
                      <td className="py-3 px-3 text-muted">{item.block}</td>
                      <td className="py-3 px-3">
                        <Badge variant={badgeVariant}>{item.type}</Badge>
                      </td>
                      <td className="py-3 px-3">
                        <span className="font-mono bg-surface px-2 py-0.5 rounded border border-border">
                          Code {item.status_code}
                        </span>
                      </td>
                      <td className="py-3 px-3 text-right font-mono font-medium">
                        {item.pr !== null ? `${(item.pr * 100).toFixed(1)}%` : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="py-8 text-center text-xs text-muted flex flex-col items-center justify-center gap-2">
            <CheckCircle2 className="w-6 h-6 text-success" />
            <p className="font-medium text-text">All inverters operating within normal nominal ranges.</p>
            <p className="text-muted">No zero-generation anomalies or critical hardware status alarms detected for this period.</p>
          </div>
        )}
      </Card>
    </div>
  );
}
