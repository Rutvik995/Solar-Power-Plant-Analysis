import React from 'react';
import { NavLink } from 'react-router-dom';
import { MessageSquareText, Info, Sun, PanelLeftClose } from 'lucide-react';

const NAV_ITEMS = [
  {
    name: 'Chat',
    path: '/',
    icon: MessageSquareText,
  },
  {
    name: 'About',
    path: '/about',
    icon: Info,
  },
];

export default function Sidebar({ isOpen = true, onToggle }) {
  if (!isOpen) return null;

  return (
    <aside className="w-[220px] min-w-[220px] max-w-[220px] bg-white border-r border-[#E2E8F0] flex flex-col shrink-0 min-h-screen">
      {/* Brand Header with Collapse Button */}
      <div className="h-14 px-4 border-b border-[#E2E8F0] flex items-center justify-between">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-lg bg-[#EFF6FF] border border-[#2563EB]/20 flex items-center justify-center text-[#2563EB] shrink-0">
            <Sun className="w-4 h-4 text-[#2563EB]" />
          </div>
          <div className="min-w-0">
            <h1 className="text-sm font-semibold tracking-tight text-[#0F172A] truncate">
              Solar Plant AI
            </h1>
            <p className="text-[11px] text-[#64748B] truncate">Analytics & Ops</p>
          </div>
        </div>

        {onToggle && (
          <button
            type="button"
            onClick={onToggle}
            className="p-1 rounded hover:bg-[#F8FAFC] text-[#64748B] hover:text-[#0F172A] transition-colors ml-1 shrink-0"
            title="Collapse sidebar"
          >
            <PanelLeftClose className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-3 space-y-1">
        <div className="px-2.5 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-[#64748B]">
          Menu
        </div>
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === '/'}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-[#EFF6FF] text-[#2563EB]'
                    : 'text-[#64748B] hover:text-[#0F172A] hover:bg-[#F8FAFC]'
                }`
              }
            >
              <Icon className="w-4 h-4 shrink-0" />
              <span className="truncate">{item.name}</span>
            </NavLink>
          );
        })}
      </nav>
    </aside>
  );
}
