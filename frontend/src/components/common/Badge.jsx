import React from 'react';

const VARIANTS = {
  primary: 'bg-[#EFF6FF] text-[#2563EB] border-[#2563EB]/20',
  success: 'bg-[#F0FDF4] text-[#16A34A] border-[#16A34A]/20',
  warning: 'bg-[#FFFBEB] text-[#D97706] border-[#D97706]/20',
  danger: 'bg-[#FEF2F2] text-[#DC2626] border-[#DC2626]/20',
  teal: 'bg-[#F0FDFA] text-[#0D9488] border-[#0D9488]/20',
  sky: 'bg-[#F0F9FF] text-[#0284C7] border-[#0284C7]/20',
  violet: 'bg-[#F5F3FF] text-[#7C3AED] border-[#7C3AED]/20',
  muted: 'bg-[#F1F5F9] text-[#64748B] border-[#E2E8F0]',
};

/**
 * Reusable Badge component with light theme tokens.
 */
export default function Badge({
  children,
  variant = 'muted',
  className = '',
  pill = true,
}) {
  const variantClass = VARIANTS[variant] || VARIANTS.muted;
  const roundedClass = pill ? 'rounded-full' : 'rounded-[6px]';

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 text-xs font-medium border ${roundedClass} ${variantClass} ${className}`}
    >
      {children}
    </span>
  );
}
