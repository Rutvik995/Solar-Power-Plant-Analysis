import React from 'react';
import { useLocation } from 'react-router-dom';
import { Plus, PanelLeftClose, PanelLeftOpen } from 'lucide-react';

const TITLES = {
  '/': 'Chat',
  '/about': 'About Platform',
};

export default function TopBar({
  mode,
  setMode,
  onNewChat,
  isSidebarOpen = true,
  onToggleSidebar,
}) {
  const location = useLocation();
  const pageTitle = TITLES[location.pathname] || 'Solar Intelligence';

  return (
    <header className="h-14 bg-white border-b border-[#E2E8F0] px-6 flex items-center justify-between sticky top-0 z-20">
      <div className="flex items-center gap-2.5">
        {onToggleSidebar && (
          <button
            type="button"
            onClick={onToggleSidebar}
            className="p-1.5 rounded-lg border border-[#E2E8F0] hover:bg-[#F8FAFC] text-[#64748B] hover:text-[#0F172A] transition-colors"
            title={isSidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
          >
            {isSidebarOpen ? (
              <PanelLeftClose className="w-4 h-4" />
            ) : (
              <PanelLeftOpen className="w-4 h-4" />
            )}
          </button>
        )}
        <h2 className="text-sm font-semibold text-[#0F172A]">{pageTitle}</h2>
      </div>

      <div className="flex items-center gap-3">
        {/* Small Live/Replay segmented toggle */}
        <div className="inline-flex items-center bg-[#F1F5F9] border border-[#E2E8F0] rounded-lg p-0.5 text-xs">
          <button
            type="button"
            onClick={() => setMode && setMode('live')}
            className={`px-2.5 py-1 rounded-[6px] transition-all ${
              mode === 'live'
                ? 'bg-white text-[#2563EB] font-semibold border border-[#E2E8F0] shadow-none'
                : 'text-[#64748B] hover:text-[#0F172A] border border-transparent'
            }`}
            title="Execute live agent queries via API"
          >
            Live
          </button>
          <button
            type="button"
            onClick={() => setMode && setMode('replay')}
            className={`px-2.5 py-1 rounded-[6px] transition-all ${
              mode === 'replay'
                ? 'bg-white text-[#2563EB] font-semibold border border-[#E2E8F0] shadow-none'
                : 'text-[#64748B] hover:text-[#0F172A] border border-transparent'
            }`}
            title="Use pre-recorded golden responses"
          >
            Replay
          </button>
        </div>

        {/* New chat button */}
        <button
          type="button"
          onClick={onNewChat}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-white border border-[#E2E8F0] hover:border-[#2563EB] text-[#0F172A] hover:text-[#2563EB] transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>New chat</span>
        </button>
      </div>
    </header>
  );
}
