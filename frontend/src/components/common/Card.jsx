import React from 'react';

/**
 * Reusable Card container component with clean light theme styling.
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
    <div className={`bg-white border border-[#E2E8F0] rounded-lg p-5 shadow-none transition-colors ${className}`}>
      {(title || subtitle || action) && (
        <div className="flex items-center justify-between pb-3 mb-4 border-b border-[#E2E8F0]">
          <div>
            {title && <h3 className="text-base font-semibold text-[#0F172A]">{title}</h3>}
            {subtitle && <p className="text-xs text-[#64748B] mt-0.5">{subtitle}</p>}
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
