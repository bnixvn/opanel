import { useEffect, useRef, useState } from 'react';
import { Bell, CircleAlert, CircleCheck, OctagonAlert, RefreshCw } from 'lucide-react';
import { followInPanel, usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import { attentionItems } from '../lib/attention.js';
import './NotificationBell.css';

// How often the bell asks again while the tab is in view.
const EVERY_MS = 5 * 60 * 1000;

// The header's bell (operator, 2026-10-18): what needs attention, on every
// page - the Dashboard's "Needs attention" card moved here. A count on the
// bell in the worst item's colour; the list opens under it, each item with
// the way to the page that fixes it.
export default function NotificationBell() {
  const {
    currentUser, formatPercent, isAdmin, navigateToPage, request, routeForPage, runBefore, storageLimitBytes,
  } = usePanel();
  const t = useT();
  const [summary, setSummary] = useState(null);
  const [updates, setUpdates] = useState(null);
  const [checking, setChecking] = useState(false);
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  async function load() {
    setChecking(true);
    const data = await request('/dashboard/summary', { silent: true });
    if (data) setSummary(data);
    setChecking(false);
    // Asking the package manager takes a moment; the rest does not wait.
    if (isAdmin) {
      const pending = await request('/dashboard/updates', { silent: true });
      if (pending) setUpdates(pending);
    }
  }

  useEffect(() => {
    load();
    const tick = () => { if (!document.hidden) load(); };
    const timer = setInterval(tick, EVERY_MS);
    // A page that has just changed something the bell reports asks for a recount.
    window.addEventListener('opanel:attention-refresh', load);
    return () => {
      clearInterval(timer);
      window.removeEventListener('opanel:attention-refresh', load);
    };
  }, [isAdmin, currentUser?.id]);

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (event) => { if (!boxRef.current?.contains(event.target)) setOpen(false); };
    const onKey = (event) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const items = attentionItems({ summary, updates, isAdmin, currentUser, storageLimitBytes, formatPercent, t });
  const worst = items[0]?.tone || '';
  const label = items.length ? t('{count} thing(s) need attention', { count: items.length }) : t('Notifications');

  return <div className="notify-bell" ref={boxRef}>
    <button type="button" className={`top-tool notify-bell-trigger${worst ? ` tone-${worst}` : ''}`}
      onClick={() => setOpen((value) => !value)} aria-haspopup="dialog" aria-expanded={open} aria-label={label} title={label}>
      <Bell size={16} aria-hidden="true"/>
      {items.length > 0 && <span className="notify-bell-count" aria-hidden="true">{items.length > 9 ? '9+' : items.length}</span>}
    </button>
    {open && <div className="notify-panel" role="dialog" aria-label={t('Needs attention')}>
      <div className="notify-panel-head">
        <strong>{t('Needs attention')}</strong>
        <button type="button" className="secondary icon-only mini" onClick={load} disabled={checking}
          aria-label={t('Check again')} title={t('Check again')}><RefreshCw size={14} className={checking ? 'spin' : ''} aria-hidden="true"/></button>
      </div>
      {!summary
        ? <div className="attention-ok is-pending" aria-busy="true"><RefreshCw size={16} className="spin" aria-hidden="true"/> {t('Checking…')}</div>
        : items.length === 0
          ? <div className="attention-ok"><CircleCheck size={16} aria-hidden="true"/> {t('Everything is fine.')}</div>
          : <div className="attention-list notify-list">
            {items.map((item) => <div key={item.key} className={`attention-item tone-${item.tone}`}>
              {item.tone === 'bad' ? <OctagonAlert size={15} aria-hidden="true"/> : item.tone === 'info' ? <RefreshCw size={15} aria-hidden="true"/> : <CircleAlert size={15} aria-hidden="true"/>}
              <span>{item.text}</span>
              <a className="button-link mini secondary" href={routeForPage(item.page)}
                onClick={(event) => followInPanel(event, () => {
                  setOpen(false);
                  if (item.before) runBefore(item.before);
                  navigateToPage(item.page);
                })}>{item.action}</a>
            </div>)}
          </div>}
    </div>}
  </div>;
}
