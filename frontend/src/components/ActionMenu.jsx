// A row's or a card's other actions, behind one "…" button (WEB-02, DB-01):
// each with its icon AND its words, so a phone has labels to read and one
// target to hit instead of a row of small icons - and the red Delete is a
// menu item at the end, apart from the others, not a button next to Edit.
//
//   <ActionMenu label={t('More actions for {name}', { name: db.db_name })} items={[
//     { label: t('Download as SQL'), icon: Download, onClick: () => download(db) },
//     { label: t('Change password'), icon: KeyRound, onClick: () => changePassword(db) },
//     'separator',
//     { label: t('Delete database'), icon: Trash2, danger: true, onClick: () => remove(db) },
//   ]}/>
//
// An item: { label, icon, onClick, href (a link - opens in a new tab with
// `external`), disabled, hidden, danger, hint (a second line), title }.
// 'separator' draws a line. `text` puts a word beside the "…" (e.g. "More").
//
// Keyboard: Enter/Space/ArrowDown on the button opens the menu at its first
// item; arrows, Home and End move; Escape closes it and returns to the
// button; Tab closes it. The menu is drawn over the page (a portal), so a
// card that clips its overflow does not cut it off; it opens upwards when
// there is no room below.
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { MoreHorizontal } from 'lucide-react';
import './ActionMenu.css';

export default function ActionMenu({ items = [], label, text, icon: TriggerIcon = MoreHorizontal, disabled = false, className = '', align = 'end' }) {
  const shown = items.filter((item) => item && (item === 'separator' || !item.hidden));
  // No separator at either end, nor two in a row.
  const list = shown.filter((item, i) => item !== 'separator' || (i > 0 && i < shown.length - 1 && shown[i - 1] !== 'separator'));
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const menuId = useId();
  const focusFirst = useRef(true);

  const close = useCallback((refocus = true) => {
    setOpen(false);
    setPlace(null);
    if (refocus) triggerRef.current?.focus({ preventScroll: true });
  }, []);

  // Where the menu goes: under the button, its end edge on the button's (or
  // its start edge, `align="start"`); above it when the page has no room
  // below; never past the window's edges.
  const measure = useCallback(() => {
    const button = triggerRef.current;
    const menu = menuRef.current;
    if (!button || !menu) return;
    const r = button.getBoundingClientRect();
    const w = menu.offsetWidth;
    const h = menu.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = align === 'start' ? r.left : r.right - w;
    left = Math.max(8, Math.min(left, vw - w - 8));
    const below = vh - r.bottom;
    const up = below < h + 12 && r.top > below;
    const top = up ? Math.max(8, r.top - h - 6) : Math.min(r.bottom + 6, vh - h - 8);
    setPlace({ left, top: Math.max(8, top), up });
  }, [align]);

  useLayoutEffect(() => {
    if (!open) return;
    measure();
  }, [open, measure]);

  useEffect(() => {
    if (!open) return undefined;
    const outside = (event) => {
      if (menuRef.current?.contains(event.target) || triggerRef.current?.contains(event.target)) return;
      close(false);
    };
    // The page scrolls or the window changes size (a phone's address bar,
    // its keyboard): the menu follows its button, and closes once the
    // button has left the window.
    let frame = 0;
    const follow = (event) => {
      if (event?.target && menuRef.current?.contains(event.target)) return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const r = triggerRef.current?.getBoundingClientRect();
        if (!r || r.bottom < 0 || r.top > window.innerHeight) close(false);
        else measure();
      });
    };
    document.addEventListener('mousedown', outside);
    document.addEventListener('touchstart', outside, { passive: true });
    window.addEventListener('resize', follow);
    window.addEventListener('scroll', follow, true);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('mousedown', outside);
      document.removeEventListener('touchstart', outside);
      window.removeEventListener('resize', follow);
      window.removeEventListener('scroll', follow, true);
    };
  }, [open, close, measure]);

  // Focus the first (or the last) item once the menu is placed.
  useEffect(() => {
    if (!open || !place) return;
    const entries = [...(menuRef.current?.querySelectorAll('[role=menuitem]:not([aria-disabled=true])') || [])];
    (focusFirst.current ? entries[0] : entries[entries.length - 1])?.focus({ preventScroll: true });
  }, [open, !!place]);

  function toggle(first = true) {
    if (disabled) return;
    focusFirst.current = first;
    if (open) close(false);
    else setOpen(true);
  }

  function onTriggerKey(event) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      focusFirst.current = event.key === 'ArrowDown';
      if (!open) setOpen(true);
    }
  }

  function onMenuKey(event) {
    const entries = [...menuRef.current.querySelectorAll('[role=menuitem]:not([aria-disabled=true])')];
    const at = entries.indexOf(document.activeElement);
    let next = null;
    if (event.key === 'ArrowDown') next = entries[(at + 1) % entries.length];
    else if (event.key === 'ArrowUp') next = entries[(at - 1 + entries.length) % entries.length];
    else if (event.key === 'Home') next = entries[0];
    else if (event.key === 'End') next = entries[entries.length - 1];
    else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); return; }
    else if (event.key === 'Tab') { close(false); return; }
    if (next) { event.preventDefault(); next.focus(); }
  }

  function run(item) {
    if (item.disabled) return;
    close(!item.href);
    item.onClick?.();
  }

  if (list.length === 0) return null;
  return <>
    <button ref={triggerRef} type="button" className={['secondary-light', 'sn-menu-trigger', text ? 'has-text' : '', className].filter(Boolean).join(' ')}
      aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined} aria-label={text ? undefined : label} title={label}
      disabled={disabled} onClick={() => toggle(true)} onKeyDown={onTriggerKey}>
      <TriggerIcon size={16} aria-hidden="true"/>{text && <span>{text}</span>}
    </button>
    {open && createPortal(<div ref={menuRef} id={menuId} className={`sn-menu${place?.up ? ' is-up' : ''}`} role="menu" aria-label={label}
      style={place ? { left: place.left, top: place.top } : { visibility: 'hidden', left: 0, top: 0 }} onKeyDown={onMenuKey}>
      {list.map((item, i) => {
        if (item === 'separator') return <div key={`sep-${i}`} className="sn-menu-sep" role="separator"/>;
        const Icon = item.icon;
        const inner = <>
          {Icon ? <Icon size={15} aria-hidden="true"/> : <span className="sn-menu-noicon" aria-hidden="true"/>}
          <span className="sn-menu-text"><span>{item.label}</span>{item.hint && <small>{item.hint}</small>}</span>
        </>;
        const cls = ['sn-menu-item', item.danger ? 'is-danger' : ''].filter(Boolean).join(' ');
        if (item.href && !item.disabled) {
          return <a key={i} role="menuitem" tabIndex={-1} className={cls} href={item.href} title={item.title}
            target={item.external ? '_blank' : undefined} rel={item.external ? 'noopener noreferrer' : undefined}
            onClick={(event) => {
              if (!item.external && item.onClick && !(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0)) {
                event.preventDefault();
                run(item);
              } else close(false);
            }}>{inner}</a>;
        }
        return <button key={i} type="button" role="menuitem" tabIndex={-1} className={cls} title={item.title}
          aria-disabled={item.disabled || undefined} onClick={() => run(item)}>{inner}</button>;
      })}
    </div>, document.body)}
  </>;
}
