import React, { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchDashboardPlantDetail } from '../../services/api';

import Card from '../common/Card';
import Badge from '../common/Badge';
import Skeleton from '../common/Skeleton';
import ErrorCard from '../common/ErrorCard';
import InverterHeatmap, { getPrHeatmapColor } from './InverterHeatmap';
import InverterDrawer from './InverterDrawer';

import {
  Building2,
  AlertTriangle,
  Award,
  TrendingDown,
  CloudSun,
  Thermometer,
  Layers,
  ChevronRight,
  Sun,
  Droplets,
} from 'lucide-react';

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  Cell,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
  Treemap,
} from 'recharts';

/**
 * Custom SVG node renderer for Recharts Treemap.
 */
function TreemapContentNode({
  x,
  y,
  width,
  height,
  depth,
  name,
  pr,
  size,
  block,
  onSelectInverter,
}) {
  if (depth === 1) {
    // Block container level: render container outline with block label
    return (
      <g>
        <rect
          x={x}
          y={y}
          width={width}
          height={height}
          style={{
            fill: '#F8FAFC',
            stroke: '#CBD5E1',
            strokeWidth: 2,
          }}
        />
        {width > 60 && height > 20 && (
          <text
            x={x + 6}
            y={y + 14}
            fill="#64748B"
            fontSize={10}
            fontWeight={600}
            className="pointer-events-none select-none uppercase tracking-wider"
          >
            {name}
          </text>
        )}
      </g>
    );
  }

  // Leaf level: inverter
  const color = getPrHeatmapColor(pr);
  const prPct = pr !== null && pr !== undefined ? (pr <= 1 ? pr * 100 : pr).toFixed(1) : null;
  const isClickable = Boolean(name && depth >= 2);

  return (
    <g
      onClick={() => isClickable && onSelectInverter(name)}
      className={isClickable ? 'cursor-pointer group' : ''}
    >
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        style={{
          fill: color,
          stroke: '#FFFFFF',
          strokeWidth: 1.5,
          rx: 3,
        }}
        className="transition-opacity group-hover:opacity-85"
      />
      {width > 42 && height > 24 && (
        <text
          x={x + 4}
          y={y + 13}
          fill="#FFFFFF"
          fontSize={10}
          fontWeight={600}
          className="pointer-events-none select-none drop-shadow-xs"
        >
          {name?.length > 12 ? name.slice(-6) : name}
        </text>
      )}
      {width > 42 && height > 36 && prPct && (
        <text
          x={x + 4}
          y={y + 25}
          fill="#FFFFFF"
          fontSize={9}
          className="pointer-events-none select-none opacity-90 drop-shadow-xs"
        >
          {prPct}%
        </text>
      )}
    </g>
  );
}

export default function PlantDetailView({
  plantId,
  startDate,
  endDate,
  agg = 'day',
  plantsMeta = [],
  onSelectPlant,
}) {
  // Drawer state for inspecting single inverter
  const [activeInverterId, setActiveInverterId] = useState(null);

  // TanStack Query for plant detail
  const isPlantSelected = Boolean(plantId) && plantId !== 'all';
  const {
    data: plantDetail,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['dashboard', 'plant', { plantId, start: startDate, end: endDate, agg }],
    queryFn: () => fetchDashboardPlantDetail({ plantId, start: startDate, end: endDate, agg }),
    enabled: isPlantSelected,
  });

  // Identify weakest block (highlighted in danger color #DC2626)
  const { blocksData, weakestBlockId } = useMemo(() => {
    const rawBlocks = plantDetail?.blocks || [];
    let minPr = Infinity;
    let weakestId = null;

    rawBlocks.forEach((b) => {
      if (b.pr !== null && b.pr !== undefined && b.pr < minPr) {
        minPr = b.pr;
        weakestId = b.block_id || b.block;
      }
    });

    const formatted = rawBlocks.map((b) => ({
      ...b,
      pr_pct: b.pr !== null && b.pr !== undefined ? Number((b.pr * 100).toFixed(1)) : 0,
    }));

    return { blocksData: formatted, weakestBlockId: weakestId };
  }, [plantDetail?.blocks]);

  // Transform Treemap data: hierarchy [ { name: block, children: [ { name: inv, size: rated_dc, pr } ] } ]
  const treemapHierarchy = useMemo(() => {
    if (!plantDetail?.treemap || plantDetail.treemap.length === 0) return [];
    const blockMap = new Map();

    plantDetail.treemap.forEach((item) => {
      const blk = item.block || 'Block';
      if (!blockMap.has(blk)) {
        blockMap.set(blk, {
          name: blk,
          children: [],
        });
      }
      blockMap.get(blk).children.push({
        name: item.inverter,
        block: blk,
        size: Number(item.rated_dc_kw) || 1000,
        pr: item.pr,
      });
    });

    return Array.from(blockMap.values());
  }, [plantDetail?.treemap]);

  // Current plant object
  const currentPlant = useMemo(() => {
    return plantsMeta.find((p) => String(p.id) === String(plantId));
  }, [plantsMeta, plantId]);

  // Handler for opening inverter drawer
  const handleOpenDrawer = (invId) => {
    if (invId) {
      setActiveInverterId(invId);
    }
  };

  // IF NO PLANT SELECTED ("all"), SHOW PLANT PICKER PROMPT
  if (!isPlantSelected) {
    return (
      <Card className="text-center py-12 max-w-2xl mx-auto shadow-sm">
        <div className="w-12 h-12 rounded-xl bg-[#EFF6FF] border border-[#2563EB]/20 flex items-center justify-center text-[#2563EB] mx-auto mb-3">
          <Building2 className="w-6 h-6" />
        </div>
        <h3 className="text-base font-bold text-[#0F172A]">Select a Plant to View Analytics</h3>
        <p className="text-xs text-[#64748B] max-w-md mx-auto mt-1 mb-6 leading-relaxed">
          Deep-dive telemetry including physical block PRs, inverter leaderboards, full-period heatmaps,
          and component treemaps requires focusing on a single plant asset.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 max-w-lg mx-auto">
          {plantsMeta.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => onSelectPlant(p.id)}
              className="p-3 bg-white border border-[#E2E8F0] hover:border-[#2563EB] hover:bg-[#EFF6FF] rounded-xl transition-all text-left group shadow-xs hover:shadow-sm"
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-bold text-xs text-[#0F172A] group-hover:text-[#2563EB]">
                  {p.name || `Plant ${p.id}`}
                </span>
                <ChevronRight className="w-3.5 h-3.5 text-[#94A3B8] group-hover:text-[#2563EB] transition-transform group-hover:translate-x-0.5" />
              </div>
              <p className="text-[11px] text-[#64748B]">Capacity: {p.capacity_mwp} MWp</p>
            </button>
          ))}
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {/* Plant Header Information Banner */}
      <div className="bg-white border border-[#E2E8F0] rounded-xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-xs">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-[#EFF6FF] border border-[#2563EB]/20 flex items-center justify-center text-[#2563EB] font-bold text-sm shrink-0">
            P{plantId}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-bold text-[#0F172A]">
                {currentPlant?.name || `Plant ${plantId}`}
              </h2>
              <Badge variant="primary">{currentPlant?.capacity_mwp} MWp DC</Badge>
            </div>
            <p className="text-xs text-[#64748B] mt-0.5">
              Scope: {startDate} to {endDate} ({agg}) • Click any inverter across charts to view full telemetry
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={() => onSelectPlant('all')}
          className="self-start sm:self-auto px-3 py-1.5 text-xs font-medium text-[#64748B] hover:text-[#0F172A] bg-[#F8FAFC] hover:bg-[#F1F5F9] rounded-lg border border-[#E2E8F0] transition-colors"
        >
          ← Change Plant
        </button>
      </div>

      {error ? (
        <ErrorCard
          title="Failed to load plant telemetry"
          message={error.message}
          onRetry={refetch}
        />
      ) : (
        <>
          {/* 1. Block PR Bars & Inverter Leaderboards */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Block PR Bars */}
            <Card
              title="Block Performance Ratio (PR)"
              subtitle="Weakest block highlighted in danger red (#DC2626)"
            >
              {isLoading ? (
                <Skeleton className="h-60" />
              ) : blocksData.length === 0 ? (
                <div className="h-48 flex items-center justify-center text-xs text-[#64748B]">
                  No block telemetry reported for this plant.
                </div>
              ) : (
                <div className="h-60 w-full pt-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={blocksData}
                      layout="vertical"
                      margin={{ top: 10, right: 30, left: 10, bottom: 5 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#E2E8F0" />
                      <XAxis
                        type="number"
                        domain={[60, 100]}
                        unit="%"
                        tick={{ fontSize: 11, fill: '#64748B' }}
                      />
                      <YAxis
                        type="category"
                        dataKey="block"
                        tick={{ fontSize: 11, fill: '#0F172A', fontWeight: 500 }}
                        width={80}
                      />
                      <Tooltip
                        content={({ active, payload }) => {
                          if (!active || !payload?.length) return null;
                          const b = payload[0].payload;
                          const isWeakest = (b.block_id || b.block) === weakestBlockId;
                          return (
                            <div className="bg-white border border-[#E2E8F0] shadow-md rounded-lg p-2.5 text-xs">
                              <p className="font-semibold text-[#0F172A]">{b.block}</p>
                              <p
                                className="font-bold mt-1"
                                style={{ color: isWeakest ? '#DC2626' : '#0D9488' }}
                              >
                                PR: {b.pr_pct}% {isWeakest && '(Weakest Block)'}
                              </p>
                              <p className="text-[#64748B]">Energy: {b.energy_mwh} MWh</p>
                              <p className="text-[#64748B]">Inverters: {b.inverter_count} units</p>
                            </div>
                          );
                        }}
                      />
                      <Bar dataKey="pr_pct" radius={[0, 4, 4, 0]}>
                        {blocksData.map((entry) => {
                          const isWeakest = (entry.block_id || entry.block) === weakestBlockId;
                          return (
                            <Cell
                              key={entry.block_id || entry.block}
                              fill={isWeakest ? '#DC2626' : '#0D9488'}
                            />
                          );
                        })}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </Card>

            {/* Best 5 & Worst 5 Inverters (Horizontal Bar Lists) */}
            <Card
              title="Inverter Leaderboard"
              subtitle="Top 5 and Bottom 5 inverters by PR • Click to inspect"
            >
              {isLoading ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <Skeleton className="h-56" />
                  <Skeleton className="h-56" />
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-1">
                  {/* Best 5 */}
                  <div className="border border-[#16A34A]/20 bg-[#F0FDF4]/40 rounded-xl p-3 flex flex-col justify-between">
                    <div>
                      <div className="flex items-center gap-1.5 text-xs font-bold text-[#16A34A] mb-3">
                        <Award className="w-4 h-4 text-[#16A34A]" />
                        <span>Top 5 Inverters</span>
                      </div>
                      <div className="space-y-2.5">
                        {plantDetail?.inverters_best?.map((inv) => {
                          const pct = inv.pr !== null ? (inv.pr * 100).toFixed(1) : 0;
                          return (
                            <div
                              key={inv.inverter_id}
                              onClick={() => handleOpenDrawer(inv.inverter_id)}
                              className="group cursor-pointer"
                              title="Click to open inverter drawer"
                            >
                              <div className="flex items-center justify-between text-xs mb-1">
                                <span className="font-semibold text-[#0F172A] group-hover:text-[#2563EB] truncate">
                                  {inv.inverter_id}
                                </span>
                                <span className="font-bold text-[#16A34A] shrink-0 ml-1">
                                  {pct}%
                                </span>
                              </div>
                              <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
                                <div
                                  className="bg-[#16A34A] h-full rounded-full transition-all"
                                  style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
                                />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </div>

                  {/* Worst 5 */}
                  <div className="border border-[#DC2626]/20 bg-[#FEF2F2]/40 rounded-xl p-3 flex flex-col justify-between">
                    <div>
                      <div className="flex items-center gap-1.5 text-xs font-bold text-[#DC2626] mb-3">
                        <TrendingDown className="w-4 h-4 text-[#DC2626]" />
                        <span>Bottom 5 Inverters</span>
                      </div>
                      <div className="space-y-2.5">
                        {plantDetail?.inverters_worst?.map((inv) => {
                          const pct = inv.pr !== null ? (inv.pr * 100).toFixed(1) : 0;
                          return (
                            <div
                              key={inv.inverter_id}
                              onClick={() => handleOpenDrawer(inv.inverter_id)}
                              className="group cursor-pointer"
                              title="Click to open inverter drawer"
                            >
                              <div className="flex items-center justify-between text-xs mb-1">
                                <span className="font-semibold text-[#0F172A] group-hover:text-[#2563EB] truncate">
                                  {inv.inverter_id}
                                </span>
                                <span className="font-bold text-[#DC2626] shrink-0 ml-1">
                                  {pct}%
                                </span>
                              </div>
                              <div className="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
                                <div
                                  className="bg-[#DC2626] h-full rounded-full transition-all"
                                  style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
                                />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </Card>
          </div>

          {/* 2. Inverter x Date Heatmap */}
          <Card
            title="Inverter Daily Performance Heatmap"
            subtitle="Full inverter PR matrix over time • Hover for telemetry, click to inspect inverter"
          >
            <InverterHeatmap
              plantId={plantId}
              startDate={startDate}
              endDate={endDate}
              onSelectInverter={handleOpenDrawer}
            />
          </Card>

          {/* 3. Recharts Treemap Hierarchy */}
          <Card
            title="Plant Topology Treemap"
            subtitle="Nested blocks and inverters, sized by rated DC capacity (kW) and colored by PR"
          >
            {isLoading ? (
              <Skeleton className="h-72" />
            ) : treemapHierarchy.length === 0 ? (
              <div className="h-44 flex items-center justify-center text-xs text-[#64748B]">
                No topology records available for treemap rendering.
              </div>
            ) : (
              <div className="h-80 w-full pt-2">
                <ResponsiveContainer width="100%" height="100%">
                  <Treemap
                    data={treemapHierarchy}
                    dataKey="size"
                    aspectRatio={4 / 3}
                    stroke="#FFFFFF"
                    content={<TreemapContentNode onSelectInverter={handleOpenDrawer} />}
                  />
                </ResponsiveContainer>
              </div>
            )}
          </Card>

          {/* 4. Weather Panel: Dual Chart */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Chart 1: Ambient and Module Temperature */}
            <Card
              title="Thermal Telemetry"
              subtitle="Ambient air temperature vs. PV module surface temperature (°C)"
            >
              {isLoading ? (
                <Skeleton className="h-64" />
              ) : !plantDetail?.weather || plantDetail.weather.length === 0 ? (
                <div className="h-48 flex items-center justify-center text-xs text-[#64748B]">
                  No ambient thermal logs available.
                </div>
              ) : (
                <div className="h-64 w-full pt-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={plantDetail.weather} margin={{ top: 10, right: 20, left: -10, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 10, fill: '#64748B' }}
                        tickFormatter={(v) => v.slice(5)}
                      />
                      <YAxis
                        unit="°C"
                        tick={{ fontSize: 10, fill: '#64748B' }}
                        stroke="#64748B"
                      />
                      <Tooltip
                        content={({ active, payload, label }) => {
                          if (!active || !payload?.length) return null;
                          return (
                            <div className="bg-white border border-[#E2E8F0] shadow-md rounded-lg p-2 text-xs">
                              <p className="font-semibold text-[#0F172A]">{label}</p>
                              {payload.map((p) => (
                                <p key={p.dataKey} style={{ color: p.color }}>
                                  {p.name}: {p.value}°C
                                </p>
                              ))}
                            </div>
                          );
                        }}
                      />
                      <Legend verticalAlign="top" align="right" wrapperStyle={{ fontSize: 11 }} />
                      <Line
                        type="monotone"
                        dataKey="avg_ambient_temp_c"
                        name="Ambient Temp"
                        stroke="#2563EB"
                        strokeWidth={2}
                        dot={false}
                      />
                      <Line
                        type="monotone"
                        dataKey="max_module_temp_c"
                        name="Module Temp"
                        stroke="#D97706"
                        strokeWidth={2}
                        dot={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </Card>

            {/* Chart 2: Irradiance & Soiling Ratio (Violet) */}
            <Card
              title="Irradiance & Soiling Telemetry"
              subtitle="Daily solar irradiance (kWh/m²) and surface soiling ratio"
            >
              {isLoading ? (
                <Skeleton className="h-64" />
              ) : !plantDetail?.weather || plantDetail.weather.length === 0 ? (
                <div className="h-48 flex items-center justify-center text-xs text-[#64748B]">
                  No irradiance or soiling telemetry recorded.
                </div>
              ) : (
                <div className="h-64 w-full pt-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={plantDetail.weather} margin={{ top: 10, right: 25, left: -10, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 10, fill: '#64748B' }}
                        tickFormatter={(v) => v.slice(5)}
                      />
                      <YAxis
                        yAxisId="irr"
                        unit="kWh"
                        tick={{ fontSize: 10, fill: '#D97706' }}
                        stroke="#D97706"
                      />
                      <YAxis
                        yAxisId="soiling"
                        orientation="right"
                        domain={[0.8, 1.0]}
                        tick={{ fontSize: 10, fill: '#7C3AED' }}
                        stroke="#7C3AED"
                      />
                      <Tooltip
                        content={({ active, payload, label }) => {
                          if (!active || !payload?.length) return null;
                          return (
                            <div className="bg-white border border-[#E2E8F0] shadow-md rounded-lg p-2 text-xs">
                              <p className="font-semibold text-[#0F172A]">{label}</p>
                              {payload.map((p) => (
                                <p key={p.dataKey} style={{ color: p.color }}>
                                  {p.name}: {p.value}
                                </p>
                              ))}
                            </div>
                          );
                        }}
                      />
                      <Legend verticalAlign="top" align="right" wrapperStyle={{ fontSize: 11 }} />
                      <Line
                        yAxisId="irr"
                        type="monotone"
                        dataKey="total_solar_radiation_kwh_m2"
                        name="Irradiance (kWh/m²)"
                        stroke="#D97706"
                        strokeWidth={2}
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
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </Card>
          </div>

          {/* Inverter Detail Slide Drawer */}
          {activeInverterId && (
            <InverterDrawer
              inverterId={activeInverterId}
              startDate={startDate}
              endDate={endDate}
              agg={agg}
              onClose={() => setActiveInverterId(null)}
            />
          )}
        </>
      )}
    </div>
  );
}
