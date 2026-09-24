// Report which tr("...") strings have no Vietnamese translation yet.
//
//   npm run i18n:check            list missing and unused keys
//   npm run i18n:check -- --strict   also exit 1 when anything is missing
//
// English is the source: a missing entry is not an error at runtime (the
// English text shows), so this is a to-do list for whoever adds UI text.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { parseAst } from 'rolldown/parseAst';

const SRC = new URL('../src/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const strict = process.argv.includes('--strict');

function files(dir) {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === 'locales' ? [] : files(path);
    return /\.(jsx?|mjs)$/.test(name) ? [path] : [];
  });
}

const used = new Set();
for (const file of files(SRC)) {
  const ast = parseAst(readFileSync(file, 'utf8'), { lang: 'jsx' });
  (function walk(node) {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (node.type === 'CallExpression' && node.callee.type === 'Identifier' && node.callee.name === 'tr') {
      const arg = node.arguments[0];
      if (arg && arg.type === 'Literal' && typeof arg.value === 'string') used.add(arg.value);
    }
    for (const key in node) if (key !== 'start' && key !== 'end') walk(node[key]);
  })(ast);
}

// load through a data: URL so Node does not warn that package.json has no "type"
const viSource = readFileSync(new URL('../src/locales/vi.js', import.meta.url), 'utf8');
const { default: vi } = await import('data:text/javascript,' + encodeURIComponent(viSource));
const placeholders = s => (s.match(/\{\d+\}/g) || []).sort().join(',');
const missing = [...used].filter(key => !(key in vi)).sort();
const mismatched = Object.keys(vi).filter(key => placeholders(key) !== placeholders(vi[key]));

console.log(`tr() strings in src: ${used.size}; Vietnamese entries: ${Object.keys(vi).length}`);
if (missing.length) console.log(`\nMissing Vietnamese (${missing.length}):\n` + missing.map(k => `  ${JSON.stringify(k)}`).join('\n'));
if (mismatched.length) console.log(`\nPlaceholder mismatch (${mismatched.length}):\n` + mismatched.map(k => `  ${JSON.stringify(k)}`).join('\n'));
if (!missing.length && !mismatched.length) console.log('All strings translated.');
if (mismatched.length || (strict && missing.length)) process.exit(1);
