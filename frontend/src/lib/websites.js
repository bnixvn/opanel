// One website's page has an address per tab: /website/<domain>[/<tab>] -
// the overview has no tab of its own in the address (as SNPanel's).
export const websiteRoute = (domain, tab = '') =>
  `/website/${encodeURIComponent(String(domain || '').trim().toLowerCase())}${tab && tab !== 'overview' ? `/${tab}` : ''}`;

export function websiteFromPathname(pathname) {
  const found = /^\/website\/([^/]+)(?:\/([A-Za-z-]+))?\/?$/.exec(pathname || '');
  if (!found) return null;
  let domain = found[1];
  try { domain = decodeURIComponent(domain); } catch { /* the raw text, then */ }
  return { domain: domain.toLowerCase(), tab: (found[2] || 'overview').toLowerCase() };
}

export const isWebsitePath = (pathname) => !!websiteFromPathname(pathname);

// What a website runs: the API's app_type and the words for it.
export const WEBSITE_MODES = [
  ['wordpress', 'WordPress'],
  ['php', 'PHP'],
  ['static', 'Static'],
];
