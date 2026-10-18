import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import './DomainPicker.css';

// A domain field with its list (operator, 2026-10-18: Email's "Turn on email
// for a domain" was a <datalist> of every domain on the server - a long, flat
// list Chrome only shows as suggestions). Type to narrow it, the domains
// grouped by account; Up/Down and Enter to pick, Esc to close. A domain that is
// not in the list can still be typed when `free` is set.
//
//   <DomainPicker value onChange options={[{ value, group }]} placeholder free emptyText/>
export default function DomainPicker({ value, onChange, options, placeholder, free = true, emptyText = '', ariaLabel }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const boxRef = useRef(null);
  const listRef = useRef(null);
  const listId = useId();
  const needle = String(value || '').trim().toLowerCase();

  // The matches, in their groups, the groups in alphabetical order.
  const matches = useMemo(() => {
    const shown = options.filter((option) => !needle || option.value.toLowerCase().includes(needle));
    return shown.sort((a, b) => (a.group || '').localeCompare(b.group || '') || a.value.localeCompare(b.value));
  }, [options, needle]);

  useEffect(() => { setActive(0); }, [needle, open]);
  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (event) => { if (!boxRef.current?.contains(event.target)) setOpen(false); };
    document.addEventListener('pointerdown', onPointer);
    return () => document.removeEventListener('pointerdown', onPointer);
  }, [open]);
  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  function choose(option) {
    onChange(option.value);
    setOpen(false);
  }

  function onKeyDown(event) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (!open) setOpen(true);
      else setActive((index) => Math.min(index + 1, matches.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((index) => Math.max(index - 1, 0));
    } else if (event.key === 'Enter' && open && matches[active]) {
      event.preventDefault();
      choose(matches[active]);
    } else if (event.key === 'Escape' && open) {
      event.preventDefault();
      setOpen(false);
    }
  }

  let lastGroup = null;
  return <div className="domain-picker" ref={boxRef}>
    <div className="domain-picker-field">
      <input value={value} placeholder={placeholder} spellCheck={false} autoComplete="off"
        role="combobox" aria-expanded={open} aria-controls={listId} aria-autocomplete="list" aria-label={ariaLabel}
        aria-activedescendant={open && matches[active] ? `${listId}-${active}` : undefined}
        onFocus={() => setOpen(true)} onClick={() => setOpen(true)} onKeyDown={onKeyDown}
        onChange={(event) => { onChange(free ? event.target.value.trim().toLowerCase() : event.target.value); setOpen(true); }}/>
      <button type="button" className="domain-picker-toggle" tabIndex={-1} aria-hidden="true"
        onClick={() => setOpen((shown) => !shown)}><ChevronDown size={15}/></button>
    </div>
    {open && <div className="domain-picker-list" role="listbox" id={listId} ref={listRef}>
      {matches.length === 0 && <div className="domain-picker-empty">{emptyText}</div>}
      {matches.map((option, index) => {
        const head = option.group !== lastGroup ? option.group : null;
        lastGroup = option.group;
        return <div key={option.value} role="presentation">
          {head && <div className="domain-picker-group" role="presentation">{head}</div>}
          <div role="option" id={`${listId}-${index}`} aria-selected={option.value === value} data-active={index === active}
            className={`domain-picker-option${index === active ? ' is-active' : ''}${option.value === value ? ' is-chosen' : ''}`}
            onPointerDown={(event) => event.preventDefault()} onClick={() => choose(option)}
            onPointerEnter={() => setActive(index)}>{option.value}</div>
        </div>;
      })}
    </div>}
  </div>;
}
