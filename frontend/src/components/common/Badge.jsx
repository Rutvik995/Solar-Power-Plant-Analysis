import React from 'react';

const VARIANTS = {
  primary: 'bg-primary/15 text-primary border-primary/30',
  success: 'bg-success/15 text-success border-success/30',
  warning: 'bg-warning/15 text-warning border-warning/30',
  danger: 'bg-danger/15 text-danger border-danger/30',
  teal: 'bg-teal/15 text-teal border-teal/30',
  sky: 'bg-sky/15 text-sky border-sky/30',
  violet: 'bg-violet/15 text-violet border-violet/30',
  muted: 'bg-surface text-muted border-border',
};

/**
 * Reusable Badge component.
 */
export default function Badge({
  children,
  variant = 'muted',
  className = '',
  pill = true,
}) {
  const variantClass = VARIANTS[variant] || VARIANTS.muted;
  const roundedClass = pill ? 'rounded-full' : 'rounded-md';

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 text-xs font-medium border ${roundedClass} ${variantClass} ${className}`}
    >
      {children}
    </span>
  );
}
