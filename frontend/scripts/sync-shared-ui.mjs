// The shared BNIX interface: src/shared/ is edited in OPanel only. This
// rewrites its MANIFEST and, given a BPanel checkout, copies the folder there.
//
//   node scripts/sync-shared-ui.mjs                  # refresh MANIFEST
//   node scripts/sync-shared-ui.mjs ../../bpanel     # ... and copy into BPanel
//
// Text files are hashed with LF line endings, so a Windows checkout (CRLF)
// and a Linux one agree; see backend/app/tests/test_shared_ui.py.
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const shared = path.resolve(here, '../src/shared');
const TEXT = /\.(css|md|txt|js|jsx|json)$/i;

function listFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...listFiles(full));
    else if (entry.name !== 'MANIFEST') out.push(path.relative(shared, full).split(path.sep).join('/'));
  }
  return out;
}

function digest(file) {
  let data = fs.readFileSync(file);
  if (TEXT.test(file)) data = Buffer.from(data.toString('utf8').replace(/\r\n/g, '\n'), 'utf8');
  return createHash('sha256').update(data).digest('hex');
}

const files = listFiles(shared).sort();
fs.writeFileSync(path.join(shared, 'MANIFEST'),
  files.map(rel => `${digest(path.join(shared, rel))}  ${rel}`).join('\n') + '\n');
console.log(`MANIFEST: ${files.length} files`);

const target = process.argv[2];
if (target) {
  const dest = path.resolve(process.cwd(), target, 'frontend/src/shared');
  if (!fs.existsSync(path.resolve(process.cwd(), target, 'frontend/src'))) {
    console.error(`not a panel checkout: ${target}`);
    process.exit(1);
  }
  fs.rmSync(dest, { recursive: true, force: true });
  fs.cpSync(shared, dest, { recursive: true });
  console.log(`copied to ${dest}`);
}
