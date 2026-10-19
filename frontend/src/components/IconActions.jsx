import './IconActions.css';

// A row's actions as a row of icon buttons, each with its name on hover and
// for a screen reader: one click each, where a "…" menu took two (operator,
// 2026-10-19). The items are ActionMenu's - { label, icon, onClick, href,
// external, danger, disabled, hidden, hint } plus `primary` for the row's
// main action - so a list can swap one for the other; 'separator' is a gap.
export default function IconActions({ items = [], label, className = '' }) {
  const shown = items.filter((item) => item && (item === 'separator' || !item.hidden));
  return <div className={['icon-actions', className].filter(Boolean).join(' ')} role="group" aria-label={label}>
    {shown.map((item, index) => {
      if (item === 'separator') return <span key={`gap-${index}`} className="icon-actions-gap" aria-hidden="true"/>;
      const Icon = item.icon;
      const classes = ['icon-action', item.primary ? 'is-primary' : '', item.danger ? 'is-danger' : ''].filter(Boolean).join(' ');
      // Its name shows at once on hover or focus (a title waits a second
      // and is easy to miss); aria-label says it to a screen reader.
      const tip = item.hint ? `${item.label} - ${item.hint}` : item.label;
      const inner = <>{Icon ? <Icon size={16} aria-hidden="true"/> : item.label}<span className="icon-action-tip" aria-hidden="true">{tip}</span></>;
      if (item.href) {
        return <a key={item.label} className={classes} href={item.href} aria-label={item.label}
          target={item.external ? '_blank' : undefined} rel={item.external ? 'noopener noreferrer' : undefined}>{inner}</a>;
      }
      return <button key={item.label} type="button" className={classes} disabled={item.disabled} onClick={item.onClick}
        aria-label={item.label}>{inner}</button>;
    })}
  </div>;
}
