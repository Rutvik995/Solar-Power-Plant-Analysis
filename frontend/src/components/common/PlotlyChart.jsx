import React, { useEffect, useRef } from 'react';
import Plotly from 'plotly.js-dist-min';

const CHART_COLORS = ['#2563EB', '#0D9488', '#D97706', '#7C3AED', '#94A3B8'];

/**
 * Robust Plotly chart renderer on white cards with light theme styling.
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

    const data = (fig.data || []).map((trace, idx) => {
      // Ensure trace color falls back cleanly to the required colorway if not specified
      if (!trace.marker?.color && !trace.line?.color) {
        return {
          ...trace,
          marker: { ...trace.marker, color: trace.marker?.color || CHART_COLORS[idx % CHART_COLORS.length] },
        };
      }
      return trace;
    });

    const layout = {
      autosize: true,
      paper_bgcolor: '#FFFFFF',
      plot_bgcolor: '#FFFFFF',
      colorway: CHART_COLORS,
      font: {
        family: 'Inter, system-ui, sans-serif',
        color: '#0F172A',
        size: 12,
      },
      margin: { l: 45, r: 25, t: 35, b: 45, ...(fig.layout?.margin || {}) },
      xaxis: {
        gridcolor: '#F1F5F9',
        linecolor: '#E2E8F0',
        tickcolor: '#E2E8F0',
        tickfont: { color: '#64748B', size: 11 },
        ...(fig.layout?.xaxis || {}),
      },
      yaxis: {
        gridcolor: '#F1F5F9',
        linecolor: '#E2E8F0',
        tickcolor: '#E2E8F0',
        tickfont: { color: '#64748B', size: 11 },
        ...(fig.layout?.yaxis || {}),
      },
      legend: {
        font: { color: '#0F172A', size: 11 },
        bgcolor: 'rgba(255, 255, 255, 0.9)',
        bordercolor: '#E2E8F0',
        borderwidth: 1,
        ...(fig.layout?.legend || {}),
      },
      ...(fig.layout || {}),
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

  return (
    <div className={`w-full bg-white border border-border rounded-lg p-3 ${className}`}>
      <div ref={containerRef} className="w-full min-h-[300px]" />
    </div>
  );
}
