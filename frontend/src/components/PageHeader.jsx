// The head of a page: what it is for, where it sits (for a view inside a
// page), and its actions - Refresh always first, in the same place on every
// page. The page's title is the top bar's: a PageHeader only repeats it
// when given `title`, for a view that has a title of its own.
//
//   <PageHeader subtitle={t('Mailboxes, forwarders and DNS records of every mail domain.')}
//     onRefresh={load} refreshing={busy}
//     actions={<button onClick={add}><Plus size={15}/> {t('New mailbox')}</button>}/>
//
//   <PageHeader breadcrumbs={[{ label: t('DNS'), onClick: back }, { label: zone.name }]} title={zone.name}/>
//
// breadcrumbs: [{ label, onClick, href }]; the last one is where the reader is.
import { RefreshCw } from 'lucide-react';
import { useT } from '../lib/t.js';
import './ui.css';

function Crumb({ crumb, last }) {
  if (last || (!crumb.onClick && !crumb.href)) return <span aria-current={last ? 'page' : undefined}>{crumb.label}</span>;
  if (crumb.href) {
    return <a href={crumb.href} onClick={crumb.onClick ? (event) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      crumb.onClick(event);
    } : undefined}>{crumb.label}</a>;
  }
  return <button type="button" className="sn-crumb-button" onClick={crumb.onClick}>{crumb.label}</button>;
}

export default function PageHeader({
  title, subtitle, breadcrumbs, actions, onRefresh, refreshing = false, refreshDisabled = false, refreshLabel,
  className = '', children,
}) {
  const t = useT();
  const crumbs = (breadcrumbs || []).filter(Boolean);
  const hasActions = !!(actions || onRefresh);
  return <header className={['sn-page-header', className].filter(Boolean).join(' ')}>
    <div className="sn-page-header-main">
      {crumbs.length > 0 && <nav className="sn-crumbs" aria-label={t('Breadcrumb')}>
        <ol>{crumbs.map((crumb, i) => <li key={i}><Crumb crumb={crumb} last={i === crumbs.length - 1}/></li>)}</ol>
      </nav>}
      {title && <h2 className="sn-page-title">{title}</h2>}
      {subtitle && <p className="sn-page-subtitle">{subtitle}</p>}
      {children}
    </div>
    {hasActions && <div className="sn-page-actions">
      {onRefresh && <button type="button" className="secondary-light sn-refresh" onClick={onRefresh} disabled={refreshing || refreshDisabled}
        aria-busy={refreshing || undefined}>
        <RefreshCw size={15} className={refreshing ? 'spin' : ''} aria-hidden="true"/>{refreshLabel || t('Refresh')}
      </button>}
      {actions}
    </div>}
  </header>;
}
