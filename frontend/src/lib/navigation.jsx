// Where everything is, as SNPanel lays it out (operator, 2026-10-17: "Làm lại
// giao diện cho giống nó"): the sidebar holds a few places per role, and a
// place with several pages - a hub - shows them as tabs under one header.
// An addon gets no sidebar entry; it is reached from the Dashboard's tools.
//
// A page key is App's (PAGE_ROUTES there); a hub's key is either its first
// page (files, users) or an address of its own (server, security-center,
// config, account) that opens its first tab.
import { Archive, CircleUserRound, Database, FolderOpen, Globe, Home, Server, Settings, ShieldCheck, Users } from 'lucide-react';
import WordPressGlyph from '../icons/WordPressGlyph.jsx';

const msg = (text) => text;

// Every page's title; translated where it is shown.
export const PAGE_TITLES = {
  dashboard: msg('Dashboard'),
  websites: msg('Websites'),
  'website-detail': msg('Website'),
  ssl: msg('SSL'),
  databases: msg('Databases'),
  cron: msg('Cron'),
  files: msg('File manager'),
  sftp: msg('SFTP accounts'),
  terminal: msg('Terminal'),
  backups: msg('Backup & Restore'),
  users: msg('Panel users'),
  auditLog: msg('Activity log'),
  settings: msg('Panel settings'),
  security: msg('Account security'),
  malware: msg('Malware Scanner'),
  php: msg('Web server & PHP'),
  firewall: msg('Firewall'),
  waf: msg('WAF'),
  wafLogs: msg('Access logs'),
  updates: msg('Updates'),
  addons: msg('Addons'),
  mcp: msg('AI assistants (MCP)'),
  mail: msg('Email'),
  dns: msg('DNS Manager'),
  notifications: msg('Notifications'),
  services: msg('Services'),
  usage: msg('Resource usage'),
  git: msg('Git'),
  wordpress: msg('WordPress Manager'),
  processes: msg('Process monitor'),
  ramUsage: msg('RAM usage'),
  diskUsage: msg('Disk usage'),
  traffic: msg('Traffic'),
};

// The facts a role's navigation depends on, from App:
// { isAdmin, isReseller, limits, terminal, malware, notifications }.
//   limits        the Resource limits addon is installed
//   terminal      this session may open the browser terminal
//   malware       the Malware Scanner addon is on
//   notifications the Notifications addon is on

// The hubs: one header, the pages as its tabs. A tab's `also` are pages
// opened from inside it, which keep it lit.
export const HUBS = [
  { key: 'server', title: msg('Server'), roles: ['admin'], tabs: [
    { page: 'processes', label: msg('Processes') },
    // Every account's use against its limits (the Resource limits addon).
    { page: 'usage', label: msg('Account resources'), when: (c) => c.limits },
    { page: 'ramUsage', label: 'RAM' },
    { page: 'diskUsage', label: msg('Disk') },
    { page: 'traffic', label: msg('Traffic') },
    { page: 'php', label: msg('Web server & PHP') },
    { page: 'services', label: msg('Services') },
    { page: 'updates', label: msg('Updates') },
  ] },
  { key: 'security-center', title: msg('Security'), roles: ['admin'], tabs: [
    { page: 'firewall', label: msg('Firewall') },
    { page: 'waf', label: 'WAF' },
    { page: 'wafLogs', label: msg('Access logs') },
    { page: 'malware', label: msg('Malware Scanner'), when: (c) => c.malware },
  ] },
  { key: 'users', title: msg('Users & packages'), roles: ['admin'], tabs: [
    { page: 'users', label: msg('Panel users') },
    // Who did what in the panel (GET /users/audit/log).
    { page: 'auditLog', label: msg('Activity log') },
  ] },
  { key: 'config', title: msg('Settings'), roles: ['admin'], tabs: [
    { page: 'settings', label: msg('Panel settings') },
    { page: 'addons', label: msg('Addons') },
    { page: 'notifications', label: msg('Notifications'), when: (c) => c.notifications },
  ] },
  { key: 'files', title: msg('Files'), roles: ['admin', 'reseller', 'customer'], tabs: [
    { page: 'files', label: msg('File manager') },
    { page: 'sftp', label: 'SFTP' },
    // The browser terminal: a customer whose package has it (OPanel only).
    { page: 'terminal', label: msg('Terminal'), when: (c) => c.terminal },
  ] },
  { key: 'account', title: msg('Account'), roles: ['reseller', 'customer'], tabs: [
    { page: 'security', label: msg('Account security') },
    { page: 'usage', label: msg('Resource usage'), when: (c) => c.limits },
  ] },
];

const roleOf = (ctx) => (ctx.isAdmin ? 'admin' : ctx.isReseller ? 'reseller' : 'customer');
export const visibleTabs = (hub, ctx) => (hub.roles.includes(roleOf(ctx)) ? hub.tabs.filter((tab) => !tab.when || tab.when(ctx)) : []);
// A hub's own address, as opposed to one of its pages.
export const isHubKey = (page) => HUBS.some((hub) => hub.key === page && !hub.tabs.some((tab) => tab.page === page));

// The hub a page is a tab of, for this role, with its visible tabs; null
// when it is in none.
export function hubOf(page, ctx) {
  for (const hub of HUBS) {
    const tabs = visibleTabs(hub, ctx);
    const tab = tabs.find((item) => item.page === page || item.also?.includes(page));
    if (tab) return { hub, tabs, tab };
  }
  return null;
}

// The page a hub's address opens: its first tab this role may see.
export function hubLanding(key, ctx) {
  const hub = HUBS.find((item) => item.key === key);
  return hub ? visibleTabs(hub, ctx)[0]?.page || null : null;
}

// The sidebar of each role: [key, label, icon].
export function sidebarItems(ctx) {
  // The one addon in the sidebar, as SNPanel's: the last entry, while it is on.
  const wordpress = ctx.wordpress ? [['wordpress', msg('WordPress Manager'), WordPressGlyph]] : [];
  if (ctx.isAdmin) {
    return [
      ['dashboard', msg('Dashboard'), Home],
      ['websites', msg('Websites'), Globe],
      ['databases', msg('Databases'), Database],
      ['files', msg('Files'), FolderOpen],
      ['backups', msg('Backup & Restore'), Archive],
      ['users', msg('Users & packages'), Users],
      ['server', msg('Server'), Server],
      ['security-center', msg('Security'), ShieldCheck],
      ['config', msg('Settings'), Settings],
      ...wordpress,
    ];
  }
  if (ctx.isReseller) {
    return [
      ['dashboard', msg('Dashboard'), Home],
      ['users', msg('Customers & packages'), Users],
      ['websites', msg('Websites'), Globe],
      ['databases', msg('Databases'), Database],
      ['files', msg('Files'), FolderOpen],
      ['backups', msg('Backup & Restore'), Archive],
      ['account', msg('Account'), CircleUserRound],
      ...wordpress,
    ];
  }
  return [
    ['dashboard', msg('Dashboard'), Home],
    ['websites', msg('Websites'), Globe],
    ['databases', msg('Databases'), Database],
    ['files', msg('Files'), FolderOpen],
    ['backups', msg('Backup & Restore'), Archive],
    ['account', msg('Account'), CircleUserRound],
    ...wordpress,
  ];
}

// Pages that belong under a sidebar entry without being a tab of it.
const PARENTS = { ssl: 'websites', 'website-detail': 'websites' };

// The sidebar entry lit on a page: its hub's, its parent's, or the
// Dashboard's - every other page is reached from there.
export function sidebarKeyOf(page, ctx, items) {
  const keys = new Set(items.map(([key]) => key));
  const found = hubOf(page, ctx);
  if (found && keys.has(found.hub.key)) return found.hub.key;
  if (keys.has(page)) return page;
  if (PARENTS[page] && keys.has(PARENTS[page])) return PARENTS[page];
  return 'dashboard';
}
