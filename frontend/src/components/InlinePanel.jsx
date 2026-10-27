// A create form that opens in the page, right under the list's search
// (operator, 2026-10-27: "New website" and "New database" as a form below
// the search box, its fields laid out across the width, instead of a sheet
// at the right edge). Drawer's props, so a page swaps one tag for the other:
// open, onClose, title, subtitle, footer (the buttons - a submit button there
// names its form with `form=`), className. Escape or × closes it; it opens
// with the focus on its first field, scrolled into view.
import { useEffect, useId, useRef } from 'react';
import { X } from 'lucide-react';
import { useT } from '../lib/t.js';
import './Drawer.css';
import './InlinePanel.css';

const FIELDS = 'input:not([disabled]):not([type=hidden]):not([type=checkbox]):not([type=radio]), select:not([disabled]), textarea:not([disabled])';

export default function InlinePanel({ open, onClose, title, subtitle, footer, className = '', children }) {
  const t = useT();
  const titleId = useId();
  const panelRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    panel?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    panel?.querySelector(`.sn-inline-body ${FIELDS}`)?.focus({ preventScroll: true });
  }, [open]);

  if (!open) return null;
  return <section ref={panelRef} className={['sn-inline-panel', className].filter(Boolean).join(' ')} aria-labelledby={titleId}
    onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose?.(); } }}>
    <header className="sn-inline-head">
      <div className="sn-drawer-titles">
        <h2 id={titleId}>{title}</h2>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {onClose && <button type="button" className="sn-drawer-x" onClick={onClose} aria-label={t('Close')} title={t('Close')}><X size={18} aria-hidden="true"/></button>}
    </header>
    <div className="sn-inline-body">{children}</div>
    {footer && <footer className="sn-inline-foot">{footer}</footer>}
  </section>;
}
