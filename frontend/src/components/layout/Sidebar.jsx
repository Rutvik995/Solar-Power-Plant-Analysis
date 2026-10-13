import React from 'react';
import { NavLink } from 'react-router-dom';
import { LayoutDashboard, MessageSquareText, Info, Sun, Zap, Radio } from 'lucide-react';

const NAV_ITEMS = [
  {
    name: 'Ask the System',
    path: '/',
    icon: MessageSquareText,
  },
  {
    name: 'About',
    path: '/about',
    icon: Info,
  },
];

export default function Sidebar() {
  return (
    <aside className="w-64 bg-surface border-r border-border flex flex-col shrink-0 min-h-screen">
      {/* Brand Header */}
      <div className="h-16 px-6 border-b border-border flex items-center gap-3">
        <div className="w-9 h-9 rounded-xl bg-primary/20 border border-primary/40 flex items-center justify-center text-primary shadow-sm shadow-primary/10">
          <Sun className="w-5 h-5 text-primary animate-pulse" />
        </div>
        <div>
          <h1 className="text-sm font-bold tracking-tight text-text flex items-center gap-1.5">
            SolarAnalytics
            <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-primary/15 text-primary border border-primary/30">
              v1.0
            </span>
          </h1>
          <p className="text-[11px] text-muted">Multi-Agent Intelligence</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-4 space-y-1.5">
        <div className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-wider text-muted">
          Platform Menu
        </div>
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3.5 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-primary/15 text-primary border border-primary/30 shadow-sm'
                    : 'text-muted hover:text-text hover:bg-card border border-transparent'
                }`
              }
            >
              <Icon className="w-4 h-4 shrink-0" />
              <span>{item.name}</span>
            </NavLink>
          );
        })}
      </nav>

      {/* System Status Footer */}
      <div className="p-4 border-t border-border mt-auto">
        <div className="bg-card border border-border/80 rounded-xl p-3">
          <div className="flex items-center justify-between text-xs mb-1.5">
            <span className="text-muted flex items-center gap-1.5">
              <Radio className="w-3.5 h-3.5 text-teal animate-pulse" />
              Live Telemetry
            </span>
            <span className="text-[10px] px-1.5 py-0.2 rounded bg-teal/15 text-teal border border-teal/30 font-medium">
              Active
            </span>
          </div>
          <div className="text-[11px] text-muted leading-tight">
            5 PV Plants • 21 Blocks
          </div>
        </div>
      </div>
    </aside>
  );
}
