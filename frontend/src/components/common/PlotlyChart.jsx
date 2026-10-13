import React, { useEffect, useRef } from 'react';
import Plotly from 'plotly.js-dist-min';

/**
 * Robust Plotly chart renderer using plotly.js-dist-min directly to avoid
 * any react-plotly.js build or SSR mismatches.
 */
export default function PlotlyChart({ figureJson, className = '' }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || !figureJson) return;

    let fig = figureJson;
    if (typeof figureJson === 'string') {
      try {
        fig = JSON.parse(figureJson);
      } catch (e) {
        console.error('Invalid Plotly JSON:', e);
        return;
      }
    }

    const data = fig.data || [];
    const layout = {
      ...(fig.layout || {}),
      autosize: true,
      paper_bgcolor: '#16213A',
      plot_bgcolor: '#111A2E',
      font: {
        family: 'Inter, system-ui, sans-serif',
        color: '#E6ECF5',
        size: 11,
      },
      margin: { l: 40, r: 20, t: 30, b: 40, ...(fig.layout?.margin || {}) },
    };

    const config = {
      responsive: true,
      displayModeBar: false,
      ...(fig.config || {}),
    };

    Plotly.newPlot(containerRef.current, data, layout, config);

    const handleResize = () => {
      if (containerRef.current) {
        Plotly.Plots.resize(containerRef.current);
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      if (containerRef.current) {
        Plotly.purge(containerRef.current);
      }
    };
  }, [figureJson]);

  return <div ref={containerRef} className={`w-full min-h-[320px] ${className}`} />;
}
