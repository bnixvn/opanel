// SNPanel's components call t(text, { name }) with named placeholders; OPanel's
// tr() numbers them. This looks the text up in OPanel's dictionary and fills
// the names in, so a component reads the same in both panels.
import { tr } from '../i18n';

export function t(text, values) {
  const out = tr(text);
  if (!values || typeof out !== 'string') return out;
  return out.replace(/\{(\w+)\}/g, (whole, name) =>
    (Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : whole));
}

// The language is fixed for the page's life (changing it reloads), so the hook
// has nothing to subscribe to.
export function useT() {
  return t;
}
