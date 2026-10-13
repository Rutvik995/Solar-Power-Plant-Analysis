import React from 'react';
import Skeleton from './Skeleton';
import Badge from './Badge';
import { ArrowUpRight, ArrowDownRight, Minus } from 'lucide-react';
import { ResponsiveContainer, LineChart, Line } from 'recharts';

/**
 * Reusable KpiCard component with metric display, delta indicator, and optional sparkline.
 */
export default function KpiCard({
  title,
  value,
  unit = '',
  previousValue,
  delta,
  deltaIsGood = true, // true if increase is positive (like yield/PR), false if increase is bad (like soiling/faults)
  icon: Icon,
  sparklineData = [],
  loading = false,
  className = '',
}) {
  if (loading) {
    return (
      <div className={`bg-card border border-border rounded-xl p-5 ${className}`}>
        <div className="flex justify-between items-center mb-3">
          <Skeleton className="w-24 h-4" />
          <Skeleton className="w-8 h-8 rounded-lg" />
        </div>
        <Skeleton className="w-32 h-8 mb-2" />
        <Skeleton className="w-20 h-4" />
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
    <div className={`bg-card border border-border rounded-xl p-5 shadow-sm hover:border-border/90 transition-all ${className}`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-muted uppercase tracking-wider">{title}</span>
        {Icon && (
          <div className="p-2 rounded-lg bg-surface border border-border/60 text-primary">
            <Icon className="w-4 h-4" />
          </div>
        )}
      </div>

      <div className="flex items-baseline gap-2 mb-3">
        <span className="text-2xl font-bold tracking-tight text-text">
          {value !== null && value !== undefined ? value : '—'}
        </span>
        {unit && <span className="text-xs text-muted font-normal">{unit}</span>}
      </div>

      <div className="flex items-center justify-between pt-2 border-t border-border/40 text-xs">
        {hasDelta ? (
          <div className="flex items-center gap-1.5">
            <Badge variant={deltaVariant}>
              {isPositive && <ArrowUpRight className="w-3 h-3" />}
              {isNegative && <ArrowDownRight className="w-3 h-3" />}
              {isNeutral && <Minus className="w-3 h-3" />}
              {Math.abs(computedDelta).toFixed(1)}%
            </Badge>
            <span className="text-muted">vs prev</span>
          </div>
        ) : (
          <span className="text-muted text-[11px]">No previous period</span>
        )}

        {sparklineData && sparklineData.length > 1 && (
          <div className="w-20 h-6">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={sparklineData}>
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke="#F5B301"
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
