// Each OPanel addon with a page of its own: the page, its name in the
// panel's language and its feature icon (icons/feature), as SNPanel lists
// its own (lib/addons.jsx there). An addon not listed opens Addons.
const msg = (text) => text;

export const ADDON_META = {
  mail: { page: 'mail', label: msg('Email'), featureIcon: 'mail' },
  dns: { page: 'dns', label: msg('DNS Manager'), featureIcon: 'dns' },
  fail2ban: { page: 'firewall', label: msg('Fail2ban'), featureIcon: 'fail2ban' },
  malware: { page: 'malware', label: msg('Malware Scanner'), featureIcon: 'malware' },
  mcp: { page: 'mcp', label: msg('AI assistants (MCP)'), featureIcon: 'mcp' },
  notifications: { page: 'notifications', label: msg('Notifications'), featureIcon: 'notifications' },
  limits: { page: 'usage', label: msg('Resource limits'), featureIcon: 'resource-usage' },
  git: { page: 'git', label: msg('Git'), featureIcon: 'git' },
  wordpress: { page: 'wordpress', label: msg('WordPress Manager'), featureIcon: 'wordpress' },
  demo: { page: 'addons', label: msg('Demo mode'), featureIcon: 'account' },
  directadmin: { page: 'importDirectadmin', label: msg('DirectAdmin Import'), featureIcon: 'directadmin-import' },
  cpanel: { page: 'importCpanel', label: msg('cPanel Import'), featureIcon: 'cpanel-import' },
};

export const addonPage = (slug) => ADDON_META[slug]?.page || 'addons';
export const addonFeatureIcon = (slug) => ADDON_META[slug]?.featureIcon || 'addons';
// The addon's name to show: the translated label, else the server's name.
export const addonLabel = (addon, t) => (ADDON_META[addon.slug]?.label ? t(ADDON_META[addon.slug].label) : addon.name);
