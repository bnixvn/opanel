// SNPanel's feature icons: the large, multi-colour pictures of the panel's
// features (the Dashboard's tiles). Drawn for SNPanel: not traced from any
// other panel's icons, and no product's logo is among them.
//
// Each icon is drawn on a 48x48 grid as shapes separated by ';':
//   R <cls> x y w h [rx]   rectangle
//   C <cls> cx cy r        circle
//   E <cls> cx cy rx ry    ellipse
//   P <cls> <path data>    path
// <cls> is one or more classes joined by '.', from FeatureIcon.css. A colour
// letter fills: b blue, i indigo, v violet, t teal, c cyan, g green, r red,
// o orange-red, a amber, m magenta, s slate, k ink, p paper; a trailing 2 is
// the hue's soft tint. 'x' before a colour strokes instead (3 units, round
// ends, the set's one weight); 'j' rounds a filled shape's corners; 'd' dots
// a stroke.
// After a '#' comes the badge: drawn on top, with whatever lies under it cut
// away 2 units around it, so the two never touch.
//
// The grid: content inside 4..44; corners 3 on large shapes and 1.5-2 on
// small ones; a badge is a disc at (37,37), r 9. Flat colour, no gradients.
// Each section has its hue (REPORT 3.4): Website blue, Email orange-red,
// Databases indigo, Files and backups amber, Security green (red blocks),
// Server slate and cyan, Accounts violet, Advanced teal, Migration magenta,
// Addons all of them. Badges: green + adds, slate gear configures, slate
// magnifier looks, green check confirms, red ban blocks.

const n = (x) => Math.round(x * 10) / 10;

// ---- Shared parts
const globe = (x, y, r) => `C b2.xb ${x} ${y} ${r - 1.5};E xb ${x} ${y} ${n(r * 0.42)} ${r - 1.5};P xb M${x - r + 1.5} ${y}h${2 * r - 3}`;
const win = (c) => `R ${c} 4 6 40 36 3;R p 7 13 34 26 1.5;C p 9.5 9.5 1.4;C p 13.5 9.5 1.4;C p 17.5 9.5 1.4`;
const doc = (c) => `R ${c} 6 4 30 40 3;R p 9 7 24 34 1.5`;
const env = (c, x, y, w, h) => `R ${c} ${x} ${y} ${w} ${h} 3;P xp M${x + 4.5} ${y + 5}L${x + w / 2} ${n(y + h * 0.55)}l${w / 2 - 4.5} ${n(5 - h * 0.55)}`;
const person = (c, x) => `C ${c} ${x} 15 8;P ${c} M${x - 14} 42v-3a11 11 0 0 1 11-11h6a11 11 0 0 1 11 11v3z`;
const cyl = (c, x, w) => {
  const r = w / 2, k = n(r * 0.45);
  const arc = `c0 3.3 ${k} 6 ${r} 6s${r}-2.7 ${r}-6`;
  const back = `c0 3.3-${k} 6-${r} 6S${x} `;
  return `P ${c} M${x} 10v8${arc}v-8z;P ${c} M${x} 20.5v7.5${arc}v-7.5${back}23.8 ${x} 20.5z;P ${c} M${x} 31v7${arc}v-7${back}34.3 ${x} 31z;E ${c}2 ${x + r} 10 ${r} 6`;
};
const folder = 'P a M4 10a3 3 0 0 1 3-3h10.3a3 3 0 0 1 2.1.9L22 10.5h19a3 3 0 0 1 3 3V39a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3z';
const shield = (c) => `P ${c} M24 4l16 5.5v12.2c0 10-6.6 18.4-16 22.3C14.6 40.1 8 31.7 8 21.7V9.5z`;
const box = (c) => `R ${c} 4 9 36 9 2.5;R ${c}2 7 20 30 22 2.5;R ${c} 16 24 12 4 2`;
const layers = (c) => `P ${c} M22.3 6Q24 5 25.7 6L40.3 13.1Q42 14 40.3 14.9L25.7 22Q24 23 22.3 22L7.7 14.9Q6 14 7.7 13.1Z;P ${c}2 M11.7 19.2 6 22l18 9 18-9-5.7-2.8L24 25.3z;P ${c}2 M11.7 27.2 6 30l18 9 18-9-5.7-2.8L24 33.3z`;
const rack = (c, w) => `R s 4 4 ${w} 11 2.5;R s 4 18.5 ${w} 11 2.5;R s 4 33 ${w} 11 2.5;C ${c} 10 9.5 2.3;C ${c} 10 24 2.3;C ${c} 10 38.5 2.3;P xp M17 9.5h${w - 19}M17 24h${w - 19}M17 38.5h${w - 19}`;
const chevrons = 'P c M14.2 4.8Q15 4 15.8 4.8L17.7 6.7Q18.5 7.5 17.7 8.3L11.3 14.7Q10.5 15.5 11.3 16.3L17.7 22.7Q18.5 23.5 17.7 24.3L15.8 26.2Q15 27 14.2 26.2L4.3 16.3Q3.5 15.5 4.3 14.7Z;P c M32.2 4.8Q33 4 33.8 4.8L43.7 14.7Q44.5 15.5 43.7 16.3L33.8 26.2Q33 27 32.2 26.2L30.3 24.3Q29.5 23.5 30.3 22.7L36.7 16.3Q37.5 15.5 36.7 14.7L30.3 8.3Q29.5 7.5 30.3 6.7Z;P c.j M25.5 5h3.5l-6 21H19.5z';
const importBox = (arrow) => `P m2 M6 25l5-6h26l5 6z;P ${arrow} M20 4.5Q20 3 21.5 3L26.5 3Q28 3 28 4.5L28 14.5Q28 16 29.5 16L34.5 16Q36 16 35 17.1L25 27.9Q24 29 23 27.9L13 17.1Q12 16 13.5 16L18.5 16Q20 16 20 14.5Z;R m 6 25 36 19 3;R m2 18 30 12 4 2`;
const gauge = (a, b, c) => `P ${a} M4 34A20 20 0 0 1 12.2 17.8L16.9 24.3A12 12 0 0 0 12 34Z;P ${b} M14 16.7A20 20 0 0 1 34 16.7L30 23.6A12 12 0 0 0 18 23.6Z;P ${c} M35.8 17.8A20 20 0 0 1 44 34L36 34A12 12 0 0 0 31.1 24.3Z;P xs M24 34l7-10;C s 24 34 4.5`;
// A history arrow round a clock: the cron clock.
const historyRing = (c) => `P x${c} M5.8 20.8A18.5 18.5 0 1 0 13.4 8.9;P ${c}.j M7.2 16.2 10.8 5.2l5.2 7.3z`;

// ---- Badges (after the '#')
const PLUS = '#C g 37 37 9;P xp M37 32.5v9M32.5 37h9';
const CHECK = '#C g 37 37 9;P xp M32.8 37.3l2.9 2.9 5.5-5.6';
const GEAR = '#P s M34.6 30.8L34.9 28.2A9 9 0 0 1 39.1 28.2L39.4 30.8A6.6 6.6 0 0 1 41.1 31.9L43.5 30.8A9 9 0 0 1 45.6 34.4L43.5 36A6.6 6.6 0 0 1 43.5 38L45.6 39.6A9 9 0 0 1 43.5 43.2L41.1 42.1A6.6 6.6 0 0 1 39.4 43.2L39.1 45.8A9 9 0 0 1 34.9 45.8L34.6 43.2A6.6 6.6 0 0 1 32.9 42.1L30.5 43.2A9 9 0 0 1 28.4 39.6L30.5 38A6.6 6.6 0 0 1 30.5 36L28.4 34.4A9 9 0 0 1 30.5 30.8L32.9 31.9A6.6 6.6 0 0 1 34.6 30.8Z;C p 37 37 2.8';
const LOOK = '#C s 37 37 9;C xp 35.6 35.6 3.3;P xp M38.2 38.2l2.8 2.8';
const BOLT = '#C a 37 37 9;P p M38.6 30.8 32.6 38.6h4.2l-1.4 5.6 6-7.8h-4.2z';
const RESTORE = '#C b 37 37 9;P xp M33.6 39.8A4.4 4.4 0 1 0 33.9 33.9;P p.j M31.4 36.4 35.9 35.9 31.9 31.9z';
const BAN = '#C r 37 37 9;C xp 37 37 4.6;P xp M33.8 40.2l6.4-6.4';
const UP = '#C g 37 37 9;P xp M37 41.5v-9M33 36.5l4-4 4 4';
const ARROW = '#C b 37 37 9;P xp M32.5 37h9M37.5 33l4 4-4 4';

export const FEATURE_ICONS = {
  // ---- Overview
  dashboard: 'R b 31 7 5 10 1.5;R b2 9 20 30 23 2;P b.j M24 5 44 21H4z;P xb M17 37.5v-5M24 37.5V28M31 37.5v-7',

  // ---- Website and domains (blue)
  websites: `${win('b')};${globe(24, 26, 11)}`,
  'website-add': `${win('b')};${globe(24, 26, 11)}${PLUS}`,
  'website-detail': `${win('b')};P xb2 M12 20h20M12 26h20M12 32h12${GEAR}`,
  domains: `${globe(17, 17, 13)}#${globe(31, 31, 13)}`,
  subdomains: `${globe(24, 13, 10)};P xs M24 24v3.5M12 33v-5.5h24V33;R b 4 33 16 11 3;R b 28 33 16 11 3;P xp M9 38.5h6M33 38.5h6`,
  redirects: 'R b2 4 18 16 24 3;R b 28 18 16 24 3;P xb M8.5 25h7M8.5 31h7M8.5 37h4;P xp M32.5 25h7M32.5 31h7M32.5 37h4;P xo M12 13C13 3 35 3 36 13;P xo M31.5 9.5l4.5 4.5 4.5-4.5',
  ssl: `P s M13 22v-6a11 11 0 0 1 22 0v6h-5v-6a6 6 0 0 0-12 0v6z;R b 8 19 32 25 4;C p 24 29.5 3.5;R p 22.5 30 3 8 1.5${CHECK}`,
  dns: 'R s 22 4 4 38 2;R s 15 40 18 4 2;P b M8 8.5Q8 6 10.5 6L31.5 6Q34 6 35.8 7.8L39.2 11.2Q41 13 39.2 14.8L35.8 18.2Q34 20 31.5 20L10.5 20Q8 20 8 17.5Z;P b2 M12.2 25.8Q14 24 16.5 24L37.5 24Q40 24 40 26.5L40 35.5Q40 38 37.5 38L16.5 38Q14 38 12.2 36.2L8.8 32.8Q7 31 8.8 29.2Z;P xp M13.5 13h16;P xb M18.5 31h16',
  applications: 'P b2 M22.3 5Q24 4 25.7 5L39.3 12.5Q41 13.5 39.3 14.5L25.7 22Q24 23 22.3 22L8.7 14.5Q7 13.5 8.7 12.5Z;P b M6 19Q6 17 7.7 18L20.8 25.3Q22.5 26.3 22.5 28.3L22.5 43Q22.5 45 20.8 44L7.7 36.7Q6 35.7 6 33.7Z;P b M25.5 28.3Q25.5 26.3 27.2 25.3L40.3 18Q42 17 42 19L42 33.7Q42 35.7 40.3 36.7L27.2 44Q25.5 45 25.5 43Z;P xp M14 27v7',
  // The sidebar's glyph (icons/WordPressGlyph.jsx) at tile size: a bold
  // "W" in a ring. Drawn for SNPanel - not the WordPress logo.
  wordpress: 'C b2 24 24 20;C b 24 24 16;P xp M13 17l5.4 15L24 21l5.6 11L35 17',
  cache: `${doc('b')};P xb2 M13.5 14h15M13.5 20h15M13.5 26h9${BOLT}`,
  'site-logs': `${doc('b')};C b 14.5 14 2;C b 14.5 21 2;C b 14.5 28 2;P xb2 M19.5 14h9M19.5 21h9M19.5 28h6${LOOK}`,
  terminal: 'R s 4 6 40 36 3;R k 7 12 34 27 1.5;P xg M13 20l5 5-5 5;P xp M22 31h9',
  // OPanel's Git addon (2026-10-17): a branch leaving the main line, its
  // three commits - in the Files and backups section's amber.
  git: 'R a2 4 4 40 40 3;P xa M17 13v22;P xa M17 30c0-7 14-5 14-12;C a 17 12 4.5;C a 17 36 4.5;C a 31 16 4.5;C p 17 12 1.8;C p 17 36 1.8;C p 31 16 1.8',

  // ---- Email (orange-red)
  mail: env('o', 4, 9, 40, 30),
  'mailbox-add': env('o', 4, 7, 36, 27) + PLUS,
  forwarders: env('o', 4, 7, 36, 27) + ARROW,
  webmail: `R o2 4 4 36 30 3;R p 7 11 30 20 1.5;C p 9.5 7.5 1.3;C p 13.5 7.5 1.3;C p 17.5 7.5 1.3;P xo2 M11 16h12M11 21h8#${env('o', 18, 22, 28, 22)}`,
  'mail-log': `R o2 16 4 28 40 3;R p 19 7 22 34 1.5;P xo2 M23 13h14M23 19.5h14M32 26h5M32 32.5h5#${env('o', 4, 24, 24, 18)}`,
  'mail-relay': `R s 18 6 26 15 2.5;R s 18 26 26 15 2.5;C c 24 13.5 2.3;C c 24 33.5 2.3;P xp M31 13.5h8M31 33.5h8#${env('o', 4, 14, 22, 18)}`,
  'mail-apps': `R s 8 4 22 40 4;R p 11 9 16 27 1.5;C p 19 40 1.4#${env('o', 20, 22, 26, 20)}`,
  'catch-all': 'P o2 M4 27l5.6-13.1A3 3 0 0 1 12.4 12h23.2a3 3 0 0 1 2.8 1.9L44 27z;R o 14 4 20 15 3;P xp M17.5 8.5l6.5 5 6.5-5;P o M4 27h10l3 5h14l3-5h10v12a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3z',

  // ---- Databases (indigo)
  databases: cyl('i', 6, 36),
  'database-add': cyl('i', 4, 32) + PLUS,
  phpmyadmin: `R i 4 6 36 32 3;R p 7 14 30 21 1.5;P xi2 M10 20.5h24M10 27.5h24M19 17v15${LOOK}`,
  mysql: `${cyl('i', 4, 32)}#C s 37 37 9;P xp M32.5 39.5a4.5 4.5 0 0 1 9 0;P xp M37 39.5l2.3-3`,

  // ---- Files and backups (amber)
  files: `${folder};R p 9 13 30 16 1.5;R a2 4 18 40 24 3`,
  sftp: `${folder};R a2 4 18 40 24 3;C xs 16 30 4.5;P xs M20.5 30H34M30 30v4.5M34 30v3.5`,
  backups: box('a') + RESTORE,
  restore: 'R a 4 17 36 9 2.5;R a2 7 28 30 16 2.5;R a 16 31.5 12 4 2;P xb M35 13.5C35 4 22 2 13 7.5;P b.j M8.8 10.1 11.2 4.5l3.6 6z',
  'backup-schedule': `R a 4 7 34 33 3;R p 7 15 28 22 1.5;P xs M13 4.5v5M29 4.5v5;R a2 11 19 5 5 1;R a2 19 19 5 5 1;R a2 27 19 5 5 1;R a2 11 27 5 5 1#C b 37 37 9;R p 31.5 31.5 11 3.5 1;R p 32.5 36 9 6.5 1`,
  'backup-destinations': 'C b2 15 18 7;C b2 24 13 9;C b2 33 18 7;R b2 15 14 18 11;R a 4 29 40 15 3;C p 10 36.5 2;P xp M17 36.5h20',
  'incremental-backup': layers('a') + UP,

  // ---- Migration (magenta)
  migration: 'R s 4 20 12 24 2.5;R s 32 20 12 24 2.5;C c 10 26 2;C c 38 26 2;P xp M7.5 33h5M35.5 33h5;R m 17 25 14 14 2;R m2 22 25 4 14 0;P xm M10 15C14 6 34 6 38 15;P xm M33.5 11.5 38 16l4.5-4.5',
  'cpanel-import': importBox('o'),
  'directadmin-import': importBox('b'),

  // ---- Accounts (violet)
  users: `C v2 32 13 6.5;P v2 M27 23h7a10 10 0 0 1 10 10v4H27z#${person('v', 18)}`,
  'user-add': person('v', 18) + PLUS,
  resellers: 'C v 24 10 6;P v M14 25a8 8 0 0 1 8-8h4a8 8 0 0 1 8 8z;P xv2 M24 28v4M10 37v-5h28v5;C v2 10 39 5;C v2 24 39 5;C v2 38 39 5',
  customers: `${person('v', 18)}#P a.j M29 30.5a2 2 0 0 1 2-2h6l8.5 8.5-8 8-8.5-8.5z;C p 33 32.5 1.8`,
  packages: 'R v2 7 23 34 21 2.5;R v 4 14 40 9 2.5;R a 21 14 6 30;P a.j M24 13c-2-5-9-7.5-9-3s6.5 3 9 3zM24 13c2-5 9-7.5 9-3s-6.5 3-9 3z',
  'activity-log': 'R v 7 6 34 38 3;R p 10 11 28 30 1.5;R s 17 3 14 7 2;P xg M14 19l2.5 2.5 4-4M14 30l2.5 2.5 4-4;P xv2 M25 20h9M25 31h9',
  branding: 'P v2 M24 4C12.4 4 4 12.4 4 23c0 10.5 8.5 19 19 19 2.7 0 4-1.6 4-3.6 0-2.3-2-3.1-2-5.4s1.8-4 4.6-4H35c5.3 0 9-3.3 9-8.7C44 11.5 35.1 4 24 4z;C b 14 22 3.5;C a 19 13 3.5;C g 29 12 3.5;C r 36 19 3.5',
  'billing-api': `R v 4 8 36 26 3;R k 4 14 36 5;R a 9 24 8 6 1.5;P xv2 M22 27h12#C s 37 37 9;P xp M35 31.5v2.5M39 31.5v2.5;R p 32.5 34 9 5 2;P xp M37 39v3`,
  nameservers: `R v 4 6 32 13 2.5;R v 4 23 32 13 2.5;C p 10 12.5 2;C p 10 29.5 2;P xp M16 12.5h14M16 29.5h14#${globe(37, 37, 9)}`,
  account: 'R v 4 8 40 32 3;R p 7 11 34 26 1.5;C v2 16 20 4.5;P v2 M9.5 32a6.5 6.5 0 0 1 13 0z;P xv M27 19h9M27 25.5h9M27 32h5',

  // ---- Security (green; red blocks)
  security: `${shield('g')};P xp M17 23.5l5 5 9-9.5`,
  'account-security': 'P g M21.6 26.6Q20.5 25.5 21.6 24.4L24.4 21.6Q25.5 20.5 26.6 21.6L41.9 36.9Q43 38 41.9 39.1L39.1 41.9Q38 43 36.9 41.9Z;P g M31.2 36.2Q30.5 35.5 31.2 34.8L34.8 31.2Q35.5 30.5 36.2 31.2L38.3 33.3Q39 34 38.3 34.7L34.7 38.3Q34 39 33.3 38.3Z;C g 16 16 12;P xp M11.5 19.5v-3a4.5 4.5 0 0 1 9 0v4M8.5 14a8 8 0 0 1 15 0M16 16v5',
  'two-factor': `R s 10 4 24 40 5;R p 13 9 18 27 1.5;C p 22 40 1.6;C g 17 22 2;C g 22 22 2;C g 27 22 2${CHECK}`,
  waf: `${shield('g')};P p.j M15.5 14.5h17l-6.5 8v7l-4 2.5v-9.5z`,
  firewall: 'P o M24 3c6 5.5 11 10 11 16.5S31 27 24 27s-11-4-11-10.5c0-3.5 1.6-6 3.6-8 .5 2.8 1.8 4.6 3.6 5.4C19.6 9.4 21.3 5.6 24 3z;P a M24 12c3 3 5.5 6 5.5 9.5S27 27 24 27s-5.5-2-5.5-5.5c0-1.8.8-3.2 1.8-4.2.3 1.4.9 2.3 1.8 2.7-.2-3.3.6-5.4 1.9-8z;R r 4 19 12 7 1.5;R r 18 19 12 7 1.5;R r 32 19 12 7 1.5;R r 4 28 5 7 1.5;R r 11 28 12 7 1.5;R r 25 28 12 7 1.5;R r 39 28 5 7 1.5;R r 4 37 12 7 1.5;R r 18 37 12 7 1.5;R r 32 37 12 7 1.5',
  fail2ban: `${shield('g')};C p 24 17 4.5;P p M15.5 32a8.5 8.5 0 0 1 17 0z${BAN}`,
  malware: `P xs M12 20l-6-3M11.5 27.5H5M12.5 34.5 7 38M32 20l6-3M32.5 27.5H39M19.5 7.5 17 4.5M24.5 7.5 27 4.5;C r2 22 12 6;E r 22 26 10 12;P xp M22 17v18${LOOK}`,
  quarantine: `${box('g')};P xg M14 33.5h16#P a.j M37 29.5l8.5 14.5H28.5z;P xk M37 34.5v3.5;C k 37 41.3 1.4`,
  'access-logs': `${doc('g')};C g 14.5 14 2;C g 14.5 21 2;C g 14.5 28 2;P xg2 M19.5 14h9M19.5 21h9M19.5 28h6#P s.j M28 37c2.5-4.3 5.4-6.4 9-6.4s6.5 2.1 9 6.4c-2.5 4.3-5.4 6.4-9 6.4s-6.5-2.1-9-6.4z;C p 37 37 3.4;C s 37 37 1.5`,

  // ---- Server (slate and cyan)
  server: rack('c', 40),
  webstack: rack('c', 34) + BOLT,
  services: 'R s 6 24 36 9 2.5;R s 6 35 36 9 2.5;C c 12 28.5 2;C c 12 39.5 2;P xp M18 28.5h18M18 39.5h18;P xc M4 13h9l4-8 6 15 4-7h17',
  processes: 'P xs M17 5.5v5M24 5.5v5M31 5.5v5M17 37.5v5M24 37.5v5M31 37.5v5M5.5 17h5M5.5 24h5M5.5 31h5M37.5 17h5M37.5 24h5M37.5 31h5;R c 10 10 28 28 3;R c2 17 17 14 14 2',
  ram: 'R c 4 12 40 22 2.5;R k 8.5 16 6 10 1;R k 17 16 6 10 1;R k 25.5 16 6 10 1;R k 34 16 6 10 1;P xa M9 37.5v2M14 37.5v2M19 37.5v2M29 37.5v2M34 37.5v2M39 37.5v2',
  disk: 'R s 7 4 34 40 3;C c2 24 19 11;P c M24 8A11 11 0 0 1 33.5 24.5L24 19z;C p 24 36 2;P xp M14 36h4M30 36h4',
  traffic: 'P s M10.5 6.5Q10.5 5 12 5L16 5Q17.5 5 17.5 6.5L17.5 24.5Q17.5 26 19 26L22.5 26Q24 26 23.2 27.2L14.8 39.8Q14 41 13.2 39.8L4.8 27.2Q4 26 5.5 26L9 26Q10.5 26 10.5 24.5Z;P c M33.2 8.2Q34 7 34.8 8.2L43.2 20.8Q44 22 42.5 22L39 22Q37.5 22 37.5 23.5L37.5 41.5Q37.5 43 36 43L32 43Q30.5 43 30.5 41.5L30.5 23.5Q30.5 22 29 22L25.5 22Q24 22 24.8 20.8Z',
  statistics: 'R c2 5 22 10 20 2;R c 19 13 10 29 2;R s 33 5 10 37 2',
  updates: 'C c 24 24 20;P xp M14 24a10 10 0 0 1 17.07-7.07M31 11.5V17h-5.5;P xp M34 24a10 10 0 0 1-17.07 7.07M17 36.5V31h5.5',
  php: `${chevrons};R s2 4 34 40 7 3.5;R c 4 34 24 7 3.5;C c 28 37.5 6.5;C p 28 37.5 2.5`,
  'php-extensions': 'P c M10 12h6a5 5 0 0 1 10 0h6a3 3 0 0 1 3 3v7a5 5 0 0 1 0 10v7a3 3 0 0 1-3 3H10a3 3 0 0 1-3-3V15a3 3 0 0 1 3-3z;P xp M17 23l-3.5 3.5 3.5 3.5M25 23l3.5 3.5-3.5 3.5',
  'php-selector': `${chevrons};R s2 4 33 12 10 3;R c 18 33 12 10 3;R s2 32 33 12 10 3;P xp M21 38l2 2 3.5-3.5`,
  cloudlinux: 'R s 4 4 40 40 3;R p 7 7 34 34 1.5;P xs2 M15.5 7v34M24 7v34M32.5 7v34;P c M10 31A14 14 0 0 1 16.6 19.1L19.5 23.8A8.5 8.5 0 0 0 15.5 31Z;P c M17.4 18.6A14 14 0 0 1 30.6 18.6L28 23.5A8.5 8.5 0 0 0 20 23.5Z;P r M31.4 19.1A14 14 0 0 1 38 31L32.5 31A8.5 8.5 0 0 0 28.5 23.8Z;P xs M24 31l5-6',
  'resource-usage': gauge('c2', 'c', 'r'),

  // ---- Advanced (teal)
  cron: `C t 24 24 12.5;${historyRing('t')};P xp M24 17.5V24l4.5 3`,
  'api-tokens': `C t 13 17 9;C p 13 17 3.5;R t 20 14 24 6 2;R t 33 18 4 7 1.5;R t 39.5 18 4 5 1.5#C s 37 37 9;P xp M34.5 33.5 31.5 37l3 3.5M39.5 33.5l3 3.5-3 3.5`,
  'panel-settings': 'R t2 4 4 40 40 3;P xp M10 14h28M10 24h28M10 34h28;P xt M10 14h7M10 24h18M10 34h11;C t 17 14 4.5;C t 28 24 4.5;C t 21 34 4.5;C p 17 14 1.8;C p 28 24 1.8;C p 21 34 1.8',
  notifications: 'P t M24 6c-7 0-12 5.4-12 12.5V27l-4 6.5V36h32v-2.5L36 27v-8.5C36 11.4 31 6 24 6z;P t2 M19.5 38.5a4.5 4.5 0 0 0 9 0z#C r 38 10 6',
  mcp: 'P t M10 6h28a6 6 0 0 1 6 6v16a6 6 0 0 1-6 6H22l-9 8v-8h-3a6 6 0 0 1-6-6V12a6 6 0 0 1 6-6z;P p M24 10.5c1 5 3 7 8 8-5 1-7 3-8 8-1-5-3-7-8-8 5-1 7-3 8-8z#P a M40.5 1.5c.7 3.5 2 4.8 5.5 5.5-3.5.7-4.8 2-5.5 5.5-.7-3.5-2-4.8-5.5-5.5 3.5-.7 4.8-2 5.5-5.5z',
  settings: 'P t M19.8 9.6L20.4 4.3A20 20 0 0 1 27.6 4.3L28.2 9.6A15 15 0 0 1 31.3 10.9L35.4 7.5A20 20 0 0 1 40.5 12.6L37.1 16.7A15 15 0 0 1 38.4 19.8L43.7 20.4A20 20 0 0 1 43.7 27.6L38.4 28.2A15 15 0 0 1 37.1 31.3L40.5 35.4A20 20 0 0 1 35.4 40.5L31.3 37.1A15 15 0 0 1 28.2 38.4L27.6 43.7A20 20 0 0 1 20.4 43.7L19.8 38.4A15 15 0 0 1 16.7 37.1L12.6 40.5A20 20 0 0 1 7.5 35.4L10.9 31.3A15 15 0 0 1 9.6 28.2L4.3 27.6A20 20 0 0 1 4.3 20.4L9.6 19.8A15 15 0 0 1 10.9 16.7L7.5 12.6A20 20 0 0 1 12.6 7.5L16.7 10.9A15 15 0 0 1 19.8 9.6Z;C t2 24 24 8.5;C p 24 24 4',
  addons: 'R a 26 4 18 18 2.5;R g 4 26 18 18 2.5;R b 4 4 18 18 2.5;C b 24 13 4;C b 13 24 4;R m 26 26 18 18 2.5;C m 35 24 4;C m 24 35 4',

  // ---- The fallback: a name with no picture.
  generic: 'R s2 6 6 36 36 6;C s 18 18 4;C s 30 18 4;C s 18 30 4;C s 30 30 4',
};

export const FEATURE_ICON_NAMES = Object.keys(FEATURE_ICONS).filter((name) => name !== 'generic');

// The icon of each page (lib/panel.jsx PAGE_ROUTES), by page key. A tile for
// a part of a page (a tab, a form) names its icon itself.
export const PAGE_FEATURE_ICONS = {
  dashboard: 'dashboard',
  websites: 'websites',
  applications: 'applications',
  ssl: 'ssl',
  databases: 'databases',
  cron: 'cron',
  files: 'files',
  backups: 'backups',
  users: 'users',
  settings: 'settings',
  'panel-settings': 'panel-settings',
  'api-tokens': 'api-tokens',
  security: 'account-security',
  sftp: 'sftp',
  git: 'git',
  terminal: 'terminal',
  php: 'php',
  firewall: 'firewall',
  fail2ban: 'fail2ban',
  dns: 'dns',
  mail: 'mail',
  mcp: 'mcp',
  notifications: 'notifications',
  'incremental-backup': 'incremental-backup',
  'cpanel-import': 'cpanel-import',
  waf: 'waf',
  'waf-site': 'waf',
  malware: 'malware',
  'malware-scan': 'malware',
  'access-logs': 'access-logs',
  updates: 'updates',
  webstack: 'webstack',
  services: 'services',
  cloudlinux: 'cloudlinux',
  'resource-usage': 'resource-usage',
  'php-selector': 'php-selector',
  'object-cache': 'cache',
  addons: 'addons',
  wordpress: 'wordpress',
  redis: 'cache',
  mysql: 'mysql',
  processes: 'processes',
  ram: 'ram',
  disk: 'disk',
  traffic: 'traffic',
};

export const featureIconForPage = (page) => PAGE_FEATURE_ICONS[page] || 'generic';
