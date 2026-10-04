import React, { useState } from 'react';

// Charts for the Resource limits addon's history, drawn as plain SVG: a day is
// 288 five-minute points and a week 168 hourly ones, too few to be worth a
// chart library. The plot stretches to its box; the labels are HTML beside it,
// so they stay sharp at any width.

const PLOT_W = 1000;
const PLOT_H = 200;

function scaleTop(values, limit, floor) {
  const peak = Math.max(0, ...values);
  const top = Math.max(peak * 1.15, limit ? limit * 1.08 : 0, floor);
  // Round up to a number that reads well on the axis.
  const magnitude = 10 ** Math.floor(Math.log10(top));
  const steps = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
  return steps.map(step => step * magnitude).find(value => value >= top) || top;
}

function polyline(values, top) {
  const step = PLOT_W / (values.length - 1);
  return values.map((value, index) => `${(index * step).toFixed(1)},${(PLOT_H - (value / top) * PLOT_H).toFixed(1)}`).join(' ');
}

/**
 * One measure over time, with its limit as a dashed line.
 *
 * points: [{ t, ... }] oldest first; pick(point) gives the value; format(value)
 * gives its label; when(t) labels a time. floor keeps an idle account's axis
 * from collapsing onto zero. The limit is named beside the title whether or
 * not there is one, so "no line" never reads as "no limit shown".
 */
export function UsageChart({ title, points, pick, limit = 0, format, when, floor = 1, emptyText, limitText, unlimitedText, peakText, averageText }) {
  const [hover, setHover] = useState(null);
  const values = (points || []).map(pick).map(value => Number(value) || 0);
  const cap = <em className={`usage-chart-cap${limit > 0 ? '' : ' is-unlimited'}`}>{limit > 0 ? `${limitText} ${format(limit)}` : unlimitedText}</em>;
  if (values.length < 2) {
    return <div className="usage-chart">
      <div className="usage-chart-head"><span>{title}{cap}</span></div>
      <div className="usage-chart-empty">{emptyText}</div>
    </div>;
  }
  const top = scaleTop(values, limit, floor);
  const line = polyline(values, top);
  const peak = Math.max(...values);
  const average = values.reduce((sum, value) => sum + value, 0) / values.length;
  const limitTop = limit ? Math.max(0, 100 - (limit / top) * 100) : null;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(share => points[Math.round(share * (points.length - 1))].t);

  function track(event) {
    const box = event.currentTarget.getBoundingClientRect();
    const share = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    setHover(Math.round(share * (values.length - 1)));
  }

  const hoverLeft = hover == null ? 0 : (hover / (values.length - 1)) * 100;
  return <div className="usage-chart">
    <div className="usage-chart-head">
      <span>{title}{cap}</span>
      <small>{peakText} {format(peak)} · {averageText} {format(average)}</small>
    </div>
    <div className="usage-chart-body">
      <div className="usage-chart-y" aria-hidden="true"><span>{format(top)}</span><span>{format(top / 2)}</span><span>{format(0)}</span></div>
      <div className="usage-chart-plot" onMouseMove={track} onMouseLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${PLOT_W} ${PLOT_H}`} preserveAspectRatio="none" role="img" aria-label={`${title}: ${peakText} ${format(peak)}`}>
          <line className="usage-chart-grid" x1="0" x2={PLOT_W} y1={PLOT_H / 2} y2={PLOT_H / 2} vectorEffect="non-scaling-stroke" />
          <polygon className="usage-chart-area" points={`0,${PLOT_H} ${line} ${PLOT_W},${PLOT_H}`} />
          <polyline className="usage-chart-line" points={line} vectorEffect="non-scaling-stroke" />
          {limit > 0 && <line className="usage-chart-limit" x1="0" x2={PLOT_W} y1={PLOT_H - (limit / top) * PLOT_H} y2={PLOT_H - (limit / top) * PLOT_H} vectorEffect="non-scaling-stroke" />}
          {hover != null && <line className="usage-chart-cursor" x1={(hoverLeft / 100) * PLOT_W} x2={(hoverLeft / 100) * PLOT_W} y1="0" y2={PLOT_H} vectorEffect="non-scaling-stroke" />}
        </svg>
        {limitTop != null && <span className="usage-chart-limit-label" style={{ top: `${limitTop}%` }}>{limitText} {format(limit)}</span>}
        {hover != null && <div className={`usage-chart-tip${hoverLeft > 70 ? ' left' : ''}`} style={{ left: `${hoverLeft}%` }}>
          <strong>{format(values[hover])}</strong><small>{when(points[hover].t, true)}</small>
        </div>}
      </div>
    </div>
    <div className="usage-chart-x" aria-hidden="true">{ticks.map((t, index) => <span key={index}>{when(t, false)}</span>)}</div>
  </div>;
}
