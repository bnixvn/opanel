// The panel's own confirm and prompt, in place of the browser's: styled,
// translated (the browser words its OK/Cancel in its own language), never
// freezing the page, with a masked field for a password.
//
//   if (!(await confirmDialog({ title: t('Delete package {name}?', { name }), danger: true, confirmLabel: t('Delete') }))) return;
//
//   const choice = await confirmDialog({
//     title: t('Delete website'), subject: site.domain, danger: true, confirmLabel: t('Delete website'),
//     message: t('…'), typeToConfirm: site.domain,
//     options: [{ name: 'files', label: t('Delete its files'), checked: true }],
//   });
//   if (!choice) return;          // cancelled
//   choice.files                  // true or false: the options as ticked
//
//   const name = await promptDialog({ title: t('New folder'), label: t('Folder name'), validate: (v) => … });
//   if (name === null) return;    // cancelled
//
//   const step = await promptDialog({ title: t('Confirm it is you'), fields: [
//     { name: 'current_password', label: t('Current password'), type: 'password', autoComplete: 'current-password' },
//     { name: 'code', label: t('Authentication code'), type: 'code' },
//   ] });                         // { current_password, code } or null
//
// confirmDialog(options) resolves to false when cancelled, true when
// confirmed, or - with `options` - an object of the ticked choices.
// promptDialog(options) resolves to null when cancelled, the value (one
// field), or an object of values (`fields`).
//
// Options, both kinds: title, message (text: a blank line starts a new
// paragraph; with no title, its first paragraph or its question is the
// title), body (a node, under the message), subject (the thing acted on -
// a domain, a user - shown on its own line), consequences (a list), danger
// (a red confirm button, focus starts on Cancel), confirmLabel, cancelLabel.
// confirmDialog: typeToConfirm (a word to type before the button works),
// options ([{ name, label, hint, checked, disabled }]). promptDialog: label,
// type ('text' | 'password' | 'code' | 'number'), defaultValue, placeholder,
// hint, required (default true), minLength, maxLength, validate(value,
// values) -> an error or '', generate (password: offer a generator), select
// ('all' | 'name': what is selected at first), autoComplete, inputMode;
// or fields: [{ name, …the same per field }].
//
// Keyboard: Escape cancels, Enter in a field confirms, Tab stays inside the
// dialog; focus returns where it was when the dialog closes. One dialog at a
// time: a second one waits for the first.
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, HelpCircle, X } from 'lucide-react';
import { useT } from '../lib/t.js';
import { Checkbox, Field, PasswordField } from './Form.jsx';
import './Dialog.css';

let enqueue = null;
const waiting = [];
let sequence = 0;

function open(kind, options) {
  return new Promise((resolve) => {
    const request = { id: ++sequence, kind, options: options || {}, resolve };
    if (enqueue) enqueue(request);
    else waiting.push(request);
  });
}

export function confirmDialog(options) {
  return open('confirm', typeof options === 'string' ? { message: options } : options);
}

export function promptDialog(options) {
  return open('prompt', typeof options === 'string' ? { label: options } : options);
}

// Mounted once, at the root, inside the LocaleProvider.
export function DialogHost() {
  const [queue, setQueue] = useState([]);
  useEffect(() => {
    enqueue = (request) => setQueue((list) => [...list, request]);
    if (waiting.length) {
      const early = waiting.splice(0);
      setQueue((list) => [...list, ...early]);
    }
    return () => { enqueue = null; };
  }, []);
  const current = queue[0];
  const close = useCallback((request, value) => {
    request.resolve(value);
    setQueue((list) => list.filter((item) => item !== request));
  }, []);
  if (!current) return null;
  return createPortal(<DialogView key={current.id} request={current} onClose={close}/>, document.body);
}

// Splits a question with no title of its own into a title and a body: at
// its first blank line, or after its first question mark - "Suspend user
// bob? This will block login…" is titled "Suspend user bob?".
function splitMessage(message) {
  if (typeof message !== 'string') return [null, message];
  const text = message.trim();
  const blank = text.indexOf('\n\n');
  if (blank > 0) return [text.slice(0, blank), text.slice(blank + 2)];
  const question = /^([^?]{1,160}\?)\s+([\s\S]+)$/.exec(text);
  if (question) return [question[1], question[2]];
  if (text.length <= 90 && !text.includes('\n')) return [text, ''];
  return [null, text];
}

function Paragraphs({ text }) {
  if (typeof text !== 'string') return text || null;
  return text.split(/\n{2,}/).filter((p) => p.trim()).map((p, i) => <p key={i}>
    {p.split('\n').map((line, j) => <span key={j}>{j > 0 && <br/>}{line}</span>)}
  </p>);
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function DialogView({ request, onClose }) {
  const t = useT();
  const { kind, options: o } = request;
  const prompt = kind === 'prompt';
  const fields = prompt ? (o.fields || [{ ...o, name: 'value' }]).map((f) => ({ type: 'text', required: true, ...f })) : [];
  const choices = !prompt ? (o.options || []) : [];
  const danger = !!o.danger;
  const [splitTitle, splitBody] = o.title ? [o.title, o.message] : splitMessage(o.message);
  const title = splitTitle || (prompt ? (fields[0]?.label || t('Enter a value')) : t('Please confirm'));
  const message = splitBody;

  const [values, setValues] = useState(() => Object.fromEntries(fields.map((f) => [f.name, f.defaultValue == null ? '' : String(f.defaultValue)])));
  const [ticks, setTicks] = useState(() => Object.fromEntries(choices.map((c) => [c.name, !!c.checked])));
  const [typed, setTyped] = useState('');
  const [touched, setTouched] = useState({});
  const [tried, setTried] = useState(false);

  const titleId = useId();
  const bodyId = useId();
  const boxRef = useRef(null);
  const firstInput = useRef(null);
  const typeRef = useRef(null);
  const cancelRef = useRef(null);
  const confirmRef = useRef(null);
  const returnTo = useRef(typeof document !== 'undefined' ? document.activeElement : null);

  function errorOf(field) {
    const raw = values[field.name] ?? '';
    const value = field.type === 'password' ? raw : raw.trim();
    if (!value) return field.required ? (field.requiredMessage || t('This field is required.')) : '';
    if (field.minLength && value.length < field.minLength) return t('At least {count} characters.', { count: field.minLength });
    if (field.maxLength && value.length > field.maxLength) return t('At most {count} characters.', { count: field.maxLength });
    if (field.type === 'number' && !Number.isFinite(Number(value))) return t('Enter a number.');
    return field.validate ? (field.validate(value, values) || '') : '';
  }
  const errors = Object.fromEntries(fields.map((f) => [f.name, errorOf(f)]));
  const typeOk = !o.typeToConfirm || typed.trim().toLowerCase() === String(o.typeToConfirm).trim().toLowerCase();

  // Focus: the first field; else the word to type; else Cancel on a
  // destructive question, the confirm button on any other.
  useEffect(() => {
    const target = firstInput.current || typeRef.current || (danger ? cancelRef.current : confirmRef.current);
    target?.focus();
    const field = fields[0];
    if (field && firstInput.current && field.select && field.type !== 'password') {
      const input = firstInput.current;
      const v = input.value || '';
      const dot = v.lastIndexOf('.');
      if (field.select === 'name' && dot > 0) input.setSelectionRange(0, dot);
      else input.select();
    }
    document.body.classList.add('sn-dialog-open');
    const back = returnTo.current;
    return () => {
      document.body.classList.remove('sn-dialog-open');
      if (back && typeof back.focus === 'function' && document.contains(back)) back.focus();
    };
  }, []);

  function cancel() {
    onClose(request, prompt ? null : false);
  }

  function submit(event) {
    event?.preventDefault();
    if (prompt) {
      const bad = fields.find((f) => errors[f.name]);
      if (bad) {
        setTried(true);
        boxRef.current?.querySelector(`[data-field="${CSS.escape(bad.name)}"] input`)?.focus();
        return;
      }
      const out = Object.fromEntries(fields.map((f) => {
        const raw = values[f.name] ?? '';
        return [f.name, f.type === 'password' ? raw : raw.trim()];
      }));
      onClose(request, o.fields ? out : out.value);
      return;
    }
    if (!typeOk) { typeRef.current?.focus(); return; }
    onClose(request, choices.length ? { ...ticks } : true);
  }

  function onKeyDown(event) {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      cancel();
      return;
    }
    if (event.key !== 'Tab' || !boxRef.current) return;
    const items = [...boxRef.current.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  // A click that starts and ends on the backdrop closes a question; a form
  // being filled in is only closed by its buttons or Escape.
  const downOnBackdrop = useRef(false);
  const dirty = prompt || typed;

  const Icon = danger ? AlertTriangle : HelpCircle;
  const confirmLabel = o.confirmLabel || (prompt ? t('OK') : danger ? t('Delete') : t('Confirm'));
  const cancelLabel = o.cancelLabel || t('Cancel');

  return <div className="sn-dialog-backdrop"
    onMouseDown={(e) => { downOnBackdrop.current = e.target === e.currentTarget; }}
    onMouseUp={(e) => { if (downOnBackdrop.current && e.target === e.currentTarget && !dirty) cancel(); downOnBackdrop.current = false; }}>
    <form ref={boxRef} className={['sn-dialog', danger ? 'is-danger' : '', o.wide ? 'is-wide' : ''].filter(Boolean).join(' ')}
      role={danger ? 'alertdialog' : 'dialog'} aria-modal="true" aria-labelledby={titleId} aria-describedby={message || o.body || o.subject || o.consequences ? bodyId : undefined}
      onKeyDown={onKeyDown} onSubmit={submit} noValidate>
      <header className="sn-dialog-head">
        <span className="sn-dialog-icon" aria-hidden="true"><Icon size={20}/></span>
        <h2 id={titleId}>{title}</h2>
        <button type="button" className="sn-dialog-x" onClick={cancel} aria-label={t('Close')} title={t('Close')}><X size={18} aria-hidden="true"/></button>
      </header>
      <div className="sn-dialog-body">
        <div id={bodyId} className="sn-dialog-text">
          {o.subject && <p className="sn-dialog-subject"><strong>{o.subject}</strong></p>}
          <Paragraphs text={message}/>
          {o.consequences?.length > 0 && <ul className="sn-dialog-consequences">{o.consequences.filter(Boolean).map((c, i) => <li key={i}>{c}</li>)}</ul>}
          {o.body}
        </div>
        {fields.map((f, i) => {
          const showError = (touched[f.name] || tried) ? errors[f.name] : '';
          const common = {
            value: values[f.name] ?? '',
            placeholder: f.placeholder,
            autoComplete: f.autoComplete,
            onBlur: () => setTouched((s) => ({ ...s, [f.name]: true })),
          };
          return <div key={f.name} data-field={f.name}>
            <Field label={o.fields || fields[0].label !== title ? f.label : <span className="sr-only">{f.label}</span>}
              hint={f.hint} error={showError} required={o.fields ? f.required : false}>
              {f.type === 'password'
                ? <PasswordField {...common} inputRef={i === 0 ? firstInput : undefined} generate={!!f.generate} copy={!!f.generate}
                  length={f.length || 20} autoComplete={f.autoComplete || 'new-password'} revealed={!!f.revealed}
                  onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))}/>
                : <input {...common} ref={i === 0 ? firstInput : undefined}
                  type={f.type === 'number' ? 'number' : 'text'}
                  inputMode={f.inputMode || (f.type === 'code' ? 'numeric' : undefined)}
                  autoComplete={f.autoComplete || (f.type === 'code' ? 'one-time-code' : 'off')}
                  className={f.type === 'code' ? 'sn-dialog-code' : undefined}
                  maxLength={f.type === 'code' ? 12 : undefined}
                  spellCheck={false} autoCapitalize="none"
                  onChange={(e) => setValues((s) => ({ ...s, [f.name]: e.target.value }))}/>}
            </Field>
          </div>;
        })}
        {choices.length > 0 && <div className="sn-dialog-options">
          {choices.map((c) => <Checkbox key={c.name} checked={ticks[c.name]} disabled={c.disabled} label={c.label} hint={c.hint}
            className={c.danger ? 'is-danger' : ''} onChange={(v) => setTicks((s) => ({ ...s, [c.name]: v }))}/>)}
        </div>}
        {o.typeToConfirm && <div className="sn-dialog-type">
          <Field label={t('Type {word} to confirm', { word: <code>{o.typeToConfirm}</code> })}>
            <input ref={typeRef} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} autoCapitalize="none"/>
          </Field>
        </div>}
      </div>
      <footer className="sn-dialog-actions">
        <button type="button" ref={cancelRef} className="secondary-light" onClick={cancel}>{cancelLabel}</button>
        <button type="submit" ref={confirmRef} className={danger ? 'danger sn-dialog-confirm' : 'sn-dialog-confirm'} disabled={!typeOk}>{confirmLabel}</button>
      </footer>
    </form>
  </div>;
}
