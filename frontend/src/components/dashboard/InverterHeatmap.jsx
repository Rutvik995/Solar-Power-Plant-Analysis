import React, { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchDashboardHeatmapMatrix } from '../../services/api';

import Skeleton from '../common/Skeleton';
import ErrorCard from '../common/ErrorCard';
import { Info, HelpCircle } from 'lucide-react';

/**
 * Returns color from red (#DC2626) through amber (#D97706) to green (#16A34A).
 * Grey (#CBD5E1) represents null or missing data.
 */
export function getPrHeatmapColor(pr) {
  if (pr === null || pr === undefined) return '#E2E8F0'; // grey for no data
  const val = pr <= 1.0 ? pr * 100 : pr;
  if (val <= 0 || val < 70) return '#DC2626'; // Red - critical / zero gen
  if (val < 80) return '#EF4444';             // Light red
  if (val < 86) return '#F59E0B';             // Amber
  if (val < 91) return '#84CC16';             // Lime / transition
  return '#16A34A';                           // Green - optimal
}

export default function InverterHeatmap({
  plantId,
  startDate,
  endDate,
  onSelectInverter,
}) {
  const [hoveredCell, setHoveredCell] = useState(null);

  const {
    data,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['dashboard', 'heatmap', { plantId, start: startDate, end: endDate }],
    queryFn: () => fetchDashboardHeatmapMatrix({ plantId, start: startDate, end: endDate }),
    enabled: Boolean(plantId) && plantId !== 'all',
  });

  const inverters = data?.inverters || [];
  const dates = data?.dates || [];
  const prMatrix = data?.pr || [];
  const statusMatrix = data?.status || [];

  return (
    <div className="space-y-3">
      {/* Heatmap Controls & Legend */}
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
        <div className="flex items-center gap-2 text-[#64748B]">
          <span>Click any cell or inverter to inspect daily telemetry drawer.</span>
          {data?.clamped_note && (
            <span className="text-[#D97706] bg-[#FFFBEB] px-2 py-0.5 rounded border border-[#D97706]/20">
              {data.clamped_note}
            </span>
          )}
        </div>

        {/* Legend */}
        <div className="flex items-center gap-3">
          <span className="text-[#64748B] font-medium text-[11px]">PR Scale:</span>
          <div className="flex items-center gap-1.5 text-[11px] text-[#0F172A]">
            <div className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-xs bg-[#DC2626]" />
              <span>&lt;70%</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-xs bg-[#F59E0B]" />
              <span>70-85%</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-xs bg-[#84CC16]" />
              <span>86-90%</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-xs bg-[#16A34A]" />
              <span>&gt;90%</span>
            </div>
            <div className="flex items-center gap-1 ml-1 pl-1.5 border-l border-[#E2E8F0]">
              <span className="w-3 h-3 rounded-xs bg-[#E2E8F0]" />
              <span className="text-[#64748B]">No Data</span>
            </div>
          </div>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="w-full h-8" />
          <Skeleton className="w-full h-48" />
        </div>
      ) : error ? (
        <ErrorCard
          title="Failed to load PR Heatmap"
          message={error.message}
          onRetry={refetch}
        />
      ) : inverters.length === 0 || dates.length === 0 ? (
        <div className="h-44 flex items-center justify-center text-xs text-[#64748B] border border-[#E2E8F0] rounded-lg">
          No inverter records found for this period.
        </div>
      ) : (
        /* Horizontally scrollable container with sticky inverter column */
        <div className="relative border border-[#E2E8F0] rounded-xl overflow-hidden bg-white shadow-xs">
          <div className="overflow-x-auto max-h-[460px] overflow-y-auto">
            <table className="border-collapse text-left text-xs min-w-full">
              {/* Header row with dates */}
              <thead className="bg-[#F8FAFC] sticky top-0 z-20 shadow-xs">
                <tr>
                  <th className="sticky left-0 z-30 bg-[#F8FAFC] py-2 px-3 border-b border-r border-[#E2E8F0] font-semibold text-[#0F172A] min-w-[160px] text-xs">
                    Inverter Asset
                  </th>
                  {dates.map((d) => {
                    const shortDate = d.slice(5); // mm-dd
                    return (
                      <th
                        key={d}
                        className="py-2 px-1 text-center font-normal text-[10px] text-[#64748B] border-b border-r border-[#E2E8F0] min-w-[28px] max-w-[28px] select-none"
                        title={d}
                      >
                        {shortDate}
                      </th>
                    );
                  })}
                </tr>
              </thead>

              {/* Inverter Rows */}
              <tbody className="divide-y divide-[#E2E8F0]">
                {inverters.map((inv, invIdx) => {
                  const prRow = prMatrix[invIdx] || [];
                  const statusRow = statusMatrix[invIdx] || [];

                  return (
                    <tr key={inv} className="hover:bg-slate-50 transition-colors">
                      {/* Sticky Inverter Name Column */}
                      <td
                        onClick={() => onSelectInverter(inv)}
                        className="sticky left-0 z-10 bg-white py-1.5 px-3 border-r border-[#E2E8F0] font-medium text-[#0F172A] hover:text-[#2563EB] cursor-pointer whitespace-nowrap text-xs shadow-xs"
                      >
                        {inv}
                      </td>

                      {/* Heatmap Cells */}
                      {dates.map((d, dateIdx) => {
                        const prVal = prRow[dateIdx];
                        const sc = statusRow[dateIdx];
                        const cellColor = getPrHeatmapColor(prVal);
                        const isHovered =
                          hoveredCell?.inv === inv && hoveredCell?.date === d;

                        return (
                          <td
                            key={d}
                            onClick={() => onSelectInverter(inv, d)}
                            onMouseEnter={() =>
                              setHoveredCell({
                                inv,
                                date: d,
                                pr: prVal,
                                status: sc,
                              })
                            }
                            onMouseLeave={() => setHoveredCell(null)}
                            className="p-0.5 border-r border-b border-[#E2E8F0]/60 text-center cursor-pointer relative group"
                          >
                            <div
                              className="w-6 h-6 mx-auto rounded-xs transition-transform group-hover:scale-115"
                              style={{ backgroundColor: cellColor }}
                            />

                            {/* Hover Tooltip */}
                            {isHovered && (
                              <div className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-44 p-2 bg-[#0F172A] text-white text-[11px] rounded-lg shadow-xl pointer-events-none text-left leading-normal border border-slate-700">
                                <span className="font-bold text-[#93C5FD] block truncate">
                                  {inv}
                                </span>
                                <span className="text-slate-300 block">{d}</span>
                                <span className="font-semibold block mt-1">
                                  PR:{' '}
                                  {prVal !== null && prVal !== undefined
                                    ? `${(prVal <= 1 ? prVal * 100 : prVal).toFixed(1)}%`
                                    : 'No data'}
                                </span>
                                {sc !== null && sc !== undefined && (
                                  <span className="text-slate-300 block">
                                    Status code: {sc}
                                  </span>
                                )}
                                <div className="absolute top-full left-1/2 -translate-x-1/2 -mt-1 border-4 border-transparent border-t-[#0F172A]" />
                              </div>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
