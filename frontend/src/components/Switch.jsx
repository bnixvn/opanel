import './Switch.css';

// An on/off switch that shows the state it is in - the WAF page's switch,
// shared: a checkbox drawn as one, with role="switch" so a screen reader says
// "on" or "off". Space toggles it as it toggles any checkbox; Enter does too,
// as a switch is expected to. `label` names it when no visible text inside
// does. `pending` says a change is on its way (waiting its turn or running):
// the switch then shows where it is going, marked as not there yet, and
// stays usable - a newer click is the one that counts.
export default function Switch({ checked, onChange, disabled = false, pending = false, label, title, className = '', children }) {
  const on = !!checked;
  function onKeyDown(event) {
    if (event.key !== 'Enter' || disabled) return;
    event.preventDefault();
    onChange?.(!on);
  }
  return <label className={['sn-switch', pending ? 'is-pending' : '', className].filter(Boolean).join(' ')} title={title}>
    <input type="checkbox" role="switch" checked={on} aria-checked={on} aria-busy={pending || undefined}
      aria-label={label} disabled={disabled} onChange={(event) => onChange?.(event.target.checked)} onKeyDown={onKeyDown} />
    <span className="sn-switch-track" aria-hidden="true"><span className="sn-switch-thumb"/></span>
    {children}
  </label>;
}
