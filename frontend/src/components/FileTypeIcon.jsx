// A solid, coloured icon for each kind of file, as the operator's reference
// (cPanel/DirectAdmin style, 2026-10-25) has them: a yellow folder, a purple
// PHP page with </>, an orange HTML one, a violet text page with lines... The
// colours are in shared/file-manager.css (.fti-*); the glyph on the page is
// white, so it reads on every one of them.
import React from 'react';

const KINDS = [
  ['php', 'code', ['php', 'phtml', 'php3', 'php4', 'php5', 'php7', 'php8', 'phar']],
  ['html', 'code', ['html', 'htm', 'xhtml', 'tpl', 'twig', 'blade']],
  ['css', 'code', ['css', 'scss', 'sass', 'less']],
  ['js', 'code', ['js', 'mjs', 'cjs', 'jsx', 'ts', 'tsx', 'vue']],
  ['code', 'code', ['py', 'rb', 'go', 'java', 'c', 'h', 'cpp', 'cs', 'rs', 'sh', 'bash', 'pl', 'lua']],
  ['json', 'code', ['json', 'map', 'lock']],
  ['config', 'lines', ['ini', 'conf', 'cnf', 'cfg', 'env', 'htaccess', 'htpasswd', 'yml', 'yaml', 'toml', 'xml']],
  ['text', 'lines', ['txt', 'md', 'log', 'csv', 'rst', 'readme', 'license']],
  ['sql', 'lines', ['sql', 'db', 'sqlite']],
  ['image', 'image', ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp', 'avif', 'tif', 'tiff']],
  ['media', 'play', ['mp4', 'webm', 'mov', 'mp3', 'wav', 'ogg', 'm4a']],
  ['archive', 'zip', ['zip', 'tar', 'gz', 'tgz', 'bz2', 'xz', 'zst', 'rar', '7z', 'tbz2', 'txz', 'wpress']],
  ['pdf', 'lines', ['pdf']],
];
const BY_EXT = new Map(KINDS.flatMap(([kind, glyph, exts]) => exts.map((ext) => [ext, [kind, glyph]])));

export function fileKind(name, isDir) {
  if (isDir) return ['folder', 'folder'];
  const lower = String(name || '').toLowerCase();
  const dot = lower.lastIndexOf('.');
  // ".htaccess", ".env": the name after the dot is the kind.
  const ext = dot >= 0 ? lower.slice(dot + 1) : lower;
  return BY_EXT.get(ext) || BY_EXT.get(lower) || ['file', 'none'];
}

const WHITE = { fill: 'none', stroke: '#fff', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round' };

function glyph(kind) {
  if (kind === 'code') return <g {...WHITE}><path d="M9.6 12.2 7.8 14.2l1.8 2"/><path d="m14.4 12.2 1.8 2-1.8 2"/><path d="m12.7 11.6-1.4 5.2"/></g>;
  if (kind === 'lines') return <g {...WHITE}><path d="M8 12h8"/><path d="M8 15h8"/><path d="M8 18h5"/></g>;
  if (kind === 'image') return <g {...WHITE}><circle cx="9.5" cy="12" r="1.3"/><path d="m7.5 18 3-3 2 2 2-2.5 2 3.5"/></g>;
  if (kind === 'play') return <path d="M10 12v5l4.2-2.5z" fill="#fff"/>;
  if (kind === 'zip') return <g fill="#fff"><rect x="11" y="9" width="2" height="1.6" rx=".4"/><rect x="11" y="12" width="2" height="1.6" rx=".4"/><rect x="10.4" y="15" width="3.2" height="3" rx=".8"/></g>;
  return null;
}

export function FileTypeIcon({ name, isDir, size = 20 }) {
  const [kind, mark] = fileKind(name, isDir);
  if (mark === 'folder') {
    return <svg width={size} height={size} viewBox="0 0 24 24" className="fti fti-folder" aria-hidden="true">
      <path d="M2.5 6.5A2 2 0 0 1 4.5 4.5h4.2a2 2 0 0 1 1.5.7l1.3 1.5h8a2 2 0 0 1 2 2v9.3a2 2 0 0 1-2 2h-15a2 2 0 0 1-2-2z" fill="currentColor"/>
    </svg>;
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" className={`fti fti-${kind}`} aria-hidden="true">
    <path d="M6.5 2h7.3l5.7 5.7v12.3a2 2 0 0 1-2 2H6.5a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" fill="currentColor"/>
    <path d="M13.8 2v4.2a1.5 1.5 0 0 0 1.5 1.5h4.2z" fill="#fff" opacity=".45"/>
    {glyph(mark)}
  </svg>;
}

export default FileTypeIcon;
