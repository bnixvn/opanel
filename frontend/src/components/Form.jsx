// The panel's form primitives. One way to label a control, mark it
// required, hint at it and say what is wrong with it - instead of a
// placeholder standing in for the label and a disabled Submit that never
// says why.
//
//   <Field label={t('Database name')} required hint={t('Letters, digits and _')} error={errors.name}>
//     <input value={name} onChange={(e) => setName(e.target.value)} onBlur={check}/>
//   </Field>
//   <Field label={t('Password')} required error={errors.password}>
//     <PasswordField value={pw} onChange={setPw} generate copy/>
//   </Field>
//   <Checkbox checked={own} onChange={setOwn} label={t('Own resource limits')} hint={t('…')}/>
//
// Field hands its control the id its <label> points at, and aria-describedby
// / aria-invalid / aria-required for the hint and the error, so a screen
// reader reads all three with the control. Switch (components/Switch.jsx) is
// the on/off control; Checkbox is for a choice that applies when a form is
// sent.
import { Children, cloneElement, isValidElement, useEffect, useId, useRef, useState } from 'react';
import { AlertCircle, Check, Copy, Dices, Eye, EyeOff } from 'lucide-react';
import { useT } from '../lib/t.js';
import { generatePassword } from '../lib/password.js';
import './Form.css';

export function Field({ label, hint, error, required = false, optional = false, id: givenId, className = '', inline = false, children }) {
  const t = useT();
  const auto = useId();
  const only = Children.count(children) === 1 && isValidElement(children) ? children : null;
  const id = givenId || only?.props.id || `field${auto}`;
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [only?.props['aria-describedby'], hintId, errorId].filter(Boolean).join(' ') || undefined;
  const control = only
    ? cloneElement(only, {
      id,
      'aria-describedby': describedBy,
      'aria-invalid': error ? true : only.props['aria-invalid'],
      'aria-required': required || only.props['aria-required'] || undefined,
    })
    : children;
  return <div className={['sn-field', inline ? 'is-inline' : '', error ? 'has-error' : '', className].filter(Boolean).join(' ')}>
    {/* One span inside the label: a grid or flex rule some form puts on its
        labels then lays out one item, not the words and the mark apart. */}
    {label && <label className="sn-field-label" htmlFor={only ? id : undefined}>
      <span className="sn-field-label-text">{label}
        {required && <b className="sn-required" aria-hidden="true">*</b>}
        {required && <i className="sr-only"> ({t('required')})</i>}
        {optional && <em className="sn-optional"> {t('(optional)')}</em>}
      </span>
    </label>}
    {control}
    {hint && <small className="sn-field-hint" id={hintId}>{hint}</small>}
    {error && <small className="sn-field-error" id={errorId} role="alert"><AlertCircle size={13} aria-hidden="true"/>{error}</small>}
  </div>;
}

// A password input with show/hide, and on request a generator ("Generate
// password") and a copy button. `onChange` receives the value, not the
// event. A generated password is shown, so it can be read and noted.
export function PasswordField({
  value = '', onChange, id, name, placeholder, autoComplete = 'new-password', disabled = false, required,
  minLength, maxLength, generate = false, length = 20, onGenerate, copy = false, revealed = false,
  inputRef, autoFocus, onBlur, onKeyDown, className = '',
  'aria-describedby': describedBy, 'aria-invalid': invalid, 'aria-required': ariaRequired, 'aria-label': ariaLabel,
}) {
  const t = useT();
  const [shown, setShown] = useState(revealed);
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  function makeOne() {
    const next = onGenerate ? onGenerate() : generatePassword(length);
    if (typeof next === 'string') onChange?.(next);
    setShown(true);
  }

  async function copyIt() {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = value;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch {}
      document.body.removeChild(ta);
    }
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 2000);
  }

  return <div className={['sn-password', className].filter(Boolean).join(' ')}>
    <div className="sn-password-input">
      <input
        ref={inputRef} id={id} name={name} type={shown ? 'text' : 'password'} value={value}
        onChange={(event) => onChange?.(event.target.value)} onBlur={onBlur} onKeyDown={onKeyDown}
        placeholder={placeholder} autoComplete={autoComplete} autoCapitalize="none" autoCorrect="off" spellCheck={false}
        disabled={disabled} required={required} minLength={minLength} maxLength={maxLength} autoFocus={autoFocus}
        aria-describedby={describedBy} aria-invalid={invalid} aria-required={ariaRequired} aria-label={ariaLabel}
      />
      <button type="button" className="sn-password-eye" onClick={() => setShown((s) => !s)} disabled={disabled}
        aria-label={shown ? t('Hide password') : t('Show password')} title={shown ? t('Hide password') : t('Show password')} aria-pressed={shown}>
        {shown ? <EyeOff size={16} aria-hidden="true"/> : <Eye size={16} aria-hidden="true"/>}
      </button>
    </div>
    {copy && <button type="button" className="secondary-light sn-password-tool" onClick={copyIt} disabled={disabled || !value}
      aria-label={t('Copy password')} title={copied ? t('Copied') : t('Copy password')}>
      {copied ? <Check size={15} aria-hidden="true"/> : <Copy size={15} aria-hidden="true"/>}
    </button>}
    {generate && <button type="button" className="secondary-light sn-password-tool sn-password-generate" onClick={makeOne} disabled={disabled}
      title={t('Generate password')}>
      <Dices size={15} aria-hidden="true"/><span>{t('Generate password')}</span>
    </button>}
  </div>;
}

// A checkbox with its label beside it - and a hint under the label - at the
// size a checkbox is, whatever the input rules around it say. `onChange`
// receives true or false.
export function Checkbox({ checked, onChange, label, hint, disabled = false, indeterminate = false, name, value, id, title, className = '', children }) {
  const ref = useRef(null);
  const auto = useId();
  const hintId = hint ? `check${auto}-hint` : undefined;
  useEffect(() => { if (ref.current) ref.current.indeterminate = !!indeterminate; }, [indeterminate]);
  return <label className={['sn-check', disabled ? 'is-disabled' : '', className].filter(Boolean).join(' ')} title={title}>
    <input ref={ref} type="checkbox" id={id} name={name} value={value} checked={!!checked} disabled={disabled}
      aria-describedby={hintId} onChange={(event) => onChange?.(event.target.checked, event)}/>
    <span className="sn-check-text">
      <span className="sn-check-label">{label ?? children}</span>
      {hint && <small id={hintId}>{hint}</small>}
    </span>
  </label>;
}
