import { useEffect, useRef, useState } from 'react';
import {
  Archive,
  BrickWall,
  Bug,
  ChevronDown,
  CircleAlert,
  CircleCheck,
  Download,
  Fingerprint,
  Lock,
  OctagonAlert,
  Plus,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
} from 'lucide-react';
import { followInPanel, usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import { toneOf, usageFigures } from '../lib/limits.js';
import { useDuration } from '../components/ServerFigures.jsx';
import { ADDON_META, addonFeatureIcon, addonLabel } from '../lib/addons.jsx';
import { TILES, dashboardSections, tileLabel, tileVisible } from '../lib/features.jsx';
import { FeatureIcon } from '../icons/feature/index.js';
import './Dashboard.css';

// The dashboard as SNPanel has it (pages/Dashboard.jsx there; operator,
// 2026-10-17: "Làm lại giao diện cho giống nó"): it says how things are, and
// is the way to everything else, the addons included.
//
// A main column - what needs attention, worst first, each with the way to
// the page that fixes it; then every feature as a tile with its picture
// (icons/feature), in sections that fold, searchable with "/" - beside a
// narrow one with the facts of the role: the server and its state for an
// administrator; the share for a reseller; usage and the account for a
// customer. Below 1100px the two are one column, in --cp-order.

const TONE_RANK = { bad: 0, warn: 1, info: 2 };
// The sections this browser has folded, by key ('attention' is the alerts).
const FOLDED_KEY = 'opanel-dashboard-folded';

// "5 minutes ago", in the reader's language, for a moment in the past.
function useAgo() {
  const t = useT();
  return (stamp) => {
    if (!stamp) return '';
    const then = new Date(stamp).getTime();
    if (Number.isNaN(then)) return stamp;
    const minutes = Math.max(0, Math.round((Date.now() - then) / 60000));
    if (minutes < 1) return t('just now');
    if (minutes < 60) return t('{count} min ago', { count: minutes });
    const hours = Math.round(minutes / 60);
    if (hours < 48) return t('{count} h ago', { count: hours });
    return t('{count} days ago', { count: Math.round(hours / 24) });
  };
}

// A share of a limit, 0 to 100, or null when there is none.
const share = (value) => (value === null || value === undefined || !Number.isFinite(Number(value)) ? null : Math.max(0, Math.min(100, Number(value))));

// For the tool search: "bao mat" finds "Bảo mật".
const fold = (text) => String(text || '').normalize('NFD').replace(/[̀-ͯ]/g, '')
  .replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase();

function readFolded() {
  try {
    const value = JSON.parse(localStorage.getItem(FOLDED_KEY) || '[]');
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

// A bar for a share, amber from 75% and red from 90% (SNPanel's Track).
function Track({ percent }) {
  const tone = toneOf(percent);
  return <span className={`resource-track${tone === 'ok' ? '' : ` tone-${tone}`}`} aria-hidden="true">
    <span style={{ width: `${percent}%` }}/>
  </span>;
}

// The way to a page: a link - a Ctrl-click opens a tab - that otherwise
// opens the page in place.
function PanelLink({ to, className, children }) {
  return <a className={className} href={to.href} onClick={(event) => followInPanel(event, to.open)}>{children}</a>;
}

// A count, with a bar when it is a share of a limit; with `to`, the way to
// the page the count is of.
function StatRow({ label, value, percent, tone = '', to }) {
  const amount = share(percent);
  const level = amount === null ? 'ok' : toneOf(amount);
  const shown = tone || (level === 'ok' ? '' : level);
  const className = `stat-row${shown ? ` tone-${shown}` : ''}`;
  const body = <>
    <span className="stat-row-label">{shown ? <CircleAlert size={13} aria-hidden="true"/> : null}{label}</span>
    <strong className="stat-row-value">{value}</strong>
    {amount !== null && <Track percent={amount}/>}
  </>;
  return to ? <PanelLink to={to} className={className}>{body}</PanelLink> : <div className={className}>{body}</div>;
}

// A fact about the server or the account; left out while it is not known.
function InfoRow({ label, value }) {
  if (value === null || value === undefined || value === '') return null;
  return <div className="info-row"><span>{label}</span><strong title={String(value)}>{value}</strong></div>;
}

// A line of the last readings, scaled to 0-100 (or to its own peak).
function Sparkline({ values: readings, max = 100 }) {
  if (!readings || readings.length === 0) return <svg className="dash-spark" viewBox="0 0 80 22" aria-hidden="true"/>;
  const values = readings.length === 1 ? [readings[0], readings[0]] : readings;
  const top = Math.max(max || 0, ...values, 1);
  const step = 80 / (values.length - 1);
  const points = values.map((value, i) => `${(i * step).toFixed(1)},${(20 - (Math.max(0, value) / top) * 18).toFixed(1)}`);
  return <svg className="dash-spark" viewBox="0 0 80 22" aria-hidden="true" preserveAspectRatio="none">
    <polyline className="dash-spark-area" points={`0,22 ${points.join(' ')} 80,22`}/>
    <polyline className="dash-spark-line" points={points.join(' ')}/>
  </svg>;
}

export default function DashboardPage() {
  const {
    addons,
    appVersion,
    currentUser,
    formatBytes,
    formatPercent,
    isAdmin,
    isReseller,
    loadResourceUsage,
    navCtx,
    navigateToPage,
    request,
    resellerPool,
    resourceUsage,
    routeForPage,
    runBefore,
    storageLimitBytes,
    usageHistory,
    websites,
  } = usePanel();
  const t = useT();
  const ago = useAgo();
  const duration = useDuration();
  const [summary, setSummary] = useState(null);
  const [updates, setUpdates] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [query, setQuery] = useState('');
  const [folded, setFolded] = useState(readFolded);
  const searchRef = useRef(null);

  async function load() {
    setRefreshing(true);
    const data = await request('/dashboard/summary', { silent: true });
    if (data) setSummary(data);
    setRefreshing(false);
    // Asking the package manager takes a moment; the rest does not wait.
    if (isAdmin) {
      const pending = await request('/dashboard/updates', { silent: true });
      if (pending) setUpdates(pending);
    }
  }
  useEffect(() => { load(); }, [isAdmin]);

  // The Server card's figures and their lines: read every 10 seconds while
  // the Dashboard is open, and not while the browser tab is in the background.
  useEffect(() => {
    if (!isAdmin) return undefined;
    const tick = () => { if (!document.hidden) loadResourceUsage(); };
    tick();
    const timer = setInterval(tick, 10000);
    document.addEventListener('visibilitychange', tick);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', tick);
    };
  }, [isAdmin]);

  // "/" anywhere but in a field goes to the tool search.
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target;
      if (target?.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName)) return;
      if (!searchRef.current) return;
      event.preventDefault();
      searchRef.current.focus();
      searchRef.current.select();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // A page's address and the way to open it in place; `before` names the
  // App action that picks the tab or form it opens on.
  const to = (page, before) => ({
    href: routeForPage(page),
    open: () => { if (before) runBefore(before); navigateToPage(page); },
  });
  const sites = summary?.websites || {};
  const total = Number(sites.total) || 0;
  const withSsl = Number(sites.with_ssl) || 0;

  // ---- what needs attention, worst first ----
  const attention = [];
  const need = (tone, key, text, page, action, before) => attention.push({ tone, key, text, page, action, before });
  if (summary && isAdmin) {
    const stopped = summary.services?.stopped || [];
    if (stopped.length) need('bad', 'services', t('Stopped: {names}', { names: stopped.join(', ') }), 'services', t('Open Services'));
    const fw = summary.firewall || {};
    if (fw.state === 'disabled') need('bad', 'firewall', t('The firewall is off.'), 'firewall', t('Turn it on'));
    const mw = summary.malware || {};
    if (mw.state === 'threats') need('bad', 'malware', t('{count} threat(s) found by the last scan.', { count: mw.threats }), 'malware', t('See what was found'));
    // A failed scheduled backup needs attention; having no schedule at all
    // does not: that is the administrator's choice.
    const bk = summary.backups || {};
    if (bk.state === 'error') need('bad', 'backups', t('A scheduled backup failed.'), 'backups', t('Open Backup & Restore'));
    if (summary.waf && summary.waf.engine === false) need('warn', 'waf', t('The WAF engine is not installed on this server.'), 'waf', t('Open WAF'));
    if (mw.installed && mw.state === 'never') need('warn', 'scanner', t('No malware scan has run yet.'), 'malware', t('Scan now'));
    const rs = summary.resources || {};
    const oom = rs.oom_last_24h || [];
    const over = rs.users_over_80 || [];
    if (oom.length) need('bad', 'oom', t('Out of memory in the last 24 hours: {names}', { names: oom.join(', ') }), 'usage', t('Open Account resources'));
    if (over.length) need('warn', 'limits', t('At 80% of a resource limit: {names}', { names: over.join(', ') }), 'usage', t('Open Account resources'));
  }
  if (summary) {
    const noSsl = sites.without_ssl || [];
    if (noSsl.length) {
      need('warn', 'ssl', noSsl.length === 1
        ? t('{domain} has no SSL certificate.', { domain: noSsl[0] })
        : t('{count} websites have no SSL certificate.', { count: noSsl.length }), 'ssl', t('Install SSL'));
    }
    if (sites.suspended > 0) need('warn', 'suspended', t('{count} website(s) suspended.', { count: sites.suspended }), 'websites', t('Open Websites'));
  }
  if (summary && !isAdmin) {
    if (!summary.two_factor?.totp && !(summary.two_factor?.passkeys > 0)) need('warn', 'twofactor', t('Two-step verification is off for your account.'), 'security', t('Turn it on'));
  }
  if (!isAdmin && currentUser) {
    const limit = storageLimitBytes(currentUser);
    const used = Number(currentUser.storage_used_bytes) || 0;
    if (limit !== null && limit > 0 && used / limit >= 0.9) {
      need(used >= limit ? 'bad' : 'warn', 'storage', t('Storage is {percent} full.', { percent: formatPercent((used / limit) * 100) }), 'files', t('Open the files'));
    }
  }
  if (updates) {
    if (updates.os > 0) need('info', 'os-updates', t('{count} system update(s) available.', { count: updates.os }), 'updates', t('Open Updates'));
    if (updates.panel?.available) need('info', 'panel-update', t('OPanel {version} is available.', { version: updates.panel.latest }), 'updates', t('Open Updates'));
  }
  attention.sort((a, b) => TONE_RANK[a.tone] - TONE_RANK[b.tone]);
  const attentionFolded = folded.includes('attention');

  function toggleFold(key) {
    setFolded((prev) => {
      const next = prev.includes(key) ? prev.filter((item) => item !== key) : [...prev, key];
      try { localStorage.setItem(FOLDED_KEY, JSON.stringify(next)); } catch {}
      return next;
    });
  }

  const recheck = <button type="button" className="secondary icon-only mini dash-recheck" onClick={load} disabled={refreshing}
    aria-label={t('Check again')} title={t('Check again')}><RefreshCw size={14} className={refreshing ? 'spin' : ''} aria-hidden="true"/></button>;
  const attentionCard = <section className={`section dash-card cp-attention${summary && attention.length === 0 ? ' is-clear' : ''}`}
    style={{ '--cp-order': 1 }} aria-label={t('Needs attention')}>
    {!summary
      ? <div className="attention-ok is-pending" aria-busy="true"><RefreshCw size={16} className="spin" aria-hidden="true"/> {t('Checking…')}</div>
      : attention.length === 0
        ? <div className="attention-ok"><CircleCheck size={16} aria-hidden="true"/> {t('Everything is fine.')}{recheck}</div>
        : <>
          <div className="dash-card-head">
            <span className="dash-card-icon"><CircleAlert size={16} aria-hidden="true"/></span>
            <h2>{t('Needs attention')}</h2>
            <span className="badge warn">{attention.length}</span>
            {recheck}
            <button type="button" className="secondary icon-only mini dash-fold" onClick={() => toggleFold('attention')}
              aria-expanded={!attentionFolded} aria-controls="dash-attention-list"
              aria-label={attentionFolded ? t('Show') : t('Hide')} title={attentionFolded ? t('Show') : t('Hide')}>
              <ChevronDown size={14} aria-hidden="true"/></button>
          </div>
          {!attentionFolded && <div className="attention-list" id="dash-attention-list">
            {attention.map((item) => <div key={item.key} className={`attention-item tone-${item.tone}`}>
              {item.tone === 'bad' ? <OctagonAlert size={15} aria-hidden="true"/> : item.tone === 'info' ? <RefreshCw size={15} aria-hidden="true"/> : <CircleAlert size={15} aria-hidden="true"/>}
              <span>{item.text}</span>
              <PanelLink to={to(item.page, item.before)} className="button-link mini secondary">{item.action}</PanelLink>
            </div>)}
          </div>}
        </>}
  </section>;

  // ---- the state of each thing that can be wrong ----
  const status = [];
  const sslRow = { key: 'ssl', page: 'ssl', icon: Lock, label: 'SSL', value: `${withSsl}/${total}`,
    tone: !total ? 'neutral' : withSsl < total ? 'warn' : 'ok',
    detail: !total ? t('No websites yet') : withSsl < total ? t('{count} without SSL', { count: total - withSsl }) : t('Every site has one') };
  if (summary && isAdmin) {
    const bk = summary.backups || {};
    const fw = summary.firewall || {};
    const mw = summary.malware || {};
    const sv = summary.services || {};
    status.push(
      { key: 'services', page: 'services', icon: Server, label: t('Services'), value: `${sv.running ?? 0}/${sv.total ?? 0}`,
        tone: (sv.stopped || []).length ? 'bad' : 'ok',
        detail: (sv.stopped || []).length ? t('Stopped: {names}', { names: sv.stopped.join(', ') }) : t('All running') },
      sslRow,
      { key: 'firewall', page: 'firewall', icon: BrickWall, label: t('Firewall'),
        tone: fw.state === 'enabled' ? 'ok' : fw.state === 'disabled' ? 'bad' : 'warn',
        value: fw.state === 'enabled' ? t('On') : fw.state === 'disabled' ? t('Off') : '—',
        detail: fw.state === 'enabled' ? t('Enforcing') : fw.state === 'disabled' ? t('Every port is open') : t('Unknown') },
      { key: 'waf', page: 'waf', icon: ShieldCheck, label: 'WAF',
        tone: summary.waf?.engine ? 'ok' : 'warn',
        value: summary.waf?.engine ? t('Installed') : t('Not installed'),
        detail: total ? t('{count} of {total} website(s) protected', { count: sites.waf_on || 0, total }) : t('No websites yet') },
      // The scanner is an addon: no row while it is not installed.
      ...(mw.installed ? [{ key: 'malware', page: 'malware', icon: Bug, label: t('Malware Scanner'),
        tone: mw.state === 'threats' ? 'bad' : mw.state === 'clean' ? 'ok' : 'warn',
        value: { clean: t('Clean'), threats: t('{count} threat(s)', { count: mw.threats }), never: t('Not scanned') }[mw.state] || '—',
        detail: [
          mw.last_scan_at ? t('Last scan {when}', { when: ago(mw.last_scan_at) }) : t('No scan yet'),
          mw.quarantined > 0 ? t('{count} quarantined', { count: mw.quarantined }) : '',
        ].filter(Boolean).join(' · ') }] : []),
      // No schedule is neither good nor bad: no amber.
      { key: 'backups', page: 'backups', icon: Archive, label: t('Backups'),
        tone: bk.state === 'error' ? 'bad' : bk.state === 'ok' ? 'ok' : bk.state === 'none' ? 'neutral' : 'warn',
        value: { ok: t('On schedule'), error: t('Failed'), never: t('Not run yet') }[bk.state] || '—',
        detail: bk.last_run_at ? t('Last run {when}', { when: ago(bk.last_run_at) })
          : bk.state === 'none' ? t('No schedule') : t('{count} schedule(s)', { count: bk.schedules || 0 }) },
    );
  }
  if (summary && !isAdmin) {
    const twoFactor = summary.two_factor || {};
    const twoStepOn = Boolean(twoFactor.totp || twoFactor.passkeys > 0);
    status.push(
      sslRow,
      { key: 'waf', page: 'waf', icon: ShieldCheck, label: 'WAF', value: `${sites.waf_on || 0}/${total}`,
        tone: !total ? 'neutral' : (sites.waf_on || 0) < total ? 'warn' : 'ok',
        detail: total ? t('{count} of {total} website(s) protected', { count: sites.waf_on || 0, total }) : t('No websites yet') },
      { key: 'twofactor', page: 'security', icon: Fingerprint, label: t('Two-step verification'),
        tone: twoStepOn ? 'ok' : 'warn',
        value: twoStepOn ? t('On') : t('Off'),
        detail: twoStepOn
          ? [twoFactor.totp ? t('Authenticator app') : '', twoFactor.passkeys > 0 ? t('{count} passkey(s)', { count: twoFactor.passkeys }) : ''].filter(Boolean).join(' + ')
          : t('Only a password protects the account') },
    );
  }
  const statusCard = (order) => <section className="section side-card" style={{ '--cp-order': order }}>
    <h3 className="side-card-title">{t('Status')}</h3>
    {summary
      ? <div className="status-list">
        {status.map((row) => <PanelLink key={row.key} to={to(row.page)} className={`status-row tone-${row.tone}`}>
          <row.icon size={15} aria-hidden="true"/>
          <span>{row.label}</span>
          <strong>{row.value}</strong>
          <small>{row.detail}</small>
        </PanelLink>)}
      </div>
      : <p className="hint" aria-busy="true">{t('Checking…')}</p>}
  </section>;

  // ---- every feature, a tile each, in sections ----
  const needle = fold(query.trim());
  const sections = dashboardSections(navCtx)
    .map(([key, title, keys]) => ({ key, title: t(title), tiles: keys.filter((tileKey) => tileVisible(tileKey, navCtx)) }))
    .map((section) => ({ ...section, tiles: section.tiles.filter((key) => !needle || fold(`${t(tileLabel(TILES[key], navCtx))} ${section.title}`).includes(needle)) }));
  // An administrator also sees each addon not installed yet, greyed, as the
  // way to install it (in Extensions).
  const missingAddons = isAdmin
    ? (addons || []).filter((addon) => !addon.installed && ADDON_META[addon.slug] && addon.slug !== 'demo'
      && (!needle || fold(`${addonLabel(addon, t)} ${t('Extensions')}`).includes(needle)))
    : [];
  const shownSections = sections.filter((section) => section.tiles.length > 0 || (section.key === 'extensions' && missingAddons.length > 0));
  const tile = (key) => {
    const item = TILES[key];
    const label = t(tileLabel(item, navCtx));
    const body = <><FeatureIcon name={item.icon} size={40}/><span className="dash-tile-label">{label}</span></>;
    if (item.href) {
      return <div key={key} className="dash-tile">
        <a className="dash-tile-link" href={item.href(navCtx)} target="_blank" rel="noreferrer noopener">{body}</a>
      </div>;
    }
    const target = to(item.page, item.before);
    return <div key={key} className="dash-tile">
      <a className="dash-tile-link" href={target.href} onClick={(event) => followInPanel(event, target.open)}>{body}</a>
    </div>;
  };
  const missingTile = (addon) => <div key={`addon-${addon.slug}`} className="dash-tile is-missing">
    <a className="dash-tile-link" href={routeForPage('addons')}
      onClick={(event) => followInPanel(event, () => navigateToPage('addons'))}
      aria-label={t('{name}: not installed. Open Addons to install it.', { name: addonLabel(addon, t) })}>
      <span className="dash-tile-art"><FeatureIcon name={addonFeatureIcon(addon.slug)} size={40} muted/><span className="dash-tile-badge" aria-hidden="true"><Download size={11}/></span></span>
      <span className="dash-tile-label">{addonLabel(addon, t)}</span>
      <small className="dash-tile-note">{t('Not installed')}</small>
    </a>
  </div>;
  const featuresCard = (order) => <section className="section dash-card dash-features" style={{ '--cp-order': order }} aria-label={t('Features')}>
    <label className="tools-search">
      <Search size={16} aria-hidden="true"/>
      <input ref={searchRef} type="search" value={query} onChange={(event) => setQuery(event.target.value)}
        placeholder={t('Search tools (press /)')} aria-label={t('Search tools')}/>
    </label>
    {shownSections.length === 0 && <p className="hint">{t('No tool matches your search.')}</p>}
    {summary && total === 0 && !needle
      && <p className="hint dash-first-hint"><Plus size={13} aria-hidden="true"/> {t('No website yet: start with New website.')}</p>}
    {shownSections.map((section) => {
      // A search opens every section it finds something in.
      const open = Boolean(needle) || !folded.includes(section.key);
      const count = section.tiles.length + (section.key === 'extensions' ? missingAddons.length : 0);
      return <div key={section.key} className={`dash-section${open ? ' is-open' : ''}`}>
        <button type="button" className="dash-section-head" aria-expanded={open} aria-controls={`dash-section-${section.key}`}
          onClick={() => toggleFold(section.key)}>
          <h3>{section.title}</h3>
          <span className="dash-section-count">{count}</span>
          <ChevronDown size={16} className="dash-section-chevron" aria-hidden="true"/>
        </button>
        {open && <div className="dash-tiles" id={`dash-section-${section.key}`}>
          {section.tiles.map(tile)}
          {section.key === 'extensions' && missingAddons.map(missingTile)}
        </div>}
      </div>;
    })}
  </section>;

  // ---- a customer's and a reseller's ----
  if (!isAdmin) {
    const used = Number(currentUser?.storage_used_bytes) || 0;
    const storageLimit = storageLimitBytes(currentUser);
    const siteLimit = Number(currentUser?.website_limit) || 0;
    const siteCount = summary ? total : (websites?.length || 0);
    // The account's own CPU, memory and processes against its limits.
    const own = usageFigures(summary?.resources?.usage, formatBytes, summary?.resources?.limits);
    // The account's first website - the list is newest first.
    const primary = (websites || []).reduce((first, site) => (!first || site.id < first.id ? site : first), null);
    const home = String(primary?.root_path || '').match(/^\/[^/]+\/[^/]+/)?.[0]
      || (currentUser?.username ? `/home/${currentUser.username}` : '');
    // A reseller's share: its customers and the disk it may hand out.
    const pool = resellerPool || {};
    const poolDisk = pool[pool.pool_oversell ? 'used_storage_limit_mb' : 'allocated_storage_limit_mb'] || 0;
    const poolRow = (label, value, max, unit = '') => <StatRow key={label} label={label}
      value={`${value}${unit} / ${max ? `${max}${unit}` : '∞'}`} percent={max ? (value / max) * 100 : null}/>;
    return <div className="dashboard cp-layout">
      <div className="cp-main">
        {attentionCard}
        {featuresCard(3)}
      </div>
      <aside className="cp-side">
        {isReseller && <section className="section side-card" style={{ '--cp-order': 4 }}>
          <h3 className="side-card-title">{t('Reseller allotment')}</h3>
          <p className="hint side-card-hint">{t('What you and your customers use, against what you were given.')}</p>
          {resellerPool ? <>
            {poolRow(t('Customers'), pool.customers || 0, pool.pool_user_limit)}
            {poolRow(t('Disk'), poolDisk, pool.pool_storage_limit_mb, ' MB')}
          </> : <p className="hint" aria-busy="true">{t('Checking…')}</p>}
        </section>}
        <section className="section side-card" style={{ '--cp-order': 2 }}>
          <h3 className="side-card-title">{isReseller ? t('Your package') : t('Usage')}</h3>
          <StatRow label={t('Storage')}
            value={currentUser?.storage_used_bytes == null ? t('Measuring…') : `${formatBytes(used)} / ${storageLimit === null ? '∞' : formatBytes(storageLimit)}`}
            percent={storageLimit === null ? null : storageLimit > 0 ? (used / storageLimit) * 100 : 100}/>
          <StatRow label={t('Websites')} value={`${siteCount} / ${siteLimit > 0 ? siteLimit : '∞'}`}
            percent={siteLimit > 0 ? (siteCount / siteLimit) * 100 : null} to={to('websites')}/>
          <StatRow label={t('Databases')} value={String(summary?.databases?.total ?? '—')} to={to('databases')}/>
          {own && <>
            <StatRow label="CPU" value={own.cpu.text} percent={own.cpu.percent}/>
            <StatRow label="RAM" value={own.memory.text} percent={own.memory.percent}/>
            <StatRow label={t('Processes')} value={own.processes.text} percent={own.processes.percent}/>
            {own.memory.oomKills > 0 && <StatRow label={t('Killed for memory')} value={String(own.memory.oomKills)} tone="bad"/>}
          </>}
        </section>
        <section className="section side-card" style={{ '--cp-order': 6 }}>
          <h3 className="side-card-title">{t('General information')}</h3>
          <InfoRow label={t('Account')} value={currentUser?.username}/>
          <InfoRow label={t('Email')} value={currentUser?.email}/>
          <InfoRow label={t('Primary domain')} value={primary?.domain}/>
          <InfoRow label={t('Home directory')} value={home}/>
          <InfoRow label={t('Server IP')} value={summary?.server?.ipv4}/>
        </section>
        {statusCard(7)}
      </aside>
    </div>;
  }

  // ---- an administrator's ----
  const cpu = resourceUsage?.cpu || {};
  const memory = resourceUsage?.memory || {};
  const disk = resourceUsage?.disk || {};
  const network = resourceUsage?.network || {};
  const throughput = (Number(network.rx_per_sec) || 0) + (Number(network.tx_per_sec) || 0);
  const server = summary?.server || {};
  const uptime = Number(server.uptime_seconds) > 0 ? duration(server.uptime_seconds) : '';
  const history = usageHistory || [];
  const figure = (page, label, value, percent, values, max) => <PanelLink key={page} to={to(page)} className={`dash-figure tone-${percent === null ? 'ok' : toneOf(percent)}`}>
    <span className="dash-figure-label">{label}</span>
    <strong>{value}</strong>
    <Sparkline values={values} max={max}/>
  </PanelLink>;
  const pct = (value) => (resourceUsage ? formatPercent(value) : '—');

  return <div className="dashboard cp-layout">
    <div className="cp-main">
      {attentionCard}
      {featuresCard(3)}
    </div>

    <aside className="cp-side">
      <section className="section side-card" style={{ '--cp-order': 2 }}>
        <h3 className="side-card-title">{t('Server')}</h3>
        <div className="dash-figures">
          {figure('processes', 'CPU', pct(cpu.percent), resourceUsage ? Number(cpu.percent) || 0 : null, history.map((h) => h.cpu))}
          {figure('ramUsage', 'RAM', pct(memory.percent), resourceUsage ? Number(memory.percent) || 0 : null, history.map((h) => h.memory))}
          {figure('diskUsage', t('Disk'), pct(disk.percent), resourceUsage ? Number(disk.percent) || 0 : null, history.map((h) => h.disk))}
          {figure('traffic', t('Network'), resourceUsage ? `${formatBytes(throughput)}/s` : '—', null, history.map((h) => h.network), 0)}
        </div>
        <InfoRow label={t('Hostname')} value={server.hostname}/>
        <InfoRow label={t('IP address')} value={server.ipv4}/>
        <InfoRow label={t('Operating system')} value={server.os}/>
        <InfoRow label={t('Kernel')} value={server.kernel}/>
        <InfoRow label={t('Uptime')} value={uptime}/>
        <InfoRow label="CPU" value={cpu.cores ? t('{count} cores', { count: cpu.cores }) : ''}/>
        <InfoRow label="RAM" value={memory.total ? formatBytes(memory.total) : ''}/>
        <InfoRow label={t('Panel version')} value={appVersion}/>
      </section>
      {statusCard(4)}
      <section className="section side-card" style={{ '--cp-order': 7 }}>
        <h3 className="side-card-title">{t('Totals')}</h3>
        <StatRow label={t('Customers')} value={summary?.users ? String(summary.users.total) : '—'} to={to('users', 'usersList')}/>
        <StatRow label={t('Websites')} tone={sites.suspended > 0 ? 'warn' : ''} to={to('websites')}
          value={!summary ? '—' : sites.suspended > 0 ? t('{total} ({count} suspended)', { total, count: sites.suspended }) : String(total)}/>
        <StatRow label={t('Databases')} value={String(summary?.databases?.total ?? '—')} to={to('databases')}/>
        {summary?.mail && <StatRow label={t('Mailboxes')} value={String(summary.mail.mailboxes ?? '—')} to={to('mail', 'mailMailboxes')}/>}
        {summary?.dns && <StatRow label={t('DNS zones')} value={String(summary.dns.zones ?? '—')} to={to('dns')}/>}
      </section>
    </aside>
  </div>;
}
