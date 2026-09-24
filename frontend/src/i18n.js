// Interface language.
//
// English is the source language: every string in the panel is written in
// English and is its own key, so a missing translation shows the English text
// rather than a blank or a key name. A table per language maps English to that
// language; placeholders are {0}, {1}, ... in the order of the arguments.
//
// The language is read once at start-up and changing it reloads the page.
// That keeps tr() a plain function -- constants built at module load (menus,
// option lists) come out in the right language without threading a context
// through six thousand lines of components.
import vi from './locales/vi.js';

export const LANGUAGES = [
  { code: 'en', label: 'English', short: 'EN' },
  { code: 'vi', label: 'Tiếng Việt', short: 'VI' },
];

const TABLES = { vi };
const STORAGE_KEY = 'opanel_lang';

function initialLanguage() {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === 'en' || TABLES[saved]) return saved;
  } catch {
    // Private mode or blocked storage: English, as for a first visit.
  }
  return 'en';
}

export const currentLanguage = typeof window === 'undefined' ? 'en' : initialLanguage();
const table = TABLES[currentLanguage] || null;

if (typeof document !== 'undefined') document.documentElement.lang = currentLanguage;

export function tr(text, ...args) {
  if (typeof text !== 'string') return text;
  let out = (table && table[text]) || text;
  if (args.length) {
    out = out.replace(/\{(\d+)\}/g, (match, index) => {
      const value = args[Number(index)];
      return value === undefined || value === null ? '' : String(value);
    });
  }
  return out;
}

export function setLanguage(code) {
  if (code === currentLanguage) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, code);
  } catch {
    return;
  }
  window.location.reload();
}

export function nextLanguage() {
  const index = LANGUAGES.findIndex(lang => lang.code === currentLanguage);
  return LANGUAGES[(index + 1) % LANGUAGES.length];
}
