// What needs attention, worst first: the header's bell (operator, 2026-10-18:
// "Needs attention làm dạng chuông thông báo ở header bên phải"). Each item
// says what is wrong and names the page that fixes it.
//
//   attentionItems({ summary, updates, isAdmin, currentUser, storageLimitBytes, formatPercent, t })
//
// `summary` is /dashboard/summary, `updates` /dashboard/updates (admins).

export const TONE_RANK = { bad: 0, warn: 1, info: 2 };

export function attentionItems({ summary, updates, isAdmin, currentUser, storageLimitBytes, formatPercent, t }) {
  const items = [];
  const need = (tone, key, text, page, action, before) => items.push({ tone, key, text, page, action, before });
  const sites = summary?.websites || {};
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
  if (!isAdmin && currentUser && storageLimitBytes) {
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
  return items.sort((a, b) => TONE_RANK[a.tone] - TONE_RANK[b.tone]);
}
