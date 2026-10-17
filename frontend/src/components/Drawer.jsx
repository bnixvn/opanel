// A side sheet for a form that creates something (FRM-06): a "+ New …"
// button in the page's header opens it from the right edge; on a phone it
// is the whole screen. The page under it stays where it was, and closing it
// (Cancel, ×, Escape, a click beside it) puts the focus back on the button
// that opened it.
//
//   <Drawer open={creating} onClose={() => setCreating(false)} title={t('New database')}
//     footer={<><button type="button" className="secondary-light" onClick={close}>{t('Cancel')}</button>
//       <button type="submit" form="db-create">{t('Create database')}</button></>}>
//     <form id="db-create" onSubmit={submit}>…</form>
//   </Drawer>
//
// Props: open, onClose, title, subtitle, footer (the buttons, at the foot -
// a submit button there names its form with `form=`), wide (a wider sheet,
// for a form with a code box), initialFocus (a selector inside the sheet;
// default the first field), className.
//
// A confirm or prompt (components/Dialog.jsx) opened from inside the sheet
// sits above it.
import { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useT } from '../lib/t.js';
import './Drawer.css';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
const FIELDS = 'input:not([disabled]):not([type=hidden]):not([type=checkbox]):not([type=radio]), select:not([disabled]), textarea:not([disabled])';

export default function Drawer({ open, onClose, title, subtitle, footer, wide = false, initialFocus, className = '', children }) {
  if (!open) return null;
  return createPortal(<DrawerView onClose={onClose} title={title} subtitle={subtitle} footer={footer} wide={wide}
    initialFocus={initialFocus} className={className}>{children}</DrawerView>, document.body);
}

function DrawerView({ onClose, title, subtitle, footer, wide, initialFocus, className, children }) {
  const t = useT();
  const titleId = useId();
  const panelRef = useRef(null);
  const returnTo = useRef(typeof document !== 'undefined' ? document.activeElement : null);
  const downOnBackdrop = useRef(false);

  useEffect(() => {
    const panel = panelRef.current;
    const target = (initialFocus && panel?.querySelector(initialFocus)) || panel?.querySelector(`.sn-drawer-body ${FIELDS}`) || panel;
    target?.focus({ preventScroll: true });
    document.body.classList.add('sn-drawer-open');
    const back = returnTo.current;
    return () => {
      document.body.classList.remove('sn-drawer-open');
      if (back && typeof back.focus === 'function' && document.contains(back)) back.focus({ preventScroll: true });
    };
  }, []);

  function onKeyDown(event) {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      onClose?.();
      return;
    }
    if (event.key !== 'Tab' || !panelRef.current) return;
    const items = [...panelRef.current.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  return <div className="sn-drawer-backdrop"
    onMouseDown={(e) => { downOnBackdrop.current = e.target === e.currentTarget; }}
    onMouseUp={(e) => { if (downOnBackdrop.current && e.target === e.currentTarget) onClose?.(); downOnBackdrop.current = false; }}>
    <aside ref={panelRef} className={['sn-drawer', wide ? 'is-wide' : '', className].filter(Boolean).join(' ')}
      role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} onKeyDown={onKeyDown}>
      <header className="sn-drawer-head">
        <div className="sn-drawer-titles">
          <h2 id={titleId}>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        <button type="button" className="sn-drawer-x" onClick={onClose} aria-label={t('Close')} title={t('Close')}><X size={18} aria-hidden="true"/></button>
      </header>
      <div className="sn-drawer-body">{children}</div>
      {footer && <footer className="sn-drawer-foot">{footer}</footer>}
    </aside>
  </div>;
}
