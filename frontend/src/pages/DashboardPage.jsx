import React, { useState, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  fetchMeta,
  fetchDashboardOverview,
} from '../services/api';

import Card from '../components/common/Card';
import KpiCard from '../components/common/KpiCard';
import Badge from '../components/common/Badge';
import Skeleton from '../components/common/Skeleton';
import ErrorCard from '../components/common/ErrorCard';
import PlantDetailView from '../components/dashboard/PlantDetailView';

import {
  Zap,
  Activity,
  Sun,
  Layers,
  AlertTriangle,
  CheckCircle2,
  ShieldAlert,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Calendar,
  Filter,
  RefreshCw,
  Building2,
  ChevronRight,
  Database,
  CloudSun,
  Thermometer,
  Gauge,
  Percent,
  Droplets,
  Info,
} from 'lucide-react';

import {
  ResponsiveContainer,
  ComposedChart,
  BarChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
  ReferenceLine,
  LabelList,
} from 'recharts';

// Glossary definitions for KPI ? tooltips
const KPI_GLOSSARY = {
  energy: 'Total electrical energy generated and delivered by the plant/fleet in Megawatt-hours (MWh).',
  pr: 'The ratio of actual energy output to theoretically expected energy output based on available sunlight.',
  specificYield: 'The total energy generated divided by the installed DC capacity (kWh/kWp), measuring generation efficiency per unit of size.',
  capacityFactor: 'The ratio of actual energy generated over a period to the maximum possible energy output if running at full capacity continuously.',
  soilingLoss: 'The accumulation of dust, dirt, and debris on solar panels that reduces sunlight absorption and power output (%).',
  availability: 'Share of inverter-days with irradiance above the threshold operating normally without faults or zero generation (%).',
  openAlerts: 'Flagged inverter-days exhibiting zero power generation under daylight or reported critical fault status codes.',
};

export default function DashboardPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  // Metadata query (plants, min/max log_date)
  const {
    data: meta,
    isLoading: metaLoading,
    error: metaError,
    refetch: refetchMeta,
  } = useQuery({
    queryKey: ['meta'],
    queryFn: fetchMeta,
  });

  const minDate = meta?.min_date || '2026-08-01';
  const maxDate = meta?.max_date || '2026-09-26';

  // Default range = the last 28 days of data
  const defaultRange = useMemo(() => {
    const end = maxDate;
    const dEnd = new Date(end);
    const dStart = new Date(dEnd);
    dStart.setDate(dStart.getDate() - 27); // 28 calendar days inclusive
    return {
      start: dStart.toISOString().slice(0, 10),
      end,
    };
  }, [maxDate]);

  // URL query params state
  const activeTab = searchParams.get('tab') || 'overview';
  const selectedPlant = searchParams.get('plant') || 'all';
  const startDate = searchParams.get('start') || defaultRange.start;
  const endDate = searchParams.get('end') || defaultRange.end;
  const agg = searchParams.get('agg') || 'day';
  const compare = searchParams.get('compare') !== 'false';

  // Table sorting state
  const [sortCol, setSortCol] = useState('pr');
  const [sortDir, setSortDir] = useState('desc');

  // Helper to update URL params
  const updateFilters = (newParams) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      Object.entries(newParams).forEach(([key, val]) => {
        if (val === null || val === undefined || val === '') {
          next.delete(key);
        } else {
          next.set(key, String(val));
        }
      });
      return next;
    });
  };

  // Preset handlers
  const handlePreset = (preset) => {
    if (preset === 'last7') {
      const dEnd = new Date(maxDate);
      const dStart = new Date(dEnd);
      dStart.setDate(dStart.getDate() - 6);
      updateFilters({
        start: dStart.toISOString().slice(0, 10),
        end: maxDate,
      });
    } else if (preset === 'last28') {
      updateFilters({
        start: defaultRange.start,
        end: defaultRange.end,
      });
    } else if (preset === 'last30') {
      const dEnd = new Date(maxDate);
      const dStart = new Date(dEnd);
      dStart.setDate(dStart.getDate() - 29);
      updateFilters({
        start: dStart.toISOString().slice(0, 10),
        end: maxDate,
      });
    } else if (preset === 'july') {
      updateFilters({
        start: '2026-07-01',
        end: '2026-07-31',
      });
    } else if (preset === 'august') {
      updateFilters({
        start: '2026-08-01',
        end: '2026-08-31',
      });
    } else if (preset === 'september') {
      updateFilters({
        start: '2026-09-01',
        end: maxDate < '2026-09-30' ? maxDate : '2026-09-30',
      });
    } else if (preset === 'october') {
      updateFilters({
        start: '2026-10-01',
        end: maxDate < '2026-10-31' ? maxDate : '2026-10-31',
      });
    } else if (preset === 'all') {
      updateFilters({
        start: minDate,
        end: maxDate,
      });
    }
  };

  const queryParams = useMemo(
    () => ({
      plantId: selectedPlant,
      start: startDate,
      end: endDate,
      agg,
    }),
    [selectedPlant, startDate, endDate, agg]
  );

  // Overview Query
  const {
    data: overview,
    isLoading: overviewLoading,
    error: overviewError,
    refetch: refetchOverview,
  } = useQuery({
    queryKey: ['dashboard', 'overview', queryParams],
    queryFn: () => fetchDashboardOverview(queryParams),
  });

  // Sorting for plants ranking table
  const sortedPlants = useMemo(() => {
    if (!overview?.plants) return [];
    const list = [...overview.plants];
    list.sort((a, b) => {
      let vA = a[sortCol];
      let vB = b[sortCol];
      if (vA === null || vA === undefined) vA = -Infinity;
      if (vB === null || vB === undefined) vB = -Infinity;
      if (typeof vA === 'string') {
        return sortDir === 'asc' ? vA.localeCompare(vB) : vB.localeCompare(vA);
      }
      return sortDir === 'asc' ? vA - vB : vB - vA;
    });
    return list;
  }, [overview?.plants, sortCol, sortDir]);

  const toggleSort = (col) => {
    if (sortCol === col) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortCol(col);
      setSortDir('desc');
    }
  };

  // Sorted PR for bar chart
  const sortedPrChartData = useMemo(() => {
    if (!overview?.plants) return [];
    return [...overview.plants]
      .filter((p) => p.pr !== null && p.pr !== undefined)
      .sort((a, b) => (b.pr || 0) - (a.pr || 0))
      .map((p) => ({
        name: p.name || `Plant ${p.plant_id}`,
        pr: Number(((p.pr || 0) * 100).toFixed(1)),
        plant_id: p.plant_id,
        capacity_mwp: p.capacity_mwp,
      }));
  }, [overview?.plants]);

  const fleetPrPct = overview?.fleet_pr ? Number((overview.fleet_pr * 100).toFixed(1)) : null;

  // Dynamic PR x-axis domain from (min - 2) to (max + 1)
  const prDomain = useMemo(() => {
    if (!sortedPrChartData || sortedPrChartData.length === 0) return [60, 100];
    const vals = sortedPrChartData.map((p) => p.pr);
    if (fleetPrPct) vals.push(fleetPrPct);
    const minVal = Math.floor(Math.min(...vals)) - 2;
    const maxVal = Math.ceil(Math.max(...vals)) + 1;
    return [Math.max(0, minVal), Math.min(100, maxVal)];
  }, [sortedPrChartData, fleetPrPct]);

  // Handle plant row click -> set plant and switch to Plant detail tab
  const handlePlantClick = (plantId) => {
    updateFilters({
      plant: plantId,
      tab: 'plant',
    });
  };

  return (
    <div className="space-y-6 pb-12 max-w-7xl mx-auto">
      {/* Page Title & Navigation Tabs */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[#0F172A]">Plant Performance Dashboard</h1>
          <p className="text-sm text-[#64748B] mt-0.5">
            Operational KPIs, telemetry trends, and fleet benchmarks without LLM latency.
          </p>
        </div>

        {/* Tab switcher */}
        <div className="flex items-center gap-1 bg-[#F1F5F9] p-1 rounded-lg border border-[#E2E8F0] shrink-0 self-start sm:self-auto">
          <button
            type="button"
            onClick={() => updateFilters({ tab: 'overview' })}
            className={`px-4 py-1.5 text-xs font-semibold rounded-md transition-all ${
              activeTab === 'overview'
                ? 'bg-white text-[#2563EB] shadow-sm'
                : 'text-[#64748B] hover:text-[#0F172A]'
            }`}
          >
            Overview
          </button>
          <button
            type="button"
            onClick={() => updateFilters({ tab: 'plant' })}
            className={`px-4 py-1.5 text-xs font-semibold rounded-md transition-all ${
              activeTab === 'plant'
                ? 'bg-white text-[#2563EB] shadow-sm'
                : 'text-[#64748B] hover:text-[#0F172A]'
            }`}
          >
            Plant detail
          </button>
        </div>
      </div>

      {/* Sticky Global Filter Bar */}
      <div className="sticky top-0 z-20 bg-[#F8FAFC]/95 backdrop-blur-md pt-2 pb-3 border-b border-[#E2E8F0]">
        <div className="bg-white border border-[#E2E8F0] rounded-xl p-3.5 shadow-sm flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2.5">
            {/* Plant Selector */}
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-medium text-[#64748B]">Plant:</span>
              <select
                value={selectedPlant}
                onChange={(e) => updateFilters({ plant: e.target.value })}
                className="bg-white border border-[#E2E8F0] text-xs font-medium text-[#0F172A] rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-[#2563EB]"
              >
                <option value="all">All Plants (Fleet)</option>
                {meta?.plants?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name || `Plant ${p.id}`} ({p.capacity_mwp} MWp)
                  </option>
                ))}
              </select>
            </div>

            {/* Presets */}
            <div className="hidden lg:flex items-center gap-1 border-l border-[#E2E8F0] pl-2.5">
              <span className="text-xs text-[#64748B] mr-1">Presets:</span>
              {[
                { id: 'last7', label: '7D' },
                { id: 'last28', label: '28D' },
                { id: 'last30', label: '30D' },
                { id: 'july', label: 'July' },
                { id: 'august', label: 'August' },
                { id: 'september', label: 'September' },
                { id: 'october', label: 'October' },
                { id: 'all', label: 'All' },
              ].map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => handlePreset(p.id)}
                  className="px-2 py-1 text-xs font-medium rounded-md bg-[#F8FAFC] border border-[#E2E8F0] text-[#0F172A] hover:bg-[#EFF6FF] hover:border-[#2563EB]/30 transition-colors"
                >
                  {p.label}
                </button>
              ))}
            </div>

            {/* Date Inputs */}
            <div className="flex items-center gap-1.5 border-l border-[#E2E8F0] pl-2.5">
              <Calendar className="w-3.5 h-3.5 text-[#64748B]" />
              <input
                type="date"
                min={minDate}
                max={endDate || maxDate}
                value={startDate}
                onChange={(e) => updateFilters({ start: e.target.value })}
                className="bg-white border border-[#E2E8F0] text-xs text-[#0F172A] rounded-lg px-2 py-1 focus:outline-none focus:border-[#2563EB]"
              />
              <span className="text-xs text-[#64748B]">to</span>
              <input
                type="date"
                min={startDate || minDate}
                max={maxDate}
                value={endDate}
                onChange={(e) => updateFilters({ end: e.target.value })}
                className="bg-white border border-[#E2E8F0] text-xs text-[#0F172A] rounded-lg px-2 py-1 focus:outline-none focus:border-[#2563EB]"
              />
            </div>
          </div>

          <div className="flex items-center gap-3">
            {/* Compare Toggle */}
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={compare}
                onChange={(e) => updateFilters({ compare: e.target.checked })}
                className="rounded border-[#E2E8F0] text-[#2563EB] focus:ring-0 focus:ring-offset-0 w-3.5 h-3.5"
              />
              <span className="text-xs font-medium text-[#64748B]">Compare prev</span>
            </label>

            {/* Aggregation Toggle (Day / Week / Month) */}
            <div className="flex items-center bg-[#F1F5F9] border border-[#E2E8F0] rounded-lg p-0.5">
              {['day', 'week', 'month'].map((a) => (
                <button
                  key={a}
                  type="button"
                  onClick={() => updateFilters({ agg: a })}
                  className={`px-2.5 py-1 text-xs font-medium rounded-md capitalize transition-colors ${
                    agg === a
                      ? 'bg-white text-[#2563EB] shadow-xs'
                      : 'text-[#64748B] hover:text-[#0F172A]'
                  }`}
                >
                  {a}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Date Clamping Note if adjusted */}
        {overview?.clamped_note && (
          <div className="mt-2 px-3 py-1.5 bg-[#FFFBEB] border border-[#D97706]/20 rounded-lg text-xs text-[#D97706] flex items-center gap-2">
            <Info className="w-3.5 h-3.5 shrink-0" />
            <span>{overview.clamped_note}</span>
          </div>
        )}
      </div>

      {/* OVERVIEW TAB CONTENT */}
      {activeTab === 'overview' && (
        <div className="space-y-6">
          {/* Top 7 KPI Cards */}
          {overviewError ? (
            <ErrorCard
              title="Failed to load Overview KPIs"
              message={overviewError.message}
              onRetry={refetchOverview}
            />
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 min-[1400px]:grid-cols-7 gap-3.5">
              {/* 1. Energy */}
              <KpiCard
                title="Energy"
                value={
                  overview?.kpis?.energy_mwh?.value !== null && overview?.kpis?.energy_mwh?.value !== undefined
                    ? Number(overview.kpis.energy_mwh.value).toLocaleString(undefined, { maximumFractionDigits: 1 })
                    : '—'
                }
                unit="MWh"
                delta={compare ? overview?.kpis?.energy_mwh?.delta : undefined}
                previousValue={compare ? overview?.kpis?.energy_mwh?.previous : undefined}
                deltaIsGood={true}
                tooltip={KPI_GLOSSARY.energy}
                icon={Zap}
                loading={overviewLoading}
              />

              {/* 2. PR */}
              <KpiCard
                title="Performance Ratio"
                value={
                  overview?.kpis?.pr?.value !== null && overview?.kpis?.pr?.value !== undefined
                    ? `${(overview.kpis.pr.value * 100).toFixed(1)}%`
                    : '—'
                }
                unit=""
                delta={compare ? overview?.kpis?.pr?.delta : undefined}
                previousValue={compare ? overview?.kpis?.pr?.previous : undefined}
                deltaIsGood={true}
                tooltip={KPI_GLOSSARY.pr}
                sparklineData={overview?.kpis?.pr?.sparkline || []}
                sparklineColor="#0D9488"
                icon={Gauge}
                loading={overviewLoading}
              />

              {/* 3. Specific Yield */}
              <KpiCard
                title="Specific Yield"
                value={
                  overview?.kpis?.specific_yield?.value !== null && overview?.kpis?.specific_yield?.value !== undefined
                    ? Number(overview.kpis.specific_yield.value).toFixed(1)
                    : '—'
                }
                unit="kWh/kWp"
                delta={compare ? overview?.kpis?.specific_yield?.delta : undefined}
                previousValue={compare ? overview?.kpis?.specific_yield?.previous : undefined}
                deltaIsGood={true}
                tooltip={KPI_GLOSSARY.specificYield}
                icon={Sun}
                loading={overviewLoading}
              />

              {/* 4. Capacity Factor */}
              <KpiCard
                title="Capacity Factor"
                value={
                  overview?.kpis?.capacity_factor?.value !== null && overview?.kpis?.capacity_factor?.value !== undefined
                    ? `${(overview.kpis.capacity_factor.value * 100).toFixed(1)}%`
                    : '—'
                }
                unit=""
                delta={compare ? overview?.kpis?.capacity_factor?.delta : undefined}
                previousValue={compare ? overview?.kpis?.capacity_factor?.previous : undefined}
                deltaIsGood={true}
                tooltip={KPI_GLOSSARY.capacityFactor}
                icon={Percent}
                loading={overviewLoading}
              />

              {/* 5. Soiling Loss (Inverted arrow) */}
              <KpiCard
                title="Soiling Loss"
                value={
                  overview?.kpis?.soiling_loss_pct?.value !== null && overview?.kpis?.soiling_loss_pct?.value !== undefined
                    ? `${Number(overview.kpis.soiling_loss_pct.value).toFixed(1)}%`
                    : '—'
                }
                unit=""
                delta={compare ? overview?.kpis?.soiling_loss_pct?.delta : undefined}
                previousValue={compare ? overview?.kpis?.soiling_loss_pct?.previous : undefined}
                deltaIsGood={true}
                invertDelta={true}
                tooltip={KPI_GLOSSARY.soilingLoss}
                icon={Droplets}
                loading={overviewLoading}
              />

              {/* 6. Availability */}
              <KpiCard
                title="Availability"
                value={
                  overview?.kpis?.availability_pct?.value !== null && overview?.kpis?.availability_pct?.value !== undefined
                    ? `${Number(overview.kpis.availability_pct.value).toFixed(2)}%`
                    : '—'
                }
                unit=""
                delta={compare ? overview?.kpis?.availability_pct?.delta : undefined}
                previousValue={compare ? overview?.kpis?.availability_pct?.previous : undefined}
                deltaIsGood={true}
                tooltip={KPI_GLOSSARY.availability}
                icon={CheckCircle2}
                loading={overviewLoading}
              />

              {/* 7. Open Alerts (Inverted arrow) */}
              <KpiCard
                title="Open Alerts"
                value={
                  overview?.kpis?.open_alerts?.value !== null && overview?.kpis?.open_alerts?.value !== undefined
                    ? overview.kpis.open_alerts.value
                    : '—'
                }
                unit="events"
                delta={compare ? overview?.kpis?.open_alerts?.delta : undefined}
                previousValue={compare ? overview?.kpis?.open_alerts?.previous : undefined}
                deltaIsGood={true}
                invertDelta={true}
                tooltip={KPI_GLOSSARY.openAlerts}
                icon={AlertTriangle}
                loading={overviewLoading}
              />
            </div>
          )}

          {/* Previous period note or no earlier data note (single line under cards, not on every card) */}
          {compare && !overviewError && (
            <div className="text-xs text-[#64748B] -mt-3 px-1">
              {overview?.previous_period?.note ? (
                <span>{overview.previous_period.note}</span>
              ) : overview?.previous_period && !overview.previous_period.has_data ? (
                <span>No earlier data for this range</span>
              ) : startDate <= minDate ? (
                <span>No earlier data for this range</span>
              ) : null}
            </div>
          )}

          {/* Plant Comparison: Sorted PR Bar Chart & Ranking Table */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* PR Benchmark Bar Chart */}
            <Card
              title="Performance Ratio Benchmark"
              subtitle={fleetPrPct ? `Fleet Average: ${fleetPrPct}%` : 'Sorted plant performance'}
              className="lg:col-span-1"
            >
              {overviewLoading ? (
                <div className="h-64 flex flex-col justify-center space-y-3">
                  <Skeleton className="w-full h-8" />
                  <Skeleton className="w-4/5 h-8" />
                  <Skeleton className="w-3/5 h-8" />
                </div>
              ) : sortedPrChartData.length === 0 ? (
                <div className="h-64 flex items-center justify-center text-xs text-[#64748B]">
                  No plant data available for this range.
                </div>
              ) : (
                <div className="h-64 w-full pt-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={sortedPrChartData}
                      layout="vertical"
                      margin={{ top: 25, right: 45, left: 15, bottom: 5 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#E2E8F0" />
                      <XAxis
                        type="number"
                        domain={prDomain}
                        unit="%"
                        tick={{ fontSize: 11, fill: '#64748B' }}
                        stroke="#E2E8F0"
                      />
                      <YAxis
                        type="category"
                        dataKey="name"
                        tick={{ fontSize: 11, fill: '#0F172A', fontWeight: 500 }}
                        stroke="#E2E8F0"
                        width={125}
                      />
                      <Tooltip
                        content={({ active, payload }) => {
                          if (!active || !payload?.length) return null;
                          const d = payload[0].payload;
                          return (
                            <div className="bg-white border border-[#E2E8F0] shadow-md rounded-lg p-2.5 text-xs">
                              <p className="font-semibold text-[#0F172A]">{d.name}</p>
                              <p className="text-[#0D9488] font-medium mt-1">PR: {d.pr}%</p>
                              <p className="text-[#64748B]">Capacity: {d.capacity_mwp} MWp</p>
                            </div>
                          );
                        }}
                      />
                      {fleetPrPct && (
                        <ReferenceLine
                          x={fleetPrPct}
                          stroke="#64748B"
                          strokeDasharray="4 4"
                          label={{
                            value: `Fleet ${fleetPrPct}%`,
                            fill: '#0F172A',
                            fontSize: 10,
                            fontWeight: 600,
                            position: 'top',
                            dy: -8,
                          }}
                        />
                      )}
                      <Bar
                        dataKey="pr"
                        fill="#0D9488"
                        radius={[0, 4, 4, 0]}
                        onClick={(entry) => handlePlantClick(entry.plant_id)}
                        className="cursor-pointer"
                      >
                        <LabelList
                          dataKey="pr"
                          position="right"
                          formatter={(v) => `${v}%`}
                          fill="#0F172A"
                          fontSize={11}
                          fontWeight={600}
                        />
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </Card>

            {/* Ranking Table */}
            <Card
              title="Plant Ranking & Health Overview"
              subtitle="Click any plant row to drill down into Plant Detail"
              className="lg:col-span-2"
            >
              {overviewLoading ? (
                <div className="space-y-2.5 pt-2">
                  <Skeleton className="w-full h-10" />
                  <Skeleton className="w-full h-10" />
                  <Skeleton className="w-full h-10" />
                </div>
              ) : sortedPlants.length === 0 ? (
                <div className="h-48 flex items-center justify-center text-xs text-[#64748B]">
                  No plant records found.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs border-collapse">
                    <thead>
                      <tr className="border-b border-[#E2E8F0] text-[#64748B] font-semibold">
                        <th
                          onClick={() => toggleSort('name')}
                          className="py-2.5 px-3 cursor-pointer hover:text-[#0F172A]"
                        >
                          <div className="flex items-center gap-1">
                            Plant
                            <ArrowUpDown className="w-3 h-3 text-[#94A3B8]" />
                          </div>
                        </th>
                        <th
                          onClick={() => toggleSort('capacity_mwp')}
                          className="py-2.5 px-3 cursor-pointer hover:text-[#0F172A]"
                        >
                          <div className="flex items-center gap-1">
                            Capacity
                            <ArrowUpDown className="w-3 h-3 text-[#94A3B8]" />
                          </div>
                        </th>
                        <th
                          onClick={() => toggleSort('energy_mwh')}
                          className="py-2.5 px-3 cursor-pointer hover:text-[#0F172A]"
                        >
                          <div className="flex items-center gap-1">
                            Energy
                            <ArrowUpDown className="w-3 h-3 text-[#94A3B8]" />
                          </div>
                        </th>
                        <th
                          onClick={() => toggleSort('pr')}
                          className="py-2.5 px-3 cursor-pointer hover:text-[#0F172A]"
                        >
                          <div className="flex items-center gap-1">
                            PR
                            <ArrowUpDown className="w-3 h-3 text-[#94A3B8]" />
                          </div>
                        </th>
                        <th
                          onClick={() => toggleSort('soiling_loss_pct')}
                          className="py-2.5 px-3 cursor-pointer hover:text-[#0F172A]"
                        >
                          <div className="flex items-center gap-1">
                            Soiling Loss
                            <ArrowUpDown className="w-3 h-3 text-[#94A3B8]" />
                          </div>
                        </th>
                        <th
                          onClick={() => toggleSort('fault_days')}
                          className="py-2.5 px-3 cursor-pointer hover:text-[#0F172A]"
                        >
                          <div className="flex items-center gap-1">
                            Fault Days
                            <ArrowUpDown className="w-3 h-3 text-[#94A3B8]" />
                          </div>
                        </th>
                        <th className="py-2.5 px-3 text-right">Health</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E2E8F0]">
                      {sortedPlants.map((p) => {
                        let healthVariant = 'success';
                        let healthLabel = 'Normal';
                        if (p.health === 'amber') {
                          healthVariant = 'warning';
                          healthLabel = 'Attention';
                        } else if (p.health === 'red') {
                          healthVariant = 'danger';
                          healthLabel = 'Critical';
                        }

                        return (
                          <tr
                            key={p.plant_id}
                            onClick={() => handlePlantClick(p.plant_id)}
                            className="hover:bg-[#EFF6FF]/60 cursor-pointer transition-colors group"
                            title="Click to view Plant Detail"
                          >
                            <td className="py-3 px-3 font-semibold text-[#0F172A] group-hover:text-[#2563EB] flex items-center gap-1.5">
                              <Building2 className="w-3.5 h-3.5 text-[#94A3B8] group-hover:text-[#2563EB]" />
                              {p.name || `Plant ${p.plant_id}`}
                            </td>
                            <td className="py-3 px-3 text-[#64748B]">
                              {p.capacity_mwp !== null ? `${p.capacity_mwp} MWp` : '—'}
                            </td>
                            <td className="py-3 px-3 text-[#0F172A] font-medium">
                              {p.energy_mwh !== null
                                ? `${Number(p.energy_mwh).toLocaleString(undefined, { maximumFractionDigits: 1 })} MWh`
                                : '—'}
                            </td>
                            <td className="py-3 px-3 font-semibold text-[#0D9488]">
                              {p.pr !== null ? `${(p.pr * 100).toFixed(1)}%` : '—'}
                            </td>
                            <td className="py-3 px-3 text-[#64748B]">
                              {p.soiling_loss_pct !== null ? `${p.soiling_loss_pct}%` : '—'}
                            </td>
                            <td className="py-3 px-3 text-[#64748B]">{p.fault_days ?? 0}d</td>
                            <td className="py-3 px-3 text-right">
                              <Badge variant={healthVariant}>{healthLabel}</Badge>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>

          {/* Daily Energy & Irradiance Chart */}
          <Card
            title="Generation & Irradiance Trend"
            subtitle="Energy output (MWh) on primary axis, Solar Irradiance (kWh/m²) on secondary axis"
          >
            {overviewLoading ? (
              <div className="h-72 flex items-center justify-center">
                <Skeleton className="w-full h-full" />
              </div>
            ) : !overview?.daily || overview.daily.length === 0 ? (
              <div className="h-64 flex items-center justify-center text-xs text-[#64748B]">
                No daily generation points in this period.
              </div>
            ) : (
              <div className="h-72 w-full pt-3">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart
                    data={overview.daily}
                    margin={{ top: 10, right: 30, left: 10, bottom: 5 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 11, fill: '#64748B' }}
                      stroke="#E2E8F0"
                      tickFormatter={(val) => {
                        try {
                          const [y, m, d] = val.split('-');
                          return `${m}/${d}`;
                        } catch {
                          return val;
                        }
                      }}
                    />
                    <YAxis
                      yAxisId="left"
                      tick={{ fontSize: 11, fill: '#2563EB' }}
                      stroke="#2563EB"
                      label={{
                        value: 'Energy (MWh)',
                        angle: -90,
                        position: 'insideLeft',
                        fill: '#2563EB',
                        fontSize: 11,
                      }}
                    />
                    <YAxis
                      yAxisId="right"
                      orientation="right"
                      tick={{ fontSize: 11, fill: '#D97706' }}
                      stroke="#D97706"
                      label={{
                        value: 'Irradiance (kWh/m²)',
                        angle: 90,
                        position: 'insideRight',
                        fill: '#D97706',
                        fontSize: 11,
                      }}
                    />
                    <Tooltip
                      content={({ active, payload, label }) => {
                        if (!active || !payload?.length) return null;
                        const dEnergy = payload.find((p) => p.dataKey === 'energy_mwh');
                        const dIrr = payload.find((p) => p.dataKey === 'irradiance_kwh_m2');
                        return (
                          <div className="bg-white border border-[#E2E8F0] shadow-md rounded-lg p-2.5 text-xs">
                            <p className="font-semibold text-[#0F172A] mb-1">{label}</p>
                            {dEnergy && (
                              <p className="text-[#2563EB] font-medium">
                                Energy: {dEnergy.value} MWh
                              </p>
                            )}
                            {dIrr && (
                              <p className="text-[#D97706] font-medium">
                                Irradiance: {dIrr.value} kWh/m²
                              </p>
                            )}
                          </div>
                        );
                      }}
                    />
                    <Legend
                      verticalAlign="top"
                      align="right"
                      wrapperStyle={{ paddingBottom: 10, fontSize: 12 }}
                    />
                    <Bar
                      yAxisId="left"
                      dataKey="energy_mwh"
                      name="Energy (MWh)"
                      fill="#2563EB"
                      radius={[4, 4, 0, 0]}
                    />
                    <Line
                      yAxisId="right"
                      type="monotone"
                      dataKey="irradiance_kwh_m2"
                      name="Irradiance (kWh/m²)"
                      stroke="#D97706"
                      strokeWidth={2}
                      dot={false}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            )}
          </Card>

          {/* Small Data Quality Card */}
          <Card
            title="Telemetry Data Quality & Coverage"
            subtitle="Rows recorded, missing calendar days, and null yield counts per plant"
          >
            {overviewLoading ? (
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <Skeleton className="h-16" />
                <Skeleton className="h-16" />
                <Skeleton className="h-16" />
              </div>
            ) : !overview?.data_quality || overview.data_quality.length === 0 ? (
              <p className="text-xs text-[#64748B]">No telemetry coverage logs available.</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1">
                {overview.data_quality.map((dq) => {
                  const plantName =
                    meta?.plants?.find((p) => p.id === dq.plant_id)?.name || `Plant ${dq.plant_id}`;
                  const isClean = dq.missing_days === 0 && dq.null_counts === 0;

                  return (
                    <div
                      key={dq.plant_id}
                      className="border border-[#E2E8F0] rounded-lg p-3 bg-[#F8FAFC] flex items-center justify-between"
                    >
                      <div>
                        <span className="font-semibold text-xs text-[#0F172A]">{plantName}</span>
                        <div className="text-[11px] text-[#64748B] mt-0.5 space-x-2">
                          <span>{dq.rows} rows</span>
                          <span>•</span>
                          <span>{dq.missing_days} missing days</span>
                          <span>•</span>
                          <span>{dq.null_counts} nulls</span>
                        </div>
                      </div>
                      <Badge variant={isClean ? 'success' : 'warning'}>
                        {isClean ? '100% Complete' : 'Gaps Detected'}
                      </Badge>
                    </div>
                  );
                })}
              </div>
            )}
          </Card>
        </div>
      )}

      {/* PLANT DETAIL TAB CONTENT */}
      {activeTab === 'plant' && (
        <PlantDetailView
          plantId={selectedPlant}
          startDate={startDate}
          endDate={endDate}
          agg={agg}
          plantsMeta={meta?.plants || []}
          onSelectPlant={(pid) => updateFilters({ plant: pid, tab: 'plant' })}
        />
      )}
    </div>
  );
}
