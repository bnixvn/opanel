import { useEffect, useState } from 'react';
import {
  Archive, Clock, Download, Eraser, ExternalLink, FileText, FolderOpen, Gauge, Globe, LayoutDashboard, Lock, LogIn, Plus, RefreshCw,
  RotateCcw, Save, ShieldCheck, SlidersHorizontal, TerminalIcon, Trash2, Wrench, X, Code2,
} from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import { websiteFromPathname, websiteRoute } from '../lib/websites.js';
import PageHeader from '../components/PageHeader.jsx';
import Tabs from '../components/Tabs.jsx';
import ActionMenu from '../components/ActionMenu.jsx';
import EmptyState from '../components/EmptyState.jsx';
import { Field } from '../components/Form.jsx';
import { Terminal } from '../components/Terminal';
import Switch from '../components/Switch.jsx';
import { AutoSslSite } from '../components/AutoSsl.jsx';
import { WordPressInstallDrawer, siteTypeLabel, sslLabel, useSiteMenu, useSiteOwners } from '../components/WebsiteParts.jsx';
import './Websites.css';

// One website's page, as SNPanel's (pages/WebsiteDetail.jsx there): its
// tabs - Overview, Domains, SSL, PHP, Security, Logs, Advanced - each with
// its own one job, instead of the settings, logs, installer and terminal
// that opened under its card in the list. Each tab has an address of its
// own: /website/<domain>/<tab>.
export default function WebsiteDetailPage() {
  const t = useT();
  const { loading, navigateToPage, openNginxCustom, refreshAll, setSelectedWebsiteId, websiteUrl, websites } = usePanel();
  const [where, setWhere] = useState(() => websiteFromPathname(window.location.pathname) || { domain: '', tab: 'overview' });
  useEffect(() => {
    const sync = () => { const next = websiteFromPathname(window.location.pathname); if (next) setWhere(next); };
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);
  const site = websites.find((item) => String(item.domain || '').toLowerCase() === where.domain);
  // Opened by its address before the list was loaded: ask for it once.
  const [looked, setLooked] = useState(false);
  useEffect(() => {
    if (site || looked) return;
    Promise.resolve(refreshAll()).finally(() => setLooked(true));
  }, [site, looked]);
  // This site is the panel's "current" one (SSL follows it), and its
  // settings are loaded for the PHP and Advanced tabs.
  useEffect(() => {
    if (!site) return;
    setSelectedWebsiteId(String(site.id));
    openNginxCustom(site);
  }, [site?.id]);
  const ownerOf = useSiteOwners();
  const siteMenu = useSiteMenu({ onDeleted: () => navigateToPage('websites') });
  const [refreshing, setRefreshing] = useState(false);

  const backToList = { label: t('Websites'), href: '/website', onClick: () => navigateToPage('websites') };
  if (!site) {
    return <>
      <PageHeader breadcrumbs={[backToList, { label: where.domain }]}/>
      <section className="section">
        {looked
          ? <EmptyState icon={Globe} title={t('No website {domain} here.', { domain: where.domain })}
            message={t('It may have been deleted, or it belongs to an account you cannot see.')}
            action={{ label: t('All websites'), icon: Globe, onClick: () => navigateToPage('websites') }}/>
          : <p className="hint">{t('Loading…')}</p>}
      </section>
    </>;
  }

  const tabs = [
    { id: 'overview', label: t('Overview'), icon: LayoutDashboard },
    { id: 'domains', label: t('Domains'), icon: Globe, badge: (site.aliases || []).length || undefined },
    { id: 'ssl', label: 'SSL', icon: Lock },
    { id: 'php', label: 'PHP', icon: Code2 },
    { id: 'cache', label: t('Cache'), icon: Gauge, hidden: site.app_type === 'static' },
    { id: 'security', label: t('Security'), icon: ShieldCheck },
    { id: 'logs', label: t('Logs'), icon: FileText },
    { id: 'advanced', label: t('Advanced'), icon: SlidersHorizontal },
  ].map((item) => ({ ...item, href: websiteRoute(site.domain, item.id) }));
  const shownTab = tabs.some((item) => item.id === where.tab && !item.hidden) ? where.tab : 'overview';
  function goTab(next, hash = '') {
    const url = `${websiteRoute(site.domain, next)}${hash ? `#${hash}` : ''}`;
    if (window.location.pathname + window.location.hash !== url) window.history.pushState({}, '', url);
    setWhere({ domain: where.domain, tab: next });
  }
  async function refresh() {
    setRefreshing(true);
    try { await refreshAll(); } finally { setRefreshing(false); }
  }
  const owner = ownerOf(site);
  const facts = [siteTypeLabel(site, t)];
  if (site.app_type !== 'static') facts.push(`PHP ${site.php_version}`);
  if (owner) facts.push(t('Owner: {name}', { name: owner }));

  return <>
    <PageHeader breadcrumbs={[backToList, { label: site.domain }]} title={site.domain} subtitle={facts.join(' · ')}
      onRefresh={refresh} refreshing={refreshing} refreshDisabled={!!loading}
      actions={<>
        <a className="button-link secondary-light" href={websiteUrl(site)} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} aria-hidden="true"/> {t('Open website')}</a>
        <ActionMenu label={t('More actions for {domain}', { domain: site.domain })} items={siteMenu(site, { inDetail: true })}/>
      </>}/>
    <Tabs className="wsd-tabs" label={t('Website sections')} value={shownTab} onChange={(next) => goTab(next)} tabs={tabs}/>
    {shownTab === 'overview' && <OverviewTab site={site} owner={owner} goTab={goTab}/>}
    {shownTab === 'domains' && <DomainsTab site={site} goTab={goTab}/>}
    {shownTab === 'ssl' && <SslTab site={site}/>}
    {shownTab === 'php' && <PhpTab site={site}/>}
    {shownTab === 'cache' && <CacheTab site={site}/>}
    {shownTab === 'security' && <SecurityTab site={site}/>}
    {shownTab === 'logs' && <LogsTab site={site}/>}
    {shownTab === 'advanced' && <AdvancedTab site={site}/>}
    <WordPressInstallDrawer/>
  </>;
}

function OverviewTab({ site, owner, goTab }) {
  const t = useT();
  const {
    NGINX_REWRITE_MODES, canManageUsers, loading, navigateToPage, openWebsiteFileManager, openWpInstall,
    setBackupTab, updateWordPressAll, websiteUrl,
  } = usePanel();
  const rewrite = NGINX_REWRITE_MODES.find((mode) => mode.value === site.nginx_rewrite_mode);
  const updated = site.ssl_updated_at ? new Date(site.ssl_updated_at).toLocaleString() : '';
  return <div className="wsd-overview">
    <section className="section wsd-card">
      <h2 className="wsd-card-title">{t('About this website')}</h2>
      <dl className="wsd-facts">
        <div><dt>{t('Address')}</dt><dd><a href={websiteUrl(site)} target="_blank" rel="noopener noreferrer">{websiteUrl(site)} <ExternalLink size={12} aria-hidden="true"/></a></dd></div>
        <div><dt>{t('Type')}</dt><dd>{siteTypeLabel(site, t)}</dd></div>
        {site.app_type !== 'static' && <div><dt>{t('PHP version')}</dt><dd>{site.php_version}</dd></div>}
        {site.app_type === 'php' && rewrite && rewrite.value !== 'none' && <div><dt>{t('Rewrite rules')}</dt><dd>{rewrite.label}</dd></div>}
        {canManageUsers && <div><dt>{t('Owner')}</dt><dd>{owner || `#${site.owner_id}`}</dd></div>}
        {site.linux_user && <div><dt>{t('Linux user')}</dt><dd><code>{site.linux_user}</code></dd></div>}
        <div><dt>{t('Folder')}</dt><dd><code>{site.root_path}</code></dd></div>
        <div><dt>SSL</dt><dd>
          <span className={`badge ${site.ssl_enabled ? 'ok' : ''}`}>{sslLabel(site, t)}</span>
          {site.ssl_wildcard && <span className="badge ok">*.{site.domain}</span>}
          {updated && <small>{t('Updated {when}', { when: updated })}</small>}
          <button type="button" className="link-button" onClick={() => goTab('ssl')}>{t('Manage SSL')}</button>
        </dd></div>
        <div><dt>WAF</dt><dd>
          <span className={`badge ${site.waf_enabled ? 'ok' : ''}`}>{site.waf_enabled ? t('WAF on') : t('WAF off')}</span>
          <button type="button" className="link-button" onClick={() => goTab('security')}>{t('Security settings')}</button>
        </dd></div>
        <div><dt>{t('Domains')}</dt><dd>
          {t('{count} extra domain(s)', { count: (site.aliases || []).length })}
          <button type="button" className="link-button" onClick={() => goTab('domains')}>{t('Manage domains')}</button>
        </dd></div>
      </dl>
    </section>

    <section className="section wsd-card">
      <h2 className="wsd-card-title">WordPress</h2>
      {site.wp_installed
        ? <>
          <p className="hint">{t('WordPress is installed here.')}</p>
          <div className="wsd-actions">
            <button type="button" className="secondary-light" disabled={!!loading} onClick={() => updateWordPressAll(site)}><RefreshCw size={14} aria-hidden="true"/> {t('Update WordPress (core + plugins + themes)')}</button>
          </div>
        </>
        : site.app_type === 'static'
          ? <p className="hint">{t('A static website runs no PHP, so WordPress cannot run here. Change the mode on the PHP tab first.')}</p>
          : <>
            <p className="hint">{t('No WordPress here yet.')}</p>
            <div className="wsd-actions">
              <button type="button" className="secondary-light" disabled={!!loading} onClick={() => openWpInstall(site)}><Download size={14} aria-hidden="true"/> {t('Install WordPress')}</button>
            </div>
          </>}
    </section>

    <section className="section wsd-card wsd-shortcuts">
      <h2 className="wsd-card-title">{t('Shortcuts')}</h2>
      <div className="wsd-actions">
        <button type="button" className="secondary-light" disabled={!!loading} onClick={() => openWebsiteFileManager(site)}><FolderOpen size={14} aria-hidden="true"/> {t('File manager')}</button>
        <button type="button" className="secondary-light" onClick={() => goTab('logs')}><FileText size={14} aria-hidden="true"/> {t('Logs')}</button>
        <button type="button" className="secondary-light" onClick={() => goTab('advanced', 'terminal')}><TerminalIcon size={14} aria-hidden="true"/> {t('Terminal')}</button>
        <button type="button" className="secondary-light" onClick={() => { setBackupTab('website'); navigateToPage('backups'); }}><Archive size={14} aria-hidden="true"/> {t('Backups')}</button>
        <button type="button" className="secondary-light" onClick={() => navigateToPage('cron')}><Clock size={14} aria-hidden="true"/> {t('Cron jobs')}</button>
      </div>
    </section>
  </div>;
}

function DomainsTab({ site, goTab }) {
  const t = useT();
  const { addWebsiteAlias, aliasDrafts, aliasModes, deleteWebsiteAlias, loading, setAliasDrafts, setAliasModes } = usePanel();
  const draft = aliasDrafts[site.id] || '';
  const mode = aliasModes[site.id] || 'alias';
  const aliases = site.aliases || [];
  function submit(event) {
    event.preventDefault();
    if (!draft.trim()) return;
    addWebsiteAlias(site);
  }
  return <section className="section">
    <PageHeader title={t('Domains')} subtitle={t('Alias serves the same app. Redirect sends visitors to {domain}.', { domain: site.domain })}/>
    <ul className="wsd-domain-list">
      <li><Globe size={14} aria-hidden="true"/><strong>{site.domain}</strong><span className="badge ok">{t('Main')}</span></li>
      {aliases.map((alias) => <li key={alias.id}>
        <Globe size={14} aria-hidden="true"/><strong>{alias.domain}</strong>
        <span className="badge">{alias.mode === 'redirect' ? t('Redirect') : t('Alias')}</span>
        <button type="button" className="mini secondary-light" disabled={!!loading} onClick={() => deleteWebsiteAlias(site, alias)}
          aria-label={t('Remove {domain}', { domain: alias.domain })}><X size={13} aria-hidden="true"/> {t('Remove')}</button>
      </li>)}
    </ul>
    {aliases.length === 0 && <p className="hint">{t('No extra domains')}</p>}
    <form className="wsd-domain-form" onSubmit={submit}>
      <Field label={t('New domain')}>
        <input value={draft} onChange={(e) => setAliasDrafts((prev) => ({ ...prev, [site.id]: e.target.value }))}
          placeholder={t('e.g. {example}', { example: 'www.example.com' })} disabled={!!loading} autoCapitalize="none" spellCheck={false}/>
      </Field>
      <Field label={t('Kind')}>
        <select value={mode} onChange={(e) => setAliasModes((prev) => ({ ...prev, [site.id]: e.target.value }))} disabled={!!loading}>
          <option value="alias">{t('Alias')}</option>
          <option value="redirect">{t('Redirect')}</option>
        </select>
      </Field>
      <button type="submit" disabled={!!loading || !draft.trim()}><Plus size={14} aria-hidden="true"/> {t('Add domain')}</button>
    </form>
    <p className="hint">{t('A new domain gets its certificate on the {tab} tab: install or renew SSL there.', { tab: <button type="button" className="link-button" onClick={() => goTab('ssl')}>SSL</button> })}</p>
  </section>;
}

// The SSL page's form, for this website only (App's renderSslManage).
function SslTab({ site }) {
  const { renderSslManage } = usePanel();
  return <>
    <AutoSslSite site={site}/>
    {renderSslManage(true)}
  </>;
}

function PhpTab({ site }) {
  const t = useT();
  const {
    NGINX_REWRITE_MODES, loading, nginxCustomEditing, phpVersionOptions, phpVersions, saveWebsiteSettings, setWebsiteSettingsForm,
    websiteConfigForm, websiteSettingsForm,
  } = usePanel();
  const ready = nginxCustomEditing?.id === site.id;
  const base = websiteConfigForm(ready ? nginxCustomEditing.site || site : site);
  const form = ready ? websiteSettingsForm : base;
  const appType = form.app_type || 'wordpress';
  const rewriteFixed = appType !== 'php';
  const dirty = ready && ['app_type', 'php_version', 'nginx_rewrite_mode'].some((key) => String(form[key] ?? '') !== String(base[key] ?? ''));
  const set = (patch) => setWebsiteSettingsForm((prev) => ({ ...prev, ...patch }));
  return <section className="section">
    <PageHeader title={t('How the website runs')} subtitle={t('Managed settings rewrite the main vhost safely.')}/>
    <div className="wsd-form-grid">
      <Field label={t('Website mode')}>
        <select value={appType} disabled={!ready || !!loading} onChange={(e) => set({
          app_type: e.target.value,
          nginx_rewrite_mode: e.target.value === 'php' ? form.nginx_rewrite_mode || 'none' : e.target.value === 'wordpress' ? 'front_controller' : 'none',
        })}>
          <option value="wordpress">{t('WordPress')}</option>
          <option value="php">{t('PHP')}</option>
          <option value="static">{t('Static')}</option>
        </select>
      </Field>
      {appType !== 'static' && <Field label={t('PHP version')}>
        <select value={form.php_version} disabled={!ready || !!loading} onChange={(e) => set({ php_version: e.target.value })}>
          {phpVersionOptions(phpVersions.installed, form.php_version).map((v) => <option key={v} value={v}>PHP {v}</option>)}
        </select>
      </Field>}
      <Field label={t('Rewrite rules')} hint={rewriteFixed ? t('Set by the website mode.') : ''}>
        <select value={rewriteFixed ? (appType === 'wordpress' ? 'front_controller' : 'none') : form.nginx_rewrite_mode}
          disabled={!ready || !!loading || rewriteFixed} onChange={(e) => set({ nginx_rewrite_mode: e.target.value })}>
          {NGINX_REWRITE_MODES.map((mode) => <option key={mode.value} value={mode.value}>{mode.label}</option>)}
        </select>
      </Field>
    </div>
    <div className={`wsd-savebar${dirty ? ' is-dirty' : ''}`}>
      <span role="status">{dirty ? t('Unsaved changes') : ''}</span>
      {dirty && <button type="button" className="secondary-light" disabled={!!loading} onClick={() => setWebsiteSettingsForm(base)}><RotateCcw size={14} aria-hidden="true"/> {t('Undo changes')}</button>}
      <button type="button" disabled={!ready || !dirty || !!loading} onClick={saveWebsiteSettings}><Save size={14} aria-hidden="true"/> {t('Save changes')}</button>
    </div>
  </section>;
}

// LiteSpeed's page cache for the site, as SNPanel's Cache tab: on or off, and
// emptied - the site's own cache folder, no other site's.
function CacheTab({ site }) {
  const t = useT();
  const { loading, refreshAll, request, setNotice } = usePanel();
  const on = site.lscache_enabled !== false;
  const [pending, setPending] = useState(null);
  async function toggle(next) {
    setPending(next);
    try {
      const data = await request(`/websites/${site.id}/lscache`, { method: 'PUT', body: JSON.stringify({ on: next }) },
        next ? t('Turning the page cache on...') : t('Turning the page cache off...'));
      if (data) { setNotice(next ? t('The page cache is on for {domain}.', { domain: site.domain }) : t('The page cache is off for {domain}.', { domain: site.domain })); await refreshAll(); }
    } finally { setPending(null); }
  }
  async function purge() {
    const data = await request(`/websites/${site.id}/lscache/purge`, { method: 'POST' }, t('Clearing the cache...'));
    if (data) setNotice(t('Cache cleared: {count} page(s) removed.', { count: data.files || 0 }));
  }
  return <section className="section">
    <PageHeader title={t('Page cache')} subtitle={t('LiteSpeed keeps a copy of the pages the website marks cacheable and serves it without running PHP.')}/>
    <div className="wsd-cache">
      <Switch checked={pending ?? on} pending={pending !== null} disabled={!!loading} onChange={toggle}>
        <span>{(pending ?? on) ? t('The page cache is on') : t('The page cache is off')}</span>
      </Switch>
      <button type="button" className="secondary-light" disabled={!!loading || !on} onClick={purge}><Eraser size={14} aria-hidden="true"/> {t('Clear cache')}</button>
    </div>
    <p className="hint">{site.app_type === 'wordpress'
      ? t('WordPress decides what is cached through the LiteSpeed Cache plugin: install and turn it on in WordPress. Its own "Purge All" empties this same cache.')
      : t('Only responses the application marks with an X-LiteSpeed-Cache-Control header are cached; everything else is served fresh.')}</p>
    <p className="hint">{site.app_type === 'wordpress'
      ? t('Turning it on or off empties what the site had stored. Logged-in visitors, form posts and URLs with a query string are never served from the cache.')
      : t('Turning it on or off empties what the site had stored.')}</p>
  </section>;
}

function SecurityTab({ site }) {
  const t = useT();
  const { loadWebsiteWafConfig, renderWafSite, selectedWafWebsiteId, wafSiteConfig } = usePanel();
  useEffect(() => { loadWebsiteWafConfig(site.id, false); }, [site.id]);
  if (String(selectedWafWebsiteId) !== String(site.id) || !wafSiteConfig) return <section className="section"><p className="hint">{t('Loading…')}</p></section>;
  return renderWafSite(true);
}

function LogsTab({ site }) {
  const t = useT();
  const { loadWebsiteLog, loading, logViewer } = usePanel();
  useEffect(() => { loadWebsiteLog(site, 'access', 200, site.domain); }, [site.id]);
  const log = logViewer?.id === site.id ? logViewer : null;
  const kind = log?.kind || 'access';
  const lines = log?.lines || 200;
  return <section className="section wsd-logs">
    <PageHeader title={t('Web server logs')} subtitle={log?.path || ''}
      onRefresh={() => loadWebsiteLog(site.id, kind, lines, site.domain)} refreshDisabled={!!loading}/>
    <div className="log-toolbar">
      <div className="segmented-control" role="group" aria-label={t('Log')}>
        {[['access', t('Access')], ['error', t('Errors (PHP + server)')]].map(([id, label]) => <button key={id} type="button" aria-pressed={kind === id}
          className={kind === id ? 'active' : ''} disabled={!!loading} onClick={() => loadWebsiteLog(site.id, id, lines, site.domain)}>{label}</button>)}
      </div>
      <select value={lines} onChange={(e) => loadWebsiteLog(site.id, kind, Number(e.target.value), site.domain)} disabled={!!loading} aria-label={t('Lines')}>
        {[[100, t('100 lines')], [200, t('200 lines')], [500, t('500 lines')], [1000, t('1000 lines')], [2000, t('2000 lines')]].map(([n, label]) => <option key={n} value={n}>{label}</option>)}
      </select>
    </div>
    <pre className="log-output">{!log ? t('Loading…') : log.exists
      ? (log.content || t('Log is empty.'))
      : (kind === 'error' ? t('No errors logged yet.') : t('Log file has not been created yet.'))}</pre>
  </section>;
}

// The website's Terminal: a real shell as its owner, in the owner's jail,
// starting in its folder with its PHP. An administrator or a reseller has
// no shell of its own here - only the customer's, through "Log in as" -
// so they are told that, with the button, instead of a socket that would
// refuse them.
function TerminalSection({ site }) {
  const t = useT();
  const { API, canManageUsers, canOpenTerminal, currentUser, isAdmin, isReseller, quickLoginUser, users } = usePanel();
  const [open, setOpen] = useState(() => window.location.hash === '#terminal');
  const owner = users.find((user) => String(user.id) === String(site.owner_id));
  const canLogInAs = canManageUsers && !currentUser?.impersonator && owner && String(owner.id) !== String(currentUser?.id);
  return <>
    <PageHeader title={t('Terminal')} subtitle={t('A bash shell as this website\'s owner, starting in the website\'s folder with its PHP version.')}
      actions={!canOpenTerminal ? null : open
        ? <button type="button" className="secondary-light" onClick={() => setOpen(false)}><X size={14} aria-hidden="true"/> {t('Close terminal')}</button>
        : <button type="button" className="secondary-light" onClick={() => setOpen(true)}><TerminalIcon size={14} aria-hidden="true"/> {t('Open terminal')}</button>}/>
    {!canOpenTerminal && <div className="wsd-term-off">
      <p className="hint">{isAdmin || isReseller
        ? t('Administrators and resellers open a customer\'s terminal by logging in as the customer.')
        : t('The terminal is not part of this account\'s package.')}</p>
      {canLogInAs && <button type="button" className="secondary-light" onClick={() => quickLoginUser(owner)}>
        <LogIn size={14} aria-hidden="true"/> {t('Log in as {name}', { name: owner.username })}</button>}
    </div>}
    {canOpenTerminal && open && <div className="wsd-terminal">
      <Terminal websiteId={site.id} apiBase={API}/>
    </div>}
  </>;
}

// The site's own rewrite rules and response headers, as SNPanel's custom
// directives: RewriteCond and RewriteRule lines, and "Name: value" headers -
// nothing else, which the server checks line by line.
const CUSTOM_EXAMPLE = `# Send an old path elsewhere
RewriteRule ^old-page$ /new-page [R=301,L]
# Refuse a folder
RewriteRule ^private/ - [F]
# A response header (one the panel also sends, like Content-Security-Policy, replaces the panel's)
X-Robots-Tag: noindex`;

function CustomRules({ site }) {
  const t = useT();
  const { loading, refreshAll, request, setNotice } = usePanel();
  const saved = site.custom_rules || '';
  const [text, setText] = useState(saved);
  useEffect(() => { setText(site.custom_rules || ''); }, [site.id, site.custom_rules]);
  const dirty = text.trim() !== saved.trim();
  async function save() {
    const data = await request(`/websites/${site.id}/custom-rules`, { method: 'PUT', body: JSON.stringify({ custom_rules: text }) }, t('Saving custom rules...'));
    if (data) { setNotice(t('Custom rules saved for {domain}.', { domain: site.domain })); await refreshAll(); }
  }
  return <section className="section">
    <PageHeader title={t('Custom rules')} subtitle={t('Rewrite rules and response headers of your own, added to the web server\'s configuration for this website.')}/>
    <textarea className="code-editor" value={text} onChange={(e) => setText(e.target.value)} rows={8} spellCheck={false}
      placeholder={CUSTOM_EXAMPLE} aria-label={t('Custom rules')}/>
    <div className={`wsd-savebar${dirty ? ' is-dirty' : ''}`}>
      <span role="status">{dirty ? t('Unsaved changes') : ''}</span>
      {dirty && <button type="button" className="secondary-light" disabled={!!loading} onClick={() => setText(saved)}><RotateCcw size={14} aria-hidden="true"/> {t('Undo changes')}</button>}
      <button type="button" disabled={!dirty || !!loading} onClick={save}><Save size={14} aria-hidden="true"/> {t('Save changes')}</button>
    </div>
    <p className="hint">{t('One rule a line: RewriteCond and RewriteRule (flags L, R=301/302/307/308, NC, OR, QSA, QSD, NE, F, G, END), or a header as "Name: value". They run after the HTTPS and domain redirects. A .htaccess file in the website keeps working as well.')}</p>
  </section>;
}

function AdvancedTab({ site }) {
  const t = useT();
  const {
    deleteWebsite, fixNginxSecurity, fixWordPressPermissions, isAdmin, loading, navigateToPage, nginxCustomEditing, setNginxCustomEditing,
    viewFullNginxConfig,
  } = usePanel();
  useEffect(() => {
    if (window.location.hash === '#terminal') document.getElementById('wsd-terminal')?.scrollIntoView({ block: 'start' });
  }, []);
  const editor = nginxCustomEditing?.id === site.id ? nginxCustomEditing : null;
  const full = editor?.mode === 'full';

  return <div className="wsd-advanced">
    {isAdmin && <section className="section">
      <PageHeader title={t('Full vhost')} subtitle={t('This is read-only. OPanel manages the main vhost template.')}
        actions={full
          ? <button type="button" className="secondary-light" onClick={() => setNginxCustomEditing((prev) => ({ ...prev, mode: 'settings', content: '' }))}><X size={14} aria-hidden="true"/> {t('Close the full vhost')}</button>
          : <button type="button" className="secondary-light" disabled={!editor || !!loading} onClick={viewFullNginxConfig}><FileText size={14} aria-hidden="true"/> {t('View the full vhost')}</button>}/>
      {full && <textarea className="code-editor" value={editor.content || ''} readOnly spellCheck={false} rows={18} aria-label={t('Full vhost')}/>}
    </section>}

    <CustomRules site={site}/>

    <section className="section">
      <PageHeader title={t('Repair')} subtitle={t('Puts back what the panel manages, when something was changed by hand.')}/>
      <div className="wsd-actions">
        <button type="button" className="secondary-light" disabled={!!loading} onClick={() => fixNginxSecurity(site.id)}><ShieldCheck size={14} aria-hidden="true"/> {t('Rewrite the web server security rules')}</button>
        {site.wp_installed && <button type="button" className="secondary-light" disabled={!!loading} onClick={() => fixWordPressPermissions(site.id)}><Wrench size={14} aria-hidden="true"/> {t('Fix WordPress file permissions')}</button>}
      </div>
    </section>

    <section className="section" id="wsd-terminal">
      <TerminalSection site={site}/>
    </section>

    <section className="section wsd-danger">
      <PageHeader title={t('Delete this website')} subtitle={t('Removes its web server configuration and certificate; you choose whether its files and databases go too.')}
        actions={<button type="button" className="danger" disabled={!!loading} onClick={async () => { if (await deleteWebsite(site.id)) navigateToPage('websites'); }}>
          <Trash2 size={14} aria-hidden="true"/> {t('Delete website')}</button>}/>
    </section>
  </div>;
}
