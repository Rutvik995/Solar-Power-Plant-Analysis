import React, { useState, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  fetchMeta,
  fetchDashboardOverview,
  fetchDashboardPlantDetail,
} from '../services/api';

import Card from '../components/common/Card';
import KpiCard from '../components/common/KpiCard';
import Badge from '../components/common/Badge';
import Skeleton from '../components/common/Skeleton';
import ErrorCard from '../components/common/ErrorCard';

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

  // URL query params state
  const activeTab = searchParams.get('tab') || 'overview';
  const selectedPlant = searchParams.get('plant') || 'all';
  const startDate = searchParams.get('start') || '2026-08-01';
  const endDate = searchParams.get('end') || '2026-09-26';
  const agg = searchParams.get('agg') || 'day';
  const compare = searchParams.get('compare') !== 'false';

  // Table sorting state
  const [sortCol, setSortCol] = useState('pr');
  const [sortDir, setSortDir] = useState('desc');

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
    } else if (preset === 'last30') {
      const dEnd = new Date(maxDate);
      const dStart = new Date(dEnd);
      dStart.setDate(dStart.getDate() - 29);
      updateFilters({
        start: dStart.toISOString().slice(0, 10),
        end: maxDate,
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

  // Plant Detail Query (runs when activeTab is 'plant' and a plant is chosen)
  const isPlantDetailActive = activeTab === 'plant';
  const effectivePlantId = selectedPlant !== 'all' ? selectedPlant : null;

  const {
    data: plantDetail,
    isLoading: plantDetailLoading,
    error: plantDetailError,
    refetch: refetchPlantDetail,
  } = useQuery({
    queryKey: ['dashboard', 'plant', { ...queryParams, plantId: effectivePlantId }],
    queryFn: () => fetchDashboardPlantDetail({ ...queryParams, plantId: effectivePlantId }),
    enabled: isPlantDetailActive && Boolean(effectivePlantId),
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
                { id: 'last30', label: '30D' },
                { id: 'august', label: 'August' },
                { id: 'september', label: 'September' },
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
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7 gap-3.5">
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
                icon={Activity}
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
                icon={Layers}
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
                icon={AlertTriangle}
                loading={overviewLoading}
              />

              {/* 6. Availability */}
              <KpiCard
                title="Availability"
                value={
                  overview?.kpis?.availability_pct?.value !== null && overview?.kpis?.availability_pct?.value !== undefined
                    ? `${Number(overview.kpis.availability_pct.value).toFixed(1)}%`
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
                icon={ShieldAlert}
                loading={overviewLoading}
              />
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
                      margin={{ top: 10, right: 30, left: 10, bottom: 5 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#E2E8F0" />
                      <XAxis
                        type="number"
                        domain={[60, 100]}
                        unit="%"
                        tick={{ fontSize: 11, fill: '#64748B' }}
                        stroke="#E2E8F0"
                      />
                      <YAxis
                        type="category"
                        dataKey="name"
                        tick={{ fontSize: 11, fill: '#0F172A', fontWeight: 500 }}
                        stroke="#E2E8F0"
                        width={85}
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
                            fill: '#64748B',
                            fontSize: 10,
                            position: 'top',
                          }}
                        />
                      )}
                      <Bar
                        dataKey="pr"
                        fill="#0D9488"
                        radius={[0, 4, 4, 0]}
                        onClick={(entry) => handlePlantClick(entry.plant_id)}
                        className="cursor-pointer"
                      />
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
        <div className="space-y-6">
          {/* Prompt if all plants selected */}
          {selectedPlant === 'all' ? (
            <Card className="text-center py-10">
              <Building2 className="w-10 h-10 text-[#2563EB] mx-auto mb-3" />
              <h3 className="text-base font-bold text-[#0F172A]">Select a Plant for In-Depth Detail</h3>
              <p className="text-xs text-[#64748B] max-w-md mx-auto mt-1 mb-5">
                Detailed block PRs, inverter leaderboards, ambient weather correlations, and treemap
                hierarchies require focusing on a specific plant.
              </p>
              <div className="flex flex-wrap justify-center gap-2">
                {meta?.plants?.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => updateFilters({ plant: p.id })}
                    className="px-4 py-2 bg-white border border-[#2563EB]/30 hover:border-[#2563EB] hover:bg-[#EFF6FF] text-[#2563EB] font-medium text-xs rounded-lg transition-colors flex items-center gap-2"
                  >
                    <span>{p.name || `Plant ${p.id}`}</span>
                    <span className="text-[#64748B] text-[11px]">({p.capacity_mwp} MWp)</span>
                  </button>
                ))}
              </div>
            </Card>
          ) : plantDetailError ? (
            <ErrorCard
              title="Failed to load Plant Detail"
              message={plantDetailError.message}
              onRetry={refetchPlantDetail}
            />
          ) : (
            <>
              {/* Plant Detail Header Banner */}
              <div className="bg-white border border-[#E2E8F0] rounded-xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-xs">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-lg bg-[#EFF6FF] border border-[#2563EB]/20 flex items-center justify-center text-[#2563EB] font-bold text-sm">
                    P{selectedPlant}
                  </div>
                  <div>
                    <h2 className="text-base font-bold text-[#0F172A]">
                      {meta?.plants?.find((p) => String(p.id) === String(selectedPlant))?.name ||
                        `Plant ${selectedPlant}`}
                    </h2>
                    <p className="text-xs text-[#64748B]">
                      Capacity:{' '}
                      {meta?.plants?.find((p) => String(p.id) === String(selectedPlant))?.capacity_mwp ||
                        '—'}{' '}
                      MWp • Range: {startDate} to {endDate}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => updateFilters({ tab: 'overview', plant: 'all' })}
                    className="px-3 py-1.5 text-xs font-medium text-[#64748B] hover:text-[#0F172A] bg-[#F8FAFC] hover:bg-[#F1F5F9] rounded-lg border border-[#E2E8F0] transition-colors"
                  >
                    ← Back to Fleet Overview
                  </button>
                </div>
              </div>

              {/* Block PR Table & Best/Worst Inverters Grid */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Block PR Table */}
                <Card title="Block Performance" subtitle="PR and yield aggregated by physical block">
                  {plantDetailLoading ? (
                    <div className="space-y-2 pt-2">
                      <Skeleton className="w-full h-8" />
                      <Skeleton className="w-full h-8" />
                    </div>
                  ) : !plantDetail?.blocks || plantDetail.blocks.length === 0 ? (
                    <p className="text-xs text-[#64748B] py-6 text-center">No block telemetry available.</p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs border-collapse">
                        <thead>
                          <tr className="border-b border-[#E2E8F0] text-[#64748B]">
                            <th className="py-2 px-2 font-semibold">Block</th>
                            <th className="py-2 px-2 font-semibold">Inverters</th>
                            <th className="py-2 px-2 font-semibold">Energy</th>
                            <th className="py-2 px-2 font-semibold text-right">PR</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-[#E2E8F0]">
                          {plantDetail.blocks.map((b) => (
                            <tr key={b.block_id || b.block}>
                              <td className="py-2.5 px-2 font-medium text-[#0F172A]">{b.block}</td>
                              <td className="py-2.5 px-2 text-[#64748B]">{b.inverter_count} units</td>
                              <td className="py-2.5 px-2 text-[#0F172A]">
                                {b.energy_mwh !== null ? `${b.energy_mwh} MWh` : '—'}
                              </td>
                              <td className="py-2.5 px-2 text-right font-semibold text-[#0D9488]">
                                {b.pr !== null ? `${(b.pr * 100).toFixed(1)}%` : '—'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </Card>

                {/* Best & Worst Inverters */}
                <Card
                  title="Inverter Leaderboard"
                  subtitle="Top 5 best & worst performers by Performance Ratio"
                >
                  {plantDetailLoading ? (
                    <div className="space-y-2 pt-2">
                      <Skeleton className="w-full h-12" />
                      <Skeleton className="w-full h-12" />
                    </div>
                  ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      {/* Best */}
                      <div className="border border-[#16A34A]/20 bg-[#F0FDF4]/50 rounded-lg p-3">
                        <span className="text-xs font-bold text-[#16A34A] block mb-2">
                          Top 5 Inverters
                        </span>
                        <div className="space-y-1.5">
                          {plantDetail?.inverters_best?.map((inv) => (
                            <div
                              key={inv.inverter_id}
                              className="flex items-center justify-between text-xs py-1 border-b border-[#16A34A]/10 last:border-0"
                            >
                              <span className="font-medium text-[#0F172A]">{inv.inverter_id}</span>
                              <span className="font-bold text-[#16A34A]">
                                {inv.pr !== null ? `${(inv.pr * 100).toFixed(1)}%` : '—'}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Worst */}
                      <div className="border border-[#DC2626]/20 bg-[#FEF2F2]/50 rounded-lg p-3">
                        <span className="text-xs font-bold text-[#DC2626] block mb-2">
                          Bottom 5 Inverters
                        </span>
                        <div className="space-y-1.5">
                          {plantDetail?.inverters_worst?.map((inv) => (
                            <div
                              key={inv.inverter_id}
                              className="flex items-center justify-between text-xs py-1 border-b border-[#DC2626]/10 last:border-0"
                            >
                              <span className="font-medium text-[#0F172A]">{inv.inverter_id}</span>
                              <span className="font-bold text-[#DC2626]">
                                {inv.pr !== null ? `${(inv.pr * 100).toFixed(1)}%` : '—'}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}
                </Card>
              </div>

              {/* Weather Telemetry Trend */}
              <Card
                title="Weather & Panel Telemetry"
                subtitle="Ambient temp, module surface temp, and soiling ratio over time"
              >
                {plantDetailLoading ? (
                  <Skeleton className="h-64" />
                ) : !plantDetail?.weather || plantDetail.weather.length === 0 ? (
                  <p className="text-xs text-[#64748B] py-8 text-center">No weather logs available.</p>
                ) : (
                  <div className="h-64 w-full pt-2">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart
                        data={plantDetail.weather}
                        margin={{ top: 10, right: 30, left: 10, bottom: 5 }}
                      >
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                        <XAxis
                          dataKey="date"
                          tick={{ fontSize: 11, fill: '#64748B' }}
                          stroke="#E2E8F0"
                        />
                        <YAxis
                          yAxisId="temp"
                          unit="°C"
                          tick={{ fontSize: 11, fill: '#64748B' }}
                          stroke="#E2E8F0"
                        />
                        <YAxis
                          yAxisId="soiling"
                          orientation="right"
                          domain={[0.8, 1.0]}
                          tick={{ fontSize: 11, fill: '#7C3AED' }}
                          stroke="#7C3AED"
                        />
                        <Tooltip
                          content={({ active, payload, label }) => {
                            if (!active || !payload?.length) return null;
                            return (
                              <div className="bg-white border border-[#E2E8F0] shadow-md rounded-lg p-2.5 text-xs">
                                <p className="font-semibold text-[#0F172A] mb-1">{label}</p>
                                {payload.map((p) => (
                                  <p key={p.dataKey} style={{ color: p.color }}>
                                    {p.name}: {p.value}
                                  </p>
                                ))}
                              </div>
                            );
                          }}
                        />
                        <Legend verticalAlign="top" align="right" wrapperStyle={{ fontSize: 12 }} />
                        <Line
                          yAxisId="temp"
                          type="monotone"
                          dataKey="ambient_c"
                          name="Ambient (°C)"
                          stroke="#2563EB"
                          strokeWidth={1.5}
                          dot={false}
                        />
                        <Line
                          yAxisId="temp"
                          type="monotone"
                          dataKey="module_c"
                          name="Module Temp (°C)"
                          stroke="#D97706"
                          strokeWidth={1.5}
                          dot={false}
                        />
                        <Line
                          yAxisId="soiling"
                          type="monotone"
                          dataKey="soiling_ratio"
                          name="Soiling Ratio"
                          stroke="#7C3AED"
                          strokeWidth={2}
                          dot={false}
                        />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </Card>

              {/* Inverter Treemap / Hierarchy Grid */}
              <Card
                title="Inverter Fleet Grid"
                subtitle="All inverters grouped by block with rated DC capacity and PR"
              >
                {plantDetailLoading ? (
                  <Skeleton className="h-48" />
                ) : !plantDetail?.treemap || plantDetail.treemap.length === 0 ? (
                  <p className="text-xs text-[#64748B] py-6 text-center">No inverter details found.</p>
                ) : (
                  <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-2.5 pt-2">
                    {plantDetail.treemap.map((inv) => {
                      const prVal = inv.pr !== null ? inv.pr * 100 : null;
                      let bgStatus = 'bg-[#F0FDF4] border-[#16A34A]/30 text-[#16A34A]';
                      if (prVal === null || prVal === 0) {
                        bgStatus = 'bg-[#FEF2F2] border-[#DC2626]/30 text-[#DC2626]';
                      } else if (prVal < 80) {
                        bgStatus = 'bg-[#FFFBEB] border-[#D97706]/30 text-[#D97706]';
                      }

                      return (
                        <div
                          key={`${inv.block}_${inv.inverter}`}
                          className={`border rounded-lg p-2 flex flex-col justify-between text-xs ${bgStatus}`}
                        >
                          <span className="font-semibold text-[#0F172A] truncate">
                            {inv.inverter}
                          </span>
                          <span className="text-[10px] text-[#64748B] truncate">{inv.block}</span>
                          <div className="mt-2 flex items-baseline justify-between font-bold">
                            <span>{prVal !== null ? `${prVal.toFixed(1)}%` : '0%'}</span>
                            <span className="text-[10px] font-normal text-[#64748B]">
                              {inv.rated_dc_kw}kW
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </Card>
            </>
          )}
        </div>
      )}
    </div>
  );
}
