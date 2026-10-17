// SNPanel's components call t(text, { name }) with named placeholders; OPanel's
// tr() numbers them. This looks the text up in OPanel's dictionary and fills
// the names in, so a component reads the same in both panels.
//
// A value may be an element (a link, a <code>): the sentence then comes back
// as a list of its pieces, for React to draw, instead of a string.
import { cloneElement, isValidElement } from 'react';
import { tr } from '../i18n';

const has = (values, name) => Object.prototype.hasOwnProperty.call(values, name);

export function t(text, values) {
  const out = tr(text);
  if (!values || typeof out !== 'string') return out;
  if (!Object.values(values).some(isValidElement)) {
    return out.replace(/\{(\w+)\}/g, (whole, name) => (has(values, name) ? String(values[name]) : whole));
  }
  return out.split(/(\{\w+\})/).filter(Boolean).map((part, i) => {
    const name = /^\{(\w+)\}$/.exec(part)?.[1];
    if (!name || !has(values, name)) return part;
    const value = values[name];
    return isValidElement(value) ? cloneElement(value, { key: i }) : String(value);
  });
}

// The language is fixed for the page's life (changing it reloads), so the hook
// has nothing to subscribe to.
export function useT() {
  return t;
}
