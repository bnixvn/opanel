// A list or page with nothing to show: an icon, what is (not) there, and -
// when there is something to do about it - the button that does it, instead
// of a sentence sending the reader to find it elsewhere.
//
//   <EmptyState icon={HardDrive} title={t('No repository yet')} message={t('Add one, then a schedule.')}
//     action={{ label: t('Add repository'), icon: Plus, onClick: () => setTab('repos') }}/>
//
// `action` is { label, onClick, icon, href, secondary } or any node.
import { isValidElement } from 'react';
import { AlertCircle } from 'lucide-react';
import { useT } from '../lib/t.js';
import './ui.css';

export default function EmptyState({ icon: Icon = AlertCircle, title, message, action, compact = false, className = '', children }) {
  const t = useT();
  const text = message === undefined && !title ? t('No data yet') : message;
  let button = null;
  if (isValidElement(action)) button = action;
  else if (action && action.label) {
    const ActionIcon = action.icon;
    const inner = <>{ActionIcon && <ActionIcon size={15} aria-hidden="true"/>}{action.label}</>;
    button = action.href
      ? <a className={`button-link${action.secondary ? ' secondary-light' : ''}`} href={action.href}
        onClick={action.onClick ? (event) => {
          if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          action.onClick(event);
        } : undefined}>{inner}</a>
      : <button type="button" className={action.secondary ? 'secondary-light' : undefined} onClick={action.onClick} disabled={action.disabled}>{inner}</button>;
  }
  return <div className={['empty-state', 'sn-empty', compact ? 'is-compact' : '', className].filter(Boolean).join(' ')}>
    {Icon && <Icon size={compact ? 28 : 40} aria-hidden="true"/>}
    {title && <p className="sn-empty-title">{title}</p>}
    {text && <p className={title ? 'sn-empty-message' : undefined}>{text}</p>}
    {children}
    {button && <div className="sn-empty-action">{button}</div>}
  </div>;
}
