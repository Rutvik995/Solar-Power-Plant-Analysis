import React from 'react';

/**
 * Reusable Card container component.
 */
export default function Card({
  title,
  subtitle,
  action,
  children,
  className = '',
  bodyClassName = '',
}) {
  return (
    <div className={`bg-card border border-border rounded-xl p-5 shadow-sm transition-all duration-200 ${className}`}>
      {(title || subtitle || action) && (
        <div className="flex items-center justify-between pb-3 mb-4 border-b border-border/60">
          <div>
            {title && <h3 className="text-base font-semibold text-text">{title}</h3>}
            {subtitle && <p className="text-xs text-muted mt-0.5">{subtitle}</p>}
          </div>
          {action && <div className="flex items-center gap-2">{action}</div>}
        </div>
      )}
      <div className={bodyClassName}>
        {children}
      </div>
    </div>
  );
}
