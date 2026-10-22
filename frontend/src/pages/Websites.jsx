import { useState } from 'react';
import { Globe, KeyRound, Lock, Plus, RefreshCw, Search, Settings as SettingsIcon, ExternalLink, X } from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import { websiteRoute } from '../lib/websites.js';
import PageHeader from '../components/PageHeader.jsx';
import Drawer from '../components/Drawer.jsx';
import IconActions from '../components/IconActions.jsx';
import EmptyState from '../components/EmptyState.jsx';
import { Checkbox, Field, PasswordField } from '../components/Form.jsx';
import { WordPressInstallDrawer, siteTypeLabel, sslLabel, useOpenSite, useSiteMenu, useSiteOwners } from '../components/WebsiteParts.jsx';
import './Websites.css';

// The website list, as SNPanel's (pages/Websites.jsx there): one card per
// site - its address, what it is (type, PHP, owner, SSL), and two controls:
// "Manage", which opens the site's own page with its tabs, and a "…" menu
// of labelled actions with Delete apart at its end. A new website is made
// in a side sheet, which keeps OPanel's own choices: whose site it is, and
// a wildcard over this server's DNS, a certificate already on the server,
// or one pasted in.
export default function WebsitesPage() {
  const t = useT();
  const {
    adminEmail, canManageUsers, createSslCerts, createSslForm, createSslMode, createWordPress, currentUser, domain, error,
    installSslAfterCreate, installWordPress, loadCreateSslCerts, loading, phpVersion, phpVersionOptions, phpVersions,
    refreshAll, renderWildcardProvider, setAdminEmail, setCreateSslForm, setCreateSslMode, setDomain, setInstallSslAfterCreate,
    setInstallWordPress, setPhpVersion, setShowCreateSite, setSiteOwnerId, setSiteType, setWebsiteSearch, setWpAdminPassword,
    setWpAdminUser, showCreateSite, siteOwnerId, siteType, users, websiteSearch, websiteUrl, websites, wildcardProvider,
    wpAdminPassword, wpAdminUser,
  } = usePanel();
  const ownerOf = useSiteOwners();
  const openSite = useOpenSite();
  const siteMenu = useSiteMenu();
  const [createTried, setCreateTried] = useState(false);
  // Creating a WordPress site takes a minute or more. The page's own "Creating..."
  // line and any error sit behind this sheet, so the sheet says both itself:
  // without that, Create looked like it did nothing (operator, 2026-10-22).
  const [submitting, setSubmitting] = useState(false);
  const [createFailed, setCreateFailed] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  // The Dashboard's "New website" tile lands here with the sheet open.
  const creating = !!showCreateSite;
  function closeCreate() {
    setShowCreateSite(false);
    setCreateTried(false);
    setCreateFailed(false);
  }

  const query = websiteSearch.trim().toLowerCase();
  const visibleWebsites = query
    ? websites.filter((site) => [site.domain, site.root_path, site.linux_user, ...(site.aliases || []).map((alias) => alias.domain)]
      .some((text) => String(text || '').toLowerCase().includes(query)))
    : websites;

  const wpFieldsEnabled = siteType === 'wordpress' && installWordPress;
  // What stops the site being created, said under its field when Create is
  // pressed: the domain alone (no scheme, no path).
  const cleanDomain = domain.trim();
  const createProblems = {
    domain: !cleanDomain ? t('Please enter a domain name.')
      : /\s|\/|:/.test(cleanDomain) || !cleanDomain.includes('.') ? t('Type the domain alone, such as example.com.') : '',
  };
  const sslChoice = installSslAfterCreate ? createSslMode : 'none';
  function chooseSsl(mode) {
    setInstallSslAfterCreate(mode !== 'none');
    if (mode !== 'none') setCreateSslMode(mode);
    if (mode === 'existing') loadCreateSslCerts();
  }
  const owners = canManageUsers ? users.filter((user) => user.id !== currentUser?.id && user.role !== 'admin') : [];

  async function submitCreate(event) {
    event.preventDefault();
    if (Object.values(createProblems).some(Boolean)) {
      setCreateTried(true);
      document.querySelector('#website-create-form [aria-invalid="true"]')?.focus();
      return;
    }
    setCreateTried(false);
    setCreateFailed(false);
    setSubmitting(true);
    let made = false;
    try { made = await createWordPress(); } finally { setSubmitting(false); }
    // Made: the sheet closes, and the list (refreshed) has the new site.
    if (made) { setDomain(''); closeCreate(); } else setCreateFailed(true);
  }

  async function refresh() {
    setRefreshing(true);
    try { await refreshAll(); } finally { setRefreshing(false); }
  }

  const wildcardVia = wildcardProvider(createSslForm.provider);
  function renderCreate() {
    return <Drawer open={creating} onClose={closeCreate}
      title={websites.length ? t('Create website') : t('Attach first domain')}
      subtitle={websites.length ? null : t('This creates the first hosted site for the current account.')}
      footer={<>
        {createTried && Object.values(createProblems).some(Boolean) && <span className="sn-drawer-foot-note" role="alert">{t('Some fields need attention.')}</span>}
        {submitting && <span className="sn-drawer-foot-note is-progress" role="status">{loading || t('Creating website...')}</span>}
        {!submitting && createFailed && error && <span className="sn-drawer-foot-note" role="alert">{error}</span>}
        <button type="button" className="secondary-light" onClick={closeCreate}>{t('Cancel')}</button>
        <button type="submit" form="website-create-form" disabled={!!loading || submitting}>
          {submitting ? <RefreshCw size={15} className="spin" aria-hidden="true"/> : <Plus size={15} aria-hidden="true"/>} {submitting ? t('Creating...') : t('Create website')}</button>
      </>}>
      <form id="website-create-form" noValidate onSubmit={submitCreate}>
        <Field label={t('Domain')} required error={createTried ? createProblems.domain : ''}>
          <input value={domain} onChange={(e) => setDomain(e.target.value)} placeholder={t('e.g. {example}', { example: 'domain.com' })} autoCapitalize="none" spellCheck={false}/>
        </Field>
        {owners.length > 0 && <Field label={t('Owner')}>
          <select value={siteOwnerId} onChange={(e) => setSiteOwnerId(e.target.value)}>
            <option value="">{t('For yourself')}</option>
            {owners.map((user) => <option key={user.id} value={user.id}>{user.username}</option>)}
          </select>
        </Field>}
        <div className="sn-drawer-grid">
          <Field label={t('Website mode')}>
            <select value={siteType} onChange={(e) => setSiteType(e.target.value)}>
              <option value="wordpress">{t('WordPress')}</option>
              <option value="php">{t('PHP')}</option>
            </select>
          </Field>
          <Field label={t('PHP version')}>
            <select value={phpVersion} onChange={(e) => setPhpVersion(e.target.value)}>
              {phpVersionOptions(phpVersions.installed, phpVersion).map((v) => <option key={v} value={v}>PHP {v}</option>)}
            </select>
          </Field>
        </div>
        {siteType === 'wordpress' && <Checkbox checked={installWordPress} onChange={setInstallWordPress}
          label={t('Install WordPress (creates database, downloads WP, configures vhost)')}/>}
        {wpFieldsEnabled && <div className="sn-drawer-section">
          <h3>{t('WordPress administrator')}</h3>
          <div className="sn-drawer-grid">
            <Field label={t('Admin user')}><input value={wpAdminUser} onChange={(e) => setWpAdminUser(e.target.value)} placeholder={t('WP admin user')} autoComplete="off"/></Field>
            <Field label={t('Admin email')} optional><input type="email" value={adminEmail} onChange={(e) => setAdminEmail(e.target.value)} placeholder={t('e.g. {example}', { example: 'admin@domain.com' })}/></Field>
            <Field className="is-wide" label={t('Admin password')} optional hint={t('Leave blank for a random one')}>
              <PasswordField value={wpAdminPassword} onChange={setWpAdminPassword} generate copy/>
            </Field>
          </div>
        </div>}
        <div className="ws-create-ssl" role="group" aria-labelledby="ws-create-ssl-label">
          <span className="sn-field-label" id="ws-create-ssl-label"><span className="sn-field-label-text">{t('SSL after creating')}</span></span>
          <div className="segmented ssl-mode-tabs">
            {[['none', t('Off'), null], ['letsencrypt', t('Let\'s Encrypt'), Lock], ['wildcard', t('Wildcard'), Globe],
              ['existing', t('Use existing'), RefreshCw], ['manual', t('Manual'), KeyRound]].map(([mode, label, Icon]) => <button
              key={mode} type="button" aria-pressed={sslChoice === mode} className={sslChoice === mode ? 'active' : ''}
              onClick={() => chooseSsl(mode)}>{Icon && <Icon size={13} aria-hidden="true"/>} {label}</button>)}
          </div>
          {sslChoice === 'letsencrypt' && <p className="hint">{t('A certificate is issued right after the site is created — the domain must already point to this server.')}</p>}
          {sslChoice === 'wildcard' && <>
            {renderWildcardProvider(wildcardVia, (provider) => setCreateSslForm((prev) => ({ ...prev, provider })))}
            {wildcardVia === 'opanel'
              ? <p className="hint">{t('The challenge record goes into this server\'s zone for the domain, created with the website. The domain\'s nameservers must point at this server. One cert for the domain and *.domain; renewals need nothing.')}</p>
              : <>
                <p className="hint">{t('Issues {names} over Cloudflare DNS. Needs an API token with {permission} for the zone.', { names: <code>domain + *.domain</code>, permission: <strong>{t('Zone → DNS → Edit')}</strong> })}</p>
                <Field label={t('Cloudflare API token')} required>
                  <PasswordField value={createSslForm.api_token} onChange={(value) => setCreateSslForm((prev) => ({ ...prev, api_token: value }))} autoComplete="off"/>
                </Field>
              </>}
            <Field label={t('Contact email')} optional>
              <input type="email" autoComplete="off" value={createSslForm.email} onChange={(e) => setCreateSslForm((prev) => ({ ...prev, email: e.target.value }))}/>
            </Field>
          </>}
          {sslChoice === 'existing' && <>
            <div className="ws-create-certs">
              <select value={createSslForm.reuse_name} onChange={(e) => setCreateSslForm((prev) => ({ ...prev, reuse_name: e.target.value }))} aria-label={t('Certificate')}>
                <option value="">{t('— pick a certificate on this server —')}</option>
                {createSslCerts.map((cert) => <option key={cert.name} value={cert.name}>{cert.domains.join(', ')} ({cert.source})</option>)}
              </select>
              <button className="secondary-light" type="button" onClick={loadCreateSslCerts}><RefreshCw size={13} aria-hidden="true"/> {t('Refresh')}</button>
            </div>
            <p className="hint">{!cleanDomain
              ? t('Type the domain above first — this list shows only certificates that cover it.')
              : createSslCerts.length === 0
                ? t('No certificate on this server covers {domain}. A *.example.com wildcard covers x.example.com but not example.com itself.', { domain: cleanDomain.toLowerCase() })
                : t('A *.example.com wildcard issued earlier covers every x.example.com.')}</p>
          </>}
          {sslChoice === 'manual' && <>
            <Field label={t('Certificate')}><textarea rows={4} placeholder="-----BEGIN CERTIFICATE-----" value={createSslForm.certificate} onChange={(e) => setCreateSslForm((prev) => ({ ...prev, certificate: e.target.value }))}/></Field>
            <Field label={t('Private key')}><textarea rows={4} placeholder="-----BEGIN PRIVATE KEY-----" value={createSslForm.private_key} onChange={(e) => setCreateSslForm((prev) => ({ ...prev, private_key: e.target.value }))}/></Field>
            <Field label={t('CA bundle')} optional><textarea rows={3} value={createSslForm.ca_bundle} onChange={(e) => setCreateSslForm((prev) => ({ ...prev, ca_bundle: e.target.value }))}/></Field>
          </>}
        </div>
        <p className="hint ws-create-hint">{wpFieldsEnabled
          ? t('WordPress will be installed and the panel will show the URL, admin account, and password after creation.')
          : t('A virtual host will be created with public_html/ folder. Upload your PHP, HTML, or static files via File Manager.')}</p>
      </form>
    </Drawer>;
  }

  return <>
    <PageHeader
      subtitle={query ? t('{count} result(s)', { count: visibleWebsites.length }) : t('{count} website(s)', { count: websites.length })}
      onRefresh={refresh} refreshing={refreshing} refreshDisabled={!!loading}
      actions={<button type="button" onClick={() => setShowCreateSite(true)}><Plus size={15} aria-hidden="true"/> {t('New website')}</button>}/>
    <section className="section ws-list-section">
      {websites.length > 0 && <div className="website-search">
        <Search size={16} aria-hidden="true"/>
        <input value={websiteSearch} onChange={(e) => setWebsiteSearch(e.target.value)}
          placeholder={t('Search domain, alias, path, or Linux user')} aria-label={t('Search websites')}/>
        {websiteSearch && <button className="secondary-light icon-button" type="button" onClick={() => setWebsiteSearch('')} aria-label={t('Clear website search')} title={t('Clear search')}><X size={15} aria-hidden="true"/></button>}
      </div>}
      {visibleWebsites.length === 0 && (query
        ? <EmptyState icon={Search} message={t('No websites match this search.')}
          action={{ label: t('Clear search'), icon: X, secondary: true, onClick: () => setWebsiteSearch('') }}/>
        : <EmptyState icon={Globe} title={t('No websites yet.')} message={t('Attach a domain to host its first site.')}
          action={{ label: t('New website'), icon: Plus, onClick: () => setShowCreateSite(true) }}/>)}
      {visibleWebsites.length > 0 && <ul className="ws-list" aria-label={t('Websites')}>
        {visibleWebsites.map((site) => {
          const owner = ownerOf(site);
          const aliasCount = (site.aliases || []).length;
          return <li className="ws-card" key={site.id}>
            <div className="ws-card-main">
              <a className="ws-domain" href={websiteRoute(site.domain)}
                onClick={(event) => { if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); openSite(site); } }}>
                <Globe size={15} aria-hidden="true"/>{site.domain}
              </a>
              <a className="ws-visit" href={websiteUrl(site)} target="_blank" rel="noopener noreferrer"
                title={t('Open {domain} in a new tab', { domain: site.domain })} aria-label={t('Open {domain} in a new tab', { domain: site.domain })}><ExternalLink size={13} aria-hidden="true"/></a>
              <small className="ws-path">{site.root_path}</small>
            </div>
            <dl className="ws-facts">
              <div><dt>{t('Type')}</dt><dd>{siteTypeLabel(site, t)}</dd></div>
              {site.app_type !== 'static' && <div><dt>PHP</dt><dd>{site.php_version}</dd></div>}
              {canManageUsers && <div><dt>{t('Owner')}</dt><dd>{owner || `#${site.owner_id}`}</dd></div>}
              <div><dt>SSL</dt><dd><span className={`badge ${site.ssl_enabled ? 'ok' : ''}`}>{sslLabel(site, t)}</span></dd></div>
            </dl>
            <div className="ws-tags">
              {site.waf_enabled && <span className="badge ok">WAF</span>}
              {/* Its type says WordPress already where it is one (operator, 2026-10-19). */}
              {site.wp_installed && (site.app_type || 'wordpress') !== 'wordpress' && <span className="badge">WordPress</span>}
              {aliasCount > 0 && <span className="badge">{t('{count} extra domain(s)', { count: aliasCount })}</span>}
            </div>
            {/* Every action an icon, one click each (operator, 2026-10-19:
                the "…" menu cost a click every time). */}
            <IconActions className="ws-actions" label={t('Website actions for {domain}', { domain: site.domain })} items={[
              { label: t('Manage'), icon: SettingsIcon, primary: true, disabled: !!loading, onClick: () => openSite(site) },
              ...siteMenu(site, { inList: true }),
            ]}/>
          </li>;
        })}
      </ul>}
    </section>
    {renderCreate()}
    <WordPressInstallDrawer/>
  </>;
}
