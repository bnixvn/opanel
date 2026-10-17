// The Dashboard's tiles, as SNPanel has them (lib/features.jsx there): one
// per feature, each with its picture (icons/feature), its label, where it
// goes and who sees it - on OPanel's pages.
const msg = (text) => text;

// A tile: label, icon (a feature icon's name), page, and maybe
//   before   - name of an App action run first: the tab or form it opens on
//   href     - a page outside the panel (a new tab), from the navigation facts
//   roles    - who sees it (default: everyone the page lets in)
//   addon    - shown only while this addon is installed
//   when     - one more condition, on the navigation's facts
//   labels   - { reseller } a label of its own for that role
export const TILES = {
  // Accounts
  users: { label: msg('Panel users'), labels: { reseller: msg('Customers') }, icon: 'users', page: 'users', before: 'usersList', roles: ['admin', 'reseller'] },
  'user-add': { label: msg('Add user'), labels: { reseller: msg('Add customer') }, icon: 'user-add', page: 'users', before: 'usersAdd', roles: ['admin', 'reseller'] },
  packages: { label: msg('Packages'), icon: 'packages', page: 'users', before: 'usersPackages', roles: ['admin', 'reseller'] },
  // Websites and domains
  websites: { label: msg('Websites'), icon: 'websites', page: 'websites' },
  'website-add': { label: msg('New website'), icon: 'website-add', page: 'websites', before: 'newWebsite' },
  ssl: { label: msg('SSL'), icon: 'ssl', page: 'ssl' },
  dns: { label: msg('DNS Manager'), icon: 'dns', page: 'dns', addon: 'dns' },
  // Email
  mailboxes: { label: msg('Mailboxes'), icon: 'mail', page: 'mail', before: 'mailMailboxes', addon: 'mail' },
  forwarders: { label: msg('Forwarders'), icon: 'forwarders', page: 'mail', before: 'mailForwarders', addon: 'mail' },
  webmail: { label: msg('Webmail'), icon: 'webmail', href: (c) => c.webmailUrl, addon: 'mail', when: (c) => !!c.webmailUrl },
  'mail-relay': { label: msg('SMTP relay'), icon: 'mail-relay', page: 'mail', before: 'mailRelay', addon: 'mail', roles: ['admin'] },
  'mail-server': { label: msg('Mail server'), icon: 'mail-log', page: 'mail', before: 'mailServer', addon: 'mail', roles: ['admin'] },
  // Databases
  databases: { label: msg('Databases'), icon: 'databases', page: 'databases' },
  'database-add': { label: msg('New database'), icon: 'database-add', page: 'databases', before: 'newDatabase' },
  phpmyadmin: { label: 'phpMyAdmin', icon: 'phpmyadmin', page: 'databases' },
  // Files and backups
  files: { label: msg('File manager'), icon: 'files', page: 'files' },
  sftp: { label: msg('SFTP accounts'), icon: 'sftp', page: 'sftp' },
  terminal: { label: msg('Terminal'), icon: 'terminal', page: 'terminal', when: (c) => c.terminal },
  git: { label: msg('Git'), icon: 'git', page: 'git', addon: 'git' },
  wordpress: { label: msg('WordPress Manager'), icon: 'wordpress', page: 'wordpress', addon: 'wordpress' },
  backups: { label: msg('Backup & Restore'), icon: 'backups', page: 'backups', before: 'backupsWebsite' },
  // Security
  firewall: { label: msg('Firewall'), icon: 'firewall', page: 'firewall', roles: ['admin'] },
  waf: { label: 'WAF', icon: 'waf', page: 'waf' },
  'access-logs': { label: msg('Access logs'), icon: 'access-logs', page: 'wafLogs' },
  fail2ban: { label: msg('Fail2ban'), icon: 'fail2ban', page: 'firewall', addon: 'fail2ban', roles: ['admin'] },
  malware: { label: msg('Malware Scanner'), icon: 'malware', page: 'malware', addon: 'malware', roles: ['admin'] },
  security: { label: msg('Account security'), icon: 'account-security', page: 'security' },
  // Server
  php: { label: msg('Web server & PHP'), icon: 'php', page: 'php', roles: ['admin'] },
  services: { label: msg('Services'), icon: 'services', page: 'services', roles: ['admin'] },
  processes: { label: msg('Processes'), icon: 'processes', page: 'processes', roles: ['admin'] },
  ram: { label: 'RAM', icon: 'ram', page: 'ramUsage', roles: ['admin'] },
  disk: { label: msg('Disk'), icon: 'disk', page: 'diskUsage', roles: ['admin'] },
  traffic: { label: msg('Network traffic'), icon: 'traffic', page: 'traffic', roles: ['admin'] },
  'account-resources': { label: msg('Account resources'), icon: 'resource-usage', page: 'usage', roles: ['admin'], when: (c) => c.limits },
  updates: { label: msg('Updates'), icon: 'updates', page: 'updates', roles: ['admin'] },
  'resource-usage': { label: msg('Resource usage'), icon: 'resource-usage', page: 'usage', roles: ['reseller', 'customer'], when: (c) => c.limits },
  // Advanced
  cron: { label: msg('Cron'), icon: 'cron', page: 'cron' },
  mcp: { label: msg('AI assistants'), icon: 'mcp', page: 'mcp', addon: 'mcp' },
  notifications: { label: msg('Notifications'), icon: 'notifications', page: 'notifications', addon: 'notifications', roles: ['admin'] },
  'panel-settings': { label: msg('Panel settings'), icon: 'panel-settings', page: 'settings', roles: ['admin'] },
  // Migration: DirectAdmin archives are restored from Backup & Restore.
  'da-import': { label: msg('DirectAdmin Import'), icon: 'directadmin-import', page: 'backups', before: 'backupsRestore', roles: ['admin'] },
  // Extensions
  addons: { label: msg('Manage addons'), icon: 'addons', page: 'addons', roles: ['admin'] },
};

// The Dashboard's sections, in order, for each role.
const ADMIN = [
  ['accounts', msg('Accounts'), ['users', 'user-add', 'packages']],
  ['websites', msg('Websites and domains'), ['websites', 'website-add', 'ssl', 'dns', 'wordpress']],
  ['email', msg('Email'), ['mailboxes', 'forwarders', 'webmail', 'mail-relay', 'mail-server']],
  ['databases', msg('Databases'), ['databases', 'database-add', 'phpmyadmin']],
  ['files', msg('Files and backups'), ['files', 'sftp', 'git', 'backups']],
  ['security', msg('Security'), ['firewall', 'waf', 'access-logs', 'fail2ban', 'malware', 'security']],
  ['server', msg('Server'), ['php', 'services', 'processes', 'ram', 'disk', 'traffic', 'account-resources', 'updates']],
  ['advanced', msg('Advanced'), ['cron', 'mcp', 'notifications', 'panel-settings']],
  ['migration', msg('Migration'), ['da-import']],
  ['extensions', msg('Extensions'), ['addons']],
];
const CUSTOMER = [
  ['websites', msg('Websites and domains'), ['websites', 'website-add', 'ssl', 'dns', 'wordpress']],
  ['email', msg('Email'), ['mailboxes', 'forwarders', 'webmail']],
  ['databases', msg('Databases'), ['databases', 'database-add', 'phpmyadmin']],
  ['files', msg('Files and backups'), ['files', 'sftp', 'terminal', 'git', 'backups']],
  ['security', msg('Security'), ['security', 'waf', 'access-logs']],
  ['advanced', msg('Advanced'), ['cron', 'mcp', 'resource-usage']],
];
const RESELLER = [
  ['customers', msg('Customers'), ['users', 'user-add', 'packages']],
  ...CUSTOMER,
];

export function dashboardSections(ctx) {
  return ctx.isAdmin ? ADMIN : ctx.isReseller ? RESELLER : CUSTOMER;
}

// Whether a tile is shown to this role, with this server's addons.
export function tileVisible(key, ctx) {
  const tile = TILES[key];
  if (!tile) return false;
  const role = ctx.isAdmin ? 'admin' : ctx.isReseller ? 'reseller' : 'customer';
  if (tile.roles && !tile.roles.includes(role)) return false;
  if (tile.addon && !ctx.installed(tile.addon)) return false;
  if (tile.when && !tile.when(ctx)) return false;
  return true;
}

export const tileLabel = (tile, ctx) => (ctx.isReseller && tile.labels?.reseller) || tile.label;
