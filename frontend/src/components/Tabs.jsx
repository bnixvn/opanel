// One tab bar for the panel: underlined, keyboard-driven (arrow keys, Home,
// End move between tabs), and on a narrow screen one row that scrolls
// sideways with a fade at the edge that has more - never a grid of wrapped
// buttons, never a word cut off with nothing saying there is more.
//
//   <Tabs label={t('Mail sections')} value={tab} onChange={setTab} tabs={[
//     { id: 'mailboxes', label: t('Mailboxes'), icon: Inbox },
//     { id: 'forwarders', label: t('Forwarders'), icon: Forward, badge: 3 },
//     { id: 'dns', label: t('DNS records'), icon: Globe, hidden: !isAdmin },
//   ]}/>
//
// A tab: { id, label, icon, badge, disabled, hidden, title }. With `id`, each
// tab is `${id}-tab-${tab.id}` and controls `${id}-panel-${tab.id}` - what a
// TabPanel (or a panel of the page's own with those ids) is.
//
// Tabs that are pages of their own (a hub's - components/HubTabs.jsx) give
// each tab an `href`: the row is then a <nav> of links, the open one
// aria-current="page", so a Ctrl-click opens a tab in a new browser tab;
// a plain click still calls onChange. Same look, same overflow.
import { useCallback, useEffect, useRef, useState } from 'react';
import './ui.css';

export const tabId = (base, id) => `${base}-tab-${id}`;

export const panelId = (base, id) => `${base}-panel-${id}`;

export function TabPanel({ tabs: base, value, children, className = '' }) {
  return <div role="tabpanel" id={panelId(base, value)} aria-labelledby={tabId(base, value)} className={className || undefined}>{children}</div>;
}

export default function Tabs({ tabs, value, onChange, label, id, className = '' }) {
  const links = (tabs || []).some((tab) => tab && tab.href);
  const shown = (tabs || []).filter((tab) => tab && !tab.hidden);
  // The tab Tab lands on: the chosen one, or the first when none is.
  const entry = shown.some((tab) => tab.id === value) ? value : shown.find((tab) => !tab.disabled)?.id;
  const listRef = useRef(null);
  const [edges, setEdges] = useState({ start: false, end: false });

  const measure = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const start = el.scrollLeft > 2;
    const end = el.scrollLeft + el.clientWidth < el.scrollWidth - 2;
    setEdges((prev) => (prev.start === start && prev.end === end ? prev : { start, end }));
  }, []);

  useEffect(() => {
    measure();
    const el = listRef.current;
    if (!el) return undefined;
    el.addEventListener('scroll', measure, { passive: true });
    let observer;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(measure);
      observer.observe(el);
    } else window.addEventListener('resize', measure);
    return () => {
      el.removeEventListener('scroll', measure);
      if (observer) observer.disconnect();
      else window.removeEventListener('resize', measure);
    };
  }, [measure, shown.length]);

  // The chosen tab is brought into view in the row - not the page.
  useEffect(() => {
    const el = listRef.current;
    const active = el?.querySelector('[aria-selected="true"], [aria-current="page"]');
    if (!el || !active) return;
    const pad = 24;
    if (active.offsetLeft < el.scrollLeft + pad) el.scrollLeft = Math.max(0, active.offsetLeft - pad);
    else if (active.offsetLeft + active.offsetWidth > el.scrollLeft + el.clientWidth - pad) el.scrollLeft = active.offsetLeft + active.offsetWidth - el.clientWidth + pad;
    measure();
  }, [value, measure]);

  function onKeyDown(event) {
    const enabled = shown.filter((tab) => !tab.disabled);
    const at = enabled.findIndex((tab) => tab.id === value);
    let next = null;
    if (event.key === 'ArrowRight') next = enabled[(at + 1) % enabled.length];
    else if (event.key === 'ArrowLeft') next = enabled[(at - 1 + enabled.length) % enabled.length];
    else if (event.key === 'Home') next = enabled[0];
    else if (event.key === 'End') next = enabled[enabled.length - 1];
    if (!next) return;
    event.preventDefault();
    onChange?.(next.id);
    requestAnimationFrame(() => listRef.current?.querySelector(`[data-tab="${CSS.escape(String(next.id))}"]`)?.focus());
  }

  const outer = ['sn-tabs', edges.start ? 'fade-start' : '', edges.end ? 'fade-end' : '', className].filter(Boolean).join(' ');
  if (links) {
    return <nav className={outer} aria-label={label}>
      <div className="sn-tabs-list" ref={listRef}>
        {shown.map((tab) => {
          const selected = tab.id === value;
          const Icon = tab.icon;
          return <a key={tab.id} href={tab.href} data-tab={tab.id} aria-current={selected ? 'page' : undefined} title={tab.title}
            className={selected ? 'sn-tab is-active' : 'sn-tab'}
            onClick={(event) => {
              if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
              event.preventDefault();
              if (!selected) onChange?.(tab.id);
            }}>
            {Icon && <Icon size={15} aria-hidden="true"/>}
            <span>{tab.label}</span>
            {tab.badge !== undefined && tab.badge !== null && tab.badge !== '' && <span className="sn-tab-badge">{tab.badge}</span>}
          </a>;
        })}
      </div>
    </nav>;
  }
  return <div className={outer}>
    <div className="sn-tabs-list" role="tablist" aria-label={label} ref={listRef} onKeyDown={onKeyDown}>
      {shown.map((tab) => {
        const selected = tab.id === value;
        const Icon = tab.icon;
        return <button key={tab.id} type="button" role="tab" data-tab={tab.id}
          id={id ? tabId(id, tab.id) : undefined} aria-controls={id && selected ? panelId(id, tab.id) : undefined}
          aria-selected={selected} tabIndex={tab.id === entry ? 0 : -1} disabled={tab.disabled} title={tab.title}
          className={selected ? 'sn-tab is-active' : 'sn-tab'} onClick={() => !selected && onChange?.(tab.id)}>
          {Icon && <Icon size={15} aria-hidden="true"/>}
          <span>{tab.label}</span>
          {tab.badge !== undefined && tab.badge !== null && tab.badge !== '' && <span className="sn-tab-badge">{tab.badge}</span>}
        </button>;
      })}
    </div>
  </div>;
}
