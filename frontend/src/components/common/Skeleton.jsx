import React from 'react';

/**
 * Reusable Skeleton loader component.
 */
export default function Skeleton({ className = '', rounded = 'rounded-md' }) {
  return (
    <div
      className={`animate-pulse bg-border/40 ${rounded} ${className}`}
      aria-hidden="true"
    />
  );
}
