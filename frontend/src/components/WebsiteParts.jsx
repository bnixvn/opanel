// What the website list and one website's page share: the words for a
// site's type and owner, its "…" menu, and the WordPress installer (as
// SNPanel's components/WebsiteParts.jsx).
import { useEffect } from 'react';
import { Download, ExternalLink, FileText, FolderOpen, Lock, RefreshCw, TerminalIcon, Trash2 } from 'lucide-react';
import { WEBSITE_MODES, websiteRoute } from '../lib/websites.js';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import Drawer from './Drawer.jsx';
import { Field, PasswordField } from './Form.jsx';

// "WordPress", "PHP", "Static" - not the API's wordpress/php/static.
const TYPE_LABELS = Object.fromEntries(WEBSITE_MODES);
export const siteTypeLabel = (site, t) => t(TYPE_LABELS[site?.app_type || 'wordpress'] || site?.app_type || '');

// What a site's certificate is, in a word or two.
export function sslLabel(site, t) {
  if (!site?.ssl_enabled) return t('No SSL');
  if (site.ssl_mode === 'manual') return t('Manual SSL');
  if (site.ssl_mode === 'reuse') return t('Shared cert');
  if (site.ssl_wildcard) return t('Wildcard');
  return t('SSL OK');
}

// Whose a site is, by name: the accounts an administrator or a reseller
// manages are loaded once for it (the API's list has the owner's id only).
export function useSiteOwners() {
  const { canManageUsers, currentUser, loadUsers, users } = usePanel();
  useEffect(() => {
    if (canManageUsers && users.length === 0) loadUsers();
  }, [canManageUsers]);
  return (site) => {
    if (!canManageUsers || !site) return '';
    if (currentUser && String(site.owner_id) === String(currentUser.id)) return currentUser.username;
    return users.find((user) => String(user.id) === String(site.owner_id))?.username || '';
  };
}

// Opens one website's page, at one of its tabs.
export function useOpenSite() {
  const { navigateToPage } = usePanel();
  return (site, tab = '', hash = '') => navigateToPage('website-detail', { path: websiteRoute(site.domain, tab), hash });
}

// A site's other actions, for its "…" menu: open it, its files, its SSL and
// logs, a terminal, WordPress, and - apart, at the end - delete it.
// `onDeleted` runs after a delete went through.
export function useSiteMenu({ onDeleted } = {}) {
  const t = useT();
  const { deleteWebsite, loading, openWebsiteFileManager, openWpInstall, updateWordPressAll, websiteUrl } = usePanel();
  const openSite = useOpenSite();
  return (site, { inDetail = false } = {}) => [
    { label: t('Open website'), icon: ExternalLink, href: websiteUrl(site), external: true, hidden: inDetail },
    { label: t('File manager'), icon: FolderOpen, onClick: () => openWebsiteFileManager(site) },
    { label: t('SSL'), icon: Lock, onClick: () => openSite(site, 'ssl'), hidden: inDetail },
    { label: t('Logs'), icon: FileText, onClick: () => openSite(site, 'logs') },
    { label: t('Terminal'), icon: TerminalIcon, onClick: () => openSite(site, 'advanced', 'terminal') },
    site.app_type === 'static' ? null : site.wp_installed
      ? { label: t('Update WordPress'), hint: t('Core, plugins and themes'), icon: RefreshCw, disabled: !!loading, onClick: () => updateWordPressAll(site) }
      : { label: t('Install WordPress'), icon: Download, disabled: !!loading, onClick: () => openWpInstall(site) },
    'separator',
    { label: t('Delete website'), icon: Trash2, danger: true, disabled: !!loading, onClick: async () => {
      if (await deleteWebsite(site.id)) onDeleted?.(site);
    } },
  ];
}

// The WordPress installer for one site, in a side sheet: opened by
// openWpInstall(site), from the list's menu or the site's page.
export function WordPressInstallDrawer() {
  const t = useT();
  const { installWpOnSite, loading, setWpInstallForm, setWpManagerSite, wpInstallForm, wpManagerMode, wpManagerSite } = usePanel();
  const site = wpManagerMode === 'install' ? wpManagerSite : null;
  const w = wpInstallForm || {};
  const tried = !!w.tried;
  const set = (key) => (value) => setWpInstallForm((prev) => ({ ...prev, [key]: value }));
  const problems = {
    admin_user: !String(w.admin_user || '').trim() ? t('Type the admin user name.') : '',
    admin_email: !String(w.admin_email || '').trim() ? t('Type the admin e-mail address.') : '',
    admin_password: String(w.admin_password || '').length < 12 ? t('At least 12 characters.') : '',
  };
  function submit(event) {
    event.preventDefault();
    if (Object.values(problems).some(Boolean)) { setWpInstallForm((prev) => ({ ...prev, tried: true })); return; }
    installWpOnSite();
  }
  const close = () => setWpManagerSite(null);
  return <Drawer open={!!site} onClose={close}
    title={site ? t('Install WordPress - {domain}', { domain: site.domain }) : ''}
    subtitle={site ? t('PHP {version}. A database is created for it, and its files go in public_html/.', { version: site.php_version }) : ''}
    footer={<>
      <button type="button" className="secondary-light" onClick={close}>{t('Cancel')}</button>
      <button type="submit" form="wp-install-form" disabled={!!loading}><Download size={14} aria-hidden="true"/> {t('Install WordPress')}</button>
    </>}>
    {site && <form id="wp-install-form" noValidate onSubmit={submit}>
      <Field label={t('Site title')} optional>
        <input value={w.title || ''} onChange={(e) => set('title')(e.target.value)} disabled={!!loading}/>
      </Field>
      <Field label={t('Admin user')} required error={tried ? problems.admin_user : ''}>
        <input value={w.admin_user || ''} onChange={(e) => set('admin_user')(e.target.value)} disabled={!!loading} autoComplete="off"/>
      </Field>
      <Field label={t('Admin email')} required error={tried ? problems.admin_email : ''}>
        <input type="email" value={w.admin_email || ''} onChange={(e) => set('admin_email')(e.target.value)} disabled={!!loading}/>
      </Field>
      <Field label={t('Admin password')} required hint={t('At least 12 characters.')} error={tried ? problems.admin_password : ''}>
        <PasswordField value={w.admin_password || ''} onChange={set('admin_password')} disabled={!!loading} generate copy/>
      </Field>
    </form>}
  </Drawer>;
}
