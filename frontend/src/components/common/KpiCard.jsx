import React, { useState } from 'react';
import Skeleton from './Skeleton';
import Badge from './Badge';
import { ArrowUpRight, ArrowDownRight, Minus, HelpCircle } from 'lucide-react';
import { ResponsiveContainer, LineChart, Line } from 'recharts';

/**
 * Reusable KpiCard component with clean light theme styling, sparklines,
 * reversed-arrow support for inverted metrics, and glossary ? tooltips.
 */
export default function KpiCard({
  title,
  value,
  unit = '',
  previousValue,
  delta,
  deltaIsGood = true,
  invertDelta = false,
  icon: Icon,
  sparklineData = [],
  sparklineColor = '#2563EB',
  loading = false,
  tooltip = '',
  className = '',
}) {
  const [showTooltip, setShowTooltip] = useState(false);

  if (loading) {
    return (
      <div className={`bg-white border border-[#E2E8F0] rounded-lg p-5 ${className}`}>
        <div className="flex justify-between items-center mb-3">
          <Skeleton className="w-24 h-4 bg-[#E2E8F0]" />
          <Skeleton className="w-8 h-8 rounded-lg bg-[#E2E8F0]" />
        </div>
        <Skeleton className="w-32 h-8 mb-2 bg-[#E2E8F0]" />
        <Skeleton className="w-20 h-4 bg-[#E2E8F0]" />
      </div>
    );
  }

  // Handle invertDelta (e.g. for soiling loss and open alerts)
  const effectiveDeltaIsGood = invertDelta ? !deltaIsGood : deltaIsGood;

  // Calculate percentage delta if not provided directly
  let computedDelta = delta;
  if (computedDelta === undefined && value !== null && previousValue !== null && previousValue !== undefined) {
    const numCurr = Number(value);
    const numPrev = Number(previousValue);
    if (!isNaN(numCurr) && !isNaN(numPrev) && numPrev !== 0) {
      computedDelta = ((numCurr - numPrev) / numPrev) * 100;
    }
  }

  const hasDelta = computedDelta !== undefined && computedDelta !== null && !isNaN(computedDelta);
  const isPositive = hasDelta && computedDelta > 0;
  const isNegative = hasDelta && computedDelta < 0;
  const isNeutral = hasDelta && Math.abs(computedDelta) < 0.01;

  let deltaVariant = 'muted';
  if (hasDelta) {
    if (isNeutral) {
      deltaVariant = 'muted';
    } else if (isPositive) {
      deltaVariant = effectiveDeltaIsGood ? 'success' : 'danger';
    } else {
      deltaVariant = effectiveDeltaIsGood ? 'danger' : 'success';
    }
  }

  return (
    <div className={`bg-white border border-[#E2E8F0] rounded-lg p-4 shadow-none transition-colors relative ${className}`}>
      {/* 1. Header with wrapped title, inline glossary ? tooltip, and non-overlapping icon */}
      <div className="flex items-start justify-between gap-1.5 mb-2 min-h-[2.5rem]">
        <div className="flex-1 min-w-0 pr-1">
          <span className="text-xs font-semibold text-[#64748B] uppercase tracking-wider leading-snug inline">
            {title}
            {tooltip && (
              <span className="relative inline-block align-baseline ml-1">
                <button
                  type="button"
                  onMouseEnter={() => setShowTooltip(true)}
                  onMouseLeave={() => setShowTooltip(false)}
                  onClick={() => setShowTooltip((p) => !p)}
                  className="text-[#94A3B8] hover:text-[#2563EB] transition-colors p-0.5 rounded focus:outline-none align-middle -translate-y-px"
                  aria-label={`Definition for ${title}`}
                >
                  <HelpCircle className="w-3.5 h-3.5" />
                </button>
                {showTooltip && (
                  <div className="absolute z-50 bottom-full left-0 mb-2 w-64 p-2.5 bg-[#0F172A] text-white text-xs rounded-lg shadow-xl border border-slate-700 pointer-events-none leading-relaxed normal-case tracking-normal font-normal">
                    <span className="block font-semibold text-[#93C5FD] mb-0.5">{title}</span>
                    <span>{tooltip}</span>
                    <div className="absolute top-full left-3 border-4 border-transparent border-t-[#0F172A]" />
                  </div>
                )}
              </span>
            )}
          </span>
        </div>
        {Icon && (
          <div className="p-1.5 rounded-lg bg-[#EFF6FF] border border-[#2563EB]/20 text-[#2563EB] shrink-0 self-start">
            <Icon className="w-4 h-4" />
          </div>
        )}
      </div>

      {/* 2. Value */}
      <div className="flex items-baseline gap-1.5 mb-1.5">
        <span className="text-2xl font-bold tracking-tight text-[#0F172A]">
          {value !== null && value !== undefined ? value : '—'}
        </span>
        {unit && <span className="text-xs text-[#64748B] font-medium">{unit}</span>}
      </div>

      {/* 3. Visible Sparkline */}
      <div className="w-full h-8 my-2">
        {sparklineData && sparklineData.length > 1 ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={sparklineData} margin={{ top: 2, right: 2, left: 2, bottom: 2 }}>
              <Line
                type="monotone"
                dataKey="value"
                stroke={sparklineColor || '#2563EB'}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-full border-b border-dashed border-[#E2E8F0]/70 flex items-center" />
        )}
      </div>

      {/* 4. Change Line */}
      <div className="flex items-center justify-between pt-2 border-t border-[#E2E8F0] text-xs">
        {hasDelta ? (
          <div className="flex items-center gap-1.5">
            <Badge variant={deltaVariant}>
              {isPositive && <ArrowUpRight className="w-3 h-3" />}
              {isNegative && <ArrowDownRight className="w-3 h-3" />}
              {isNeutral && <Minus className="w-3 h-3" />}
              {Math.abs(computedDelta).toFixed(1)}%
            </Badge>
            <span className="text-[#64748B]">vs prev</span>
          </div>
        ) : (
          <span />
        )}
      </div>
    </div>
  );
}
