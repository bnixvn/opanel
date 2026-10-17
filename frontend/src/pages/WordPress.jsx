import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity, AlertTriangle, ArrowLeft, Bug, Copy, Download, ExternalLink, KeyRound, LogIn, Package, Palette, Plus, RefreshCw,
  ScanSearch, Search, Settings2, Trash2, Wrench, X,
} from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import PageHeader from '../components/PageHeader.jsx';
import Tabs from '../components/Tabs.jsx';
import Switch from '../components/Switch.jsx';
import Drawer from '../components/Drawer.jsx';
import EmptyState from '../components/EmptyState.jsx';
import { Field, PasswordField } from '../components/Form.jsx';
import { confirmDialog } from '../components/Dialog.jsx';
import WordPressGlyph from '../icons/WordPressGlyph.jsx';
import './WordPress.css';

const msg = (text) => text;

// The WordPress Manager addon, as SNPanel's (pages/WordPress.jsx there):
// every WordPress the panel found in the caller's websites, and for each a
// Manage view - the core, plugins, themes, sign-in to wp-admin. Whatever
// changes a site runs as a task on the server; the page follows it and
// reloads when it is done.

const JOB_LABELS = {
  refresh: msg('Refresh'),
  scan: msg('Scan'),
  core: msg('Update WordPress'),
  plugins: msg('Plugins'),
  themes: msg('Themes'),
  maintenance: msg('Maintenance mode'),
  debug: msg('Debugging'),
  update: msg('Update everything'),
};
const JOB_STATUS = {
  queued: ['', msg('Queued')],
  running: ['warn', msg('Running')],
  completed: ['ok', msg('Done')],
  failed: ['bad', msg('Failed')],
  interrupted: ['bad', msg('Interrupted')],
};
const isPending = (job) => job && (job.status === 'queued' || job.status === 'running');
// The newest task still on its way that turns one switch.
const pendingSwitch = (jobs, match) => (jobs || []).find((job) => isPending(job) && match(job));
const safeHref = (url) => (typeof url === 'string' && /^https?:\/\/[^\s"'<>`]+$/i.test(url) ? url : null);
const readInstallParam = () => {
  const id = Number(new URLSearchParams(window.location.search).get('install'));
  return Number.isInteger(id) && id > 0 ? id : null;
};
const when = (value) => (value ? new Date(value).toLocaleString() : '');

function SecretBox({ title, value, onClose }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  return <div className="wpm-secret" role="status">
    <strong>{title}</strong>
    <code>{value}</code>
    <button type="button" className="mini secondary-light" onClick={async () => { try { await navigator.clipboard.writeText(value); setCopied(true); } catch { /* the text is shown */ } }}>
      <Copy size={13} aria-hidden="true"/> {copied ? t('Copied') : t('Copy')}</button>
    <small>{t('Shown this once. Copy it now.')}</small>
    <button type="button" className="mini secondary-light wpm-secret-close" onClick={onClose} aria-label={t('Close')}><X size={13} aria-hidden="true"/></button>
  </div>;
}

export default function WordPressPage() {
  const t = useT();
  const { isAdmin, loading, navigateToPage, request, setError, setNotice, wordpressEnabled } = usePanel();
  const [overview, setOverview] = useState(null);
  const [installId, setInstallId] = useState(readInstallParam);
  const [detail, setDetail] = useState(null);
  const [owner, setOwner] = useState('');
  const [scanSite, setScanSite] = useState('');
  const [selected, setSelected] = useState([]);
  const [installing, setInstalling] = useState(false);
  const [secret, setSecret] = useState(null);
  const [watching, setWatching] = useState([]);
  const busy = !!loading;
  const installIdRef = useRef(installId);
  installIdRef.current = installId;

  const loadList = useCallback(async () => {
    const data = await request('/wordpress', { silent: true });
    if (data) setOverview(data);
    return data;
  }, [request]);
  const loadDetail = useCallback(async (id, quiet = false) => {
    const data = await request(`/wordpress/installs/${id}`, quiet ? { silent: true } : {}, quiet ? '' : t('Loading...'));
    if (data) setDetail(data);
    return data;
  }, [request]);

  useEffect(() => { if (wordpressEnabled) loadList(); }, [wordpressEnabled]);
  useEffect(() => {
    if (!wordpressEnabled || !installId) { setDetail(null); return; }
    loadDetail(installId);
  }, [wordpressEnabled, installId]);
  useEffect(() => {
    const onPop = () => setInstallId(readInstallParam());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);
  // A scan or a task started elsewhere (another tab, a teammate): followed too.
  useEffect(() => {
    const running = (overview?.active_jobs || []).map((job) => job.id).filter((id) => !watching.includes(id));
    if (running.length) setWatching((list) => [...new Set([...list, ...running])]);
  }, [overview]);

  // Follow the tasks; when one ends, say so and reload.
  useEffect(() => {
    if (watching.length === 0) return undefined;
    const timer = setInterval(async () => {
      let moved = false;
      for (const id of watching) {
        const job = await request(`/wordpress/jobs/${id}`, { silent: true });
        if (!job) { setWatching((list) => list.filter((x) => x !== id)); continue; }
        if (isPending(job)) { moved = true; continue; }
        setWatching((list) => list.filter((x) => x !== id));
        const what = t(JOB_LABELS[job.kind] || job.kind);
        if (job.status === 'completed') setNotice(t('{task} finished: {target}', { task: what, target: job.label }));
        else setError(t('{task} failed on {target}: {error}', { task: what, target: job.label, error: job.error || '' }));
        await loadList();
        if (installIdRef.current) await loadDetail(installIdRef.current, true);
      }
      if (moved && installIdRef.current) await loadDetail(installIdRef.current, true);
      if (moved && !installIdRef.current) await loadList();
    }, 2500);
    return () => clearInterval(timer);
  }, [watching]);

  function watch(data) {
    const jobs = data?.jobs || (data?.job ? [data.job] : []);
    if (!jobs.length) return;
    setWatching((list) => [...new Set([...list, ...jobs.map((job) => job.id)])]);
    const job = jobs[0];
    setNotice(jobs.length > 1
      ? t('{count} task(s) started', { count: jobs.length })
      : t('{task} started: {target}', { task: t(JOB_LABELS[job.kind] || job.kind), target: job.label }));
    if (installIdRef.current) loadDetail(installIdRef.current, true);
    else loadList();
  }

  async function post(path, body, label) {
    const data = await request(path, { method: 'POST', body: JSON.stringify(body || {}) }, label);
    if (data) watch(data);
    return data;
  }

  function openInstall(id) {
    setInstallId(id);
    setDetail(null);
    window.history.pushState({}, '', id ? `/wordpress?install=${id}` : '/wordpress');
  }

  // The link is made fresh for each click and opened in a tab made first,
  // so a popup blocker does not catch it.
  async function signIn(item, userId) {
    const tab = window.open('about:blank', '_blank');
    const data = await request(`/wordpress/installs/${item.id}/login`, { method: 'POST', body: JSON.stringify(userId ? { user_id: userId } : {}) }, t('Opening wp-admin...'));
    const url = safeHref(data?.url);
    if (!url) { if (tab) tab.close(); return; }
    if (tab) { tab.opener = null; tab.location.replace(url); } else window.location.assign(url);
  }

  async function updateSelected() {
    if (!selected.length) return;
    if (!(await confirmDialog({ title: t('Update {count} site(s)?', { count: selected.length }), message: t('WordPress, every plugin and every theme are updated to their latest versions.'), confirmLabel: t('Update now') }))) return;
    await post('/wordpress/bulk-update', { install_ids: selected }, t('Starting the updates...'));
    setSelected([]);
  }

  if (wordpressEnabled === false) {
    return <section className="section">
      <EmptyState icon={WordPressGlyph} message={t('The WordPress Manager addon is not installed on this server.')}
        action={isAdmin ? { label: t('Go to Addons'), icon: Package, onClick: () => navigateToPage('addons') } : undefined}/>
    </section>;
  }
  if (installId) {
    return <div className="wpm-page">
      {secret && <SecretBox title={secret.title} value={secret.value} onClose={() => setSecret(null)}/>}
      <ManageView id={installId} detail={detail} back={() => openInstall(null)} reload={() => loadDetail(installId, true)}
        post={post} signIn={signIn} setSecret={setSecret} busy={busy}/>
    </div>;
  }
  if (!overview) return <section className="section"><p className="hint">{t('Loading WordPress sites...')}</p></section>;

  const items = overview.items.filter((item) => !owner || String(item.owner_id) === owner);
  const running = new Map(overview.active_jobs.filter((job) => job.install_id).map((job) => [job.install_id, job]));
  const scanning = overview.active_jobs.find((job) => job.kind === 'scan');
  const selectable = items.filter((item) => item.status === 'ok' && item.state === 'installed');
  const allChecked = selectable.length > 0 && selectable.every((item) => selected.includes(item.id));
  const withWordPress = new Set(overview.items.filter((item) => !item.rel_path && item.status === 'ok').map((item) => item.website_id));

  return <div className="wpm-page">
    {secret && <SecretBox title={secret.title} value={secret.value} onClose={() => setSecret(null)}/>}
    <PageHeader subtitle={t('Every WordPress in your websites: versions, updates, plugins and themes, and one-click sign-in to wp-admin.')}
      onRefresh={loadList} refreshDisabled={busy}
      actions={<>
        <button type="button" className="secondary-light" disabled={busy || selected.length === 0} onClick={updateSelected}><Download size={15} aria-hidden="true"/> {t('Update selected')}{selected.length ? ` (${selected.length})` : ''}</button>
        <button type="button" disabled={busy} onClick={() => setInstalling(true)}><Plus size={15} aria-hidden="true"/> {t('Install WordPress')}</button>
      </>}/>
    <section className="section wpm-main">
      <div className="wpm-toolbar">
        {overview.owners.length > 1 && <select value={owner} onChange={(e) => setOwner(e.target.value)} aria-label={t('Owner')}>
          <option value="">{t('All owners')}</option>
          {overview.owners.map((o) => <option key={o.id} value={o.id}>{o.username}</option>)}
        </select>}
        <select value={scanSite} onChange={(e) => setScanSite(e.target.value)} aria-label={t('Website to scan')}>
          <option value="">{t('Every website')}</option>
          {overview.websites.map((w) => <option key={w.id} value={w.id}>{w.domain}</option>)}
        </select>
        <button type="button" className="secondary-light" disabled={busy || !!scanning}
          onClick={() => post('/wordpress/scan', scanSite ? { website_id: Number(scanSite) } : {}, t('Scanning...'))}><ScanSearch size={15} aria-hidden="true"/> {t('Scan')}</button>
        {scanning && <span className="wpm-running"><RefreshCw size={14} className="spin" aria-hidden="true"/> {scanning.stage || t('Scanning...')}</span>}
      </div>
      {items.length === 0
        ? <EmptyState icon={WordPressGlyph} message={t('No WordPress found yet. Install one, or scan your websites for one uploaded by hand.')}
          action={{ label: t('Scan'), icon: ScanSearch, onClick: () => post('/wordpress/scan', {}, t('Scanning...')) }}/>
        : <div className="data-table-wrap">
          <table className="data-table wpm-table wpm-cards">
            <thead><tr>
              <th scope="col"><input type="checkbox" checked={allChecked} aria-label={t('Select all')}
                onChange={() => setSelected(allChecked ? [] : selectable.map((item) => item.id))}/></th>
              <th scope="col">{t('Website')}</th>
              <th scope="col">WordPress</th>
              <th scope="col">{t('Plugins')}</th>
              <th scope="col">{t('Themes')}</th>
              <th scope="col">{t('Last checked')}</th>
              <th scope="col"><span className="sr-only">{t('Actions')}</span></th>
            </tr></thead>
            <tbody>
              {items.map((item) => {
                const job = running.get(item.id);
                const usable = item.status === 'ok' && item.state === 'installed';
                return <tr key={item.id}>
                  <td className="wpm-check-cell"><input type="checkbox" disabled={!usable} checked={selected.includes(item.id)} aria-label={t('Select {name}', { name: item.title || item.domain })}
                    onChange={() => setSelected((s) => (s.includes(item.id) ? s.filter((x) => x !== item.id) : [...s, item.id]))}/></td>
                  <td className="wpm-site">
                    <button type="button" className="wpm-link" onClick={() => openInstall(item.id)}><strong>{item.title || item.domain}</strong></button>
                    {safeHref(item.url) && <a href={safeHref(item.url)} target="_blank" rel="noreferrer noopener" className="wpm-url">{item.url}</a>}
                    <small><code>{item.rel_path ? `/${item.rel_path}` : '/'}</code>{item.owner_username && (isAdmin || overview.owners.length > 1) ? ` · ${item.owner_username}` : ''}</small>
                    {item.status === 'missing' && <span className="badge bad">{t('Missing')}</span>}
                    {item.state !== 'installed' && item.status === 'ok' && <span className="badge warn">{t('Needs setup')}</span>}
                  </td>
                  <td data-label="WordPress">{item.core_version || '-'} {item.core_update && <span className="badge warn">{t('Update {version}', { version: item.core_update })}</span>}</td>
                  <td data-label={t('Plugins')}>{item.checked ? <>{item.plugins_total} {item.plugins_updates > 0 && <span className="badge warn">{t('{count} update(s)', { count: item.plugins_updates })}</span>}</> : '-'}</td>
                  <td data-label={t('Themes')}>{item.checked ? <>{item.themes_total} {item.themes_updates > 0 && <span className="badge warn">{t('{count} update(s)', { count: item.themes_updates })}</span>}</> : '-'}</td>
                  <td className="wpm-when" data-label={t('Last checked')}>{job ? <span className="badge warn"><RefreshCw size={12} className="spin" aria-hidden="true"/> {t(JOB_LABELS[job.kind] || job.kind)}</span>
                    : item.last_error ? <span className="badge bad" title={item.last_error}>{t('Error')}</span>
                    : item.last_checked_at ? when(item.last_checked_at) : <span className="data-table-muted">{t('Not yet')}</span>}</td>
                  <td className="wpm-actions"><div className="wpm-row-actions">
                    <button type="button" className="mini secondary-light" disabled={busy || !usable} onClick={() => signIn(item)}><LogIn size={13} aria-hidden="true"/> {t('Log in')}</button>
                    <button type="button" className="mini" disabled={busy} onClick={() => openInstall(item.id)}><Settings2 size={13} aria-hidden="true"/> {t('Manage')}</button>
                  </div></td>
                </tr>;
              })}
            </tbody>
          </table>
        </div>}
    </section>
    <InstallDrawer open={installing} websites={overview.websites.filter((w) => !withWordPress.has(w.id))} busy={busy}
      close={() => setInstalling(false)}
      submit={async (websiteId, body) => {
        const data = await request(`/maintenance/wordpress/${websiteId}/install`, { method: 'POST', body: JSON.stringify(body) }, t('Installing WordPress...'));
        if (!data) return;
        setInstalling(false);
        setSecret({ title: t('Password of {user}', { user: data.admin_user }), value: body.admin_password });
        await post('/wordpress/scan', { website_id: websiteId }, t('Scanning...'));
      }}/>
  </div>;
}

// WordPress on a website that has none yet, at its root: the panel makes
// its database and installs it as the website's user.
function InstallDrawer({ open, websites, busy, close, submit }) {
  const t = useT();
  const [form, setForm] = useState({ website_id: '', title: '', admin_user: 'admin', admin_email: '', admin_password: '' });
  const [tried, setTried] = useState(false);
  const set = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value }));
  const siteId = form.website_id || String(websites[0]?.id || '');
  const site = websites.find((w) => String(w.id) === siteId);
  const problems = {
    admin_user: !form.admin_user.trim() ? t('Type the admin user name.') : '',
    admin_password: form.admin_password.length < 12 ? t('At least 12 characters.') : '',
  };
  function onSubmit(event) {
    event.preventDefault();
    if (!site || Object.values(problems).some(Boolean)) { setTried(true); return; }
    submit(site.id, { title: form.title || site.domain, admin_user: form.admin_user.trim(), admin_email: form.admin_email.trim() || `admin@${site.domain}`, admin_password: form.admin_password });
  }
  return <Drawer open={open} onClose={close} title={t('Install WordPress')}
    subtitle={t('On a website that has none yet: the panel makes its database and installs WordPress in its public_html.')}
    footer={<>
      <button type="button" className="secondary-light" onClick={close}>{t('Cancel')}</button>
      <button type="submit" form="wpm-install-form" disabled={busy || !site}><Download size={14} aria-hidden="true"/> {t('Install WordPress')}</button>
    </>}>
    {websites.length === 0 ? <p className="hint">{t('Every website already has a WordPress, or there is no website yet.')}</p>
      : <form id="wpm-install-form" noValidate onSubmit={onSubmit}>
        <Field label={t('Website')}>
          <select value={siteId} onChange={(e) => set('website_id')(e.target.value)}>
            {websites.map((w) => <option key={w.id} value={w.id}>{w.domain}{w.owner_username ? ` (${w.owner_username})` : ''}</option>)}
          </select>
        </Field>
        <Field label={t('Site title')} optional><input value={form.title} onChange={(e) => set('title')(e.target.value)} placeholder={site?.domain || ''}/></Field>
        <div className="sn-drawer-grid">
          <Field label={t('Admin user')} required error={tried ? problems.admin_user : ''}><input value={form.admin_user} onChange={(e) => set('admin_user')(e.target.value)} autoComplete="off" spellCheck={false}/></Field>
          <Field label={t('Admin email')} optional><input type="email" value={form.admin_email} onChange={(e) => set('admin_email')(e.target.value)} placeholder={site ? `admin@${site.domain}` : ''}/></Field>
        </div>
        <Field label={t('Admin password')} required hint={t('At least 12 characters.')} error={tried ? problems.admin_password : ''}>
          <PasswordField value={form.admin_password} onChange={set('admin_password')} generate copy/>
        </Field>
      </form>}
  </Drawer>;
}

const TABS = [
  ['overview', Activity, msg('Overview')],
  ['plugins', Package, msg('Plugins')],
  ['themes', Palette, msg('Themes')],
  ['activity', ScanSearch, msg('Activity')],
];

function ManageView({ id, detail, back, reload, post, signIn, setSecret, busy }) {
  const t = useT();
  const { request } = usePanel();
  const [tab, setTab] = useState('overview');
  if (!detail || detail.id !== id) {
    return <section className="section"><button type="button" className="secondary-light" onClick={back}><ArrowLeft size={15} aria-hidden="true"/> {t('WordPress Manager')}</button>
      <p className="hint">{t('Loading...')}</p></section>;
  }
  const inv = detail.inventory || {};
  const usable = detail.status === 'ok' && detail.state === 'installed';
  const runningJob = (detail.jobs || []).find(isPending);
  const base = `/wordpress/installs/${id}`;

  async function resetPassword(admin) {
    if (!(await confirmDialog({ title: t('Give {user} a new password?', { user: admin.login }), message: t('The panel makes one and shows it once; the old one stops working.'), confirmLabel: t('Reset password') }))) return;
    const data = await request(`${base}/password`, { method: 'POST', body: JSON.stringify({ user_id: admin.id }) }, t('Setting the password...'));
    if (data?.password) setSecret({ title: t('Password of {user}', { user: data.login }), value: data.password });
  }

  return <>
    <PageHeader breadcrumbs={[{ label: t('WordPress Manager'), onClick: back, href: '/wordpress' }, { label: detail.title || detail.domain }]}
      title={detail.title || detail.domain}
      onRefresh={() => post(`${base}/refresh`, {}, t('Starting...'))} refreshDisabled={busy || !!runningJob || detail.status !== 'ok'}
      actions={<button type="button" disabled={busy || !usable} onClick={() => signIn(detail)}><LogIn size={15} aria-hidden="true"/> {t('Log in as admin')}</button>}>
      <p className="sn-page-subtitle">{safeHref(detail.url) && <a href={safeHref(detail.url)} target="_blank" rel="noreferrer noopener">{detail.url} <ExternalLink size={12} aria-hidden="true"/></a>}
        {' · '}<code>{detail.rel_path ? `/${detail.rel_path}` : '/'}</code>{detail.owner_username ? ` · ${t('Account: {name}', { name: detail.owner_username })}` : ''}</p>
    </PageHeader>
    <section className="section wpm-manage">
      {runningJob && <p className="wpm-running"><RefreshCw size={14} className="spin" aria-hidden="true"/> {t(JOB_LABELS[runningJob.kind] || runningJob.kind)}{runningJob.stage ? `: ${runningJob.stage}` : ''}</p>}
      {detail.last_error && <p className="wpm-error"><AlertTriangle size={15} aria-hidden="true"/> {t('The last check failed: {error}', { error: detail.last_error })}</p>}
      {!usable && <p className="wpm-error"><AlertTriangle size={15} aria-hidden="true"/> {detail.status !== 'ok' ? t('The last scan did not find this WordPress: scan its website again.') : t('This WordPress is not set up yet: open the site to finish its setup.')}</p>}
      <Tabs className="wpm-tabs" label={t('WordPress sections')} value={tab} onChange={setTab}
        tabs={TABS.map(([key, Icon, label]) => ({ id: key, icon: Icon, label: t(label) }))}/>
      {tab === 'overview' && <Overview detail={detail} inv={inv} base={base} post={post} busy={busy || !usable} signIn={signIn} resetPassword={resetPassword}/>}
      {tab === 'plugins' && <Items kind="plugin" detail={detail} items={inv.plugins || []} base={base} post={post} busy={busy || !usable}/>}
      {tab === 'themes' && <Items kind="theme" detail={detail} items={inv.themes || []} base={base} post={post} busy={busy || !usable}/>}
      {tab === 'activity' && <ActivityTab detail={detail}/>}
    </section>
  </>;
}

function Overview({ detail, inv, base, post, busy, signIn, resetPassword }) {
  const t = useT();
  const admins = inv.admins || [];
  const updateTo = async (version) => {
    if (await confirmDialog({ title: t('Update WordPress to {version}?', { version }), message: t('Its database is updated after the files.'), confirmLabel: t('Update now') })) {
      post(`${base}/core`, { action: 'update', version }, t('Starting...'));
    }
  };
  const autoJob = pendingSwitch(detail.jobs, (job) => job.kind === 'core' && job.params?.action === 'auto_update');
  return <div className="wpm-tab">
    <div className="wpm-facts">
      <div><span>WordPress</span><strong>{detail.core_version || '-'}</strong>
        {detail.core_update && <small className="wpm-update">{t('{version} is available', { version: detail.core_update })}</small>}
        {(detail.core_update || inv.core_minor_update) && <div className="wpm-fact-buttons">
          {inv.core_minor_update && inv.core_minor_update !== detail.core_update && <button type="button" className="mini" disabled={busy} onClick={() => updateTo(inv.core_minor_update)}>{t('Update to {version}', { version: inv.core_minor_update })}</button>}
          {detail.core_update && <button type="button" className="mini" disabled={busy} onClick={() => updateTo(detail.core_update)}>{t('Update to {version}', { version: detail.core_update })}</button>}
        </div>}
      </div>
      <div><span>PHP</span><strong>{detail.php_version || '-'}</strong></div>
      <div><span>{t('Plugins')}</span><strong>{detail.plugins_total ?? '-'}</strong>{detail.plugins_updates > 0 && <small className="wpm-update">{t('{count} update(s)', { count: detail.plugins_updates })}</small>}</div>
      <div><span>{t('Themes')}</span><strong>{detail.themes_total ?? '-'}</strong>{detail.themes_updates > 0 && <small className="wpm-update">{t('{count} update(s)', { count: detail.themes_updates })}</small>}</div>
      <div><span>{t('Last checked')}</span><strong>{detail.last_checked_at ? when(detail.last_checked_at) : t('Not yet')}</strong></div>
      <div><span>{t('Folder')}</span><code className="wpm-path">{detail.path}</code></div>
    </div>

    <div className="wpm-toolbar">
      <button type="button" disabled={busy || !(detail.core_update || detail.plugins_updates || detail.themes_updates)}
        onClick={async () => (await confirmDialog({ title: t('Update everything?'), message: t('WordPress, every plugin and every theme are updated to their latest versions.'), confirmLabel: t('Update now') })) && post(`${base}/update`, {}, t('Starting...'))}>
        <Download size={15} aria-hidden="true"/> {t('Update everything')}</button>
    </div>

    <div className="wpm-switches">
      <label className="wpm-switch"><span><Settings2 size={15} aria-hidden="true"/> {t('Automatic core updates')}</span>
        <select value={autoJob ? autoJob.params.mode : (inv.auto_update_core || 'minor')} disabled={busy || !!autoJob}
          onChange={(e) => post(`${base}/core`, { action: 'auto_update', mode: e.target.value }, t('Starting...'))}>
          <option value="off">{t('Off')}</option>
          <option value="minor">{t('Minor (security) releases')}</option>
          <option value="all">{t('Every release')}</option>
        </select>
        <small>{t('WordPress updates itself on its own schedule.')}</small></label>
      <SiteSwitch kind="maintenance" icon={Wrench} title={t('Maintenance mode')} on={!!inv.maintenance} detail={detail} base={base} post={post} busy={busy}
        hint={t('Visitors see "Briefly unavailable for scheduled maintenance" while it is on.')}/>
      <SiteSwitch kind="debug" icon={Bug} title={t('Debugging')} on={!!inv.debug} detail={detail} base={base} post={post} busy={busy}
        hint={t('Errors go to wp-content/debug.log, never to the visitors.')}/>
    </div>

    <h3 className="wpm-subhead">{t('Administrators')}</h3>
    {admins.length === 0 ? <p className="hint">{t('None found yet: refresh.')}</p> : <div className="data-table-wrap">
      <table className="data-table wpm-cards">
        <thead><tr><th scope="col">{t('Username')}</th><th scope="col">{t('E-mail')}</th><th scope="col"><span className="sr-only">{t('Actions')}</span></th></tr></thead>
        <tbody>{admins.map((a) => <tr key={a.id}>
          <td className="wpm-card-title"><strong>{a.login}</strong>{a.name && a.name !== a.login ? <small className="data-table-muted"> {a.name}</small> : null}</td>
          <td data-label={t('E-mail')} className="wpm-wrap">{a.email}</td>
          <td className="wpm-actions"><div className="wpm-row-actions">
            <button type="button" className="mini secondary-light" disabled={busy} onClick={() => signIn(detail, a.id)}><LogIn size={13} aria-hidden="true"/> {t('Log in')}</button>
            <button type="button" className="mini secondary-light" disabled={busy} onClick={() => resetPassword(a)}><KeyRound size={13} aria-hidden="true"/> {t('Reset password')}</button>
          </div></td>
        </tr>)}</tbody>
      </table>
    </div>}
  </div>;
}

// Maintenance mode or debugging: a switch showing whether it is on - or,
// while a change of it is on its way, where it is going.
function SiteSwitch({ kind, icon: Icon, title, hint, on, detail, base, post, busy }) {
  const t = useT();
  const job = pendingSwitch(detail.jobs, (j) => j.kind === kind);
  const shown = job ? !!job.params?.on : on;
  return <div className="wpm-switch"><span><Icon size={15} aria-hidden="true"/> {title}</span>
    <Switch checked={shown} pending={!!job} disabled={busy} label={title}
      onChange={(next) => post(`${base}/${kind}`, { on: next }, t('Starting...'))}>
      <span>{shown ? t('On') : t('Off')}</span>
    </Switch>
    <small>{hint}</small></div>;
}

// The panel's own sign-in plugin is not one of the site's.
const isSsoPlugin = (item) => item.name === 'opanel-sso' && item.status === 'must-use';

function Items({ kind, detail, items: listed, base, post, busy }) {
  const t = useT();
  const { request } = usePanel();
  const items = listed.filter((item) => !isSsoPlugin(item));
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState('');
  const [found, setFound] = useState(null);
  const plugin = kind === 'plugin';
  const path = `${base}/${plugin ? 'plugins' : 'themes'}`;
  const updates = items.filter((item) => item.update);
  const act = (action, slugs, extra = {}) => post(path, { action, slugs, ...extra }, t('Starting...'));
  const itemJob = (name, actions) => pendingSwitch(detail.jobs, (job) => job.kind === (plugin ? 'plugins' : 'themes')
    && actions.includes(job.params?.action) && (job.params?.slugs || []).includes(name));

  async function search(event) {
    event.preventDefault();
    const data = await request(`${base}/search?kind=${kind}&q=${encodeURIComponent(query.trim())}`, {}, t('Searching wordpress.org...'));
    if (data) setFound(data.items);
  }
  async function remove(item) {
    if (!(await confirmDialog({
      title: plugin ? t('Delete the plugin {name}?', { name: item.title || item.name }) : t('Delete the theme {name}?', { name: item.title || item.name }),
      message: plugin ? t('It is deactivated first; its files are removed.') : t('Its files are removed.'), danger: true, confirmLabel: t('Delete'),
    }))) return;
    act('delete', [item.name]);
  }

  return <div className="wpm-tab">
    <div className="wpm-toolbar">
      <button type="button" disabled={busy} onClick={() => setAdding((v) => !v)}><Plus size={15} aria-hidden="true"/> {plugin ? t('Add plugin') : t('Add theme')}</button>
      <button type="button" className="secondary-light" disabled={busy || updates.length === 0}
        onClick={async () => (await confirmDialog({ title: plugin ? t('Update every plugin with an update waiting?') : t('Update every theme with an update waiting?'), confirmLabel: t('Update now') })) && act('update_all', [])}>
        <Download size={15} aria-hidden="true"/> {t('Update all')} {updates.length ? `(${updates.length})` : ''}</button>
    </div>
    {adding && <div className="wpm-panel">
      <div className="wpm-panel-head"><strong>{plugin ? t('Add a plugin from wordpress.org') : t('Add a theme from wordpress.org')}</strong>
        <button type="button" className="secondary-light icon-button" onClick={() => { setAdding(false); setFound(null); }} aria-label={t('Close')} title={t('Close')}><X size={15} aria-hidden="true"/></button></div>
      <form className="wpm-search" onSubmit={search}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={plugin ? 'contact form' : 'astra'} spellCheck="false" autoFocus/>
        <button type="submit" disabled={busy || query.trim().length < 2}><Search size={15} aria-hidden="true"/> {t('Search')}</button>
      </form>
      {found && (found.length === 0 ? <p className="hint">{t('Nothing found.')}</p> : <div className="wpm-found">
        {found.map((f) => <div key={f.slug} className="wpm-found-row">
          <div><strong>{f.name}</strong><small><code>{f.slug}</code> · {f.version}{f.active_installs != null ? ` · ${t('{count}+ sites', { count: Number(f.active_installs).toLocaleString() })}` : ''} · {t('{rating}% rating', { rating: f.rating })}</small></div>
          <div className="wpm-row-actions">
            <button type="button" className="mini secondary-light" disabled={busy} onClick={() => act('install', [f.slug], { activate: false })}>{t('Install')}</button>
            <button type="button" className="mini" disabled={busy} onClick={() => act('install', [f.slug], { activate: true })}>{t('Install and activate')}</button>
          </div>
        </div>)}
      </div>)}
    </div>}
    {items.length === 0 ? <p className="hint">{t('None found yet: refresh.')}</p> : <div className="data-table-wrap">
      <table className="data-table wpm-items wpm-cards">
        <thead><tr>
          <th scope="col">{t('Name')}</th>
          <th scope="col">{t('Version')}</th>
          <th scope="col">{t('Status')}</th>
          <th scope="col">{t('Automatic updates')}</th>
          <th scope="col"><span className="sr-only">{t('Actions')}</span></th>
        </tr></thead>
        <tbody>{items.map((item) => {
          const active = item.status === 'active' || item.status === 'active-network';
          const locked = item.status === 'must-use' || item.status === 'dropin';
          const can = !locked && !busy;
          const activating = itemJob(item.name, ['activate', 'deactivate']);
          const shownActive = activating ? activating.params.action === 'activate' : active;
          const autoJob = itemJob(item.name, ['auto_on', 'auto_off']);
          const shownAuto = autoJob ? autoJob.params.action === 'auto_on' : !!item.auto_update;
          const name = item.title || item.name;
          return <tr key={item.name}>
            <td className="wpm-card-title"><strong>{name}</strong><small className="data-table-muted"> {item.name}</small></td>
            <td data-label={t('Version')}>{item.version} {item.update && <span className="badge warn">{t('Update {version}', { version: item.update_version || '' })}</span>}</td>
            <td data-label={t('Status')}>{plugin
              ? (locked ? <span className="badge">{item.status}</span>
                : <Switch checked={shownActive} pending={!!activating} disabled={!can} label={t('{name} is active', { name })}
                  onChange={(on) => act(on ? 'activate' : 'deactivate', [item.name])}>
                  <span>{shownActive ? t('Active') : t('Inactive')}</span>
                </Switch>)
              // A theme is not switched off: another one is activated.
              : (item.status === 'active' ? <span className="badge ok">{t('Active')}</span>
                : item.status === 'parent' ? <span className="badge">{t('Parent theme')}</span>
                : <button type="button" className="mini secondary-light" disabled={!can || !!activating} onClick={() => act('activate', [item.name])}>{t('Activate')}</button>)}
            </td>
            <td data-label={t('Automatic updates')}>{locked ? '-' : <Switch checked={shownAuto} pending={!!autoJob} disabled={!can} label={t('Automatic updates of {name}', { name })}
              onChange={(on) => act(on ? 'auto_on' : 'auto_off', [item.name])}>
              <span>{shownAuto ? t('On') : t('Off')}</span>
            </Switch>}</td>
            <td className="wpm-actions"><div className="wpm-row-actions">
              {item.update && <button type="button" className="mini" disabled={!can} onClick={() => act('update', [item.name])}><Download size={13} aria-hidden="true"/> {t('Update')}</button>}
              {!locked && !(!plugin && (item.status === 'active' || item.status === 'parent')) && <button type="button" className="mini danger icon-button" disabled={!can}
                onClick={() => remove(item)} aria-label={t('Delete {name}', { name: item.name })} title={t('Delete')}><Trash2 size={14} aria-hidden="true"/></button>}
            </div></td>
          </tr>;
        })}</tbody>
      </table>
    </div>}
  </div>;
}

// What was done to this WordPress, newest first, with each task's log.
function ActivityTab({ detail }) {
  const t = useT();
  const jobs = detail.jobs || [];
  if (jobs.length === 0) return <p className="hint">{t('No task yet.')}</p>;
  return <ul className="wpm-activity">
    {jobs.map((job) => {
      const [tone, label] = JOB_STATUS[job.status] || ['', job.status];
      return <li key={job.id}>
        <div className="wpm-activity-head">
          <strong>{t(JOB_LABELS[job.kind] || job.kind)}</strong>
          <span className={`badge ${tone}`}>{t(label)}</span>
          <small>{when(job.finished_at || job.created_at)}</small>
        </div>
        {job.error && <p className="wpm-error">{job.error}</p>}
        {job.log && <details><summary>{t('Log')}</summary><pre className="log-output">{job.log}</pre></details>}
      </li>;
    })}
  </ul>;
}
