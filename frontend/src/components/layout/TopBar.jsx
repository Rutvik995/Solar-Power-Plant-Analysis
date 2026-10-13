import React from 'react';
import { useLocation } from 'react-router-dom';
import { ShieldCheck, Cpu, Database } from 'lucide-react';
import Badge from '../common/Badge';

const TITLES = {
  '/': 'Ask the System (Multi-Agent Assistant)',
  '/about': 'About Platform & Architecture',
};

export default function TopBar() {
  const location = useLocation();
  const pageTitle = TITLES[location.pathname] || 'Solar Intelligence';

  return (
    <header className="h-16 bg-surface/80 backdrop-blur-md border-b border-border px-6 flex items-center justify-between sticky top-0 z-20">
      <div>
        <h2 className="text-base font-semibold text-text">{pageTitle}</h2>
      </div>

      <div className="flex items-center gap-3">
        <Badge variant="teal">
          <Database className="w-3 h-3" />
          <span>PostgreSQL Live</span>
        </Badge>
        <Badge variant="violet">
          <Cpu className="w-3 h-3" />
          <span>LangGraph Engine</span>
        </Badge>
        <Badge variant="success">
          <ShieldCheck className="w-3 h-3" />
          <span>Grounding Verified</span>
        </Badge>
      </div>
    </header>
  );
}
