// Release notes on the Updates page: what the releases after the installed
// one change, and the whole history as a page of its own. The notes come from
// the repository's releases (backend: services/changelog.py).
import React, { useEffect, useState } from 'react';
import { ArrowLeft, ChevronLeft, ChevronRight, ExternalLink, RefreshCw, ScrollText } from 'lucide-react';
import { Markdown } from './Markdown.jsx';
import './Changelog.css';

const day = value => (value ? String(value).slice(0, 10) : '');

function Release({ release, t, open }) {
  return <details className="changelog-release" open={open}>
    <summary>
      <strong>v{release.version}</strong>
      {release.date && <span className="changelog-date">{day(release.date)}</span>}
      {release.current && <span className="badge ok">{t('Installed')}</span>}
      {release.newer && <span className="badge warn">{t('New')}</span>}
      {release.url && <a className="changelog-link" href={release.url} target="_blank" rel="noopener noreferrer"
                         onClick={event => event.stopPropagation()} aria-label={t('Open the release page')}>
        <ExternalLink size={13}/></a>}
    </summary>
    <Markdown text={release.notes || release.name} className="markdown changelog-notes"/>
  </details>;
}

// On the Updates page: the releases waiting to be installed, else the notes
// of the one that is.
export function ChangelogPreview({ request, t, onOpenHistory, reloadKey }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    let live = true;
    (async () => {
      const newer = await request('/updates/changelog?newer=true&per_page=5', { silent: true });
      if (!live || !newer) return;
      if (newer.total > 0) { setData({ ...newer, mode: 'newer' }); return; }
      const latest = await request('/updates/changelog?per_page=1', { silent: true });
      if (live && latest) setData({ ...latest, mode: 'current' });
    })();
    return () => { live = false; };
  }, [reloadKey]);
  const releases = data?.releases || [];
  return <section className="section changelog-section">
    <div className="section-title">
      <div><h2>{data?.mode === 'newer' ? t("What's new in the update") : t("What's new")}</h2>
        <p className="hint">{data?.mode === 'newer'
          ? t('Read what the update changes before you run it.')
          : t('This server runs the newest release. Its notes:')}</p></div>
      <button className="secondary-light" onClick={onOpenHistory}><ScrollText size={14}/> {t('Changelog')}</button>
    </div>
    {!data && <p className="hint">{t('Loading...')}</p>}
    {data?.error && <p className="hint">{data.error}</p>}
    {releases.map((release, n) => <Release key={release.version} release={release} t={t} open={n === 0}/>)}
    {data?.mode === 'newer' && data.total > releases.length &&
      <div className="actions"><button className="secondary-light" onClick={onOpenHistory}>{t('Every release')}</button></div>}
  </section>;
}

// The whole history, a page of releases at a time.
export function ChangelogHistory({ request, t, onBack }) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = async (refresh = false) => {
    setBusy(true);
    const answer = await request(`/updates/changelog?page=${page}&per_page=10${refresh ? '&refresh=true' : ''}`);
    setBusy(false);
    if (answer) setData(answer);
  };
  useEffect(() => { load(); }, [page]);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.per_page)) : 1;
  return <section className="section changelog-section">
    <div className="section-title">
      <div className="waf-detail-title">
        <button className="secondary" onClick={onBack}><ArrowLeft size={14}/> {t('Updates')}</button>
        <div><h2>{t('Changelog')}</h2>
          <p className="hint">{t('Every release of the panel, newest first.')}
            {data?.current_version ? ` ${t('Installed')}: v${data.current_version}.` : ''}</p></div>
      </div>
      <button className="secondary-light" disabled={busy} onClick={() => load(true)}>
        <RefreshCw size={14} className={busy ? 'spin' : ''}/> {t('Refresh')}</button>
    </div>
    {!data && <p className="hint">{t('Loading...')}</p>}
    {data?.error && <p className="hint">{data.error}</p>}
    {(data?.releases || []).map((release, n) =>
      <Release key={release.version} release={release} t={t} open={page === 1 && n === 0}/>)}
    {pages > 1 && <div className="changelog-pager">
      <button className="secondary-light" disabled={busy || page <= 1} onClick={() => setPage(page - 1)}
              aria-label={t('Newer releases')}><ChevronLeft size={14}/></button>
      <span>{page} / {pages}</span>
      <button className="secondary-light" disabled={busy || page >= pages} onClick={() => setPage(page + 1)}
              aria-label={t('Older releases')}><ChevronRight size={14}/></button>
    </div>}
  </section>;
}
