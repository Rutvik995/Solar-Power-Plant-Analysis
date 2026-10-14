import React from 'react';
import Skeleton from './Skeleton';
import Badge from './Badge';
import { ArrowUpRight, ArrowDownRight, Minus } from 'lucide-react';
import { ResponsiveContainer, LineChart, Line } from 'recharts';

/**
 * Reusable KpiCard component with clean light theme styling.
 */
export default function KpiCard({
  title,
  value,
  unit = '',
  previousValue,
  delta,
  deltaIsGood = true,
  icon: Icon,
  sparklineData = [],
  loading = false,
  className = '',
}) {
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
      deltaVariant = deltaIsGood ? 'success' : 'danger';
    } else {
      deltaVariant = deltaIsGood ? 'danger' : 'success';
    }
  }

  return (
    <div className={`bg-white border border-[#E2E8F0] rounded-lg p-5 shadow-none transition-colors ${className}`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-[#64748B] uppercase tracking-wider">{title}</span>
        {Icon && (
          <div className="p-2 rounded-lg bg-[#EFF6FF] border border-[#2563EB]/20 text-[#2563EB]">
            <Icon className="w-4 h-4" />
          </div>
        )}
      </div>

      <div className="flex items-baseline gap-2 mb-3">
        <span className="text-2xl font-bold tracking-tight text-[#0F172A]">
          {value !== null && value !== undefined ? value : '—'}
        </span>
        {unit && <span className="text-xs text-[#64748B] font-normal">{unit}</span>}
      </div>

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
          <span className="text-[#64748B] text-[11px]">No previous period</span>
        )}

        {sparklineData && sparklineData.length > 1 && (
          <div className="w-20 h-6">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={sparklineData}>
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke="#2563EB"
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}
