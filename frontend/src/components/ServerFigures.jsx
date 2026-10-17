// Pieces the server's pages share - RAM, Disk and Traffic - in OPanel's
// markup: a labelled fact, a bar of what takes a whole, and a duration.
import { useT } from '../lib/t.js';

// A fact, "—" while it is not known.
export function InfoRow({ label, value }) {
  return <div className="info-row">
    <span>{label}</span>
    <strong title={typeof value === 'string' ? value : undefined}>{value || '—'}</strong>
  </div>;
}

// One bar for a whole and the parts it is made of, each part as wide as its
// share, and their legend under it. parts: [[key, label, bytes]]; the key
// gives the colour (.ram-<key>).
export function BreakdownBar({ parts, whole, formatBytes }) {
  const width = (bytes) => `${Math.max(0, Math.min(100, ((Number(bytes) || 0) / (whole || 1)) * 100))}%`;
  return <div className="ram-breakdown-bar" role="img"
    aria-label={parts.map(([, label, bytes]) => `${label}: ${formatBytes(bytes)}`).join(', ')}>
    {parts.map(([key, , bytes]) => <span key={key} className={`ram-part ram-${key}`} style={{ width: width(bytes) }}/>)}
  </div>;
}

export function BreakdownLegend({ parts, formatBytes }) {
  return <ul className="ram-breakdown-legend">
    {parts.map(([key, label, bytes]) => <li key={key}>
      <i className={`ram-swatch ram-${key}`} aria-hidden="true"/>
      <span>{label}</span>
      <b>{formatBytes(bytes)}</b>
    </li>)}
  </ul>;
}

// "3 d 4 h", or "4 h 12 min" under a day.
export function useDuration() {
  const t = useT();
  return (seconds) => {
    const total = Math.max(0, Number(seconds) || 0);
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    return days ? t('{days} d {hours} h', { days, hours })
      : t('{hours} h {minutes} min', { hours, minutes: Math.floor((total % 3600) / 60) });
  };
}
