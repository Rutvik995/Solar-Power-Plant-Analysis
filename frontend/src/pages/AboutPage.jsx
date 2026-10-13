import React from 'react';
import Card from '../components/common/Card';
import Badge from '../components/common/Badge';
import {
  ShieldCheck,
  Cpu,
  Database,
  Calculator,
  Workflow,
  Sparkles,
  Layers,
  FileText,
  AlertCircle,
} from 'lucide-react';

export default function AboutPage() {
  return (
    <div className="space-y-6 max-w-5xl mx-auto pb-12">
      {/* Platform Banner */}
      <div className="bg-surface p-6 rounded-xl border border-border">
        <div className="flex items-center gap-3 mb-2">
          <div className="p-2.5 rounded-lg bg-primary/15 text-primary border border-primary/30">
            <Sparkles className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-xl font-bold text-text">Solar Power Plant Multi-Agent System</h2>
            <p className="text-xs text-muted mt-0.5">
              LLM-assisted telemetry intelligence platform built on LangGraph, SQLAlchemy, and FastAPI.
            </p>
          </div>
        </div>

        <div className="mt-4 pt-4 border-t border-border/60 flex flex-wrap gap-2 text-xs">
          <Badge variant="teal">PostgreSQL 14+</Badge>
          <Badge variant="violet">LangGraph StateGraph</Badge>
          <Badge variant="primary">Groq / Llama 3.3</Badge>
          <Badge variant="success">AST SQL Guardrails</Badge>
          <Badge variant="sky">Run-Isolated Budgeting</Badge>
        </div>
      </div>

      {/* Core Axiom & Security */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card
          title="Core Design Principle"
          subtitle="Separation between qualitative reasoning and quantitative computation"
          action={<Badge variant="primary">Grounding Verified</Badge>}
        >
          <blockquote className="p-3 rounded-lg bg-surface border-l-2 border-primary text-xs text-text italic leading-relaxed">
            "The LLM plans, coordinates, and explains; database and analytical tools compute. Numbers come directly from database tools, and an automated grounding check flags mismatches."
          </blockquote>
          <p className="text-xs text-muted mt-3 leading-relaxed">
            All solar formulas (PR, capacity factor, specific yield, robust peer deviation) are computed directly by database tools and metrics algorithms, and a post-synthesis grounding check flags mismatches before final output.
          </p>
        </Card>

        <Card
          title="Defense-in-Depth Security"
          subtitle="Multi-layer protection against malicious execution or accidental data loss"
          action={<Badge variant="success">Read-Only Guarded</Badge>}
        >
          <ul className="space-y-2 text-xs text-muted">
            <li className="flex items-start gap-2">
              <span className="font-mono text-primary font-bold">L1:</span>
              <span><strong>SQLGlot AST Parse:</strong> Rejects any query that is not a strictly validated SELECT or WITH-SELECT.</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="font-mono text-teal font-bold">L2:</span>
              <span><strong>Table Allowlist:</strong> Rejects queries referencing tables outside the 4 approved solar telemetry entities.</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="font-mono text-violet font-bold">L3:</span>
              <span><strong>SQLAlchemy Engine Guard:</strong> Intercepts connection events to enforce read-only execution at driver level.</span>
            </li>
          </ul>
        </Card>
      </div>

      {/* Multi-Agent Architecture */}
      <Card
        title="Multi-Agent Pipeline Architecture"
        subtitle="Autonomous agent nodes orchestrated via directed acyclic graph (DAG)"
        action={<Badge variant="violet">LangGraph StateGraph</Badge>}
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
          <div className="p-3.5 rounded-lg bg-surface border border-border space-y-1.5">
            <div className="font-bold text-text flex items-center gap-1.5">
              <Workflow className="w-3.5 h-3.5 text-primary" />
              Orchestrator
            </div>
            <p className="text-muted leading-relaxed">
              Planner LLM analyzes user intent, scope, and entities to emit a valid DAG Plan with topological dependencies.
            </p>
          </div>

          <div className="p-3.5 rounded-lg bg-surface border border-border space-y-1.5">
            <div className="font-bold text-text flex items-center gap-1.5">
              <Cpu className="w-3.5 h-3.5 text-teal" />
              Specialist Agents
            </div>
            <p className="text-muted leading-relaxed">
              Data Agent (pure SQL), Performance Agent, Fault Agent, and Environmental Agent execute parallel task layers.
            </p>
          </div>

          <div className="p-3.5 rounded-lg bg-surface border border-border space-y-1.5">
            <div className="font-bold text-text flex items-center gap-1.5">
              <ShieldCheck className="w-3.5 h-3.5 text-sky" />
              Validator Node
            </div>
            <p className="text-muted leading-relaxed">
              Verifies task status, dependency outputs, and success criteria. Triggers replanning if criteria are not fulfilled.
            </p>
          </div>

          <div className="p-3.5 rounded-lg bg-surface border border-border space-y-1.5">
            <div className="font-bold text-text flex items-center gap-1.5">
              <FileText className="w-3.5 h-3.5 text-violet" />
              Synthesizer Node
            </div>
            <p className="text-muted leading-relaxed">
              Drafts grounded final response and performs regex numerical audit against all stored tool metrics before output.
            </p>
          </div>
        </div>
      </Card>

      {/* Key Formulas & Engineering Rules */}
      <Card
        title="Key Solar KPI Formulas & Business Rules"
        subtitle="Standardized engineering calculations from semantic layer"
        action={<Badge variant="primary">Semantic Layer</Badge>}
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
          <div className="p-3 rounded-lg bg-surface border border-border/80 space-y-1">
            <div className="font-semibold text-text">Performance Ratio (PR) — BR-01, BR-04</div>
            <div className="font-mono text-primary bg-card px-2 py-1 rounded text-[11px]">
              PR = Total Yield (kWh) / (Rated DC Capacity × Insolation H)
            </div>
            <p className="text-muted text-[11px] mt-1">
              Guarded by minimum insolation threshold (H &gt; 0.5 kWh/m²). When aggregated across blocks or plants, yields and expected yields are summed separately rather than averaging individual PRs.
            </p>
          </div>

          <div className="p-3 rounded-lg bg-surface border border-border/80 space-y-1">
            <div className="font-semibold text-text">Peer Deviation Score — Robust MAD Z-Score</div>
            <div className="font-mono text-teal bg-card px-2 py-1 rounded text-[11px]">
              Z = (PR_inverter - median(PR_peers)) / (1.4826 × MAD(PR_peers))
            </div>
            <p className="text-muted text-[11px] mt-1">
              Groups inverters by identical day and block. Flags persistent underperformance when Z &lt; -2.0 for consecutive days.
            </p>
          </div>

          <div className="p-3 rounded-lg bg-surface border border-border/80 space-y-1">
            <div className="font-semibold text-text">Soiling Loss Percentage — BR-07</div>
            <div className="font-mono text-sky bg-card px-2 py-1 rounded text-[11px]">
              Loss % = (1 - Soiling Ratio) × 100
            </div>
            <p className="text-muted text-[11px] mt-1">
              Ratio of 1.0 represents clean modules. Cleaning events are detected automatically when soiling ratio jumps by &gt; 5%.
            </p>
          </div>

          <div className="p-3 rounded-lg bg-surface border border-border/80 space-y-1">
            <div className="font-semibold text-text">Zero Generation Anomaly</div>
            <div className="font-mono text-violet bg-card px-2 py-1 rounded text-[11px]">
              Yield &le; 1.0 kWh AND Insolation H &gt; 0.5 kWh/m²
            </div>
            <p className="text-muted text-[11px] mt-1">
              Identifies complete inverter trip under sunny conditions. Skips standby status codes (e.g. Code 1).
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
}
