import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

/**
 * Reusable ErrorCard component for displaying fetch or execution failures.
 */
export default function ErrorCard({
  title = 'Something went wrong',
  message,
  onRetry,
  className = '',
}) {
  return (
    <div
      className={`border border-danger/30 bg-danger/10 text-danger rounded-xl p-5 flex flex-col md:flex-row items-start md:items-center justify-between gap-4 ${className}`}
    >
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-danger/20 text-danger shrink-0 mt-0.5">
          <AlertTriangle className="w-5 h-5" />
        </div>
        <div>
          <h4 className="font-semibold text-sm text-text">{title}</h4>
          <p className="text-xs text-muted mt-1 leading-relaxed">
            {message || 'An unexpected error occurred while processing data.'}
          </p>
        </div>
      </div>
      {onRetry && (
        <button
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-danger/20 hover:bg-danger/30 text-danger transition-colors shrink-0"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Retry
        </button>
      )}
    </div>
  );
}
