import React, { useState } from 'react';

export const DEFAULT_GLOSSARY = {
  'performance ratio': 'The ratio of actual energy output to theoretically expected energy output based on available sunlight.',
  'pr': 'The ratio of actual energy output to theoretically expected energy output based on available sunlight.',
  'soiling': 'The accumulation of dust, dirt, and debris on solar panels that reduces sunlight absorption and power output.',
  'zero generation': 'A condition where an inverter produces no electrical power during daylight hours despite available solar irradiance.',
  'zero-generation': 'A condition where an inverter produces no electrical power during daylight hours despite available solar irradiance.',
  'anomaly': 'A statistically significant deviation in inverter performance compared to its peers or expected operating levels.',
  'anomalies': 'A statistically significant deviation in inverter performance compared to its peers or expected operating levels.',
  'status code': 'A numerical operational code reported by inverters indicating normal operation, warnings, or fault conditions.',
  'specific yield': 'The total energy generated divided by the plant\'s installed DC capacity, measuring generation efficiency per unit of size.',
  'capacity factor': 'The ratio of actual electrical energy generated over a period to the maximum possible energy output if running at full capacity continuously.',
  'peer comparison': 'A benchmarking technique evaluating an inverter\'s output against neighboring inverters within the same block or plant under identical weather conditions.',
};

/**
 * Single tooltip trigger with dotted underline and hover/tap popup.
 */
export function GlossaryTooltip({ term, definition, children }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <span
      className="relative inline-block border-b border-dotted border-[#64748B] cursor-help font-inherit text-inherit"
      onMouseEnter={() => setIsOpen(true)}
      onMouseLeave={() => setIsOpen(false)}
      onClick={(e) => {
        e.stopPropagation();
        setIsOpen((prev) => !prev);
      }}
      title={definition}
    >
      {children || term}
      {isOpen && (
        <span
          role="tooltip"
          className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 w-64 p-2.5 bg-[#0F172A] text-white text-xs font-normal rounded-lg shadow-xl text-left pointer-events-none normal-case leading-normal whitespace-normal transition-opacity duration-150 border border-slate-700"
        >
          <span className="block font-semibold text-[#93C5FD] mb-1 capitalize">{term}</span>
          <span>{definition}</span>
          <span className="absolute top-full left-1/2 -translate-x-1/2 -mt-1 border-4 border-transparent border-t-[#0F172A]" />
        </span>
      )}
    </span>
  );
}

/**
 * Wraps any occurrences of glossary terms inside text with GlossaryTooltip.
 */
export default function GlossaryText({ text, glossary = {}, className = '' }) {
  if (text === null || text === undefined) return null;
  if (typeof text !== 'string') return <span className={className}>{String(text)}</span>;

  // Strip leading/trailing bold markdown markers if whole sentence is wrapped
  let cleanText = text;
  let isWholeBold = false;
  if (cleanText.startsWith('**') && cleanText.endsWith('**') && cleanText.length > 4) {
    cleanText = cleanText.slice(2, -2);
    isWholeBold = true;
  }

  // Merge provided glossary with default
  const mergedGlossary = { ...DEFAULT_GLOSSARY, ...(glossary || {}) };

  // Normalize glossary keys for matching
  const termMap = new Map();
  for (const [key, def] of Object.entries(mergedGlossary)) {
    if (!def) continue;
    const lowerKey = key.toLowerCase();
    termMap.set(lowerKey, def);
    if (lowerKey.includes('_')) {
      termMap.set(lowerKey.replace(/_/g, ' '), def);
    }
  }

  // Add plural/variations
  if (termMap.has('anomaly')) {
    termMap.set('anomalies', termMap.get('anomaly'));
  }
  if (termMap.has('performance ratio') && !termMap.has('pr')) {
    termMap.set('pr', termMap.get('performance ratio'));
  }

  // Build regex sorted by length descending so longer phrases match first
  const sortedKeys = Array.from(termMap.keys()).sort((a, b) => b.length - a.length);
  if (sortedKeys.length === 0) {
    return <span className={className}>{isWholeBold ? <strong>{cleanText}</strong> : cleanText}</span>;
  }

  const escapedKeys = sortedKeys.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const regex = new RegExp(`\\b(${escapedKeys.join('|')})\\b`, 'gi');

  const parts = [];
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(cleanText)) !== null) {
    if (match.index > lastIndex) {
      parts.push(cleanText.substring(lastIndex, match.index));
    }
    const matchedTerm = match[0];
    const definition = termMap.get(matchedTerm.toLowerCase()) || termMap.get(matchedTerm.toLowerCase().replace(/_/g, ' '));

    parts.push(
      <GlossaryTooltip key={match.index} term={matchedTerm} definition={definition}>
        {matchedTerm}
      </GlossaryTooltip>
    );
    lastIndex = regex.lastIndex;
  }

  if (lastIndex < cleanText.length) {
    parts.push(cleanText.substring(lastIndex));
  }

  const content = parts.length > 0 ? parts : cleanText;
  return (
    <span className={className}>
      {isWholeBold ? <strong>{content}</strong> : content}
    </span>
  );
}
