// The File manager's folder tree, the left column as cPanel and DirectAdmin
// have it (operator, 2026-10-25). Each folder's subfolders are read when it
// is opened, through the same listing the file list uses; the folder the list
// shows is always open in the tree, with its ancestors.
import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, HardDrive } from 'lucide-react';
import { FileTypeIcon } from './FileTypeIcon.jsx';

const dirsOf = items => (items || []).filter(item => item.is_dir)
  .map(item => ({ name: item.name, path: item.path }))
  .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));

const ancestors = path => {
  const parts = String(path || '').split('/').filter(Boolean);
  return parts.map((_, n) => parts.slice(0, n + 1).join('/'));
};

// homeLabel/homeEntries: the tree starts at the account's home (operator,
// 2026-11-07): /home/<user> with each website and apps/<name> under it, and
// the folders of the one being browsed open beneath it. Files are still only
// changed inside a website or an application, never in the home itself.
export function FolderTree({ request, base, targetKey, current, currentItems, rootLabel, onOpen, t,
  homeLabel = '', homeEntries = [], atHome = false, onHome = () => {} }) {
  // path -> [{ name, path }] once read, 'loading' while it is.
  const [children, setChildren] = useState({});
  const [open, setOpen] = useState(() => new Set(['']));
  const pending = useRef(new Set());

  const load = async path => {
    if (!base || pending.current.has(path)) return;
    pending.current.add(path);
    setChildren(prev => (prev[path] ? prev : { ...prev, [path]: 'loading' }));
    const data = await request(`${base}?path=${encodeURIComponent(path)}`, { silent: true });
    pending.current.delete(path);
    setChildren(prev => ({ ...prev, [path]: dirsOf(data?.items) }));
  };

  // Another website or application: start again from its root. `base` too:
  // opened straight at /filemanager, the website is chosen after the first
  // render, and the root was never read (operator, 2026-10-25).
  useEffect(() => {
    // What the list already shows for its folder is kept, not read again.
    setChildren(Array.isArray(currentItems) ? { [current || '']: dirsOf(currentItems) } : {});
    setOpen(new Set(['', ...ancestors(current)]));
    pending.current = new Set();
    if (base) ['', ...ancestors(current)].filter(path => path !== (current || '')).forEach(load);
  }, [targetKey, base]);

  // The folder in the list, and every folder above it, open.
  useEffect(() => {
    const chain = ['', ...ancestors(current)];
    setOpen(prev => new Set([...prev, ...chain]));
    chain.forEach(path => { if (!children[path] && path !== current) load(path); });
  }, [current, targetKey, base]);

  // What the list shows is the truth for that folder: a folder just made or
  // deleted there shows here at once.
  useEffect(() => {
    if (!Array.isArray(currentItems)) return;
    setChildren(prev => ({ ...prev, [current || '']: dirsOf(currentItems) }));
  }, [currentItems]);

  const toggle = path => {
    setOpen(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else { next.add(path); if (!children[path]) load(path); }
      return next;
    });
  };

  const node = (path, name, depth, isRoot = false) => {
    const kids = children[path];
    const expanded = open.has(path);
    const active = (current || '') === path;
    const leaf = Array.isArray(kids) && kids.length === 0;
    return <li key={path || '/'} role="treeitem" aria-expanded={leaf ? undefined : expanded} aria-selected={active}>
      <div className={`fm-tree-row${active ? ' active' : ''}`} style={{ paddingLeft: `${8 + depth * 14}px` }}>
        <button type="button" className="fm-tree-toggle" tabIndex={-1} aria-hidden="true"
          onClick={() => toggle(path)} disabled={leaf} style={leaf ? { visibility: 'hidden' } : undefined}>
          {expanded ? <ChevronDown size={14}/> : <ChevronRight size={14}/>}
        </button>
        <button type="button" className="fm-tree-name" title={path ? `/${path}` : rootLabel}
          onClick={() => { onOpen(path); if (!expanded) toggle(path); }}>
          {isRoot ? <HardDrive size={15} aria-hidden="true"/> : <FileTypeIcon isDir size={18}/>}<span>{name}</span>
        </button>
      </div>
      {expanded && kids === 'loading' && <div className="fm-tree-loading" style={{ paddingLeft: `${30 + depth * 14}px` }}>{t('Loading...')}</div>}
      {expanded && Array.isArray(kids) && kids.length > 0 &&
        <ul role="group">{kids.map(kid => node(kid.path, kid.name, depth + 1))}</ul>}
    </li>;
  };

  const entryRow = (entry, depth) => {
    if (entry.active && !atHome) return <React.Fragment key={entry.key}>{node('', entry.label, depth)}</React.Fragment>;
    return <li key={entry.key} role="treeitem" aria-selected={false}>
      <div className="fm-tree-row" style={{ paddingLeft: `${8 + depth * 14}px` }}>
        <span className="fm-tree-toggle" aria-hidden="true" style={{ visibility: 'hidden' }}><ChevronRight size={14}/></span>
        <button type="button" className="fm-tree-name" title={entry.title || entry.label} onClick={entry.onSelect}>
          <FileTypeIcon isDir size={18}/><span>{entry.label}</span>
        </button>
      </div>
    </li>;
  };

  if (homeLabel) {
    const sites = homeEntries.filter(entry => entry.kind !== 'app');
    const apps = homeEntries.filter(entry => entry.kind === 'app');
    return <nav className="fm-tree" aria-label={t('Folders')}>
      <ul role="tree"><li role="treeitem" aria-expanded aria-selected={atHome}>
        <div className={`fm-tree-row${atHome ? ' active' : ''}`} style={{ paddingLeft: '8px' }}>
          <span className="fm-tree-toggle" aria-hidden="true"><ChevronDown size={14}/></span>
          <button type="button" className="fm-tree-name" title={homeLabel} onClick={onHome}>
            <HardDrive size={15} aria-hidden="true"/><span>{homeLabel}</span>
          </button>
        </div>
        <ul role="group">
          {sites.map(entry => entryRow(entry, 1))}
          {apps.length > 0 && <li role="treeitem" aria-expanded>
            <div className="fm-tree-row" style={{ paddingLeft: '22px' }}>
              <span className="fm-tree-toggle" aria-hidden="true"><ChevronDown size={14}/></span>
              <span className="fm-tree-name"><FileTypeIcon isDir size={18}/><span>apps</span></span>
            </div>
            <ul role="group">{apps.map(entry => entryRow(entry, 2))}</ul>
          </li>}
        </ul>
      </li></ul>
    </nav>;
  }

  return <nav className="fm-tree" aria-label={t('Folders')}>
    <ul role="tree">{node('', rootLabel || t('root'), 0, true)}</ul>
  </nav>;
}

export default FolderTree;
