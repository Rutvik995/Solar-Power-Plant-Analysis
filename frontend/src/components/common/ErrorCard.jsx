import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

/**
 * Reusable ErrorCard component for displaying fetch or execution failures in light theme.
 */
export default function ErrorCard({
  title = 'Something went wrong',
  message,
  onRetry,
  className = '',
}) {
  return (
    <div
      className={`border border-[#DC2626]/20 bg-[#FEF2F2] text-[#DC2626] rounded-lg p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 ${className}`}
    >
      <div className="flex items-start gap-3">
        <div className="p-1.5 rounded-md bg-[#DC2626]/10 text-[#DC2626] shrink-0 mt-0.5">
          <AlertTriangle className="w-4 h-4" />
        </div>
        <div>
          <h4 className="font-semibold text-sm text-[#0F172A]">{title}</h4>
          <p className="text-xs text-muted mt-0.5 leading-relaxed">
            {message || 'An unexpected error occurred while processing data.'}
          </p>
        </div>
      </div>
      {onRetry && (
        <button
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-white border border-[#DC2626]/30 hover:bg-[#FEF2F2] text-[#DC2626] transition-colors shrink-0"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          <span>Retry</span>
        </button>
      )}
    </div>
  );
}
