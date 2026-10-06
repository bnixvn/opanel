import React, { useEffect, useState, useCallback, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import ace from 'ace-builds/src-noconflict/ace';
import 'ace-builds/src-noconflict/ext-language_tools';
import 'ace-builds/src-noconflict/ext-searchbox';
import 'ace-builds/src-noconflict/mode-css';
import 'ace-builds/src-noconflict/mode-html';
import 'ace-builds/src-noconflict/mode-ini';
import 'ace-builds/src-noconflict/mode-javascript';
import 'ace-builds/src-noconflict/mode-json';
import 'ace-builds/src-noconflict/mode-php';
import 'ace-builds/src-noconflict/mode-text';
import 'ace-builds/src-noconflict/mode-yaml';
import 'ace-builds/src-noconflict/theme-textmate';
import 'ace-builds/src-noconflict/theme-tomorrow_night';
import { Activity, Archive, ArrowLeft, Bot, BrickWall, Bug, Check, CheckCircle, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Layers, Lock, LockKeyhole, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, PackageOpen, Pencil, Save, ScrollText, Search, Server, Settings as SettingsIcon, Shield, ShieldAlert, ShieldCheck, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Zap, ExternalLink, Ban, Bell, Eye, Mail, Inbox, Forward, Send } from 'lucide-react';
import { Terminal } from './components/Terminal';
import { UsageChart } from './components/UsageChart';
import { LANGUAGES, currentLanguage, nextLanguage, setLanguage, tr } from './i18n';
import './shared/style.css';
import './shared/brand.css';
import './shared/ui.css';
import './shared/file-manager.css';

const API = import.meta.env.VITE_API_URL || '/api';
const DEFAULT_SERVICE_NAMES = ['opanel-api', 'nginx', 'php8.3-fpm', 'php8.4-fpm', 'mariadb', 'redis-server'];
const PHP_VERSION_ORDER = ['7.4', '8.1', '8.2', '8.3', '8.4', '8.5'];
const NGINX_REWRITE_MODES = [
  { value: 'none', label: tr("None / static PHP") },
  { value: 'front_controller', label: tr("PHP front controller") },
  { value: 'laravel', label: tr("Laravel") },
  { value: 'codeigniter', label: tr("CodeIgniter") },
  { value: 'seohburl', label: tr("SEO HB URL") },
];
const WEEKDAY_LABELS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
// Common cron schedules, offered as a dropdown next to the raw expression.
const CRON_PRESETS = [
  ['* * * * *', 'Every minute'],
  ['*/5 * * * *', 'Every 5 minutes'],
  ['*/15 * * * *', 'Every 15 minutes'],
  ['*/30 * * * *', 'Every 30 minutes'],
  ['0 * * * *', 'Every hour'],
  ['0 */6 * * *', 'Every 6 hours'],
  ['0 0 * * *', 'Daily at 00:00'],
  ['0 2 * * *', 'Daily at 02:00'],
  ['0 0 * * 0', 'Weekly, Sunday 00:00'],
  ['0 0 1 * *', 'Monthly, day 1 at 00:00'],
];
const DNS_TYPES = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'SRV', 'CAA'];
const DNS_TTLS = [60, 300, 900, 1800, 3600, 14400, 43200, 86400];
const DNS_PLACEHOLDERS = {
  A: '203.0.113.10', AAAA: '2001:db8::10', CNAME: 'target.example.com', MX: 'mail.example.com',
  TXT: 'v=spf1 mx -all', NS: 'ns1.example.net', SRV: '5 5060 sip.example.com', CAA: 'issue letsencrypt.org',
};
const emptyDnsRecord = () => ({ type: 'A', name: '', value: '', priority: '', ttl: '' });
const normalizeCron = value => String(value || '').trim().split(/\s+/).join(' ');
// Which tool groups a viewer folded on the dashboard (a convenience, per browser).
const DASHBOARD_COLLAPSED_KEY = 'opanel.dashboard.collapsedTools';

const PAGE_ROUTES = {
  dashboard: '/',
  websites: '/website',
  ssl: '/ssl',
  databases: '/database',
  cron: '/cron',
  files: '/filemanager',
  sftp: '/sftp',
  backups: '/backups',
  users: '/users',
  settings: '/panel-settings',
  config: '/settings',
  security: '/security',
  malware: '/malware',
  php: '/php',
  firewall: '/firewall',
  waf: '/waf',
  wafLogs: '/waf-logs',
  updates: '/updates',
  addons: '/addons',
  mcp: '/mcp',
  mail: '/email',
  dns: '/dns',
  notifications: '/notifications',
  services: '/services',
  usage: '/resource-usage',
};
const ROUTE_PAGES = new Map([
  ...Object.entries(PAGE_ROUTES).map(([pageName, path]) => [path, pageName]),
  ['/dashboard', 'dashboard'],
  ['/websites', 'websites'],
  ['/databases', 'databases'],
  ['/files', 'files'],
  ['/file-manager', 'files'],
  ['/website', 'websites'],
]);

function pageFromPathname(pathname) {
  const normalized = `/${String(pathname || '').replace(/^\/+|\/+$/g, '')}`.toLowerCase();
  return ROUTE_PAGES.get(normalized) || 'dashboard';
}

function routeForPage(pageName) {
  return PAGE_ROUTES[pageName] || PAGE_ROUTES.dashboard;
}

function phpVersionOptions(installed = [], current = '') {
  const list = sortPhpVersions(installed);
  if (current && !list.includes(current)) return sortPhpVersions([...list, current]);
  return list;
}

function sortPhpVersions(versions = []) {
  return [...versions].sort((a, b) => {
    const ai = PHP_VERSION_ORDER.indexOf(a);
    const bi = PHP_VERSION_ORDER.indexOf(b);
    if (ai !== -1 || bi !== -1) return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
    return String(a).localeCompare(String(b), undefined, { numeric: true });
  });
}

function websiteConfigForm(site = {}) {
  const appType = site.app_type || 'wordpress';
  return {
    app_type: appType,
    php_version: site.php_version || '8.4',
    nginx_rewrite_mode: appType === 'wordpress'
      ? 'front_controller'
      : appType === 'static'
        ? 'none'
        : site.nginx_rewrite_mode || 'none',
  };
}

function editorParamsFromLocation() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('view') !== 'editor') return null;
  const websiteId = params.get('website_id');
  const path = params.get('path') || 'public_html/index.html';
  if (!websiteId) return null;
  return { websiteId: String(websiteId), path };
}

function aceModeName(mode) {
  if (mode === 'PHP') return 'php';
  if (mode === 'JavaScript') return 'javascript';
  if (mode === 'CSS') return 'css';
  if (mode === 'HTML') return 'html';
  if (mode === 'JSON') return 'json';
  if (mode === 'YAML') return 'yaml';
  if (mode === 'Config') return 'ini'; // .env, .htaccess, .ini, .conf -> Ace's ini mode
  return 'text';
}

function formatLogCount(value = 0) {
  const count = Number(value) || 0;
  if (count >= 1000) return `${(count / 1000).toFixed(count >= 10000 ? 0 : 1)}k`;
  return String(count);
}

function formatLogDuration(value = 0) {
  const duration = Number(value);
  return `${Number.isFinite(duration) ? duration : 0} ms`;
}

// --- WebAuthn wire format -------------------------------------------------
// The API speaks base64url (what the spec uses on the wire); the browser API
// speaks ArrayBuffer. These two convert at the boundary and nowhere else.
function b64urlToBuf(value) {
  const padded = (value || '').replace(/-/g, '+').replace(/_/g, '/');
  const binary = atob(padded + '='.repeat((4 - (padded.length % 4)) % 4));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

function bufToB64url(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function passkeysSupported() {
  return typeof window !== 'undefined'
    && !!window.PublicKeyCredential
    && !!(navigator.credentials && navigator.credentials.create);
}

function csvEscape(value) {
  let text = String(value ?? '');
  // Neutralize spreadsheet formulas before quoting. Three of the exported WAF
  // access-log columns -- path, user_agent and reason -- are copied verbatim
  // out of the OpenLiteSpeed access line, so their contents are chosen by
  // whoever sent the request to the hosted site. A cell starting with =, +, -,
  // @, tab or CR is a formula to a spreadsheet, which is what an admin opens
  // the exported .csv with. Quoting alone does not stop it: the consumer
  // evaluates the cell after unquoting. A leading apostrophe is the standard
  // neutralizer and is what every major spreadsheet treats as "this is text".
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function formatApiError(detail, fallback = tr('Request failed.')) {
  if (detail === null || detail === undefined || detail === '') return fallback;
  // Messages the backend sends are English; show the translation when there is one.
  if (typeof detail === 'string') return tr(detail.replace(/^Value error,\s*/i, '')) || fallback;
  if (typeof detail === 'number' || typeof detail === 'boolean') return String(detail);

  if (Array.isArray(detail)) {
    const messages = detail.map(item => formatApiErrorItem(item)).filter(Boolean);
    return messages.length ? messages.join('\n') : fallback;
  }

  if (typeof detail === 'object') {
    if (detail.detail !== undefined) return formatApiError(detail.detail, fallback);
    if (detail.message !== undefined) return formatApiError(detail.message, fallback);
    if (detail.msg !== undefined) return formatApiError(detail.msg, fallback);
    try { return JSON.stringify(detail); } catch { return fallback; }
  }

  return fallback;
}

function formatApiErrorItem(item) {
  if (!item || typeof item !== 'object') return formatApiError(item, '');
  const message = formatApiError(item.msg ?? item.message ?? item.detail, tr("Invalid value"));
  const loc = Array.isArray(item.loc)
    ? item.loc.filter(part => part !== 'body' && part !== 'query' && part !== 'path').join('.')
    : '';
  return loc ? `${loc}: ${message}` : message;
}

function NotificationToast({ type, message, onClose }) {
  if (!message) return null;
  const isError = type === 'error';
  const Icon = isError ? AlertCircle : Check;
  return <div className={`app-toast ${isError ? 'app-toast-error' : 'app-toast-success'}`} role={isError ? 'alert' : 'status'} aria-live={isError ? 'assertive' : 'polite'}>
    <Icon className="app-toast-icon" size={18}/>
    <div className="app-toast-content">
      <strong>{isError ? tr("Action failed") : tr("Completed")}</strong>
      <span>{message}</span>
    </div>
    <button className="app-toast-close" onClick={onClose} aria-label={tr("Dismiss notification")} title={tr("Dismiss notification")}><X size={16}/></button>
  </div>;
}

function isHostnameDomain(value = '') {
  return /^(?!-)([a-z0-9-]{1,63}\.)+[a-z]{2,}$/i.test(String(value).trim());
}

function defaultPanelSslEmail(hostname = '') {
  const host = String(hostname || '').trim().toLowerCase();
  return isHostnameDomain(host) ? `admin@${host}` : '';
}

function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => {}, () => fallbackCopy(text));
  } else {
    fallbackCopy(text);
  }
}
function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch {}
  document.body.removeChild(ta);
}

function aceThemePath(theme) {
  return theme === 'dark' ? 'ace/theme/tomorrow_night' : 'ace/theme/textmate';
}

function CodeEditor({ value, mode, disabled, theme, onChange, onCursorChange }) {
  const hostRef = useRef(null);
  const editorRef = useRef(null);
  const suppressChangeRef = useRef(false);
  const onChangeRef = useRef(onChange);
  const onCursorChangeRef = useRef(onCursorChange);

  useEffect(() => { onChangeRef.current = onChange; }, [onChange]);
  useEffect(() => { onCursorChangeRef.current = onCursorChange; }, [onCursorChange]);

  // Mount once. Sizing, font metrics and scrolling stay Ace's job: it renders
  // only the visible rows and drives its own scrollbars, so re-implementing any
  // of that desyncs the renderer from the document (last lines unreachable,
  // duplicate scrollbars). Height and colours come from CSS instead.
  useEffect(() => {
    if (!hostRef.current) return undefined;
    const editor = ace.edit(hostRef.current, {
      mode: `ace/mode/${aceModeName(mode)}`,
      theme: aceThemePath(theme),
      value: value || '',
      readOnly: !!disabled,
      showPrintMargin: false,
      highlightActiveLine: true,
      // Ace stamps font-size on the host inline (it defaults the option), so
      // CSS cannot set it — both font properties live here and only the line
      // height stays in CSS, which Ace measures off the DOM. var() keeps the
      // token the single source of truth for the stack.
      fontSize: 13,
      fontFamily: 'var(--font-mono)',
      tabSize: 2,
      useSoftTabs: true,
      wrap: false,
      selectionStyle: 'text',
      enableBasicAutocompletion: true,
      enableLiveAutocompletion: true,
      enableSnippets: false,
    });
    editor.session.setUseWorker(false);
    editor.session.setNewLineMode('unix');

    const reportCursor = () => {
      const pos = editor.getCursorPosition();
      onCursorChangeRef.current?.({ line: pos.row + 1, column: pos.column + 1 });
    };
    const handleChange = () => {
      if (suppressChangeRef.current) return;
      onChangeRef.current?.(editor.getValue());
    };

    editor.session.on('change', handleChange);
    editor.selection.on('changeCursor', reportCursor);
    editorRef.current = editor;
    reportCursor();

    return () => {
      editorRef.current = null;
      editor.destroy();
    };
  }, []);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const nextValue = value || '';
    if (nextValue === editor.getValue()) return;
    const cursor = editor.getCursorPosition();
    suppressChangeRef.current = true;
    editor.setValue(nextValue, -1);
    const newRow = Math.max(0, Math.min(cursor.row, editor.session.getLength() - 1));
    editor.moveCursorTo(newRow, cursor.column);
    suppressChangeRef.current = false;
  }, [value]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.session.setMode(`ace/mode/${aceModeName(mode)}`);
  }, [mode]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.setReadOnly(!!disabled);
  }, [disabled]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.setTheme(aceThemePath(theme));
  }, [theme]);

  return <div className="code-editor-host" ref={hostRef}></div>;
}

function App() {
  // Auth is now cookie-based (HttpOnly opanel_session). The SPA does not see
  // the JWT at all. We track only whether the user is authenticated in memory.
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [currentUser, setCurrentUser] = useState(null);
  const [bootstrapping, setBootstrapping] = useState(true);
  const [standaloneEditor] = useState(() => editorParamsFromLocation());
  const [theme, setTheme] = useState(() => {
    const saved = localStorage.getItem('opanel-theme');
    if (saved === 'dark' || saved === 'light') return saved;
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  });
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('opanel-theme', theme);
  }, [theme]);
  const toggleTheme = () => setTheme(prev => prev === 'dark' ? 'light' : 'dark');
  // Shows the language in use; a click switches to the other one (and reloads).
  function renderLanguageToggle(className) {
    const current = LANGUAGES.find(lang => lang.code === currentLanguage) || LANGUAGES[0];
    const next = nextLanguage();
    const label = tr('Switch to {0}', next.label);
    return <button type="button" className={className} onClick={() => setLanguage(next.code)} aria-label={label} title={label}>
      <span className="lang-code">{current.short}</span>
    </button>;
  }
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  // Demo mode: the public demo accounts the login page offers, and the admin's
  // settings form on the Addons page.
  const [demoInfo, setDemoInfo] = useState(null);
  const [demoSettings, setDemoSettings] = useState(null);
  const [demoForm, setDemoForm] = useState({ accounts: [], show_on_login: true });
  const [otpCode, setOtpCode] = useState('');
  const [needsTwoFactor, setNeedsTwoFactor] = useState(false);
  const [page, setPage] = useState(() => pageFromPathname(window.location.pathname));
  const [domain, setDomain] = useState('');
  // Who a new website is for: '' is the account creating it.
  const [siteOwnerId, setSiteOwnerId] = useState('');
  const [websiteSearch, setWebsiteSearch] = useState('');
  const [databaseSearch, setDatabaseSearch] = useState('');
  const [adminEmail, setAdminEmail] = useState('');
  const [wpAdminUser, setWpAdminUser] = useState('admin');
  const [wpAdminPassword, setWpAdminPassword] = useState('');
  const [phpVersion, setPhpVersion] = useState('8.4');
  const [siteType, setSiteType] = useState('wordpress');
  const [installSslAfterCreate, setInstallSslAfterCreate] = useState(false);
  const [installWordPress, setInstallWordPress] = useState(true);
  const [nginxCustomEditing, setNginxCustomEditing] = useState(null); // Website settings editor state
  const [websiteSettingsForm, setWebsiteSettingsForm] = useState(websiteConfigForm());
  const [logViewer, setLogViewer] = useState(null); // {id, domain, kind, lines, path, content, exists}
  const [terminalViewer, setTerminalViewer] = useState(null); // {id, domain}
  const [websites, setWebsites] = useState([]);
  const [aliasDrafts, setAliasDrafts] = useState({});
  const [aliasModes, setAliasModes] = useState({});
  const [databases, setDatabases] = useState([]);
  const [newDatabase, setNewDatabase] = useState({ db_name: '', db_user: '', db_password: '' });
  const [createdDbInfo, setCreatedDbInfo] = useState(null);
  // { db, ownerId } while the move-owner dialog is open.
  const [dbOwnerModal, setDbOwnerModal] = useState(null);
  const [copiedField, setCopiedField] = useState(null);
  const [users, setUsers] = useState([]);
  const [usageLoading, setUsageLoading] = useState(false);
  const [resourceUsage, setResourceUsage] = useState(null);
  const [serviceStates, setServiceStates] = useState({});
  const [serviceNames, setServiceNames] = useState(DEFAULT_SERVICE_NAMES);
  const [backupTab, setBackupTab] = useState('website');
  const [backups, setBackups] = useState([]);
  const [backupJobs, setBackupJobs] = useState([]);
  const [userBackups, setUserBackups] = useState([]);
  // Restore: choose where the backups are, give that source what it needs,
  // pick the accounts, restore. The remote password lives only in this state.
  const BLANK_RESTORE_REMOTE = { protocol: 'sftp', host: '', port: 22, username: '', password: '', private_key: '', use_key: false, path: '' };
  const [restoreSource, setRestoreSource] = useState('local');
  const [restoreTargetId, setRestoreTargetId] = useState('');
  const [restoreRemote, setRestoreRemote] = useState(BLANK_RESTORE_REMOTE);
  // null until a source has been read; then { items, host_key, directories }.
  const [restoreList, setRestoreList] = useState(null);
  const [restoreListError, setRestoreListError] = useState('');
  const [restoreListing, setRestoreListing] = useState(false);
  const [restorePicks, setRestorePicks] = useState([]);
  const [restoreChoice, setRestoreChoice] = useState({});
  const [restoreFilter, setRestoreFilter] = useState('');
  const [restoreOverwrite, setRestoreOverwrite] = useState(false);
  const [restoreJobId, setRestoreJobId] = useState('');
  const [selectedBackupUserId, setSelectedBackupUserId] = useState('');
  const [backupSchedules, setBackupSchedules] = useState([]);
  const [newBackupSchedule, setNewBackupSchedule] = useState({ user_ids: [], all_users: false, schedule: '0 2 * * *', target_id: '', retention: 7 });
  const [sftpTargets, setSftpTargets] = useState([]);
  const [selectedSftpTargetId, setSelectedSftpTargetId] = useState('');
  const BLANK_TARGET = {
    name: '', kind: 'sftp', remote_path: '/backups/opanel',
    host: '', port: 22, username: '', password: '', private_key: '',
    s3_endpoint: '', s3_region: 'us-east-1', s3_bucket: '',
    s3_access_key: '', s3_secret_key: '', s3_use_path_style: false,
  };
  const [newSftpTarget, setNewSftpTarget] = useState(BLANK_TARGET);
  const [targetObjects, setTargetObjects] = useState({ id: null, bucket: '', items: [] });
  const [selectedWebsiteId, setSelectedWebsiteId] = useState(() => standaloneEditor?.websiteId || '');
  const [sslMode, setSslMode] = useState('letsencrypt');
  const [manualSslForm, setManualSslForm] = useState({ certificate: '', private_key: '', ca_bundle: '' });
  const [manualSslFiles, setManualSslFiles] = useState({ certificate: null, private_key: null, ca_bundle: null });
  const [wildcardSslForm, setWildcardSslForm] = useState({ api_token: '', email: '', provider: '' });
  const [availableCerts, setAvailableCerts] = useState([]);   // SSL page: certs on the box covering currentSite
  const [reuseCertName, setReuseCertName] = useState('');
  // Create-website SSL section
  const [createSslMode, setCreateSslMode] = useState('letsencrypt'); // letsencrypt|wildcard|existing|manual
  const [createSslForm, setCreateSslForm] = useState({ api_token: '', email: '', provider: '', reuse_name: '', certificate: '', private_key: '', ca_bundle: '' });
  const [createSslCerts, setCreateSslCerts] = useState([]);
  const [cronSchedule, setCronSchedule] = useState('*/15 * * * *');
  const [cronCommand, setCronCommand] = useState('');
  const [cronItems, setCronItems] = useState([]);
  const [cronUser, setCronUser] = useState('');
  const [filePath, setFilePath] = useState(() => standaloneEditor?.path || 'public_html/index.html');
  const [fileListPath, setFileListPath] = useState('public_html');
  const [fileUploadDir, setFileUploadDir] = useState('public_html');
  const [files, setFiles] = useState([]);
  const [fileJobs, setFileJobs] = useState([]);
  const [fileContent, setFileContent] = useState('');
  const [selectedFilePaths, setSelectedFilePaths] = useState([]);
  const [archiveFormat, setArchiveFormat] = useState('zip');
  const [editorCursor, setEditorCursor] = useState({ line: 1, column: 1 });
  const [newUser, setNewUser] = useState({ username: '', email: '', password: '', role: 'end_user', website_limit: 5, storage_limit_mb: 1024, database_limit: 10, mailbox_limit: 10, reseller_id: '', pool_user_limit: 0, pool_storage_limit_mb: 0, pool_oversell: false, cpu_percent: 0, memory_mb: 0, process_limit: 0, io_read_mbps: 0, io_write_mbps: 0, group_cpu_percent: 0, group_memory_mb: 0, group_process_limit: 0, group_io_read_mbps: 0, group_io_write_mbps: 0 });
  // A reseller's share of the server and what it has handed out (GET /users/pool).
  const [resellerPool, setResellerPool] = useState(null);
  const [editingUser, setEditingUser] = useState(null);
  const [editingUserForm, setEditingUserForm] = useState({ email: '', role: 'end_user', website_limit: 5, storage_limit_mb: 1024 });
  const [phpConfig, setPhpConfig] = useState({ php_version: '8.4', display_errors: 'Off', max_execution_time: 300, max_input_time: 600, max_input_vars: 10000, memory_limit: '1024M', post_max_size: '1024M', upload_max_filesize: '1024M', opcache_enable: true });
  const [phpVersions, setPhpVersions] = useState({ installed: [], supported: [] });
  const [phpTuning, setPhpTuning] = useState(null);
  const [phpExtensions, setPhpExtensions] = useState(null);
  const [firewallStatus, setFirewallStatus] = useState(null);
  const [firewallPort, setFirewallPort] = useState('80');
  const [firewallProtocol, setFirewallProtocol] = useState('tcp');
  const [firewallAllowIp, setFirewallAllowIp] = useState('');
  const [firewallAllowPort, setFirewallAllowPort] = useState('');
  const [firewallAllowProtocol, setFirewallAllowProtocol] = useState('tcp');
  const [firewallBlockIp, setFirewallBlockIp] = useState('');
  const [firewallBlockPort, setFirewallBlockPort] = useState('');
  const [firewallBlockProtocol, setFirewallBlockProtocol] = useState('tcp');
  const [firewallBlockNote, setFirewallBlockNote] = useState('');
  // The address list opens on demand and loads a page at a time: a box can
  // hold thousands of blocks.
  const [showFirewallIpList, setShowFirewallIpList] = useState(false);
  const [firewallIpList, setFirewallIpList] = useState(null);
  const [firewallIpQuery, setFirewallIpQuery] = useState('');
  const [firewallIpAction, setFirewallIpAction] = useState('');
  const [firewallIpPage, setFirewallIpPage] = useState(1);
  const [firewallIpReload, setFirewallIpReload] = useState(0);
  const [firewallDeleteNumber, setFirewallDeleteNumber] = useState('');
  const [firewallBlocklists, setFirewallBlocklists] = useState(null);
  const [firewallBlocklistUrl, setFirewallBlocklistUrl] = useState('');
  const [wafRules, setWafRules] = useState({ status: null, default_rules: '', custom_rules: '' });
  const [badBots, setBadBots] = useState({ patterns: [], max_patterns: 400 });
  const [badBotText, setBadBotText] = useState('');
  const [wafBotExtra, setWafBotExtra] = useState('');
  const [wafCustomRules, setWafCustomRules] = useState('');
  const [selectedWafWebsiteId, setSelectedWafWebsiteId] = useState('');
  const [wafSiteConfig, setWafSiteConfig] = useState(null);
  const [wafAccessLogs, setWafAccessLogs] = useState({ entries: [], total: 0, domains: [], limit: 50, offset: 0, paths: {} });
  const [wafAccessFilters, setWafAccessFilters] = useState({ domain: '', verdict: '', q: '', limit: 50, offset: 0 });
  const [wafAccessAutoRefresh, setWafAccessAutoRefresh] = useState(5);
  const [assignUserId, setAssignUserId] = useState('');
  const [assignWebsiteId, setAssignWebsiteId] = useState('');
  const [passkeyStatus, setPasskeyStatus] = useState(null);
  const [passkeyName, setPasskeyName] = useState('');
  const [twoFactorStatus, setTwoFactorStatus] = useState(null);
  const [twoFactorSetup, setTwoFactorSetup] = useState(null);
  const [twoFactorCode, setTwoFactorCode] = useState('');
  const [malwareScanStatus, setMalwareScanStatus] = useState(null);
  const [scanTargetWebsiteId, setScanTargetWebsiteId] = useState('');
  const [scanResults, setScanResults] = useState(null);
  const [scanJob, setScanJob] = useState(null);
  const [scanJobs, setScanJobs] = useState([]);
  const [networkStatus, setNetworkStatus] = useState({ ipv4: [], ipv6: [], ipv6_available: false, ipv6_enabled: false });
  const [quarantine, setQuarantine] = useState([]);
  const [malwareSchedules, setMalwareSchedules] = useState({
    system: { scope: 'system', scan_root: '/', enabled: false, frequency: 'weekly', weekday: 0, hour: 2, minute: 0, day: 1 },
    web: { scope: 'web', scan_root: '/home', enabled: false, frequency: 'daily', weekday: 0, hour: 3, minute: 30, day: 1 },
  });
  const [scanLoading, setScanLoading] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    if (!notice) return undefined;
    const timer = setTimeout(() => setNotice(''), 5000);
    return () => clearTimeout(timer);
  }, [notice]);
  const [loading, setLoading] = useState('');
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  // Which schedule pickers the user switched to a hand-written expression.
  const [customSchedules, setCustomSchedules] = useState({});
  const [malwareDetailJob, setMalwareDetailJob] = useState(null);
  const [chmodTarget, setChmodTarget] = useState(null);
  const [sftpInfo, setSftpInfo] = useState(null);
  const [dashSummary, setDashSummary] = useState(null);
  const [showCreateSftp, setShowCreateSftp] = useState(false);
  const [sftpForm, setSftpForm] = useState({ owner_id: '', suffix: '', website_id: '', subpath: '', password: '' });
  const [sftpPasswordFor, setSftpPasswordFor] = useState(null);
  // Create forms stay folded until asked for, so each page opens on its list.
  const [showCreateSite, setShowCreateSite] = useState(false);
  const [showCreateDb, setShowCreateDb] = useState(false);
  const userMenuRef = useRef(null);
  const [panelSettings, setPanelSettings] = useState({ app_name: 'opanel', panel_url: '', panel_hostname: '', panel_port: 2222, logo_url: '', favicon_url: '/favicon.png', ssl_enabled: false });
  const [panelSettingsForm, setPanelSettingsForm] = useState({ app_name: 'opanel', panel_hostname: '', panel_port: 2222, ssl_enabled: false });
  const [panelSettingsTab, setPanelSettingsTab] = useState('general');
  const [appVersion, setAppVersion] = useState('');
  const [panelLogoFile, setPanelLogoFile] = useState(null);
  const [panelFaviconFile, setPanelFaviconFile] = useState(null);
  const [panelSslEmail, setPanelSslEmail] = useState('');
  const [updatesStatus, setUpdatesStatus] = useState(null);
  const [addonList, setAddonList] = useState([]);
  const [addonOpen, setAddonOpen] = useState('');
  const [f2bSettings, setF2bSettings] = useState(null);
  const [f2bDraft, setF2bDraft] = useState(null);
  const [f2bBanned, setF2bBanned] = useState([]);
  const [f2bLog, setF2bLog] = useState('');
  // The Fail2ban addon's own status, for its section on the Firewall page.
  const [f2bAddon, setF2bAddon] = useState(null);
  const [showUpdateLog, setShowUpdateLog] = useState(false);
  const [osUpdating, setOsUpdating] = useState(false);
  const [panelUpdating, setPanelUpdating] = useState(false);
  const [panelUpdateLog, setPanelUpdateLog] = useState([]);
  const panelUpdateInterval = useRef(null);
  const wafAccessFiltersRef = useRef(wafAccessFilters);
  const [osAutoUpdate, setOsAutoUpdate] = useState({ enabled: true, mode: 'security', auto_reboot: false });
  const [panelAutoUpdate, setPanelAutoUpdate] = useState({ enabled: true, time: '03:30' });
  // API Tokens
  const [apiTokens, setApiTokens] = useState([]);
  const [apiTokenForm, setApiTokenForm] = useState({ name: '', scopes: ['provisioning:read', 'provisioning:write'], expires_days: 365, ip_allowlist: '' });
  const [createdToken, setCreatedToken] = useState(null);
  const [mcpInfo, setMcpInfo] = useState(null);
  // Notifications addon: whether it is on, the admin's channel settings (and an
  // editable copy whose secret fields start blank), and this account's choices.
  const [notifyInfo, setNotifyInfo] = useState(null);
  // Email addon
  const [mailInfo, setMailInfo] = useState(null);
  // Resource limits addon: whether it is installed, and each visible account's limits and use.
  const [limitsInfo, setLimitsInfo] = useState(null);
  // The dashboard's view of it: the signed-in account's day and week of history,
  // which range the charts show, a reseller's own account or its whole group,
  // and what the administrator's busiest-accounts list is sorted by.
  const [limitsHistory, setLimitsHistory] = useState(null);
  const [dashRange, setDashRange] = useState('day');
  const [dashScope, setDashScope] = useState('account');
  const [topAccountsSort, setTopAccountsSort] = useState('cpu');
  // An account picked on the dashboard, opened for editing once Users has loaded.
  const [pendingEditUserId, setPendingEditUserId] = useState(null);
  // The dashboard's tool panel: the search, and which groups are folded.
  const [toolQuery, setToolQuery] = useState('');
  const [collapsedTools, setCollapsedTools] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(DASHBOARD_COLLAPSED_KEY) || '[]');
      return Array.isArray(saved) ? saved : [];
    } catch { return []; }
  });
  const toolSearchRef = useRef(null);
  const [mailTab, setMailTab] = useState('mailboxes');
  const [mailFilter, setMailFilter] = useState({ domain_id: '', q: '' });
  const [mailPage, setMailPage] = useState(1);
  const [mailboxList, setMailboxList] = useState(null);
  const [forwarderList, setForwarderList] = useState(null);
  const [showCreateMailbox, setShowCreateMailbox] = useState(false);
  const [mailboxForm, setMailboxForm] = useState({ domain_id: '', local_part: '', password: '', quota_mb: '' });
  const [showCreateForwarder, setShowCreateForwarder] = useState(false);
  const [forwarderForm, setForwarderForm] = useState({ domain_id: '', local_part: '', destinations: '' });
  const [mailDomainForm, setMailDomainForm] = useState({ domain: '', owner_id: '' });
  const [mailDns, setMailDns] = useState(null);
  const [mailboxEdit, setMailboxEdit] = useState(null);
  const [forwarderEdit, setForwarderEdit] = useState(null);
  const [catchAllDraft, setCatchAllDraft] = useState({});
  const [mailSettings, setMailSettings] = useState(null);
  const [mailSettingsForm, setMailSettingsForm] = useState(null);
  const [mailRelays, setMailRelays] = useState(null);
  const [relayForm, setRelayForm] = useState(null);
  const [dnsCustomForm, setDnsCustomForm] = useState(null);
  const [rspamdView, setRspamdView] = useState('history');
  const [rspamdStat, setRspamdStat] = useState(null);
  const [rspamdHistory, setRspamdHistory] = useState(null);
  const [rspamdFilter, setRspamdFilter] = useState({ q: '', action: '', page: 1 });
  const [rspamdLog, setRspamdLog] = useState(null);
  const [rspamdLogQuery, setRspamdLogQuery] = useState({ lines: 500, q: '' });
  const [eximLog, setEximLog] = useState(null);
  const [eximLogQuery, setEximLogQuery] = useState({ lines: 500, q: '' });
  // DNS Manager addon: the zone list, one open zone (a sub-page) and settings.
  const [dnsInfo, setDnsInfo] = useState(null);
  const [dnsTab, setDnsTab] = useState('zones');
  const [dnsZones, setDnsZones] = useState(null);
  const [dnsQuery, setDnsQuery] = useState('');
  const [dnsPage, setDnsPage] = useState(1);
  const [dnsZone, setDnsZone] = useState(null);
  const [dnsDelegation, setDnsDelegation] = useState(null);
  const [dnsRecordForm, setDnsRecordForm] = useState(emptyDnsRecord);
  const [dnsRecordEdit, setDnsRecordEdit] = useState(null);
  const [dnsRecordFilter, setDnsRecordFilter] = useState('');
  const [dnsSettingsForm, setDnsSettingsForm] = useState(null);
  const [notifySettings, setNotifySettings] = useState(null);
  const [notifyForm, setNotifyForm] = useState(null);
  const [notifyPrefs, setNotifyPrefs] = useState(null);
  const [notifyTest, setNotifyTest] = useState({ email: '', telegram: '' });
  const [notifyLink, setNotifyLink] = useState(null);
  const [showNotifyLog, setShowNotifyLog] = useState(false);
  const [notifyLog, setNotifyLog] = useState(null);
  const [notifyLogPage, setNotifyLogPage] = useState(1);
  const [notifyLogStatus, setNotifyLogStatus] = useState('');
  const [mcpTokens, setMcpTokens] = useState([]);
  const [mcpAllTokens, setMcpAllTokens] = useState([]);
  const [mcpForm, setMcpForm] = useState({ name: '', can_write: false, expires_days: 90 });
  const [mcpCreated, setMcpCreated] = useState(null);
  // Profile modal
  const [showProfileModal, setShowProfileModal] = useState(false);
  const [profileForm, setProfileForm] = useState({ email: '', password: '', current_password: '', code: '' });
  // Users page tabs
  const [usersTab, setUsersTab] = useState('list');
  const [userSearch, setUserSearch] = useState('');
  // Hosting plans
  const [plans, setPlans] = useState([]);
  const [editingPlan, setEditingPlan] = useState(null);
  const [editingPlanForm, setEditingPlanForm] = useState({ name: '', website_limit: 1, storage_limit_mb: 1024, database_limit: 10, mailbox_limit: 10, php_version: '8.4', app_type: 'php', auto_ssl: false, active: true });
  const [newPlan, setNewPlan] = useState({ slug: '', name: '', website_limit: 1, storage_limit_mb: 1024, database_limit: 10, mailbox_limit: 10, cpu_percent: 0, memory_mb: 0, process_limit: 0, io_read_mbps: 0, io_write_mbps: 0, php_version: '8.4', app_type: 'php', auto_ssl: false });
  // WordPress manager
  const [wpManagerSite, setWpManagerSite] = useState(null);
  const [wpManagerMode, setWpManagerMode] = useState('install'); // 'install' | 'update'
  const [wpInstallForm, setWpInstallForm] = useState({ admin_user: 'admin', admin_email: '', admin_password: '', title: '' });
  const noticeTimer = useRef(null);
  const isAdmin = currentUser?.role === 'admin';
  // A reseller manages its customers and packages, never the server.
  const isReseller = currentUser?.role === 'reseller';
  const canManageUsers = isAdmin || isReseller;
  const currentSite = websites.find(site => String(site.id) === String(selectedWebsiteId));

  const navigateToPage = useCallback((nextPage, options = {}) => {
    const route = routeForPage(nextPage);
    if (!route) return;
    const nextUrl = route;
    if (!options.replace && window.location.pathname !== route) {
      window.history.pushState({}, '', nextUrl);
    } else if (options.replace && window.location.pathname !== route) {
      window.history.replaceState({}, '', nextUrl);
    }
    // Opening Email from the sidebar leaves a DNS records sub-page.
    if (nextPage === 'mail') setMailDns(null);
    if (nextPage === 'dns') { setDnsZone(null); setDnsTab('zones'); }
    setPage(nextPage);
  }, []);

  // Auto-dismiss notices after 6 seconds
  useEffect(() => {
    if (notice) {
      if (noticeTimer.current) clearTimeout(noticeTimer.current);
      noticeTimer.current = setTimeout(() => setNotice(''), 6000);
    }
    return () => { if (noticeTimer.current) clearTimeout(noticeTimer.current); };
  }, [notice]);

  function readCookie(name) {
    const match = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/[$()*+./?[\\\]^{|}]/g, '\\$&') + '=([^;]*)'));
    return match ? decodeURIComponent(match[1]) : '';
  }

  function clearReadableSessionCookies() {
    try {
      document.cookie = 'opanel_csrf=; Max-Age=0; path=/; SameSite=Lax';
      if (window.location.protocol === 'https:') {
        document.cookie = 'opanel_csrf=; Max-Age=0; path=/; SameSite=Lax; Secure';
      }
    } catch {}
  }

  function currentPanelHost() {
    return window.location.hostname || '';
  }

  function currentPanelPort() {
    const port = Number(window.location.port || 2222);
    return Number.isFinite(port) && port > 0 ? port : 2222;
  }

  function formFromPanelSettings(data = {}) {
    let hostname = data.panel_hostname || currentPanelHost();
    let port = Number(data.panel_port || currentPanelPort());
    if ((!hostname || !port) && data.panel_url) {
      try {
        const parsed = new URL(data.panel_url);
        hostname = hostname || parsed.hostname;
        port = port || Number(parsed.port || 2222);
      } catch {}
    }
    return {
      app_name: data.app_name || 'opanel',
      panel_hostname: hostname,
      panel_port: Number.isFinite(port) && port > 0 ? port : 2222,
      ssl_enabled: !!data.ssl_enabled,
    };
  }

  function clearSession(message = tr("Your session expired. Please log in again.")) {
    // Old localStorage token from a previous deploy: nuke it for safety.
    try { localStorage.removeItem('token'); } catch {}
    clearReadableSessionCookies();
    setIsAuthenticated(false);
    setCurrentUser(null);
    setNeedsTwoFactor(false);
    setOtpCode('');
    setWebsites([]);
    setDatabases([]);
    setUsers([]);
    setResourceUsage(null);
    setServiceStates({});
    setServiceNames(DEFAULT_SERVICE_NAMES);
    setBackupTab('website');
    setBackups([]);
    setBackupJobs([]);
    setCronItems([]);
    setCronUser('');
    setUserBackups([]);
    setRestoreList(null);
    setRestorePicks([]);
    setRestoreRemote(BLANK_RESTORE_REMOTE);
    setSelectedBackupUserId('');
    setBackupSchedules([]);
    setSftpTargets([]);
    setSelectedSftpTargetId('');
    setTwoFactorStatus(null);
    setTwoFactorSetup(null);
    setTwoFactorCode('');
    setMalwareScanStatus(null);
    setScanTargetWebsiteId('');
    setScanResults(null);
    setScanJob(null);
    setScanJobs([]);
    setScanLoading(false);
    setUpdatesStatus(null);
    setFirewallBlocklists(null);
    setWafRules({ status: null, default_rules: '', custom_rules: '' });
    setWafCustomRules('');
    setSelectedWafWebsiteId('');
    setWafSiteConfig(null);
    setWafAccessLogs({ entries: [], total: 0, domains: [], limit: 50, offset: 0, paths: {} });
    setWafAccessFilters({ domain: '', verdict: '', q: '', limit: 50, offset: 0 });
    setWafAccessAutoRefresh(5);
    setLogViewer(null);
    setNginxCustomEditing(null);
    setTerminalViewer(null);
    setSelectedWebsiteId('');
    setMobileMenuOpen(false);
    navigateToPage('dashboard', { replace: true });
    setError('');
    setNotice(message);
  }

  function handleAuthExpired(status, detail = '') {
    if (status === 401 || detail === 'Could not validate credentials' || detail === 'Not authenticated') {
      clearSession();
      return true;
    }
    return false;
  }

  async function request(path, options = {}, label = '') {
    try {
      setError('');
      if (label) setLoading(label);
      const { silent, ...fetchOptions } = options;
      const method = (fetchOptions.method || 'GET').toUpperCase();
      const isFormData = typeof FormData !== 'undefined' && fetchOptions.body instanceof FormData;
      const headers = isFormData ? { ...(fetchOptions.headers || {}) } : {
        'Content-Type': 'application/json',
        ...(fetchOptions.headers || {}),
      };
      // CSRF: echo the opanel_csrf cookie back in a header for mutating
      // requests. The backend rejects mismatches when the request was
      // authenticated via cookie.
      if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
        const csrf = readCookie('opanel_csrf');
        if (csrf) headers['X-CSRF-Token'] = csrf;
      }
      const res = await fetch(`${API}${path}`, {
        ...fetchOptions,
        credentials: 'include',
        headers,
      });
      const text = await res.text();
      let data;
      try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text || tr("HTTP {0}", res.status) }; }
      if (!res.ok && handleAuthExpired(res.status, data.detail)) return null;
      if (!res.ok && !silent) setError(formatApiError(data.detail, tr("Request failed with status {0}", res.status)));
      if (res.ok && data?.message && !silent) setNotice(tr(data.message));
      return res.ok ? data : null;
    } catch (err) {
      setError(tr("Cannot connect to the {0} API at {1}. Check opanel-api and the panel port.", panelSettings.app_name || tr("opanel"), API));
      return null;
    } finally {
      if (label) setLoading('');
    }
  }

  async function login(creds = null) {
    const signInAs = creds && typeof creds.username === 'string' ? creds.username : username;
    const signInWith = creds && typeof creds.password === 'string' ? creds.password : password;
    try {
      setError('');
      setLoading(tr("Logging in..."));
      const body = new URLSearchParams({ username: signInAs, password: signInWith });
      if (needsTwoFactor || otpCode) body.set('otp', otpCode);
      const res = await fetch(`${API}/auth/login`, {
        method: 'POST',
        body,
        credentials: 'include',
      });
      let data = await res.json().catch(() => ({}));
      if (res.ok && data.requires_passkey) {
        // The password was accepted and the account has a passkey, so try that
        // first. `requires_2fa` alongside it means the account also has an
        // authenticator app, which is the way through when the passkey cannot
        // be used here -- a borrowed machine, a browser without WebAuthn, a
        // key left at home.
        const codeAvailable = Boolean(data.requires_2fa);
        const fallbackToCode = (message) => {
          if (!codeAvailable) { setError(message); return false; }
          setNeedsTwoFactor(true);
          setNotice(tr("Enter your authentication code instead."));
          return true;
        };

        if (!passkeysSupported()) {
          fallbackToCode(tr("This account uses a passkey, but this browser does not support them."));
          return;
        }
        const options = data.passkey_options || {};
        options.challenge = b64urlToBuf(options.challenge);
        (options.allowCredentials || []).forEach(item => { item.id = b64urlToBuf(item.id); });
        let assertion;
        try {
          assertion = await navigator.credentials.get({ publicKey: options });
        } catch (err) {
          fallbackToCode(tr("Passkey sign-in was cancelled."));
          return;
        }
        if (!assertion) { fallbackToCode(tr("No passkey was offered.")); return; }
        const retry = new URLSearchParams({ username: signInAs, password: signInWith });
        retry.set('passkey', JSON.stringify({
          id: assertion.id,
          rawId: bufToB64url(assertion.rawId),
          type: assertion.type,
          response: {
            clientDataJSON: bufToB64url(assertion.response.clientDataJSON),
            authenticatorData: bufToB64url(assertion.response.authenticatorData),
            signature: bufToB64url(assertion.response.signature),
            userHandle: assertion.response.userHandle ? bufToB64url(assertion.response.userHandle) : null,
          },
        }));
        const second = await fetch(`${API}/auth/login`, { method: 'POST', body: retry, credentials: 'include' });
        data = await second.json().catch(() => ({}));
        if (!second.ok) {
          // A rejected passkey is not a reason to strand someone who also has
          // a code, but say what happened rather than silently switching.
          if (codeAvailable) {
            setNeedsTwoFactor(true);
            setError(formatApiError(data.detail, tr("That passkey could not be verified.")));
            setNotice(tr("Enter your authentication code instead."));
          } else {
            setError(formatApiError(data.detail, tr("That passkey could not be verified.")));
          }
          return;
        }
      }
      if (res.ok && data.requires_2fa) {
        setNeedsTwoFactor(true);
        setNotice(tr("Enter your authentication code."));
      } else if (data.access_token) {
        // Don't keep the token anywhere: the HttpOnly cookie just got set by
        // the response. JS code MUST NOT touch the JWT.
        setIsAuthenticated(true);
        setNeedsTwoFactor(false);
        setOtpCode('');
        setNotice(tr("Login successful."));
        await loadCurrentUser();
      } else {
        setError(formatApiError(data.detail, tr("Login failed with status {0}", res.status)));
      }
    } catch (err) {
      setError(tr("Cannot connect to the {0} API at {1}. Check opanel-api and the panel port.", panelSettings.app_name || tr("opanel"), API));
    } finally {
      setLoading('');
    }
  }

  async function loadPasskeys() {
    const data = await request('/auth/passkeys', {}, tr("Loading passkeys..."));
    if (data) setPasskeyStatus(data);
  }

  async function registerPasskey() {
    if (!passkeysSupported()) {
      setError(tr("This browser does not support passkeys."));
      return;
    }
    const currentPassword = prompt(tr("Enter your current password to confirm:"));
    if (!currentPassword) return;
    const body = { current_password: currentPassword };
    // Only asked for while an authenticator app is still the active factor.
    if (passkeyStatus?.totp_enabled) {
      const code = prompt(tr("Enter the 6-digit code from your authenticator:"));
      if (!code) return;
      body.code = code;
    }
    const options = await request(
      '/auth/passkeys/register/begin',
      { method: 'POST', body: JSON.stringify(body) },
      tr("Preparing passkey..."),
    );
    if (!options) return;

    options.challenge = b64urlToBuf(options.challenge);
    options.user.id = b64urlToBuf(options.user.id);
    (options.excludeCredentials || []).forEach(item => { item.id = b64urlToBuf(item.id); });

    let credential;
    try {
      credential = await navigator.credentials.create({ publicKey: options });
    } catch (err) {
      setError(tr("Passkey setup was cancelled, or this device could not create one."));
      return;
    }
    if (!credential) { setError(tr("No passkey was created.")); return; }

    const data = await request('/auth/passkeys/register/complete', {
      method: 'POST',
      body: JSON.stringify({
        name: passkeyName.trim(),
        credential: {
          id: credential.id,
          rawId: bufToB64url(credential.rawId),
          type: credential.type,
          response: {
            clientDataJSON: bufToB64url(credential.response.clientDataJSON),
            attestationObject: bufToB64url(credential.response.attestationObject),
          },
          transports: credential.response.getTransports ? credential.response.getTransports() : [],
        },
      }),
    }, tr("Saving passkey..."));
    if (data) {
      setPasskeyStatus(data);
      setPasskeyName('');
      setNotice(tr("Passkey added. It is now required to sign in."));
      await loadCurrentUser();
    }
  }

  async function removePasskey(item) {
    if (!confirm(tr("Remove the passkey \"{0}\"?\n\nIf it is the last one, this account goes back to a password only.", item.name))) return;
    const currentPassword = prompt(tr("Enter your current password to confirm:"));
    if (!currentPassword) return;
    const data = await request(`/auth/passkeys/${item.id}/delete`, {
      method: 'POST', body: JSON.stringify({ current_password: currentPassword }),
    }, tr("Removing passkey..."));
    if (data) {
      setPasskeyStatus(data);
      setNotice(tr("Removed {0}.", item.name));
      await loadCurrentUser();
    }
  }

  async function logout() {
    try {
      // Best-effort server logout: clears cookies and bumps token_version.
      await fetch(`${API}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
        headers: (() => {
          const csrf = readCookie('opanel_csrf');
          return csrf ? { 'X-CSRF-Token': csrf } : {};
        })(),
      });
    } catch {}
    clearSession(tr("Logged out."));
  }

  async function loadCurrentUser({ clearOnUnauthorized = true } = {}) {
    try {
      const res = await fetch(`${API}/auth/session`, { credentials: 'include' });
      if (!res.ok) {
        if (res.status === 401) {
          if (clearOnUnauthorized) clearSession(tr("Session expired."));
          else {
            clearReadableSessionCookies();
            setCurrentUser(null);
            setIsAuthenticated(false);
          }
        }
        return null;
      }
      const data = await res.json();
      if (!data.authenticated || !data.user) {
        if (clearOnUnauthorized) clearSession(tr("Session expired."));
        else {
          clearReadableSessionCookies();
          setCurrentUser(null);
          setIsAuthenticated(false);
        }
        return null;
      }
      setCurrentUser(data.user);
      setIsAuthenticated(true);
      return data.user;
    } catch {
      setCurrentUser(null);
      return null;
    }
  }

  async function loadPanelSettings() {
    try {
      const res = await fetch(`${API}/panel-settings/public`, { credentials: 'include' });
      if (!res.ok) return null;
      const data = await res.json();
      setPanelSettings(data);
      setPanelSettingsForm(formFromPanelSettings(data));
      return data;
    } catch {
      return null;
    }
  }

  async function loadAppVersion() {
    try {
      const res = await fetch(`${API}/health`, { credentials: 'include' });
      if (!res.ok) return;
      const data = await res.json();
      setAppVersion(data.version || '');
    } catch {}
  }

  async function savePanelSettings() {
    const wantsSsl = !!panelSettingsForm.ssl_enabled;
    const hasSsl = !!panelSettings.ssl_enabled;
    const hostname = String(panelSettingsForm.panel_hostname || '').trim();
    const port = Number(panelSettingsForm.panel_port || 2222);
    const currentHostname = panelSettings.panel_hostname || currentPanelHost();
    const hostnameChanged = hostname && hostname !== currentHostname;

    if (wantsSsl && (!hasSsl || hostnameChanged)) {
      const sslEmail = String(currentUser?.email || panelSslEmail || defaultPanelSslEmail(hostname) || '').trim();
      const nameData = await request('/panel-settings', {
        method: 'PATCH',
        body: JSON.stringify({ app_name: panelSettingsForm.app_name }),
      }, tr("Saving panel settings..."));
      if (!nameData) return;
      const sslData = await request('/panel-settings/ssl', {
        method: 'POST',
        body: JSON.stringify({ panel_hostname: hostname, panel_port: port, ...(sslEmail ? { email: sslEmail } : {}) }),
      }, tr("Installing panel SSL..."));
      if (sslData) {
        setPanelSettings(sslData);
        setPanelSettingsForm(formFromPanelSettings(sslData));
        setNotice(sslData.message || tr("Panel SSL installed. The panel may restart in a moment."));
      }
      return;
    }

    const payload = hasSsl && !wantsSsl
      ? { app_name: panelSettingsForm.app_name, panel_url: `http://${hostname}:${port}` }
      : { app_name: panelSettingsForm.app_name, panel_hostname: hostname };
    const data = await request('/panel-settings', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }, tr("Saving panel settings..."));
    if (data) {
      setPanelSettings(data);
      setPanelSettingsForm(formFromPanelSettings(data));
      setNotice(hasSsl && !wantsSsl ? tr("Panel SSL disabled. The panel remains reachable by IP and port over HTTP.") : tr("Panel settings updated."));
    }
  }

  async function uploadPanelAsset(kind) {
    const file = kind === 'logo' ? panelLogoFile : panelFaviconFile;
    if (!file) return;
    const body = new FormData();
    body.append('file', file);
    const data = await request(`/panel-settings/${kind}`, { method: 'POST', body }, tr("Uploading {0}...", kind));
    if (data) {
      setPanelSettings(data);
      setPanelSettingsForm(formFromPanelSettings(data));
      if (kind === 'logo') setPanelLogoFile(null);
      if (kind === 'favicon') setPanelFaviconFile(null);
    }
  }

  function brandInitials(value = panelSettings.app_name) {
    const words = String(value || 'opanel').trim().split(/\s+/).filter(Boolean);
    const initials = words.length > 1 ? `${words[0][0]}${words[1][0]}` : words[0]?.slice(0, 2);
    return (initials || 'BP').toUpperCase();
  }

  function renderBrandMark(extraClass = '') {
    const classes = ['brand-mark', panelSettings.logo_url ? 'has-logo' : '', extraClass].filter(Boolean).join(' ');
    return <span className={classes}>{panelSettings.logo_url ? <img src={panelSettings.logo_url} alt="" /> : brandInitials()}</span>;
  }

  // Bootstrap: ask for session state without turning an anonymous visit into
  // a console-level 401.
  useEffect(() => {
    (async () => {
      try {
        await loadCurrentUser({ clearOnUnauthorized: false });
      } catch {}
      finally { setBootstrapping(false); }
    })();
  }, []);

  useEffect(() => { loadPanelSettings(); loadAppVersion(); }, []);

  useEffect(() => {
    const appName = panelSettings.app_name || 'opanel';
    document.title = appName;
    const configuredFaviconUrl = panelSettings.favicon_url || '/favicon.png';
    const faviconUrl = configuredFaviconUrl.includes('?')
      ? configuredFaviconUrl
      : `${configuredFaviconUrl}?v=${encodeURIComponent(appVersion || 'current')}`;
    const pathname = faviconUrl.split('?', 1)[0].toLowerCase();
    const faviconType = pathname.endsWith('.ico') ? 'image/x-icon'
      : pathname.endsWith('.jpg') || pathname.endsWith('.jpeg') ? 'image/jpeg'
        : pathname.endsWith('.webp') ? 'image/webp'
          : 'image/png';
    document.querySelectorAll('link[rel~="icon"]').forEach(link => link.remove());
    const link = document.createElement('link');
    link.rel = 'icon';
    link.type = faviconType;
    link.href = faviconUrl;
    document.head.appendChild(link);
  }, [panelSettings, appVersion]);

  useEffect(() => {
    if (currentUser?.email) setPanelSslEmail(currentUser.email);
  }, [currentUser?.email]);

  async function refreshAll() {
    const [refreshedUser, siteData, dbData] = await Promise.all([
      loadCurrentUser(),
      request('/websites'),
      request('/databases'),
    ]);
    if (siteData) {
      setWebsites(siteData);
      if (!selectedWebsiteId && siteData[0]) setSelectedWebsiteId(String(siteData[0].id));
    }
    if (dbData) setDatabases(dbData);
    loadPhpVersions();
  }

  async function loadResellerPool() {
    const data = await request('/users/pool', { silent: true });
    if (data) setResellerPool(data);
  }

  async function loadUsers() {
    const data = await request('/users');
    if (data) {
      setUsers(data);
      if (!selectedBackupUserId && data[0]) setSelectedBackupUserId(String(data[0].id));
      setNewBackupSchedule(prev => (!prev.all_users && (!prev.user_ids || prev.user_ids.length === 0) && data[0]) ? ({ ...prev, user_ids: [String(data[0].id)] }) : prev);
      // Disk usage costs a du over every site an account owns, so it is not
      // part of the list. Fetch it after the rows are on screen and fill it in.
      loadUserUsage();
    }
  }

  async function loadUserUsage() {
    setUsageLoading(true);
    // No global spinner: the list is already usable, and a spinner over it
    // would undo the point of showing it early.
    const data = await request('/users/usage', {}, '');
    setUsageLoading(false);
    if (!data) return;
    const byId = new Map(data.map(row => [row.id, row]));
    setUsers(prev => prev.map(user => byId.has(user.id) ? { ...user, ...byId.get(user.id) } : user));
  }

  async function loadResourceUsage() {
    const data = await request('/services/resource-usage');
    if (data) setResourceUsage(data);
  }

  // A reseller's share: customers and disk, nothing else.
  const POOL_FIELDS = ['pool_user_limit', 'pool_storage_limit_mb'];
  // Resource limits addon: an account's own, and a reseller's caps on its group.
  const limitsOn = !!limitsInfo?.installed;
  const RL_FIELDS = ['cpu_percent', 'memory_mb', 'process_limit', 'io_read_mbps', 'io_write_mbps'];
  const limitValues = (form, prefix = '') => Object.fromEntries(RL_FIELDS.map(field => [prefix + field, Number(form[prefix + field]) || 0]));

  async function createUser() {
    const body = {
      username: newUser.username, email: newUser.email, password: newUser.password,
      role: isAdmin ? newUser.role : 'end_user',
      website_limit: Number(newUser.website_limit), storage_limit_mb: Number(newUser.storage_limit_mb),
      database_limit: Number(newUser.database_limit), mailbox_limit: Number(newUser.mailbox_limit),
    };
    if (isAdmin && newUser.role === 'end_user' && newUser.reseller_id) body.reseller_id = Number(newUser.reseller_id);
    if (isAdmin && newUser.role === 'reseller') {
      POOL_FIELDS.forEach(field => { body[field] = Number(newUser[field]) || 0; });
      body.pool_oversell = !!newUser.pool_oversell;
    }
    if (limitsOn) {
      Object.assign(body, limitValues(newUser));
      if (isAdmin && newUser.role === 'reseller') Object.assign(body, limitValues(newUser, 'group_'));
    }
    const data = await request('/users', { method: 'POST', body: JSON.stringify(body) }, tr("Creating user..."));
    if (data) {
      setNotice(tr("Created user {0}", data.username));
      setNewUser({ username: '', email: '', password: '', role: 'end_user', website_limit: 5, storage_limit_mb: 1024, database_limit: 10, mailbox_limit: 10, reseller_id: '', pool_user_limit: 0, pool_storage_limit_mb: 0, pool_oversell: false, cpu_percent: 0, memory_mb: 0, process_limit: 0, io_read_mbps: 0, io_write_mbps: 0, group_cpu_percent: 0, group_memory_mb: 0, group_process_limit: 0, group_io_read_mbps: 0, group_io_write_mbps: 0 });
      await loadUsers();
      if (isReseller) await loadResellerPool();
    }
  }

  function startEditingUser(user) {
    const matchedPlan = plans.find(p => p.website_limit === user.website_limit && p.storage_limit_mb === user.storage_limit_mb);
    setEditingUser(user);
    setEditingUserForm({
      email: user.email || '',
      role: user.role || 'end_user',
      website_limit: user.website_limit ?? 5,
      storage_limit_mb: user.storage_limit_mb ?? 1024,
      database_limit: user.database_limit ?? 10,
      mailbox_limit: user.mailbox_limit ?? 10,
      reseller_id: user.reseller_id ? String(user.reseller_id) : '',
      pool_user_limit: user.pool_user_limit ?? 0,
      pool_storage_limit_mb: user.pool_storage_limit_mb ?? 0,
      pool_oversell: !!user.pool_oversell,
      ...Object.fromEntries(RL_FIELDS.flatMap(field => [[field, user[field] ?? 0], [`group_${field}`, user[`group_${field}`] ?? 0]])),
      _password: '',
      _planId: matchedPlan ? String(matchedPlan.id) : '',
    });
  }

  function cancelEditingUser() {
    setEditingUser(null);
    setEditingUserForm({ email: '', role: 'end_user', website_limit: 5, storage_limit_mb: 1024 });
  }

  async function updatePanelUser() {
    if (!editingUser) return;
    const websiteLimit = Number(editingUserForm.website_limit);
    const storageLimitMb = Number(editingUserForm.storage_limit_mb);
    if (!editingUserForm.email.trim()) { setError(tr("Email is required.")); return; }
    if (!Number.isInteger(websiteLimit) || websiteLimit < 0 || websiteLimit > 1000) {
      setError(tr("Website limit must be between 0 and 1000."));
      return;
    }
    if (!Number.isInteger(storageLimitMb) || storageLimitMb < 0 || storageLimitMb > 1024 * 1024) {
      setError(tr("Storage limit must be between 0 and 1048576 MB."));
      return;
    }
    const payload = {
      email: editingUserForm.email.trim(),
      website_limit: websiteLimit,
      storage_limit_mb: storageLimitMb,
    };
    const mailboxLimit = Number(editingUserForm.mailbox_limit);
    if (editingUserForm.mailbox_limit !== undefined && Number.isInteger(mailboxLimit) && mailboxLimit >= 0 && mailboxLimit <= 10000) {
      payload.mailbox_limit = mailboxLimit;
    }
    const databaseLimit = Number(editingUserForm.database_limit);
    if (Number.isInteger(databaseLimit) && databaseLimit >= 0 && databaseLimit <= 1000) payload.database_limit = databaseLimit;
    if (isAdmin) {
      if (editingUser.id !== currentUser?.id) payload.role = editingUserForm.role;
      if (editingUserForm.role === 'end_user') payload.reseller_id = Number(editingUserForm.reseller_id) || 0;
      if (editingUserForm.role === 'reseller') {
        POOL_FIELDS.forEach(field => { payload[field] = Number(editingUserForm[field]) || 0; });
        payload.pool_oversell = !!editingUserForm.pool_oversell;
      }
    }
    if (limitsOn) {
      Object.assign(payload, limitValues(editingUserForm));
      if (isAdmin && editingUserForm.role === 'reseller') Object.assign(payload, limitValues(editingUserForm, 'group_'));
    }
    const data = await request(`/users/${editingUser.id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }, tr("Updating {0}...", editingUser.username));
    if (data) {
      // Change password if provided
      if (editingUserForm._password && editingUserForm._password.length >= 12) {
        const pwPayload = { password: editingUserForm._password };
        if (editingUser.id === currentUser?.id) {
          const currentPassword = prompt(tr("Enter your current password to confirm:"));
          if (!currentPassword) { setNotice(tr("User updated but password was not changed.")); cancelEditingUser(); await loadUsers(); return; }
          pwPayload.current_password = currentPassword;
          if (currentUser?.totp_enabled) {
            const code = prompt(tr("Enter your 2FA code:"));
            if (code) pwPayload.code = code.trim();
          }
        }
        await request(`/users/${editingUser.id}/password`, { method: 'POST', body: JSON.stringify(pwPayload) }, tr("Changing password for {0}...", editingUser.username));
      }
      setNotice(tr("Updated user {0}.", data.username));
      if (data.id === currentUser?.id) setCurrentUser(prev => ({ ...prev, ...data }));
      cancelEditingUser();
      await loadUsers();
      if (isReseller) await loadResellerPool();
    }
  }

  async function changeUserPassword(user) {
    const password = prompt(tr("Enter a new password for {0} (minimum 12 characters):", user.username));
    if (!password) return;
    if (password.length < 12) { setError(tr("Password must be at least 12 characters.")); return; }
    const payload = { password };
    if (user.id === currentUser?.id) {
      const currentPassword = prompt(tr("Enter your current password to confirm this change:"));
      if (!currentPassword) return;
      payload.current_password = currentPassword;
      if (currentUser?.totp_enabled) {
        const code = prompt(tr("Enter the 6-digit code from your authenticator:"));
        if (!code) return;
        payload.code = code.trim();
      }
    }
    const data = await request(`/users/${user.id}/password`, { method: 'POST', body: JSON.stringify(payload) }, tr("Changing password for {0}...", user.username));
    if (data?.message) setNotice(data.message);
  }

  async function deletePanelUser(user) {
    if (!user || user.id === currentUser?.id) return;
    if (!confirm(tr("Delete panel user {0} and permanently delete all owned websites, files, databases, and Linux user data?", user.username))) return;
    const data = await request(`/users/${user.id}`, { method: 'DELETE' }, tr("Deleting user {0}...", user.username));
    if (data) {
      const count = data.deleted_websites?.length || 0;
      setNotice(tr("Deleted user {0}{1}", user.username, count ? tr(" and {0} website(s)", count) : ''));
      await loadUsers();
      await loadWebsites();
    }
  }

  async function toggleUserActive(user) {
    if (!user) return;
    const action = user.is_active ? 'suspend' : 'unsuspend';
    if (!confirm(`${action === 'suspend' ? tr("Suspend") : tr("Unsuspend")} ${user.username}?`)) return;
    try {
      await request(`/users/${user.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: !user.is_active }),
      }, `${action === 'suspend' ? tr("Suspending") : tr("Unsuspending")} ${user.username}...`);
      setNotice(`${user.username} ${action === 'suspend' ? tr("suspended") : tr("unsuspended")}.`);
      await loadUsers();
    } catch (err) {
      setError(tr("Failed to {0} {1}: {2}", action, user.username, err?.message || err));
    }
  }

  async function quickLoginUser(user) {
    if (!user) return;
    if (!confirm(tr("Login as {0}? New websites will belong to this user.", user.username))) return;
    // Impersonation re-prompts TOTP when the calling admin has 2FA enabled.
    // Try without the code first; if the backend says one is required, ask
    // and resend. Sending the OTP via FormData keeps it out of the URL.
    let body;
    if (currentUser?.totp_enabled) {
      const code = prompt(tr("Enter the 6-digit code from your authenticator to confirm impersonation of {0}:", user.username));
      if (!code) return;
      body = new URLSearchParams({ otp: code.trim() });
    }
    const data = await request(
      `/auth/impersonate/${user.id}`,
      body
        ? { method: 'POST', body, headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }
        : { method: 'POST' },
      tr("Logging in as {0}...", user.username),
    );
    // Handle case where backend says 2FA is required (e.g., stale user object).
    if (data?.requires_2fa) {
      const code = prompt(tr("Enter the 6-digit code from your authenticator to confirm impersonation of {0}:", user.username));
      if (!code) return;
      const retryBody = new URLSearchParams({ otp: code.trim() });
      const retryData = await request(
        `/auth/impersonate/${user.id}`,
        { method: 'POST', body: retryBody, headers: { 'Content-Type': 'application/x-www-form-urlencoded' } },
        tr("Logging in as {0}...", user.username),
      );
      if (retryData?.access_token) {
        setNotice(tr("Logged in as {0}.", user.username));
        await loadCurrentUser();
        navigateToPage('websites');
        await refreshAll();
      }
      return;
    }
    if (data?.access_token) {
      setNotice(tr("Logged in as {0}.", user.username));
      await loadCurrentUser();
      navigateToPage('websites');
      await refreshAll();
    }
  }

  // Ends a "Login as" session and restores the admin's own one, which the
  // server kept aside: no second login.
  async function returnToImpersonator() {
    const admin = currentUser?.impersonator;
    if (!admin) return;
    const data = await request('/auth/impersonation/return', { method: 'POST' }, tr("Going back to {0}...", admin));
    if (data?.access_token) {
      setNotice(tr("Back to {0}.", admin));
      await loadCurrentUser();
      navigateToPage('users');
      await refreshAll();
    }
  }

  async function changeMyPassword() { if (!currentUser) return; await changeUserPassword(currentUser); }

  // --- API Tokens ---
  async function loadApiTokens() {
    const data = await request('/api-tokens');
    if (Array.isArray(data)) setApiTokens(data);
  }

  async function createApiToken() {
    if (!apiTokenForm.name.trim()) return;
    setCreatedToken(null);
    const data = await request('/api-tokens', { method: 'POST', body: JSON.stringify(apiTokenForm) }, tr("Creating API token..."));
    if (data?.token) {
      setCreatedToken(data);
      setApiTokenForm({ name: '', scopes: ['provisioning:read', 'provisioning:write'], expires_days: 365, ip_allowlist: '' });
      loadApiTokens();
    }
  }

  async function deleteApiToken(token) {
    if (!confirm(tr("Delete token \"{0}\"? This cannot be undone.", token.name))) return;
    const data = await request(`/api-tokens/${token.id}`, { method: 'DELETE' }, tr("Deleting token..."));
    if (data?.ok) { setNotice(tr("Token deleted.")); loadApiTokens(); }
  }

  // --- MCP ---
  // The endpoint an MCP client is pointed at, spelled out in full because the
  // client runs somewhere else and cannot resolve a relative path.
  const mcpEndpoint = new URL(`${API}/mcp`, window.location.origin).href;

  async function loadMcpInfo() {
    const data = await request('/mcp/info', { silent: true }, '');
    if (data) setMcpInfo(data);
    return data;
  }

  async function loadDemoInfo() {
    try {
      const res = await fetch(`${API}/demo/info`, { credentials: 'include' });
      setDemoInfo(res.ok ? await res.json() : null);
    } catch {
      setDemoInfo(null);
    }
  }

  function applyDemoSettings(data) {
    setDemoSettings(data);
    setDemoForm({
      accounts: (data.accounts || []).map(a => ({ user_id: String(a.user_id), password: a.password || '' })),
      show_on_login: data.show_on_login !== false,
    });
  }

  async function loadDemoSettings() {
    const data = await request('/demo/settings', { silent: true }, '');
    if (data) applyDemoSettings(data);
  }

  function randomDemoPassword() {
    const chars = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    return 'Demo-' + Array.from(crypto.getRandomValues(new Uint8Array(12)), b => chars[b % chars.length]).join('');
  }

  async function saveDemoSettings() {
    const accounts = demoForm.accounts
      .filter(a => a.user_id)
      .map(a => ({ user_id: Number(a.user_id), password: a.password }));
    if (!confirm(tr("Save the demo accounts?\n\nEach one gets the public password shown here, loses its two-factor sign-in and becomes read-only while Demo mode is on. An account taken off the list gets a new random password."))) return;
    const data = await request('/demo/settings', {
      method: 'PUT', body: JSON.stringify({ accounts, show_on_login: demoForm.show_on_login }),
    }, tr("Saving demo accounts..."));
    if (data) {
      applyDemoSettings(data);
      setNotice(tr("Demo accounts saved."));
    }
  }

  // --- DNS Manager ---
  async function loadDnsInfo() {
    const data = await request('/dns/overview', { silent: true }, '');
    if (data) {
      setDnsInfo(data);
      if (data.settings) setDnsSettingsForm(prev => prev || { ...data.settings });
    }
    return data;
  }

  async function loadDnsZones(page = dnsPage) {
    const params = new URLSearchParams({ page: String(page), per_page: '50' });
    if (dnsQuery.trim()) params.set('q', dnsQuery.trim());
    const data = await request(`/dns/zones?${params}`, { silent: true }, '');
    if (data) setDnsZones(data);
  }

  // Every domain on the panel has DNS: opening the page makes any zone that
  // is still missing (a new import, say) before the list is read.
  async function syncDnsZones() {
    const data = await request('/dns/sync', { method: 'POST', silent: true }, '');
    if (data?.created?.length) loadDnsZones();
  }

  async function deleteDnsZone(zone) {
    const typed = prompt(tr("Every record of {0} is deleted, and the domain stops resolving once its nameservers point here. Type the domain name to confirm.", zone.name));
    if (typed === null) return;
    if (typed.trim().toLowerCase().replace(/\.$/, '') !== zone.name) { setError(tr("The name did not match; nothing was deleted.")); return; }
    const data = await request(`/dns/zones/${zone.id}?confirm=${encodeURIComponent(typed.trim())}`, { method: 'DELETE' }, tr("Deleting the zone..."));
    if (!data) return;
    setNotice(tr("Zone {0} deleted.", data.name));
    setDnsZone(null);
    loadDnsInfo();
    loadDnsZones();
  }

  async function openDnsZone(zone) {
    if (page !== 'dns') navigateToPage('dns');
    setDnsZone({ zone, records: null });
    setDnsDelegation(null);
    setDnsRecordEdit(null);
    setDnsRecordFilter('');
    setDnsRecordForm(emptyDnsRecord());
    const data = await request(`/dns/zones/${zone.id}`, {}, '');
    if (!data) { setDnsZone(null); return; }
    setDnsZone(data);
    const check = await request(`/dns/zones/${zone.id}/delegation`, { silent: true }, '');
    setDnsDelegation(check || { status: 'unknown', expected: [], found: [] });
  }

  function dnsRecordBody(form) {
    const body = { type: form.type, name: String(form.name || '').trim() || '@', value: String(form.value || '').trim() };
    if (['MX', 'SRV'].includes(form.type) && String(form.priority ?? '').trim() !== '') body.priority = Number(form.priority);
    if (String(form.ttl ?? '').trim() !== '') body.ttl = Number(form.ttl);
    return body;
  }

  async function addDnsRecord() {
    const data = await request(`/dns/zones/${dnsZone.zone.id}/records`, { method: 'POST', body: JSON.stringify(dnsRecordBody(dnsRecordForm)) }, tr("Saving the record..."));
    if (!data) return;
    setDnsZone(data);
    setDnsRecordForm(prev => ({ ...prev, name: '', value: '', priority: '' }));
    setNotice(tr("Record added."));
  }

  async function saveDnsRecordEdit() {
    const { old, form } = dnsRecordEdit;
    const body = { old: { name: old.name, type: old.type, content: old.content }, new: dnsRecordBody(form) };
    const data = await request(`/dns/zones/${dnsZone.zone.id}/records`, { method: 'PUT', body: JSON.stringify(body) }, tr("Saving the record..."));
    if (!data) return;
    setDnsZone(data);
    setDnsRecordEdit(null);
    setNotice(tr("Record saved."));
  }

  async function deleteDnsRecord(record) {
    const question = record.mail
      ? tr("The Email addon keeps this record for {0} and writes it again when the email settings of {0} change. Delete the {1} record of {2} anyway?", record.mail, record.type, record.name)
      : tr("Delete the {0} record of {1}: {2}?", record.type, record.name, record.value);
    if (!confirm(question)) return;
    const body = { name: record.name, type: record.type, content: record.content };
    const data = await request(`/dns/zones/${dnsZone.zone.id}/records/delete`, { method: 'POST', body: JSON.stringify(body) }, tr("Deleting the record..."));
    if (!data) return;
    setDnsZone(data);
    if (dnsRecordEdit?.old?.content === record.content) setDnsRecordEdit(null);
    setNotice(tr("Record deleted."));
  }

  async function restoreDnsDefaults() {
    if (!confirm(tr("Put back the records the panel manages for {0}: nameservers, websites and email. Records you added are kept.", dnsZone.zone.name))) return;
    const data = await request(`/dns/zones/${dnsZone.zone.id}/defaults`, { method: 'POST' }, tr("Restoring the panel's records..."));
    if (!data) return;
    setDnsZone(data);
    setNotice(tr("The panel's records are back in {0}.", dnsZone.zone.name));
  }

  async function saveDnsSettings() {
    const f = dnsSettingsForm;
    const body = { ns1: f.ns1.trim(), ns2: f.ns2.trim(), hostmaster: f.hostmaster.trim(), default_ttl: Number(f.default_ttl), auto_zone: !!f.auto_zone };
    const data = await request('/dns/settings', { method: 'PUT', body: JSON.stringify(body) }, tr("Saving DNS settings..."));
    if (!data) return;
    setDnsSettingsForm({ ...data });
    setNotice(tr("DNS settings saved."));
    loadDnsInfo();
  }

  async function loadLimitsInfo() {
    const data = await request('/resource-limits', { silent: true }, '');
    if (data) setLimitsInfo(data);
  }

  async function loadLimitsHistory() {
    if (!currentUser?.id) return;
    const data = await request(`/resource-limits/${currentUser.id}/history`, { silent: true }, '');
    if (data) setLimitsHistory(data);
  }

  // --- Email ---
  async function loadMailInfo() {
    const data = await request('/mail/overview', { silent: true }, '');
    if (data) setMailInfo(data);
    return data;
  }

  function mailQuery(page) {
    const params = new URLSearchParams({ page: String(page), per_page: '50' });
    if (mailFilter.domain_id) params.set('domain_id', mailFilter.domain_id);
    if (mailFilter.q.trim()) params.set('q', mailFilter.q.trim());
    return params.toString();
  }

  async function loadMailboxes(page = mailPage) {
    const data = await request(`/mail/mailboxes?${mailQuery(page)}`, { silent: true }, '');
    if (data) setMailboxList(data);
  }

  async function loadForwarders(page = mailPage) {
    const data = await request(`/mail/forwarders?${mailQuery(page)}`, { silent: true }, '');
    if (data) setForwarderList(data);
  }

  function refreshMail() {
    loadMailInfo();
    if (mailTab === 'mailboxes') loadMailboxes();
    if (mailTab === 'forwarders') loadForwarders();
  }

  function mailPassword() {
    // The server wants at least one letter and one digit.
    let value = '';
    do { value = generateRandomPassword(16); } while (!/[A-Za-z]/.test(value) || !/\d/.test(value));
    return value;
  }

  function splitAddresses(text) {
    return String(text || '').split(/[\s,;]+/).map(item => item.trim()).filter(Boolean);
  }

  async function addMailDomain() {
    const domain = mailDomainForm.domain.trim().toLowerCase();
    if (!domain) return;
    const body = { domain };
    if (isAdmin && mailDomainForm.owner_id) body.owner_id = Number(mailDomainForm.owner_id);
    const data = await request('/mail/domains', { method: 'POST', body: JSON.stringify(body) }, tr("Turning on email..."));
    if (data) {
      setMailDomainForm({ domain: '', owner_id: '' });
      setNotice(tr("Email is on for {0}. Publish its DNS records next.", data.domain));
      await loadMailInfo();
      openMailDns(data);
    }
  }

  async function deleteMailDomain(domain) {
    const typed = prompt(tr("This deletes every mailbox of {0} with all its mail, and its forwarders. It cannot be undone.\n\nType the domain name to confirm:", domain.domain));
    if (typed === null) return;
    if (typed.trim().toLowerCase() !== domain.domain) { setError(tr("The name did not match; nothing was deleted.")); return; }
    const data = await request(`/mail/domains/${domain.id}?confirm=${encodeURIComponent(domain.domain)}`, { method: 'DELETE' }, tr("Deleting..."));
    if (data) {
      setNotice(tr("Email for {0} deleted.", domain.domain));
      if (mailFilter.domain_id === String(domain.id)) setMailFilter(prev => ({ ...prev, domain_id: '' }));
      refreshMail();
    }
  }

  async function saveCatchAll(domain) {
    const value = String(catchAllDraft[domain.id] ?? domain.catch_all ?? '').trim();
    const data = await request(`/mail/domains/${domain.id}`, { method: 'PUT', body: JSON.stringify({ catch_all: value }) }, tr("Saving..."));
    if (data) {
      setNotice(value ? tr("Mail to unknown addresses at {0} now goes to {1}.", domain.domain, value) : tr("Mail to unknown addresses at {0} is now refused.", domain.domain));
      setCatchAllDraft(prev => { const next = { ...prev }; delete next[domain.id]; return next; });
      loadMailInfo();
    }
  }

  async function rotateMailDkim(domain) {
    if (!confirm(tr("Make a new DKIM key for {0}?\n\nMail is signed with the new key at once, so update the DKIM record in DNS right away: until you do, receivers cannot verify the signature.", domain.domain))) return;
    const data = await request(`/mail/domains/${domain.id}/dkim/rotate`, { method: 'POST' }, tr("Creating a new key..."));
    if (data) { setNotice(tr("New DKIM key created. Update the DKIM record.")); openMailDns(domain); }
  }

  async function toggleWebmailHost(domain, enabled) {
    if (enabled && !confirm(tr("Serve webmail at webmail.{0}?\n\nIts A record must already point at this server: a Let's Encrypt certificate is issued for it now.", domain.domain))) return;
    if (!enabled && !confirm(tr("Stop serving webmail at webmail.{0}? Webmail stays available on the server's own address.", domain.domain))) return;
    const data = await request(`/mail/domains/${domain.id}/webmail-host`, { method: 'POST', body: JSON.stringify({ enabled }) },
      enabled ? tr("Issuing a certificate for webmail.{0}...", domain.domain) : tr("Removing..."));
    if (data) {
      setNotice(enabled ? tr("Webmail is now at https://webmail.{0}/", domain.domain) : tr("webmail.{0} removed.", domain.domain));
      loadMailInfo();
    }
  }

  async function createMailbox() {
    const body = { domain_id: Number(mailboxForm.domain_id), local_part: mailboxForm.local_part.trim().toLowerCase(), password: mailboxForm.password };
    if (String(mailboxForm.quota_mb).trim() !== '') body.quota_mb = Number(mailboxForm.quota_mb);
    const data = await request('/mail/mailboxes', { method: 'POST', body: JSON.stringify(body) }, tr("Creating mailbox..."));
    if (data) {
      setNotice(tr("{0} created.", data.address));
      setMailboxForm(prev => ({ ...prev, local_part: '', password: '' }));
      setShowCreateMailbox(false);
      loadMailboxes();
      loadMailInfo();
    }
  }

  async function saveMailboxEdit() {
    const edit = mailboxEdit;
    const body = {};
    if (edit.password) body.password = edit.password;
    if (String(edit.quota_mb) !== String(edit.box.quota_mb)) body.quota_mb = Number(edit.quota_mb);
    if (!Object.keys(body).length) { setMailboxEdit(null); return; }
    const data = await request(`/mail/mailboxes/${edit.box.id}`, { method: 'PUT', body: JSON.stringify(body) }, tr("Saving..."));
    if (data) { setNotice(tr("{0} saved.", data.address)); setMailboxEdit(null); loadMailboxes(); }
  }

  async function setMailboxEnabled(box, enabled) {
    if (!enabled && !confirm(tr("Suspend {0}?\n\nIt keeps receiving mail, but nobody can sign in to it or send from it until it is resumed.", box.address))) return;
    const data = await request(`/mail/mailboxes/${box.id}`, { method: 'PUT', body: JSON.stringify({ enabled }) }, tr("Saving..."));
    if (data) { setNotice(enabled ? tr("{0} resumed.", box.address) : tr("{0} suspended.", box.address)); loadMailboxes(); }
  }

  async function deleteMailbox(box) {
    if (!confirm(tr("Delete {0} and all of its mail?\n\nThis cannot be undone.", box.address))) return;
    const data = await request(`/mail/mailboxes/${box.id}`, { method: 'DELETE' }, tr("Deleting..."));
    if (data) { setNotice(tr("{0} deleted.", box.address)); loadMailboxes(); loadMailInfo(); }
  }

  async function openWebmail(box) {
    // Opened inside the click so no popup blocker stops it; the signed link
    // arrives a moment later.
    const win = window.open('about:blank', '_blank');
    const data = await request(`/mail/mailboxes/${box.id}/webmail`, { method: 'POST' }, tr("Opening webmail..."));
    if (data?.url) {
      if (win) { win.opener = null; win.location.href = data.url; } else window.location.href = data.url;
    } else if (win) {
      win.close();
    }
  }

  async function createForwarder() {
    const body = { domain_id: Number(forwarderForm.domain_id), local_part: forwarderForm.local_part.trim().toLowerCase(), destinations: splitAddresses(forwarderForm.destinations) };
    const data = await request('/mail/forwarders', { method: 'POST', body: JSON.stringify(body) }, tr("Creating forwarder..."));
    if (data) {
      setNotice(tr("{0} now forwards to {1}.", data.address, data.destinations.join(', ')));
      setForwarderForm(prev => ({ ...prev, local_part: '', destinations: '' }));
      setShowCreateForwarder(false);
      loadForwarders();
      loadMailInfo();
    }
  }

  async function saveForwarderEdit() {
    const data = await request(`/mail/forwarders/${forwarderEdit.item.id}`, { method: 'PUT', body: JSON.stringify({ destinations: splitAddresses(forwarderEdit.destinations) }) }, tr("Saving..."));
    if (data) { setNotice(tr("{0} saved.", data.address)); setForwarderEdit(null); loadForwarders(); }
  }

  async function deleteForwarder(item) {
    if (!confirm(tr("Delete the forwarder {0}?", item.address))) return;
    const data = await request(`/mail/forwarders/${item.id}`, { method: 'DELETE' }, tr("Deleting..."));
    if (data) { setNotice(tr("{0} deleted.", item.address)); loadForwarders(); loadMailInfo(); }
  }

  function applyMailDnsView(domain, data) {
    const custom = data?.custom || {};
    setMailDns({ domain, records: data?.records || [], relay: data?.relay || null, canCustomize: !!data?.can_customize, dnsZone: data?.dns_zone || null });
    setDnsCustomForm({
      spf: custom.spf || '',
      dmarc: custom.dmarc || '',
      records: (custom.records || []).map(r => ({ ...r, priority: r.priority ?? '' })),
    });
  }

  async function openMailDns(domain) {
    setMailDns({ domain, records: null, relay: null });
    const data = await request(`/mail/domains/${domain.id}/dns`, { silent: true }, '');
    applyMailDnsView(domain, data);
  }

  function dnsRecordsBody(rows) {
    return rows
      .filter(r => String(r.value || '').trim())
      .map(r => ({
        type: r.type,
        name: String(r.name || '@').trim() || '@',
        value: String(r.value).trim(),
        ...(r.type === 'MX' && String(r.priority ?? '').trim() !== '' ? { priority: Number(r.priority) } : {}),
      }));
  }

  async function saveDnsCustom() {
    const f = dnsCustomForm;
    const body = { spf: f.spf.trim(), dmarc: f.dmarc.trim(), records: dnsRecordsBody(f.records) };
    const data = await request(`/mail/domains/${mailDns.domain.id}/dns`, { method: 'PUT', body: JSON.stringify(body) }, tr("Saving..."));
    if (data) { setNotice(tr("Saved. Publish the records at the DNS provider of {0}.", mailDns.domain.domain)); applyMailDnsView(mailDns.domain, data); }
  }

  async function saveDomainRelay(value) {
    const data = await request(`/mail/domains/${mailDns.domain.id}/relay`, { method: 'PUT', body: JSON.stringify({ relay: value }) }, tr("Saving..."));
    if (data) { setNotice(tr("Outgoing mail for {0} saved.", mailDns.domain.domain)); applyMailDnsView(mailDns.domain, data); }
  }

  // --- Email: relays (administrators) ---
  async function loadMailRelays() {
    const data = await request('/mail/relays', {}, '');
    if (data) setMailRelays(data);
  }

  function editRelay(relay) {
    setRelayForm(relay
      ? { ...relay, password: '', dns_records: (relay.dns_records || []).map(r => ({ ...r, priority: r.priority ?? '' })), make_default: false }
      : { name: '', host: '', port: 587, tls: 'starttls', username: '', password: '', spf_include: '', dns_records: [],
          make_default: !(mailRelays?.relays || []).length });
  }

  async function saveRelay() {
    const f = relayForm;
    const body = {
      name: f.name.trim(), host: f.host.trim(), port: Number(f.port) || 587, tls: f.tls,
      username: f.username.trim(), password: f.password, spf_include: f.spf_include.trim(),
      dns_records: dnsRecordsBody(f.dns_records), make_default: !!f.make_default,
    };
    const data = await request(f.id ? `/mail/relays/${f.id}` : '/mail/relays', { method: f.id ? 'PUT' : 'POST', body: JSON.stringify(body) }, tr("Saving relay..."));
    if (data) { setMailRelays(data); setRelayForm(null); setNotice(tr("Relay saved. Domains that use it need its DNS records.")); }
  }

  async function deleteRelay(relay) {
    if (!confirm(tr("Delete the relay {0}?\n\nDomains that use it go back to the default relay.", relay.name))) return;
    const data = await request(`/mail/relays/${relay.id}`, { method: 'DELETE' }, tr("Deleting..."));
    if (data) { setMailRelays(data); setNotice(tr("{0} deleted.", relay.name)); }
  }

  async function setDefaultRelay(relayId) {
    const data = await request('/mail/default-relay', { method: 'PUT', body: JSON.stringify({ relay_id: relayId }) }, tr("Saving..."));
    if (data) { setMailRelays(data); setNotice(relayId ? tr("Default relay saved.") : tr("Mail now leaves directly, except for domains with a relay of their own.")); }
  }

  // --- Email: Rspamd and logs (administrators) ---
  async function loadRspamdStat() {
    const data = await request('/mail/rspamd/stat', {}, '');
    setRspamdStat(data || null);
  }

  async function loadRspamdHistory(page = rspamdFilter.page, action = rspamdFilter.action) {
    const params = new URLSearchParams({ page: String(page), per_page: '50' });
    if (rspamdFilter.q.trim()) params.set('q', rspamdFilter.q.trim());
    if (action) params.set('action', action);
    const data = await request(`/mail/rspamd/history?${params}`, {}, '');
    setRspamdHistory(data || { items: [], total: 0, page: 1, per_page: 50 });
    setRspamdFilter(prev => ({ ...prev, page, action }));
  }

  async function loadRspamdLog(query = rspamdLogQuery) {
    const params = new URLSearchParams({ lines: String(query.lines) });
    if (query.q.trim()) params.set('q', query.q.trim());
    const data = await request(`/mail/rspamd/log?${params}`, {}, '');
    setRspamdLog(data?.lines || []);
  }

  async function loadEximLog(query = eximLogQuery) {
    const params = new URLSearchParams({ lines: String(query.lines) });
    if (query.q.trim()) params.set('q', query.q.trim());
    const data = await request(`/mail/log?${params}`, {}, '');
    setEximLog(data?.lines || []);
  }

  function applyMailSettings(data) {
    setMailSettings(data);
    setMailSettingsForm({ ...data.settings });
  }

  async function loadMailSettings() {
    const data = await request('/mail/settings', { silent: true }, '');
    if (data) applyMailSettings(data);
  }

  async function saveMailSettings() {
    const f = mailSettingsForm;
    const body = {
      auth_rate_per_hour: Number(f.auth_rate_per_hour) || 0,
      local_rate_per_hour: Number(f.local_rate_per_hour) || 0,
      max_message_mb: Number(f.max_message_mb) || 50,
      spam_header_score: Number(f.spam_header_score) || 6,
      spam_reject_score: Number(f.spam_reject_score) || 15,
      greylisting: !!f.greylisting,
      default_quota_mb: Number(f.default_quota_mb) || 1024,
    };
    const data = await request('/mail/settings', { method: 'PUT', body: JSON.stringify(body) }, tr("Applying mail settings..."));
    if (data) { setNotice(tr("Mail settings applied.")); loadMailSettings(); }
  }

  async function loadNotifyInfo() {
    const data = await request('/notifications/info', { silent: true }, '');
    if (data) setNotifyInfo(data);
    return data;
  }

  function applyNotifySettings(data) {
    setNotifySettings(data);
    setNotifyForm({ ...data, smtp_password: '', telegram_bot_token: '' });
  }

  async function loadNotifications() {
    const info = await loadNotifyInfo();
    if (!info?.enabled) return;
    const prefs = await request('/notifications/me', {}, '');
    if (prefs) setNotifyPrefs(prefs);
    if (isAdmin) {
      const data = await request('/notifications/settings', {}, '');
      if (data) applyNotifySettings(data);
    }
  }

  async function saveNotifySettings(extra = {}) {
    const f = notifyForm || {};
    const body = {
      language: f.language, email_enabled: !!f.email_enabled, smtp_host: f.smtp_host || '', smtp_port: Number(f.smtp_port) || 587,
      smtp_security: f.smtp_security, smtp_username: f.smtp_username || '', from_address: f.from_address || '', from_name: f.from_name || '',
      telegram_enabled: !!f.telegram_enabled, admin_emails: f.admin_emails || '', admin_telegram_chats: f.admin_telegram_chats || '',
      admin_events: f.admin_events || {}, ...extra,
    };
    if (f.smtp_password) body.smtp_password = f.smtp_password;
    if (f.telegram_bot_token) body.telegram_bot_token = f.telegram_bot_token;
    const data = await request('/notifications/settings', { method: 'PUT', body: JSON.stringify(body) }, tr("Saving notification settings..."));
    if (data) {
      applyNotifySettings(data);
      setNotice(tr("Notification settings saved."));
      const prefs = await request('/notifications/me', {}, '');
      if (prefs) setNotifyPrefs(prefs);
    }
  }

  async function sendNotifyTest(channel) {
    const data = await request('/notifications/test', { method: 'POST', body: JSON.stringify({ channel, recipient: (notifyTest[channel] || '').trim() }) },
      tr("Sending a test..."));
    if (data) setNotice(channel === 'email' ? tr("Test email sent.") : tr("Test Telegram message sent."));
  }

  async function saveNotifyPrefs(patch) {
    const data = await request('/notifications/me', { method: 'PUT', body: JSON.stringify(patch) }, '');
    if (data) setNotifyPrefs(data);
  }

  async function startTelegramLink() {
    const data = await request('/notifications/me/telegram/link', { method: 'POST' }, '');
    if (data?.url) {
      setNotifyLink(data);
      window.open(data.url, '_blank', 'noopener');
    }
  }

  async function verifyTelegramLink() {
    const data = await request('/notifications/me/telegram/verify', { method: 'POST' }, tr("Checking Telegram..."));
    if (data) { setNotifyPrefs(data); setNotifyLink(null); setNotice(tr("Telegram linked.")); }
  }

  async function unlinkTelegram() {
    if (!confirm(tr("Stop sending notifications to your Telegram?"))) return;
    const data = await request('/notifications/me/telegram', { method: 'DELETE' }, '');
    if (data) setNotifyPrefs(data);
  }

  async function sendMyNotifyTest() {
    const data = await request('/notifications/me/test', { method: 'POST' }, tr("Sending a test..."));
    if (data) setNotice(tr("Test sent to your channels."));
  }

  async function loadDashboardSummary() {
    const data = await request('/dashboard/summary', { silent: true }, '');
    if (data) setDashSummary(data);
  }

  async function loadSftp() {
    const data = await request('/sftp', { silent: true }, '');
    if (data) setSftpInfo(data);
  }

  async function createSftpAccount() {
    const body = {
      suffix: sftpForm.suffix.trim().toLowerCase(),
      website_id: sftpForm.website_id ? Number(sftpForm.website_id) : null,
      subpath: sftpForm.subpath.trim(),
      password: sftpForm.password,
      ...(isAdmin && sftpForm.owner_id ? { owner_id: Number(sftpForm.owner_id) } : {}),
    };
    const data = await request('/sftp/accounts', { method: 'POST', body: JSON.stringify(body) }, tr("Creating SFTP account..."));
    if (data) {
      setNotice(tr("SFTP account {0} created.", data.username));
      setSftpForm(prev => ({ ...prev, suffix: '', subpath: '', password: '' }));
      setShowCreateSftp(false);
      await loadSftp();
    }
  }

  async function saveSftpPassword() {
    if (!sftpPasswordFor) return;
    const { account, password } = sftpPasswordFor;
    const data = await request(`/sftp/accounts/${account.id}/password`, { method: 'POST', body: JSON.stringify({ password }) }, tr("Changing password..."));
    if (data) { setSftpPasswordFor(null); setNotice(tr("Password of {0} changed.", account.username)); }
  }

  async function deleteSftpAccount(account) {
    if (!confirm(tr("Delete SFTP account {0}? Files in its folder are kept.", account.username))) return;
    const data = await request(`/sftp/accounts/${account.id}`, { method: 'DELETE' }, tr("Deleting SFTP account..."));
    if (data) { setNotice(tr("SFTP account {0} deleted.", account.username)); await loadSftp(); }
  }

  async function loadMcp() {
    const info = await loadMcpInfo();
    if (!info) return;
    const mine = await request('/mcp/tokens', {}, '');
    if (Array.isArray(mine)) setMcpTokens(mine);
  }

  async function loadMcpAllTokens() {
    const data = await request('/mcp/tokens?all=true', {}, '');
    if (Array.isArray(data)) setMcpAllTokens(data);
  }

  async function createMcpToken() {
    if (!mcpForm.name.trim()) return;
    setMcpCreated(null);
    const body = {
      name: mcpForm.name.trim(),
      can_write: mcpForm.can_write,
      expires_days: Number(mcpForm.expires_days) || 90,
    };
    const data = await request('/mcp/tokens', { method: 'POST', body: JSON.stringify(body) }, tr("Creating MCP token..."));
    if (data?.token) {
      setMcpCreated(data);
      setMcpForm({ name: '', can_write: false, expires_days: 90 });
      loadMcp();
    }
  }

  async function revokeMcpToken(token, fromAddonPanel = false) {
    const owner = token.username && token.username !== currentUser?.username ? ` (${token.username})` : '';
    if (!confirm(tr("Revoke MCP token \"{0}\"{1}? Anything using it stops working at once.", token.name, owner))) return;
    const data = await request(`/mcp/tokens/${token.id}`, { method: 'DELETE' }, tr("Revoking token..."));
    if (data?.ok) {
      setNotice(tr("MCP token revoked."));
      loadMcp();
      if (fromAddonPanel) loadMcpAllTokens();
    }
  }

  // --- Profile ---
  async function updateMyEmail() {
    if (!profileForm.email.trim()) return;
    const data = await request('/users/me', { method: 'PATCH', body: JSON.stringify({ email: profileForm.email }) }, tr("Updating email..."));
    if (data?.email) {
      setCurrentUser(data);
      setNotice(tr("Email updated."));
      setProfileForm(prev => ({ ...prev, email: data.email }));
    }
  }

  async function changeMyPasswordFromProfile() {
    if (!profileForm.password || profileForm.password.length < 12) { setError(tr("Password must be at least 12 characters.")); return; }
    const payload = { password: profileForm.password };
    if (profileForm.current_password) payload.current_password = profileForm.current_password;
    if (profileForm.code) payload.code = profileForm.code;
    const data = await request(`/users/${currentUser.id}/password`, { method: 'POST', body: JSON.stringify(payload) }, tr("Changing password..."));
    if (data?.message) {
      setNotice(data.message);
      setProfileForm(prev => ({ ...prev, password: '', current_password: '', code: '' }));
    }
  }

  function openProfileModal() {
    setProfileForm({ email: currentUser?.email || '', password: '', current_password: '', code: '' });
    setShowProfileModal(true);
  }

  // --- Hosting Plans ---
  async function loadPlans() {
    const data = await request('/plans');
    if (Array.isArray(data)) setPlans(data);
  }

  // --- WordPress Install on existing site ---
  function openWpInstall(site) {
    setWpManagerSite(site);
    setWpManagerMode('install');
    setWpInstallForm({ admin_user: 'admin', admin_email: currentUser?.email || '', admin_password: '', title: site.domain });
  }

  function openWpUpdate(site) {
    setWpManagerSite(site);
    setWpManagerMode('update');
  }

  async function installWpOnSite() {
    if (!wpManagerSite || wpManagerMode !== 'install') return;
    if (!wpInstallForm.admin_password || wpInstallForm.admin_password.length < 12) { setError(tr("WP admin password must be at least 12 characters.")); return; }
    const data = await request(`/maintenance/wordpress/${wpManagerSite.id}/install`, {
      method: 'POST', body: JSON.stringify(wpInstallForm),
    }, tr("Installing WordPress on {0}...", wpManagerSite.domain));
    if (data?.message) {
      setNotice(tr("{0}\nAdmin: {1} | Password: {2}", data.message, data.admin_user, wpInstallForm.admin_password));
      setWpManagerSite(null);
      refreshAll();
    }
  }

  async function updateWpOnSite() {
    if (!wpManagerSite || wpManagerMode !== 'update') return;
    const data = await request(`/maintenance/wordpress/${wpManagerSite.id}/update-all`, {
      method: 'POST',
    }, tr("Updating WordPress on {0}...", wpManagerSite.domain));
    if (data?.message) {
      setNotice(data.message);
      setWpManagerSite(null);
    }
  }

  async function createPlan() {
    if (!newPlan.slug.trim() || !newPlan.name.trim()) return;
    const data = await request('/plans', { method: 'POST', body: JSON.stringify({ ...newPlan, ...limitValues(newPlan) }) }, tr("Creating plan..."));
    if (data?.id) {
      setNotice(tr("Plan \"{0}\" created.", data.name));
      setNewPlan({ slug: '', name: '', website_limit: 1, storage_limit_mb: 1024, database_limit: 10, mailbox_limit: 10, cpu_percent: 0, memory_mb: 0, process_limit: 0, io_read_mbps: 0, io_write_mbps: 0, php_version: '8.4', app_type: 'php', auto_ssl: false });
      loadPlans();
    }
  }

  function startEditingPlan(plan) {
    setEditingPlan(plan);
    setEditingPlanForm({ name: plan.name, website_limit: plan.website_limit, storage_limit_mb: plan.storage_limit_mb, database_limit: plan.database_limit ?? 10, mailbox_limit: plan.mailbox_limit ?? 10, ...limitValues(plan), php_version: plan.php_version, app_type: plan.app_type, auto_ssl: plan.auto_ssl, active: plan.active });
  }

  async function updatePlan() {
    if (!editingPlan) return;
    const data = await request(`/plans/${editingPlan.id}`, { method: 'PATCH', body: JSON.stringify({ ...editingPlanForm, ...limitValues(editingPlanForm) }) }, tr("Updating plan..."));
    if (data?.id) { setNotice(tr("Plan updated.")); setEditingPlan(null); loadPlans(); }
  }

  async function deletePlanItem(plan) {
    if (!confirm(tr("Delete plan \"{0}\"?", plan.name))) return;
    const data = await request(`/plans/${plan.id}`, { method: 'DELETE' }, tr("Deleting plan..."));
    if (data?.ok) { setNotice(tr("Plan deleted.")); setEditingPlan(null); loadPlans(); }
  }

  async function loadTwoFactorStatus() {
    const data = await request('/auth/2fa/status');
    if (data) setTwoFactorStatus(data);
  }

  async function setupTwoFactorAuth() {
    const currentPassword = prompt(tr("Enter your current password to generate a new 2FA secret:"));
    if (!currentPassword) return;
    const payload = { current_password: currentPassword };
    if (currentUser?.totp_enabled) {
      const code = prompt(tr("Enter the 6-digit code from your authenticator:"));
      if (!code) return;
      payload.code = code.trim();
    }
    const data = await request('/auth/2fa/setup', { method: 'POST', body: JSON.stringify(payload) }, tr("Preparing 2FA..."));
    if (data) {
      setTwoFactorSetup(data);
      setTwoFactorStatus({ enabled: false });
    }
  }

  async function enableTwoFactorAuth() {
    const data = await request('/auth/2fa/enable', { method: 'POST', body: JSON.stringify({ code: twoFactorCode }) }, tr("Enabling 2FA..."));
    if (data) {
      setTwoFactorStatus(data);
      setTwoFactorSetup(null);
      setTwoFactorCode('');
      await loadCurrentUser();
      setNotice(tr("2FA enabled."));
    }
  }

  async function disableTwoFactorAuth() {
    const currentPassword = prompt(tr("Enter your current password to disable 2FA:"));
    if (!currentPassword) return;
    const data = await request(
      '/auth/2fa/disable',
      { method: 'POST', body: JSON.stringify({ current_password: currentPassword, code: twoFactorCode }) },
      tr("Disabling 2FA..."),
    );
    if (data) {
      setTwoFactorStatus(data);
      setTwoFactorCode('');
      await loadCurrentUser();
      setNotice(tr("2FA disabled."));
    }
  }

  async function resetUserTwoFactor(user) {
    if (!confirm(tr("Reset 2FA for {0}?", user.username))) return;
    const data = await request(`/users/${user.id}/2fa/reset`, { method: 'POST' }, tr("Resetting 2FA for {0}...", user.username));
    if (data?.message) { setNotice(data.message); await loadUsers(); }
  }

  async function loadNetworkStatus() {
    const data = await request('/panel-settings/network', { silent: true }, '');
    if (data) setNetworkStatus(data);
    return data;
  }

  async function toggleIpv6(enable) {
    const data = await request('/panel-settings/network/ipv6', {
      method: 'POST',
      body: JSON.stringify({ enabled: enable }),
    }, enable ? tr("Enabling IPv6...") : tr("Disabling IPv6..."));
    if (data) {
      setNetworkStatus(data);
      setNotice(data.message || tr("IPv6 {0}.", enable ? tr("enabled") : tr("disabled")));
    }
  }

  async function loadMalwareScanStatus(quiet = false) {
    const data = await request('/panel-settings/malware-scan', quiet ? { silent: true } : {},
      quiet ? '' : tr("Loading malware scan status..."));
    if (data) setMalwareScanStatus(data);
  }

  async function toggleMalwareRealtime(enable) {
    if (enable && !confirm(
      tr("Turn on real-time protection?\n\n")
      + tr("• Linux Malware Detect watches every file under /home and the temp directories (/tmp, /var/tmp, /dev/shm) with inotify, and scans new or changed files within seconds.\n")
      + tr("• It uses more RAM the more files it watches — heavy on a box with many WordPress sites.\n")
      + tr("• Hits are NOT auto-quarantined — they are listed for you to act on.\n\n")
      + tr("Turning it off returns to scheduled scans only.")
    )) return;
    const data = await request('/panel-settings/malware-scan/realtime', {
      method: 'POST',
      body: JSON.stringify({ enabled: enable }),
    }, enable ? tr("Enabling real-time protection...") : tr("Disabling real-time protection..."));
    if (data) {
      setPanelSettings(data);
      setNotice(data.message || tr("Real-time protection {0}.", enable ? tr("enabled") : tr("disabled")));
      await loadMalwareScanStatus();
    }
  }

  async function updateMalwareSignatures() {
    const data = await request('/panel-settings/malware-scan/update-signatures', { method: 'POST' }, tr("Updating malware signatures..."));
    if (data) { setNotice(data.detail || tr("Signatures updated.")); await loadMalwareScanStatus(); }
  }

  async function toggleAutoQuarantine(enable) {
    const data = await request('/panel-settings/malware-scan/auto-quarantine', {
      method: 'POST',
      body: JSON.stringify({ enabled: enable }),
    }, 'Saving...');
    if (data) { setPanelSettings(data); setNotice(data.message || ''); await loadMalwareScanStatus(); }
  }

  async function loadQuarantine() {
    const data = await request('/panel-settings/malware-scan/quarantine', { silent: true }, '');
    if (data && Array.isArray(data.entries)) setQuarantine(data.entries);
  }

  async function quarantineThreat(path, signature) {
    if (!confirm(tr("Move this file out of the site into quarantine?\n\n{0}\n\nIt stops being served immediately. You can restore it from the Quarantine list if it turns out to be a false positive.", path))) return;
    const data = await request('/panel-settings/malware-scan/quarantine', {
      method: 'POST',
      body: JSON.stringify({ path, signature: signature || '' }),
    }, tr("Quarantining file..."));
    if (data && Array.isArray(data.entries)) { setQuarantine(data.entries); setNotice(tr("File moved to quarantine.")); }
  }

  async function restoreQuarantine(id, path) {
    if (!confirm(tr("Restore this file to its original location?\n\n{0}\n\nOnly do this if you are sure it is a false positive — it will be served again.", path))) return;
    const data = await request(`/panel-settings/malware-scan/quarantine/${id}/restore`, { method: 'POST' }, tr("Restoring file..."));
    if (data && Array.isArray(data.entries)) { setQuarantine(data.entries); setNotice(tr("File restored to its original location.")); }
  }

  async function deleteQuarantine(id, path) {
    if (!confirm(tr("Permanently delete this quarantined file?\n\n{0}\n\nThis cannot be undone.", path))) return;
    const data = await request(`/panel-settings/malware-scan/quarantine/${id}`, { method: 'DELETE' }, tr("Deleting quarantined file..."));
    if (data && Array.isArray(data.entries)) { setQuarantine(data.entries); setNotice(tr("Quarantined file deleted.")); }
  }

  async function installLmd() {
    if (!confirm(tr("Add Linux Malware Detect to this server?\n\nLMD layers its web-focused signatures on top of ClamAV (catches PHP shells ClamAV misses) and uses the running clamd as its engine. Install runs in the background."))) return;
    const data = await request('/panel-settings/malware-scan/install-lmd', { method: 'POST' }, tr("Installing Linux Malware Detect..."));
    if (data) { setPanelSettings(data); setNotice(data.message || tr("Linux Malware Detect install started.")); await loadMalwareScanStatus(); }
  }

  async function runMalwareScan() {
    if (!scanTargetWebsiteId) return;
    if (scanTargetWebsiteId === 'system'
      && !confirm(tr("Scan the whole server (/) now? The first full scan can run for a long time."))) return;
    setScanResults(null);
    setScanJob(null);
    setScanLoading(true);
    try {
      const body = scanTargetWebsiteId === 'system'
        ? { scope: 'system', scan_root: '/' }
        : scanTargetWebsiteId === 'all'
          ? { all: true }
          : { website_id: Number(scanTargetWebsiteId) };
      const data = await request('/panel-settings/malware-scan/run', {
        method: 'POST',
        body: JSON.stringify(body),
      }, tr("Starting malware scan..."));
      if (data) {
        setScanJob(data);
        await loadMalwareScanJobs();
        setNotice(tr("Malware scan started."));
      }
    } finally {
      setScanLoading(false);
    }
  }

  async function loadMalwareSchedule() {
    const data = await request('/panel-settings/malware-scan/schedule', { silent: true }, '');
    if (data && data.system && data.web) setMalwareSchedules(data);
    return data;
  }

  async function saveMalwareSchedule(scope) {
    const s = malwareSchedules[scope] || {};
    const data = await request('/panel-settings/malware-scan/schedule', {
      method: 'PUT',
      body: JSON.stringify({
        [scope]: {
          enabled: !!s.enabled,
          frequency: s.frequency || 'weekly',
          hour: Number(s.hour) || 0,
          minute: Number(s.minute) || 0,
          weekday: Number(s.weekday) || 0,
          day: Number(s.day) || 1,
        },
      }),
    }, tr("Saving scan schedule..."));
    if (data && data.system && data.web) {
      setMalwareSchedules(data);
      setNotice(data[scope]?.enabled ? tr("Scheduled scan saved.") : tr("Scheduled scan disabled."));
    }
  }

  async function loadMalwareScanJob(jobId) {
    const data = await request(`/panel-settings/malware-scan/jobs/${jobId}`, {}, '');
    if (!data) return null;
    if (['done', 'infected', 'error', 'interrupted'].includes(data.status)) {
      setScanLoading(false);
      setScanJob(null);
      setScanResults(null);
      if (data.status === 'infected' || data.infected > 0) {
        setNotice(tr("{0} threat(s) found.", data.infected));
      } else if (['error', 'interrupted'].includes(data.status)) {
        setError(data.error || data.message || tr("Malware scan failed."));
      } else {
        setNotice(tr("Scan complete: {0} files scanned, no threats found.", data.scanned || 0));
      }
      await loadMalwareScanJobs();
    } else {
      setScanJob(data);
    }
    return data;
  }

  async function loadMalwareScanJobs() {
    const data = await request('/panel-settings/malware-scan/jobs', { silent: true }, '');
    if (data?.jobs) setScanJobs(data.jobs);
    return data?.jobs || [];
  }

  function showMalwareScanJob(job) {
    setScanJob(job);
    setScanResults(job);
    setScanLoading(['queued', 'running'].includes(job?.status));
  }

  // A history entry opens on its own page: summary, threats and the log.
  async function openMalwareScanDetail(job) {
    setMalwareDetailJob(job);
    window.scrollTo({ top: 0, behavior: 'smooth' });
    const data = await request(`/panel-settings/malware-scan/jobs/${job.job_id}`, { silent: true }, '');
    if (data) setMalwareDetailJob(data);
  }

  async function loadLatestMalwareScanJob() {
    const data = await request('/panel-settings/malware-scan/jobs/latest', { silent: true }, '');
    if (!data) return null;
    if (['done', 'infected', 'error', 'interrupted'].includes(data.status)) {
      setScanJob(null);
      setScanResults(null);
      setScanLoading(false);
    } else {
      setScanJob(data);
    }
    return data;
  }

  async function startClamavDaemon() {
    const data = await request('/panel-settings/malware-scan/start', { method: 'POST' }, tr("Starting ClamAV daemon..."));
    if (data) {
      setNotice(data.message || tr("ClamAV daemon started."));
      await loadMalwareScanStatus();
    }
  }

  async function assignDomainToUser() {
    if (!assignWebsiteId || !assignUserId) return;
    const data = await request(`/websites/${assignWebsiteId}`, { method: 'PATCH', body: JSON.stringify({ owner_id: Number(assignUserId) }) }, tr("Assigning domain to user..."));
    if (data) {
      // Name the account, not its row id -- the operator picked a username from
      // the dropdown and has no idea which number that was.
      const owner = users.find(user => String(user.id) === String(assignUserId));
      setNotice(tr("{0} now belongs to {1}.", data.domain, owner?.username || tr("user #{0}", assignUserId)));
      await refreshAll();
    }
  }

  async function createWordPress() {
    const cleanDomain = domain.trim().toLowerCase();
    const cleanAdminEmail = adminEmail.trim();
    if (!cleanDomain) { setError(tr("Please enter a domain name.")); return; }
    const installWp = siteType === 'wordpress' && installWordPress;
    const body = {
      domain: cleanDomain,
      php_version: phpVersion,
      app_type: siteType,
      install_wordpress: installWp,
      title: cleanDomain,
    };
    if (siteOwnerId) body.owner_id = Number(siteOwnerId);
    if (installWp) {
      body.admin_user = wpAdminUser;
      body.admin_email = cleanAdminEmail || `admin@${cleanDomain}`;
      body.admin_password = wpAdminPassword || 'StrongPass123!';
    }
    const data = await request('/websites', { method: 'POST', body: JSON.stringify(body) },
      installWp ? tr("Creating WordPress website...") : tr("Creating website..."));
    if (data) {
      if (installWp) {
        setNotice(tr("Created WordPress site: https://{0}\nAdmin: {1} | Password: {2}", cleanDomain, wpAdminUser, wpAdminPassword || tr("StrongPass123!")));
      } else {
        setNotice(tr("Created site {0}. Upload your files to public_html/ folder.", cleanDomain));
      }
      if (installSslAfterCreate) await applySslForNewSite(data.id, cleanDomain);
      refreshAll();
    }
  }

  // Wildcard certificates: over this server's zone when DNS Manager is on,
  // else Cloudflare.
  function wildcardProvider(chosen, site) {
    if (chosen) return chosen;
    if (site?.ssl_wildcard && site?.ssl_dns_provider) return site.ssl_dns_provider;
    return dnsInfo?.installed ? 'opanel' : 'cloudflare';
  }

  function renderWildcardProvider(value, onChange) {
    if (!dnsInfo?.installed) return null;
    return <div className="segmented wildcard-provider" role="radiogroup" aria-label={tr("DNS for the challenge")}>
      <button type="button" className={value === 'opanel' ? 'active' : ''} aria-pressed={value === 'opanel'} onClick={() => onChange('opanel')}><Network size={13}/> {tr("This server's DNS")}</button>
      <button type="button" className={value === 'cloudflare' ? 'active' : ''} aria-pressed={value === 'cloudflare'} onClick={() => onChange('cloudflare')}><Globe size={13}/> Cloudflare</button>
    </div>;
  }

  async function applySslForNewSite(id, siteDomain) {
    if (createSslMode === 'wildcard' && wildcardProvider(createSslForm.provider) === 'opanel') {
      const b = { provider: 'opanel' };
      const em = String(createSslForm.email || '').trim(); if (em) b.email = em;
      await request(`/websites/${id}/ssl/wildcard`, { method: 'POST', body: JSON.stringify(b) }, tr("Issuing wildcard certificate..."));
    } else if (createSslMode === 'wildcard') {
      const token = String(createSslForm.api_token || '').trim();
      if (!token) { setError(tr("Enter a Cloudflare API token for wildcard SSL.")); return; }
      const b = { provider: 'cloudflare', api_token: token };
      const em = String(createSslForm.email || '').trim(); if (em) b.email = em;
      await request(`/websites/${id}/ssl/wildcard`, { method: 'POST', body: JSON.stringify(b) }, tr("Issuing wildcard certificate..."));
    } else if (createSslMode === 'existing') {
      if (!createSslForm.reuse_name) { setError(tr("Pick a certificate to use.")); return; }
      await request(`/websites/${id}/ssl/reuse`, { method: 'POST', body: JSON.stringify({ name: createSslForm.reuse_name }) }, tr("Applying existing certificate..."));
    } else if (createSslMode === 'manual') {
      const form = new FormData();
      form.append('certificate_text', createSslForm.certificate);
      form.append('private_key_text', createSslForm.private_key);
      if (createSslForm.ca_bundle.trim()) form.append('ca_bundle_text', createSslForm.ca_bundle);
      await request(`/websites/${id}/ssl/manual`, { method: 'POST', body: form }, tr("Installing manual SSL..."));
    } else {
      await request(`/websites/${id}/ssl`, { method: 'POST' }, tr("Installing Let's Encrypt SSL..."));
    }
  }

  async function loadCreateSslCerts() {
    const cleanDomain = domain.trim().toLowerCase();
    if (!cleanDomain) { setCreateSslCerts([]); return; }
    const data = await request(`/websites/available-certificates?domain=${encodeURIComponent(cleanDomain)}`, { silent: true });
    setCreateSslCerts(Array.isArray(data) ? data : []);
  }

  // Keep the "Use existing" cert list in sync with the domain field.
  useEffect(() => {
    if (!installSslAfterCreate || createSslMode !== 'existing') return;
    const t = setTimeout(loadCreateSslCerts, 400);
    return () => clearTimeout(t);
  }, [domain, createSslMode, installSslAfterCreate]);

  async function deleteWebsite(id) {
    if (!confirm(tr("Delete this website including files, vhost, and database?"))) return;
    const data = await request(`/websites/${id}?delete_files=true&delete_database=true`, { method: 'DELETE' }, tr("Deleting website..."));
    if (data) refreshAll();
  }

  async function enableSsl(id) {
    const data = await request(`/websites/${id}/ssl`, { method: 'POST' }, tr("Installing Let's Encrypt SSL..."));
    if (data) refreshAll();
  }

  async function issueWildcardSsl() {
    if (!selectedWebsiteId) return;
    if (wildcardProvider(wildcardSslForm.provider, currentSite) === 'opanel') {
      const body = { provider: 'opanel' };
      const email = String(wildcardSslForm.email || '').trim();
      if (email) body.email = email;
      const data = await request(`/websites/${selectedWebsiteId}/ssl/wildcard`, { method: 'POST', body: JSON.stringify(body) }, tr("Issuing wildcard certificate over this server's DNS..."));
      if (data) { setNotice(tr("Wildcard certificate issued for {0} and *.{1}.", data.domain, data.domain)); setWildcardSslForm({ api_token: '', email: '', provider: '' }); refreshAll(); }
      return;
    }
    const token = String(wildcardSslForm.api_token || '').trim();
    if (!token && !currentSite?.ssl_wildcard) { setError(tr("Enter a Cloudflare API token (Zone → DNS → Edit).")); return; }
    const body = { provider: 'cloudflare' };
    if (token) body.api_token = token;
    const email = String(wildcardSslForm.email || '').trim();
    if (email) body.email = email;
    const data = await request(`/websites/${selectedWebsiteId}/ssl/wildcard`, { method: 'POST', body: JSON.stringify(body) }, tr("Issuing wildcard certificate via Cloudflare DNS..."));
    if (data) { setNotice(tr("Wildcard certificate issued for {0} and *.{1}.", data.domain, data.domain)); setWildcardSslForm({ api_token: '', email: '', provider: '' }); refreshAll(); }
  }

  async function loadAvailableCerts() {
    if (!selectedWebsiteId) return;
    const data = await request(`/websites/${selectedWebsiteId}/available-certificates`, {}, tr("Loading certificates on this server..."));
    const list = Array.isArray(data) ? data : [];
    setAvailableCerts(list);
    const covering = list.find(c => c.covers_domain);
    setReuseCertName(prev => prev || (covering ? covering.name : ''));
  }

  async function reuseExistingSsl() {
    if (!selectedWebsiteId || !reuseCertName) { setError(tr("Pick a certificate to use.")); return; }
    const data = await request(`/websites/${selectedWebsiteId}/ssl/reuse`, { method: 'POST', body: JSON.stringify({ name: reuseCertName }) }, tr("Applying certificate..."));
    if (data) { setNotice(tr("{0} now uses {1}.", data.domain, reuseCertName.split(':')[1])); refreshAll(); }
  }

  async function addWebsiteAlias(site) {
    const cleanAlias = String(aliasDrafts[site.id] || '').trim().toLowerCase();
    const aliasMode = aliasModes[site.id] || 'alias';
    if (!cleanAlias) { setError(tr("Enter a domain.")); return; }
    const data = await request(`/websites/${site.id}/aliases`, {
      method: 'POST',
      body: JSON.stringify({ domain: cleanAlias, mode: aliasMode }),
    }, tr("Adding {0} {1}...", aliasMode === 'redirect' ? tr("redirect") : tr("alias"), cleanAlias));
    if (data) {
      setNotice(aliasMode === 'redirect'
        ? tr("Added redirect {0} -> {1}.", cleanAlias, site.domain)
        : tr("Added alias {0}. Re-run SSL after DNS points to this server.", cleanAlias));
      setAliasDrafts(prev => ({ ...prev, [site.id]: '' }));
      setNginxCustomEditing(prev => {
        if (!prev || prev.id !== site.id) return prev;
        const nextSite = prev.site || site;
        return { ...prev, site: { ...nextSite, aliases: [...(nextSite.aliases || []), data] } };
      });
      await refreshAll();
    }
  }

  async function deleteWebsiteAlias(site, alias) {
    const label = alias.mode === 'redirect' ? 'redirect' : 'alias';
    if (!confirm(tr("Remove {0} {1} from {2}?", label, alias.domain, site.domain))) return;
    const data = await request(`/websites/${site.id}/aliases/${alias.id}`, { method: 'DELETE' }, tr("Removing {0} {1}...", label, alias.domain));
    if (data) {
      setNotice(tr("Removed {0} {1}.", label, alias.domain));
      setNginxCustomEditing(prev => {
        if (!prev || prev.id !== site.id) return prev;
        const nextSite = prev.site || site;
        return { ...prev, site: { ...nextSite, aliases: (nextSite.aliases || []).filter(item => item.id !== alias.id) } };
      });
      await refreshAll();
    }
  }

  async function installManualSsl() {
    if (!selectedWebsiteId) return;
    const hasCert = manualSslFiles.certificate || manualSslForm.certificate.trim();
    const hasKey = manualSslFiles.private_key || manualSslForm.private_key.trim();
    if (!hasCert || !hasKey) {
      setError(tr("Certificate and private key are required."));
      return;
    }
    const form = new FormData();
    if (manualSslFiles.certificate) form.append('certificate', manualSslFiles.certificate);
    else form.append('certificate_text', manualSslForm.certificate);
    if (manualSslFiles.private_key) form.append('private_key', manualSslFiles.private_key);
    else form.append('private_key_text', manualSslForm.private_key);
    if (manualSslFiles.ca_bundle) form.append('ca_bundle', manualSslFiles.ca_bundle);
    else if (manualSslForm.ca_bundle.trim()) form.append('ca_bundle_text', manualSslForm.ca_bundle);
    const data = await request(`/websites/${selectedWebsiteId}/ssl/manual`, { method: 'POST', body: form }, tr("Installing manual SSL..."));
    if (data) {
      setManualSslForm({ certificate: '', private_key: '', ca_bundle: '' });
      setManualSslFiles({ certificate: null, private_key: null, ca_bundle: null });
      refreshAll();
    }
  }

  async function openNginxCustom(site) {
    setLogViewer(null);
    setTerminalViewer(null);
    setWebsiteSettingsForm(websiteConfigForm(site));
    setNginxCustomEditing({
      id: site.id,
      domain: site.domain,
      site,
      mode: 'settings',
      content: '',
    });
  }

  async function viewFullNginxConfig() {
    if (!nginxCustomEditing) return;
    const data = await request(`/websites/${nginxCustomEditing.id}/nginx-config`, {}, tr("Loading VHost Config..."));
    if (data !== null) {
      setNginxCustomEditing(prev => ({ ...prev, mode: 'full', content: data?.nginx_config || '' }));
    }
  }

  async function saveWebsiteSettings() {
    if (!nginxCustomEditing) return;
    const original = nginxCustomEditing.site || {};
    const body = {};
    const nextAppType = websiteSettingsForm.app_type || original.app_type || 'wordpress';
    const nextPhp = websiteSettingsForm.php_version || original.php_version || '8.4';
    const nextRewrite = nextAppType === 'wordpress'
      ? 'front_controller'
      : nextAppType === 'static'
        ? 'none'
        : websiteSettingsForm.nginx_rewrite_mode || 'none';

    if (nextAppType !== (original.app_type || 'wordpress')) body.app_type = nextAppType;
    if (nextAppType !== 'static' && nextPhp !== original.php_version) body.php_version = nextPhp;
    if (nextRewrite !== (original.nginx_rewrite_mode || (original.app_type === 'wordpress' ? 'front_controller' : 'none'))) {
      body.nginx_rewrite_mode = nextRewrite;
    }
    if (Object.keys(body).length === 0) return;

    const data = await request(`/websites/${nginxCustomEditing.id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }, tr("Saving {0} settings...", nginxCustomEditing.domain));
    if (data) {
      setNotice(tr("Updated settings for {0}.", nginxCustomEditing.domain));
      setWebsiteSettingsForm(websiteConfigForm(data));
      setNginxCustomEditing(prev => prev ? ({ ...prev, site: data }) : prev);
      await refreshAll();
    }
  }

  async function loadWebsiteLog(siteOrId = logViewer?.id, kind = logViewer?.kind || 'access', lines = logViewer?.lines || 200, domainLabel = logViewer?.domain || '') {
    const websiteId = typeof siteOrId === 'object' ? siteOrId.id : siteOrId;
    const domainName = typeof siteOrId === 'object' ? siteOrId.domain : domainLabel;
    if (!websiteId) return;
    const data = await request(`/websites/${websiteId}/logs?kind=${encodeURIComponent(kind)}&lines=${encodeURIComponent(lines)}`, {}, tr("Loading {0} log...", kind));
    if (data) {
      setLogViewer({
        id: websiteId,
        domain: data.domain || domainName,
        kind: data.kind || kind,
        lines: data.lines || lines,
        path: data.path || '',
        content: data.content || '',
        exists: !!data.exists,
      });
    }
  }

  async function openWebsiteLogs(site) {
    setNginxCustomEditing(null);
    setTerminalViewer(null);
    setLogViewer({ id: site.id, domain: site.domain, kind: 'access', lines: 200, path: '', content: '', exists: true });
    await loadWebsiteLog(site, 'access', 200, site.domain);
  }

  function openWebsiteTerminal(site) {
    setNginxCustomEditing(null);
    setLogViewer(null);
    setTerminalViewer({ id: site.id, domain: site.domain });
  }

  async function toggleWebsiteWaf(site) {
    const next = !site.waf_enabled;
    const data = await request(`/websites/${site.id}/waf`, {
      method: 'PATCH',
      body: JSON.stringify({ waf_enabled: next }),
    }, tr("{0} WAF for {1}...", next ? tr("Enabling") : tr("Disabling"), site.domain));
    if (data) {
      setNotice(tr("{0} WAF for {1}.", next ? tr("Enabled") : tr("Disabled"), site.domain));
      await refreshAll();
      if (String(selectedWafWebsiteId) === String(site.id)) await loadWebsiteWafConfig(site.id, false);
    }
  }

  async function fixWordPressPermissions(id) {
    const data = await request(`/maintenance/wordpress/${id}/fix-permissions`, { method: 'POST' }, tr("Fixing permissions..."));
    if (data?.message) setNotice(data.message);
  }

  async function fixNginxSecurity(id) {
    const data = await request(`/websites/${id}/fix-nginx-security`, { method: 'POST' }, tr("Rewriting webserver security template..."));
    if (data?.message) setNotice(data.message);
  }

  function dbOwnerLabel(item) {
    const owner = users.find(user => user.id === item.owner_id);
    const site = websites.find(w => w.id === item.website_id);
    if (site) return `${owner?.username || `user #${item.owner_id}`} · ${site.domain}`;
    return `${owner?.username || `user #${item.owner_id}`} · no website`;
  }

  // Only offered for a database no website points at. One that belongs to a
  // site moves with the site, so the two can never drift apart.
  function dbOwnerChoices(item) {
    return users.filter(user => user.id !== item?.owner_id);
  }

  function openDbOwnerModal(item) {
    const choices = dbOwnerChoices(item);
    if (choices.length === 0) { setError(tr("No other account to move it to.")); return; }
    setDbOwnerModal({ db: item, ownerId: String(choices[0].id) });
  }

  async function submitDbOwnerChange() {
    if (!dbOwnerModal) return;
    const { db: item, ownerId } = dbOwnerModal;
    const choice = users.find(user => String(user.id) === String(ownerId));
    if (!choice) { setError(tr("Pick an account to move it to.")); return; }
    const data = await request(`/databases/${item.id}/owner`, {
      method: 'POST', body: JSON.stringify({ owner_id: choice.id }),
    }, tr("Moving database..."));
    if (data) {
      setDbOwnerModal(null);
      setNotice(tr("{0} now belongs to {1}.", item.db_name, choice.username));
      await refreshAll();
    }
  }

  async function changeDbPassword(id) {
    const newPass = prompt(tr("Enter a new database password, minimum 12 characters:"));
    if (!newPass) return;
    await request(`/databases/${id}/password`, { method: 'POST', body: JSON.stringify({ password: newPass }) }, tr("Changing database password..."));
  }

  async function deleteDatabase(id, dbName) {
    if (!confirm(tr("Delete database \"{0}\"? This action cannot be undone.", dbName))) return;
    const data = await request(`/databases/${id}`, { method: 'DELETE' }, tr("Deleting database..."));
    if (data) {
      setNotice(tr("Database \"{0}\" deleted successfully.", dbName));
      await refreshAll();
    }
  }

  function generateRandomPassword(length = 20) {
    const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#%^*_+-';
    const arr = new Uint8Array(length);
    crypto.getRandomValues(arr);
    return Array.from(arr, b => chars[b % chars.length]).join('');
  }

  async function createDatabase() {
    const validDbName = /^[a-zA-Z0-9_]+$/;
    const dbName = newDatabase.db_name.trim();
    const dbUser = newDatabase.db_user.trim();
    const dbPass = newDatabase.db_password.trim();
    if (!dbName) { setError(tr("Please enter a database name.")); return; }
    if (!validDbName.test(dbName)) { setError(tr("Database name can only contain letters, numbers and underscores (no spaces or special characters).")); return; }
    if (dbUser && !validDbName.test(dbUser)) { setError(tr("Database user can only contain letters, numbers and underscores (no spaces or special characters).")); return; }
    if (dbPass && dbPass.length < 12) { setError(tr("Password must be at least 12 characters.")); return; }
    if (dbPass && /[^\x20-\x7E]/.test(dbPass)) { setError(tr("Password contains invalid characters. Use only ASCII characters.")); return; }
    const body = {
      db_name: dbName,
      db_user: dbUser || null,
      db_password: dbPass || null,
    };
    const data = await request('/databases', { method: 'POST', body: JSON.stringify(body) }, tr("Creating database..."));
    if (data) {
      setCreatedDbInfo({ db_name: data.db_name, db_user: data.db_user, db_password: data.db_password });
      setNewDatabase({ db_name: '', db_user: '', db_password: '' });
      await refreshAll();
    }
  }

  async function addCron() {
    const data = await request('/maintenance/cron', { method: 'POST', body: JSON.stringify({ website_id: Number(selectedWebsiteId), schedule: cronSchedule, command: cronCommand }) }, tr("Adding cron job..."));
    if (data) {
      if (data.cron_user) setCronUser(data.cron_user);
      setNotice(tr("Cron job added{0}.", data.cron_user ? tr(" as {0}", data.cron_user) : ''));
      await listCron();
    }
  }

  async function listCron() {
    if (!selectedWebsiteId) return;
    const data = await request(`/maintenance/cron/${selectedWebsiteId}`, {}, tr("Loading cron jobs..."));
    if (data?.items) setCronItems(data.items);
    if (data?.cron_user) setCronUser(data.cron_user);
  }

  async function deleteCron(index) {
    if (!confirm(tr("Delete cron #{0}?", index))) return;
    index = Number(index);
    if (Number.isNaN(index)) return;
    const data = await request('/maintenance/cron', { method: 'DELETE', body: JSON.stringify({ website_id: Number(selectedWebsiteId), index }) }, tr("Deleting cron job..."));
    if (data) {
      if (data.cron_user) setCronUser(data.cron_user);
      setNotice(tr("Cron job deleted."));
      await listCron();
    }
  }

  async function listFiles(path = fileListPath, websiteId = selectedWebsiteId) {
    if (!websiteId) return;
    const data = await request(`/maintenance/files/${websiteId}?path=${encodeURIComponent(path)}`, {}, tr("Loading file list..."));
    if (data?.items) { setFiles(data.items); setFileListPath(path); setFileUploadDir(path || ''); setSelectedFilePaths([]); }
  }

  async function readFile(pathOverride = filePath, websiteId = selectedWebsiteId) {
    const targetPath = pathOverride || filePath;
    if (!websiteId || !targetPath) return;
    if (pathOverride) setFilePath(pathOverride);
    const data = await request(`/maintenance/files/${websiteId}/read?path=${encodeURIComponent(targetPath)}`, {}, tr("Reading file..."));
    if (data?.content !== undefined) {
      setFileContent(data.content);
      setEditorCursor({ line: 1, column: 1 });
    }
  }

  async function writeFile() {
    const data = await request('/maintenance/files/write', { method: 'POST', body: JSON.stringify({ website_id: Number(selectedWebsiteId), path: filePath, content: fileContent }) }, tr("Saving file..."));
    if (data) { await listFiles(fileListPath); await loadCurrentUser(); }
  }

  async function downloadFile(path) {
    if (!selectedWebsiteId || !path) return;
    try {
      setError(''); setLoading(tr("Downloading file..."));
      const res = await fetch(`${API}/maintenance/files/${selectedWebsiteId}/download?path=${encodeURIComponent(path)}`, { credentials: 'include' });
      if (!res.ok) { const data = await res.json().catch(() => ({})); if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, tr("Download failed."))); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = path.split('/').pop() || 'download';
      document.body.appendChild(link); link.click(); link.remove();
      URL.revokeObjectURL(url);
    } catch (err) { setError(tr("File download failed.")); }
    finally { setLoading(''); }
  }

  function fileEditorUrl(websiteId, path) {
    const url = new URL(window.location.href);
    url.pathname = routeForPage('files');
    url.search = '';
    url.hash = '';
    url.searchParams.set('view', 'editor');
    url.searchParams.set('website_id', String(websiteId));
    url.searchParams.set('path', path);
    return url.toString();
  }

  function openFileEditorTab(path, websiteId = selectedWebsiteId) {
    if (!websiteId || !path) return;
    window.open(fileEditorUrl(websiteId, path), '_blank', 'noopener,noreferrer');
  }

  async function makeFileDirectory() {
    if (!selectedWebsiteId) return;
    const name = prompt(tr("Folder name:"));
    if (!name) return;
    const data = await request('/maintenance/files/mkdir', { method: 'POST', body: JSON.stringify({ website_id: Number(selectedWebsiteId), path: fileListPath || '', name }) }, tr("Creating folder..."));
    if (data) await listFiles(fileListPath);
  }

  async function makeFile() {
    if (!selectedWebsiteId) return;
    const name = prompt(tr("File name:"), 'new-file.txt');
    if (!name) return;
    const data = await request('/maintenance/files/create', { method: 'POST', body: JSON.stringify({ website_id: Number(selectedWebsiteId), path: fileListPath || '', name }) }, tr("Creating file..."));
    if (data) {
      await listFiles(fileListPath);
      const newPath = [fileListPath, name].filter(Boolean).join('/');
      openFileEditorTab(newPath);
    }
  }

  async function saveFilePermissions() {
    if (!chmodTarget) return;
    const data = await request('/maintenance/files/chmod', { method: 'POST', body: JSON.stringify({ website_id: Number(selectedWebsiteId), path: chmodTarget.path, mode: chmodTarget.mode }) }, tr("Changing permissions..."));
    if (data) { setChmodTarget(null); setNotice(tr("Permissions of {0} set to {1}.", chmodTarget.name, chmodTarget.mode)); await listFiles(fileListPath); }
  }

  async function renameFileItem(item) {
    if (!item) return;
    const newName = prompt(tr("New name:"), item.name);
    if (!newName || newName === item.name) return;
    const data = await request('/maintenance/files/rename', { method: 'POST', body: JSON.stringify({ website_id: Number(selectedWebsiteId), path: item.path, new_name: newName }) }, 'Renaming...');
    if (data) await listFiles(fileListPath);
  }

  async function deleteSelectedFiles() {
    if (selectedFilePaths.length === 0) return;
    if (!confirm(tr("Delete {0} selected item(s)?", selectedFilePaths.length))) return;
    const data = await request('/maintenance/files/delete', { method: 'POST', body: JSON.stringify({ website_id: Number(selectedWebsiteId), paths: selectedFilePaths }) }, tr("Deleting selected files..."));
    if (data) { await listFiles(fileListPath); await loadCurrentUser(); }
  }

  async function transferFileItems(action, paths) {
    if (!selectedWebsiteId || !paths?.length) return;
    // Whole phrases per action: "{0}ing" only works in English.
    const copying = action === 'copy';
    const destination = prompt(copying ? tr("Copy to folder:") : tr("Move to folder:"), fileListPath || 'public_html');
    if (destination === null) return;
    const targetPath = destination.trim() || fileListPath || 'public_html';
    const data = await request(`/maintenance/files/${action}`, {
      method: 'POST',
      body: JSON.stringify({ website_id: Number(selectedWebsiteId), paths, destination_path: targetPath }),
    }, copying ? tr("Copying files...") : tr("Moving files..."));
    if (data) { await listFiles(fileListPath); await loadCurrentUser(); }
  }

  async function copySelectedFiles() {
    await transferFileItems('copy', selectedFilePaths);
  }

  async function moveSelectedFiles() {
    await transferFileItems('move', selectedFilePaths);
  }

  async function archiveSelectedFiles() {
    if (selectedFilePaths.length === 0) return;
    const ext = archiveFormat === 'tar.gz' ? 'tar.gz' : 'zip';
    const outputName = prompt(tr("Archive file name:"), `archive-${Date.now()}.${ext}`);
    if (!outputName) return;
    const data = await request('/maintenance/files/archive', {
      method: 'POST',
      body: JSON.stringify({ website_id: Number(selectedWebsiteId), base_path: fileListPath || '', paths: selectedFilePaths, output_name: outputName, format: archiveFormat }),
    }, tr("Creating archive..."));
    if (data) { await listFiles(fileListPath); await loadCurrentUser(); }
  }

  function isExtractableArchive(item) {
    if (!item || item.is_dir) return false;
    const name = String(item.name || item.path || '').toLowerCase();
    return name.endsWith('.zip') || name.endsWith('.tar.gz') || name.endsWith('.tgz');
  }

  async function extractArchive(item) {
    if (!isExtractableArchive(item) || !selectedWebsiteId) return;
    const defaultDestination = parentFilePath(item.path);
    const destination = prompt(tr("Extract to folder:"), defaultDestination);
    if (destination === null) return;
    const destinationPath = destination.trim();
    const data = await request('/maintenance/files/extract', {
      method: 'POST',
      body: JSON.stringify({
        website_id: Number(selectedWebsiteId),
        archive_path: item.path,
        destination_path: destinationPath,
      }),
    }, tr("Starting extraction..."));
    if (data?.job_id) {
      upsertFileJob(data);
      setSelectedFilePaths([]);
    }
  }

  async function extractSelectedArchive() {
    if (selectedFilePaths.length !== 1) return;
    const item = files.find(file => file.path === selectedFilePaths[0]);
    await extractArchive(item);
  }

  function upsertFileJob(job) {
    if (!job?.job_id) return;
    setFileJobs(prev => [job, ...prev.filter(item => item.job_id !== job.job_id)].slice(0, 6));
  }

  async function loadFileJob(jobId) {
    try {
      const res = await fetch(`${API}/maintenance/files/jobs/${jobId}`, { credentials: 'include' });
      const text = await res.text();
      let data;
      try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text || tr("HTTP {0}", res.status) }; }
      if (!res.ok && handleAuthExpired(res.status, data.detail)) return null;
      if (!res.ok) return null;
      return data;
    } catch {
      return null;
    }
  }

  async function loadFileJobs(websiteId = selectedWebsiteId) {
    if (!websiteId) return;
    const data = await request(`/maintenance/files/jobs?website_id=${encodeURIComponent(websiteId)}`);
    if (data?.jobs) {
      setFileJobs(prev => [
        ...data.jobs,
        ...prev.filter(job => String(job.website_id) !== String(websiteId)),
      ].slice(0, 6));
    }
  }

  useEffect(() => {
    const activeJobs = fileJobs.filter(job => ['queued', 'running'].includes(job.status));
    if (activeJobs.length === 0) return undefined;

    const poll = async () => {
      for (const job of activeJobs) {
        const data = await loadFileJob(job.job_id);
        if (!data) continue;
        upsertFileJob(data);
        if (data.status === 'done') {
          setNotice(data.message || tr("Extraction completed"));
          await listFiles(data.destination_path || fileListPath);
          await loadCurrentUser();
        } else if (data.status === 'error') {
          setError(formatApiError(data.error, tr("Extraction failed")));
        }
      }
    };

    const timer = window.setInterval(poll, 3000);
    return () => window.clearInterval(timer);
  }, [fileJobs]);

  useEffect(() => {
    if (page === 'files' && selectedWebsiteId) loadFileJobs(selectedWebsiteId);
  }, [page, selectedWebsiteId]);

  async function openWebsiteFileManager(site) {
    setNginxCustomEditing(null);
    setLogViewer(null);
    setTerminalViewer(null);
    setSelectedWebsiteId(String(site.id));
    navigateToPage('files');
    setFileListPath('public_html');
    setFileUploadDir('public_html');
    await listFiles('public_html', site.id);
  }

  async function uploadSiteFile(file) {
    if (!file) return;
    if (!selectedWebsiteId) { setError(tr("Please select a website first.")); return; }
    const uploadDir = fileUploadDir.trim();
    const form = new FormData();
    form.append('file', file);
    try {
      setError('');
      setLoading(tr("Uploading file..."));
      const csrfToken = readCookie('opanel_csrf');
      const headers = csrfToken ? { 'X-CSRF-Token': csrfToken } : {};
      const res = await fetch(`${API}/maintenance/files/${selectedWebsiteId}/upload?path=${encodeURIComponent(uploadDir)}`, {
        method: 'POST',
        credentials: 'include',
        headers,
        body: form,
      });
      const responseText = await res.text();
      let data;
      try { data = responseText ? JSON.parse(responseText) : {}; } catch { data = { detail: responseText || tr("HTTP {0}", res.status) }; }
      if (!res.ok) { if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, tr("Upload failed."))); return; }
      setNotice(tr("Uploaded {0} to {1}.", file.name, uploadDir || tr("site root")));
      if (String(fileListPath || '') === uploadDir) await listFiles(uploadDir);
      await loadCurrentUser();
    } catch (err) { setError(tr("File upload failed.")); }
    finally { setLoading(''); }
  }

  async function createBackup() {
    const data = await request('/maintenance/backup', { method: 'POST', body: JSON.stringify({ website_id: Number(selectedWebsiteId) }) }, tr("Queueing backup..."));
    if (data?.job_id) { setNotice(tr("Backup queued. It will keep running on the server.")); await loadBackupJobs(); }
    else if (data?.backup_file) { setNotice(tr("Created backup: {0}", data.backup_file)); await listBackups(); }
  }

  async function listBackups() {
    const data = await request(`/maintenance/backups/${selectedWebsiteId}`);
    if (data?.items) setBackups(data.items);
  }

  async function loadBackupJobs(showLoading = false) {
    const data = await request('/maintenance/backup-jobs', {}, showLoading ? tr("Loading backup logs...") : '');
    if (data?.jobs) {
      const hasActive = data.jobs.some(job => ['queued', 'running'].includes(job.status));
      setBackupJobs(prev => {
        const hadActive = prev.some(job => ['queued', 'running'].includes(job.status));
        if (hadActive && !hasActive) {
          setTimeout(() => {
            if (selectedWebsiteId) listBackups();
            if (selectedBackupUserId) listUserBackups(selectedBackupUserId);
          }, 0);
        }
        return data.jobs;
      });
    }
  }

  async function refreshBackupArea() {
    await listBackups();
    await loadBackupJobs();
    if (selectedBackupUserId) await listUserBackups(selectedBackupUserId);
  }

  async function refreshUserBackupArea() {
    await loadUsers();
    await loadBackupJobs();
    if (selectedBackupUserId) await listUserBackups(selectedBackupUserId);
  }

  async function refreshScheduledBackupArea() {
    await loadUsers();
    await loadSftpTargets();
    await loadBackupSchedules();
    await loadBackupJobs();
  }

  async function listUserBackups(userId = selectedBackupUserId) {
    if (!userId) return;
    const data = await request(`/maintenance/user-backups/${userId}`);
    if (data?.items) setUserBackups(data.items);
  }

  async function createUserBackup() {
    if (!selectedBackupUserId) return;
    const body = {
      user_id: Number(selectedBackupUserId),
      target_id: selectedSftpTargetId ? Number(selectedSftpTargetId) : null,
    };
    const data = await request('/maintenance/user-backup', { method: 'POST', body: JSON.stringify(body) }, tr("Queueing full user backup..."));
    if (data?.job_id) { setNotice(tr("Full user backup queued. It will keep running on the server.")); await loadBackupJobs(); }
    else if (data?.backup_file) {
      setNotice(data.remote_file ? tr("Full user backup uploaded: {0}", data.remote_file) : tr("Created full user backup: {0}", data.backup_file));
      await listUserBackups();
    }
  }

  async function loadBackupSchedules() {
    const data = await request('/maintenance/backup-schedules');
    if (data) setBackupSchedules(data);
  }

  function restoreGroupKey(item) {
    return `${item.kind}:${item.account || item.username || '#' + item.ref}`;
  }

  // One row per account and kind, newest archive first. An archive whose
  // account cannot be told from its name or folder is a row of its own.
  function restoreGroups(items = restoreList?.items || []) {
    const groups = new Map();
    for (const item of items) {
      const key = restoreGroupKey(item);
      if (!groups.has(key)) groups.set(key, { key, kind: item.kind, account: item.account || item.username || '', items: [] });
      groups.get(key).items.push(item);
    }
    const out = [...groups.values()];
    out.forEach(group => group.items.sort((a, b) => (b.modified_at || '').localeCompare(a.modified_at || '')));
    return out.sort((a, b) => (a.account || '~' + a.items[0].filename).localeCompare(b.account || '~' + b.items[0].filename));
  }

  function restoreChosen(group) {
    return group.items.find(item => item.ref === restoreChoice[group.key]) || group.items[0];
  }

  // What the server needs to reach the source. Listing trusts whatever SFTP
  // key answers and shows it; the restore then insists on that same key.
  function restoreSourceBody(source = restoreSource, forRun = false) {
    if (source === 'target') return { source, target_id: Number(restoreTargetId) || null };
    if (source === 'remote') {
      const { use_key, ...remote } = restoreRemote;
      return {
        source,
        remote: {
          ...remote,
          host: remote.host.trim(),
          port: Number(remote.port) || (remote.protocol === 'sftp' ? 22 : 21),
          private_key: remote.protocol === 'sftp' && use_key ? remote.private_key : '',
          host_key_fingerprint: forRun ? (restoreList?.host_key?.fingerprint || '') : '',
        },
      };
    }
    return { source: 'local' };
  }

  function chooseRestoreSource(source) {
    if (source === restoreSource) return;
    setRestoreSource(source);
    setRestoreList(null);
    setRestoreListError('');
    setRestorePicks([]);
    setRestoreChoice({});
    setRestoreFilter('');
  }

  async function loadRestoreList(source = restoreSource) {
    if (source === 'target' && !restoreTargetId) return;
    if (source === 'remote' && !restoreRemote.host.trim()) {
      setRestoreListError(tr("Enter the server's host name or IP address."));
      return;
    }
    setRestoreListing(true);
    setRestoreListError('');
    try {
      const csrf = readCookie('opanel_csrf');
      const res = await fetch(`${API}/maintenance/restore/list`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
        body: JSON.stringify(restoreSourceBody(source)),
      });
      const text = await res.text();
      let data;
      try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text || tr("HTTP {0}", res.status) }; }
      if (!res.ok) {
        if (handleAuthExpired(res.status, data.detail)) return;
        // Shown next to the form that caused it, not in the page banner:
        // a wrong password is something to fix right there.
        setRestoreList(null);
        setRestoreListError(formatApiError(data.detail, tr("Could not read this source.")));
        return;
      }
      setRestoreList(data);
      const keys = new Set((data.items || []).map(restoreGroupKey));
      setRestorePicks(prev => prev.filter(key => keys.has(key)));
      describeRestoreItems(data.items || []);
    } catch {
      setRestoreListError(tr("Could not read this source."));
    } finally {
      setRestoreListing(false);
    }
  }

  async function describeRestoreItems(items) {
    // The slow half: finding a manifest in an archive written before the
    // manifest moved to the front means decompressing all of it. The list is
    // already on screen, so this fills in behind it without a spinner, a few
    // at a time so one slow archive does not hold up the rest.
    const files = items
      .filter(item => item.kind === 'opanel' && item.backup_file && item.websites == null)
      .map(item => item.backup_file);
    for (let start = 0; start < files.length; start += 3) {
      const data = await request('/maintenance/user-restore-backups/describe', {
        method: 'POST', body: JSON.stringify({ backup_files: files.slice(start, start + 3) }), silent: true,
      }, '');
      if (!data?.items) continue;
      const byFile = new Map(data.items.map(row => [row.backup_file, row]));
      setRestoreList(prev => prev && ({
        ...prev,
        items: prev.items.map(item => byFile.has(item.ref) ? {
          ...item,
          username: byFile.get(item.ref).username || item.username,
          websites: byFile.get(item.ref).websites,
          valid: byFile.get(item.ref).valid,
          error: byFile.get(item.ref).error || '',
        } : item),
      }));
    }
  }

  async function uploadRestoreArchives(files) {
    const picked = Array.from(files || []);
    if (picked.length === 0) return;
    const form = new FormData();
    picked.forEach(file => form.append('files', file));
    const data = await request('/maintenance/restore/upload', { method: 'POST', body: form }, tr("Uploading backups..."));
    if (data) {
      setNotice(tr("Uploaded {0} backup(s).", data.items?.length || picked.length));
      await loadRestoreList('local');
    }
  }

  async function deleteRestoreArchive(item) {
    if (!confirm(tr("Delete this backup from the server?\n{0}", item.filename))) return;
    const data = await request(`/maintenance/restore/local?ref=${encodeURIComponent(item.ref)}`, { method: 'DELETE' }, tr("Deleting backup..."));
    if (data) await loadRestoreList('local');
  }

  async function runRestore() {
    const chosen = restoreGroups().filter(group => restorePicks.includes(group.key)).map(restoreChosen);
    if (chosen.length === 0) return;
    const names = chosen.map(item => `  - ${item.account || item.username || '?'} (${item.kind === 'directadmin' ? 'DirectAdmin' : 'OPanel'}): ${item.filename}`).join('\n');
    const notes = [];
    if (chosen.some(item => item.kind === 'opanel')) notes.push(tr("This overwrites that account's sites and databases with what is in the archive."));
    if (chosen.some(item => item.kind === 'directadmin')) notes.push(restoreOverwrite
      ? tr("Overwrite is on. A user or website already on this server is replaced by what the archive carries: its files are copied over the ones there, its databases are re-imported, and the panel user gets a new password. Websites the account has that the archive does not mention are kept.")
      : tr("Overwrite is off. An archive whose user or domains are already on this server stops without touching them; the others still import."));
    if (restoreSource !== 'local') notes.push(tr("Each archive is downloaded to this server first and deleted again once it has been restored."));
    if (chosen.length > 1) notes.push(tr("They run one after another on the server, so you can leave this page."));
    if (!confirm(tr("Restore {0} account(s)?\n\n{1}\n\n{2}", chosen.length, names, notes.join('\n\n')))) return;
    const data = await request('/maintenance/restore/run', {
      method: 'POST',
      body: JSON.stringify({
        ...restoreSourceBody(restoreSource, true),
        items: chosen.map(item => ({ kind: item.kind, ref: item.ref })),
        overwrite: restoreOverwrite,
      }),
    }, tr("Starting restore..."));
    if (data?.job_id) {
      setRestoreJobId(data.job_id);
      setRestorePicks([]);
      loadBackupJobs();
    }
  }

  async function createBackupSchedule() {
    const selectedUserIds = (newBackupSchedule.user_ids || []).map(Number).filter(Boolean);
    if (!newBackupSchedule.all_users && selectedUserIds.length === 0) return;
    const body = {
      user_id: selectedUserIds[0] || null,
      user_ids: newBackupSchedule.all_users ? [] : selectedUserIds,
      all_users: !!newBackupSchedule.all_users,
      schedule: newBackupSchedule.schedule,
      target_id: newBackupSchedule.target_id ? Number(newBackupSchedule.target_id) : null,
      retention: Number(newBackupSchedule.retention || 7),
      is_active: true,
    };
    const data = await request('/maintenance/backup-schedules', { method: 'POST', body: JSON.stringify(body) }, tr("Saving backup schedule..."));
    if (data) {
      setNotice(tr("Backup schedule saved."));
      await loadBackupSchedules();
    }
  }

  // `who` is passed in: the label helper is local to renderBackups, and
  // reaching for it here threw before the confirm ever opened, which is why
  // the button appeared to do nothing.
  async function runBackupScheduleNow(item, who) {
    // The same run the timer would do, just early: same rotation slot, same
    // retention. Worth saying so, because it overwrites today's slot.
    if (!confirm(tr("Run this schedule now?\n\n{0} - {1}\n\nIt writes today's rotation slot, overwriting last week's copy for that day.", who, item.schedule))) return;
    const data = await request(`/maintenance/backup-schedules/${item.id}/run`, { method: 'POST' }, tr("Starting schedule..."));
    if (data) {
      setNotice(tr("Schedule started. Watch Backup logs for progress."));
      loadBackupJobs();
    }
  }

  async function deleteBackupSchedule(id) {
    if (!confirm(tr("Delete this backup schedule?"))) return;
    const data = await request(`/maintenance/backup-schedules/${id}`, { method: 'DELETE' }, tr("Deleting backup schedule..."));
    if (data) await loadBackupSchedules();
  }

  async function loadSftpTargets() {
    const data = await request('/maintenance/backup-targets');
    if (data) {
      setSftpTargets(data);
      if (!selectedSftpTargetId && data[0]) setSelectedSftpTargetId(String(data[0].id));
    }
  }

  async function createSftpTarget() {
    const body = {
      ...newSftpTarget,
      port: Number(newSftpTarget.port || 22),
      password: newSftpTarget.password || null,
      private_key: newSftpTarget.private_key || null,
      s3_secret_key: newSftpTarget.s3_secret_key || null,
    };
    const data = await request('/maintenance/backup-targets', { method: 'POST', body: JSON.stringify(body) }, tr("Saving backup destination..."));
    if (data) {
      setNotice(tr("Saved {0} destination {1}", data.kind === 's3' ? 'S3' : tr("SFTP"), data.name));
      setNewSftpTarget(BLANK_TARGET);
      await loadSftpTargets();
    }
  }

  async function deleteSftpTarget(id) {
    if (!confirm(tr("Delete this backup destination?"))) return;
    const data = await request(`/maintenance/backup-targets/${id}`, { method: 'DELETE' }, tr("Deleting destination..."));
    if (data) await loadSftpTargets();
  }

  async function loadTargetObjects(id) {
    if (targetObjects.id === id) { setTargetObjects({ id: null, bucket: '', items: [] }); return; }
    const data = await request(`/maintenance/backup-targets/${id}/objects`, {}, tr("Reading destination..."));
    if (data) setTargetObjects({ id, bucket: data.bucket, items: data.items || [] });
  }

  async function deleteTargetObject(id, key) {
    if (!confirm(tr("Delete this backup from the bucket?\n{0}", key))) return;
    const data = await request(`/maintenance/backup-targets/${id}/objects?key=${encodeURIComponent(key)}`,
      { method: 'DELETE' }, tr("Deleting from bucket..."));
    if (data?.deleted) {
      setNotice(tr("Deleted {0}", data.deleted));
      setTargetObjects(prev => ({ ...prev, items: prev.items.filter(item => item.key !== data.deleted) }));
    }
  }

  async function testBackupTarget(id) {
    const data = await request(`/maintenance/backup-targets/${id}/test`, { method: 'POST' }, tr("Testing destination..."));
    if (data?.ok) setNotice(data.message || tr("Destination works."));
  }

  async function createSftpBackup() {
    if (!selectedWebsiteId || !selectedSftpTargetId) return;
    const data = await request('/maintenance/backup-sftp', {
      method: 'POST',
      body: JSON.stringify({ website_id: Number(selectedWebsiteId), target_id: Number(selectedSftpTargetId) }),
    }, tr("Queueing SFTP backup..."));
    if (data?.job_id) {
      setNotice(tr("SFTP backup queued. It will keep running on the server."));
      await loadBackupJobs();
    } else if (data?.remote_file) {
      setNotice(tr("SFTP backup uploaded: {0}", data.remote_file));
      await listBackups();
    }
  }

  async function restoreBackup(file) {
    if (!confirm(tr("Restore this backup to the current website?\n{0}", file))) return;
    await request('/maintenance/restore', { method: 'POST', body: JSON.stringify({ website_id: Number(selectedWebsiteId), backup_file: file }) }, tr("Restoring backup..."));
  }

  async function downloadBackup(file) {
    if (!selectedWebsiteId) return;
    try {
      setError(''); setLoading(tr("Downloading backup..."));
      const res = await fetch(`${API}/maintenance/backups/${selectedWebsiteId}/download?backup_file=${encodeURIComponent(file)}`, { credentials: 'include' });
      if (!res.ok) { const data = await res.json().catch(() => ({})); if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, tr("Download failed."))); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = file.split('/').pop() || 'backup.tar.gz';
      document.body.appendChild(link); link.click(); link.remove();
      URL.revokeObjectURL(url);
      setNotice(tr("Backup downloaded."));
    } catch (err) { setError(tr("Backup download failed.")); }
    finally { setLoading(''); }
  }

  async function downloadUserBackup(file) {
    try {
      setError(''); setLoading(tr("Downloading full user backup..."));
      const res = await fetch(`${API}/maintenance/user-backups-download?backup_file=${encodeURIComponent(file)}`, { credentials: 'include' });
      if (!res.ok) { const data = await res.json().catch(() => ({})); if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, tr("Download failed."))); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = file.split('/').pop() || 'user-backup.tar.gz';
      document.body.appendChild(link); link.click(); link.remove();
      URL.revokeObjectURL(url);
      setNotice(tr("Full user backup downloaded."));
    } catch (err) { setError(tr("Full user backup download failed.")); }
    finally { setLoading(''); }
  }

  async function restoreUserBackup(file) {
    if (!confirm(tr("Restore this full user backup? Missing panel user and websites will be created.\n{0}", file))) return;
    const data = await request('/maintenance/user-restore', { method: 'POST', body: JSON.stringify({ backup_file: file }) }, tr("Restoring full user backup..."));
    if (data) {
      const parts = [
        tr("Restored user {0}", data.username),
        `${data.websites?.length || 0} website(s)`,
        `${data.databases?.length || 0} database(s)`,
      ];
      const certs = data.certificates || [];
      if (certs.length) {
        const expired = certs.filter(item => item.expired);
        parts.push(`${certs.length} certificate(s) restored`);
        if (expired.length) parts.push(tr("{0} need a new certificate (expired)", expired.map(item => item.domain).join(', ')));
      }
      if (data.ssl_warnings?.length) parts.push(tr("SSL not restored for {0} site(s)", data.ssl_warnings.length));
      setNotice(parts.join('. ') + '.');
      await refreshAll();
      await loadUsers();
      await listUserBackups();
    }
  }

  // A backup that went to S3 exists twice. Deleting the local copy on its own
  // left the offsite one behind with nothing in the panel mentioning it, which
  // read as "the delete did not work". Ask, name what is out there, and never
  // remove an offsite copy without being told to.
  async function confirmBackupDelete(file) {
    const name = file.split('/').pop();
    // Send the whole path: weekday rotation names every account's Monday copy
    // "monday.tar.gz", so the account folder is what tells them apart.
    const found = await request(`/maintenance/backup-remote-copies?backup_file=${encodeURIComponent(file)}`, {}, '');
    const copies = found?.items || [];
    if (copies.length === 0) {
      return { ok: confirm(tr("Delete this backup?\n{0}", name)), alsoRemote: true };
    }
    const where = copies.map(item => `  - ${item.target} (${item.bucket}/${item.key})`).join('\n');
    return {
      ok: confirm(tr("Delete this backup?\n{0}\n\nThis also removes the copy on:\n{1}", name, where)),
      alsoRemote: true,
    };
  }

  async function deleteUserBackup(file) {
    const choice = await confirmBackupDelete(file);
    if (!choice.ok) return;
    const data = await request(
      `/maintenance/user-backups?backup_file=${encodeURIComponent(file)}&also_remote=${choice.alsoRemote}`,
      { method: 'DELETE' }, tr("Deleting full user backup..."));
    if (data) {
      const remote = data.removed_remote || [];
      setNotice(remote.length
        ? tr("Deleted, including {0} copy on S3.", remote.length)
        : tr("Deleted the local backup."));
      await listUserBackups();
    }
  }

  async function openPhpMyAdmin(databaseId) {
    try {
      setError(''); setLoading(tr("Opening phpMyAdmin..."));
      const csrfToken = readCookie('opanel_csrf');
      const headers = csrfToken ? { 'X-CSRF-Token': csrfToken } : {};
      const res = await fetch(`${API}/databases/${databaseId}/phpmyadmin-sso`, {
        method: 'POST',
        credentials: 'include',
        headers,
      });
      const data = await res.json().catch(() => ({}));
      if (handleAuthExpired(res.status, data.detail)) return;
      if (!res.ok || !data.url) { setError(formatApiError(data.detail, tr("Cannot open phpMyAdmin."))); return; }
      window.open(data.url, '_blank', 'noopener,noreferrer');
    } catch (err) { setError(tr("Cannot open phpMyAdmin.")); }
    finally { setLoading(''); }
  }

  async function downloadDatabase(databaseId, databaseName) {
    try {
      setError(''); setLoading(tr("Downloading database..."));
      const res = await fetch(`${API}/databases/${databaseId}/download`, { credentials: 'include' });
      if (!res.ok) { const data = await res.json().catch(() => ({})); if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, tr("Download failed."))); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = `${databaseName || 'database'}.sql`;
      document.body.appendChild(link); link.click(); link.remove();
      URL.revokeObjectURL(url);
      setNotice(tr("Database SQL downloaded."));
    } catch (err) { setError(tr("Database download failed.")); }
    finally { setLoading(''); }
  }

  async function deleteBackup(file) {
    const choice = await confirmBackupDelete(file);
    if (!choice.ok) return;
    const data = await request(
      `/maintenance/backups/${selectedWebsiteId}?backup_file=${encodeURIComponent(file)}&also_remote=${choice.alsoRemote}`,
      { method: 'DELETE' }, tr("Deleting backup..."));
    if (data) {
      const remote = data.removed_remote || [];
      setNotice(remote.length
        ? tr("Deleted, including {0} copy on S3.", remote.length)
        : tr("Deleted the local backup."));
      await listBackups();
    }
  }

  async function uploadBackup(file) {
    if (!file || !selectedWebsiteId) return;
    const form = new FormData();
    form.append('file', file);
    try {
      setError(''); setLoading(tr("Uploading backup..."));
      const csrfToken = readCookie('opanel_csrf');
      const headers = csrfToken ? { 'X-CSRF-Token': csrfToken } : {};
      const res = await fetch(`${API}/maintenance/backups/${selectedWebsiteId}/upload`, {
        method: 'POST',
        credentials: 'include',
        headers,
        body: form,
      });
      const responseText = await res.text();
      let data;
      try { data = responseText ? JSON.parse(responseText) : {}; } catch { data = { detail: responseText || tr("HTTP {0}", res.status) }; }
      if (!res.ok) { if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, tr("Upload failed."))); return; }
      if (data.backup_file) { setNotice(tr("Uploaded backup: {0}", data.backup_file)); await listBackups(); }
    } catch (err) { setError(tr("Upload backup failed.")); }
    finally { setLoading(''); }
  }

  async function checkService(name) {
    const data = await request('/services/action', { method: 'POST', body: JSON.stringify({ name, action: 'status' }) });
    setServiceStates(prev => ({ ...prev, [name]: data || { stdout: '', stderr: error || tr("Cannot check"), returncode: 1 } }));
    return data;
  }

  async function loadServiceNames() {
    const data = await request('/services/list');
    const names = data?.services?.length ? data.services : serviceNames;
    setServiceNames(names);
    return names;
  }

  async function checkAllServices() {
    setLoading(tr("Checking services..."));
    const names = await loadServiceNames();
    for (const name of names) { await checkService(name); }
    setLoading('');
  }

  async function runServiceAction(name, action) {
    await request('/services/action', { method: 'POST', body: JSON.stringify({ name, action }) }, `${action} ${name}...`);
    await checkService(name);
  }

  async function loadPhpExtensions() {
    const data = await request('/maintenance/php/extensions/all', {});
    if (data) setPhpExtensions(data);
  }

  async function changePhpExtension(name, version, action) {
    const pkg = `lsphp${version.replace('.', '')}-${name}`;
    const question = action === 'install'
      ? tr("Install {0} for PHP {1}? Every website on PHP {1} gets it, and OpenLiteSpeed restarts.", pkg, version)
      : tr("Remove {0} from PHP {1}? Websites on PHP {1} that use it will stop working, and OpenLiteSpeed restarts.", pkg, version);
    if (!confirm(question)) return;
    const data = await request(`/maintenance/php/extensions/${encodeURIComponent(version)}/${encodeURIComponent(name)}/${action}`, { method: 'POST' },
      action === 'install' ? tr("Installing {0}...", pkg) : tr("Removing {0}...", pkg));
    if (data) {
      setNotice(action === 'install' ? tr("{0} installed for PHP {1}.", name, version) : tr("{0} removed from PHP {1}.", name, version));
      await loadPhpExtensions();
    }
  }

  async function installPhpExtensionEverywhere(name, versions) {
    if (!confirm(tr("Install {0} for PHP {1}? Every website on those versions gets it, and OpenLiteSpeed restarts once.", name, versions.join(', ')))) return;
    const data = await request(`/maintenance/php/extensions/${encodeURIComponent(name)}/install-all`, { method: 'POST' }, tr("Installing {0}...", name));
    if (data) {
      setNotice(tr("{0} installed for PHP {1}.", name, (data.versions || versions).join(', ')));
      await loadPhpExtensions();
    }
  }

  async function loadPhpConfig(version = phpConfig.php_version) {
    const data = await request(`/maintenance/php-config?php_version=${encodeURIComponent(version)}`, {}, tr("Loading PHP config..."));
    if (data) setPhpConfig(prev => ({ ...prev, ...data, php_version: version }));
  }

  async function updatePhpConfig() {
    const data = await request('/maintenance/php-config', {
      method: 'POST',
      body: JSON.stringify({ ...phpConfig, max_execution_time: Number(phpConfig.max_execution_time), max_input_time: Number(phpConfig.max_input_time), max_input_vars: Number(phpConfig.max_input_vars), opcache_enable: !!phpConfig.opcache_enable }),
    }, tr("Updating PHP config..."));
    if (data?.target) { setNotice(tr("Updated PHP config: {0}", data.target)); await loadPhpConfig(phpConfig.php_version); }
  }

  async function restorePhpDefaults() {
    if (!confirm(tr("Restore default PHP {0} values?", phpConfig.php_version))) return;
    const data = await request('/maintenance/php-config/defaults', {
      method: 'POST',
      body: JSON.stringify({ php_version: phpConfig.php_version }),
    }, tr("Restoring PHP defaults..."));
    if (data?.values) {
      setPhpConfig(prev => ({ ...prev, ...data.values }));
      setNotice(tr("Restored PHP {0} defaults.", phpConfig.php_version));
    }
  }

  async function loadPhpVersions() {
    const data = await request('/maintenance/php-versions', {}, tr("Loading PHP versions..."));
    if (data) setPhpVersions({
      installed: sortPhpVersions(data.installed || []),
      supported: sortPhpVersions(data.supported || []),
    });
  }

  async function installPhpVersion(version) {
    if (!confirm(tr("Install PHP {0}? This will install lsphp{1} via apt.", version, version.replace('.','')))) return;
    const data = await request(`/maintenance/php-versions/${version}/install`, { method: 'POST' }, tr("Installing PHP {0}...", version));
    if (data) { setNotice(tr("PHP {0} installed successfully.", version)); await loadPhpVersions(); await loadServiceNames(); }
  }

  async function loadPhpTuning() {
    const data = await request(`/maintenance/php/tuning?php_version=${encodeURIComponent(phpConfig.php_version)}`, {}, tr("Loading PHP tuning..."));
    if (data) setPhpTuning(data);
  }

  async function applyPhpTuning() {
    if (!confirm(tr("Auto-tune PHP {0} for this server? This will overwrite the OPanel ini file and restart OpenLiteSpeed.", phpConfig.php_version))) return;
    const data = await request(`/maintenance/php/tuning?php_version=${encodeURIComponent(phpConfig.php_version)}`, { method: 'POST' }, tr("Applying PHP tuning..."));
    if (data) {
      setNotice(tr("PHP tuned: memory_limit={0}, OPcache={1}MB, LSAPI workers={2}", data.memory_limit, data.opcache_memory_consumption, data.lsapi_children));
      setPhpTuning(null);
      await loadPhpConfig(phpConfig.php_version);
    }
  }

  async function loadFirewall() {
    const data = await request('/firewall/status', {}, tr("Loading firewall..."));
    if (data) setFirewallStatus(data);
  }

  async function runFirewallAction(path, options = {}, label = tr("Updating firewall...")) {
    const data = await request(path, options, label);
    if (data) { setNotice((data.stdout || data.stderr || tr("Firewall updated.")).trim()); await loadFirewall(); }
  }

  async function enableFirewall() {
    if (!confirm(tr("Enable iptables firewall now? Make sure SSH and web ports are allowed."))) return;
    await runFirewallAction('/firewall/enable', { method: 'POST' }, tr("Enabling firewall..."));
  }
  async function disableFirewall() {
    if (!confirm(tr("Disable iptables firewall?"))) return;
    await runFirewallAction('/firewall/disable', { method: 'POST' }, tr("Disabling firewall..."));
  }
  async function reloadFirewall() { await runFirewallAction('/firewall/reload', { method: 'POST' }, tr("Reloading firewall...")); }
  async function openFirewallPort() { await runFirewallAction('/firewall/allow-port', { method: 'POST', body: JSON.stringify({ port: firewallPort, protocol: firewallProtocol }) }, tr("Opening port...")); }
  async function allowFirewallIp() { await runFirewallAction('/firewall/allow-ip', { method: 'POST', body: JSON.stringify({ ip: firewallAllowIp, port: firewallAllowPort || null, protocol: firewallAllowProtocol }) }, tr("Allowing IP...")); }
  async function blockFirewallIp() {
    if (!confirm(tr("Block {0}?", firewallBlockIp || tr("this IP")))) return;
    await runFirewallAction('/firewall/block-ip', { method: 'POST', body: JSON.stringify({ ip: firewallBlockIp, port: firewallBlockPort || null, protocol: firewallBlockProtocol, note: firewallBlockNote.trim() || null }) }, tr("Blocking IP..."));
    setFirewallBlockNote('');
  }
  async function deleteFirewallRule(numberOverride = firewallDeleteNumber, label = '') {
    const ruleNumber = String(numberOverride || '').trim();
    if (!ruleNumber) return;
    if (!confirm(label ? tr("Remove the firewall rule for {0}?", label) : tr("Delete firewall rule #{0}?", ruleNumber))) return;
    await runFirewallAction(`/firewall/rules/${encodeURIComponent(ruleNumber)}`, { method: 'DELETE' }, tr("Deleting rule..."));
    setFirewallDeleteNumber('');
    setFirewallIpReload(n => n + 1);
  }

  useEffect(() => {
    if (!showFirewallIpList) return undefined;
    let cancelled = false;
    // Typing into the search waits for a pause; paging and filters load at once.
    const timer = setTimeout(async () => {
      const params = new URLSearchParams({ q: firewallIpQuery.trim(), action: firewallIpAction, page: String(firewallIpPage), size: '50' });
      const data = await request(`/firewall/ip-rules?${params}`, {});
      if (!cancelled && data) setFirewallIpList(data);
    }, firewallIpQuery ? 250 : 0);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [showFirewallIpList, firewallIpQuery, firewallIpAction, firewallIpPage, firewallIpReload]);

  function parseFirewallBlocklistUrls(text) {
    const lines = String(text || '').split('\n');
    const urls = [];
    let inUrls = false;
    for (const raw of lines) {
      const line = raw.trim();
      if (line === 'URLs:') { inUrls = true; continue; }
      if (line === 'Networks:' || line === 'Timer:') break;
      if (inUrls && /^https?:\/\//i.test(line)) urls.push(line);
    }
    return urls;
  }

  async function loadFirewallBlocklists() {
    const data = await request('/firewall/blocklists', {}, tr("Loading blocklists..."));
    if (data) setFirewallBlocklists(data);
  }

  async function addFirewallBlocklistUrl() {
    const url = firewallBlocklistUrl.trim();
    if (!url) return;
    const data = await request('/firewall/blocklists', { method: 'POST', body: JSON.stringify({ url }) }, tr("Adding blocklist URL..."));
    if (data) {
      setNotice((data.stdout || data.stderr || tr("Blocklist URL added.")).trim());
      setFirewallBlocklistUrl('');
      await loadFirewallBlocklists();
    }
  }

  async function deleteFirewallBlocklistUrl(url) {
    if (!confirm(tr("Delete blocklist URL?\n{0}", url))) return;
    const data = await request('/firewall/blocklists/delete', { method: 'POST', body: JSON.stringify({ url }) }, tr("Deleting blocklist URL..."));
    if (data) {
      setNotice((data.stdout || data.stderr || tr("Blocklist URL removed.")).trim());
      await loadFirewallBlocklists();
    }
  }

  async function updateFirewallBlocklistsNow() {
    const data = await request('/firewall/blocklists/update', { method: 'POST' }, tr("Refreshing blocklists..."));
    if (data) {
      setNotice((data.stdout || data.stderr || tr("Blocklists refreshed.")).trim());
      await loadFirewall();
      await loadFirewallBlocklists();
    }
  }

  async function loadWafRules() {
    const data = await request('/waf/rules', {}, tr("Loading WAF rules..."));
    if (data) setWafRules(data);
  }

  function closeWafSiteConfig() {
    setWafSiteConfig(null);
    setSelectedWafWebsiteId('');
    setWafCustomRules('');
    setWafBotExtra('');
  }

  async function loadWebsiteWafConfig(websiteId = selectedWafWebsiteId, showLoading = true) {
    if (!websiteId) {
      setWafSiteConfig(null);
      return;
    }
    const data = await request(`/waf/websites/${websiteId}`, {}, showLoading ? tr("Loading website WAF...") : '');
    if (data) {
      setSelectedWafWebsiteId(String(websiteId));
      setWafSiteConfig(data);
      setWafCustomRules(data.custom_rules || '');
      setWafBotExtra((data.bot_extra || []).join('\n'));
    }
  }

  function toggleWafDefaultRule(ruleId, enabled) {
    setWafSiteConfig(prev => {
      if (!prev) return prev;
      const current = new Set(prev.enabled_rule_ids || []);
      if (enabled) current.add(ruleId); else current.delete(ruleId);
      return {
        ...prev,
        enabled_rule_ids: Array.from(current),
        default_rules: (prev.default_rules || []).map(rule => rule.id === ruleId ? { ...rule, enabled } : rule),
      };
    });
  }

  async function saveWebsiteWafRules() {
    if (!selectedWafWebsiteId || !wafSiteConfig) return;
    const data = await request(`/waf/websites/${selectedWafWebsiteId}`, {
      method: 'PUT',
      body: JSON.stringify({
        enabled_rule_ids: wafSiteConfig.enabled_rule_ids || [],
        custom_rules: wafCustomRules,
        bot_blocking_enabled: !!wafSiteConfig.bot_blocking_enabled,
        bot_extra: wafBotExtra,
      }),
    }, tr("Saving website WAF rules..."));
    if (data) {
      setWafSiteConfig(data);
      setWafCustomRules(data.custom_rules || '');
      setWafBotExtra((data.bot_extra || []).join('\n'));
      setNotice(data.message || tr("Website WAF rules saved."));
      await refreshAll();
    }
  }

  async function loadBadBots() {
    const data = await request('/waf/bad-bots', { silent: true }, '');
    if (data) {
      setBadBots({ ...data, patterns: data.patterns || [] });
      setBadBotText((data.patterns || []).join('\n'));
    }
  }

  async function saveBadBots() {
    const count = badBotText.split(/[\r\n,]+/).map(s => s.trim()).filter(s => s && !s.startsWith('#')).length;
    if (!confirm(
      tr("Apply this bad bot list to every website?\n\n{0} pattern(s).\n\n", count)
      + tr("Requests whose User-Agent contains one of them get 403 on every site that has bot blocking on.")
    )) return;
    const data = await request('/waf/bad-bots', {
      method: 'PUT',
      body: JSON.stringify({ patterns: badBotText }),
    }, tr("Applying bad bot list to all websites..."));
    if (data) {
      setBadBots({ ...data, patterns: data.patterns || [] });
      setBadBotText((data.patterns || []).join('\n'));
      setNotice(data.message || tr("Bad bot list saved."));
      if (selectedWafWebsiteId) await loadWebsiteWafConfig(selectedWafWebsiteId, false);
    }
  }

  async function loadWafAccessLogs(nextFilters = wafAccessFilters, showLoading = true) {
    const filters = { ...wafAccessFilters, ...nextFilters };
    const params = new URLSearchParams();
    if (filters.domain) params.set('domain', filters.domain);
    if (filters.verdict) params.set('verdict', filters.verdict);
    if (filters.q) params.set('q', filters.q);
    params.set('limit', String(filters.limit || 50));
    params.set('offset', String(filters.offset || 0));
    const data = await request(`/waf/access-logs?${params.toString()}`, {}, showLoading ? tr("Loading access logs...") : '');
    if (data) {
      setWafAccessFilters(filters);
      setWafAccessLogs(data);
    }
  }

  function exportWafAccessLogs() {
    const rows = wafAccessLogs.entries || [];
    const header = ['verdict', 'time', 'site', 'method', 'path', 'ip', 'reason', 'status', 'user_agent'];
    const csv = [
      header.join(','),
      ...rows.map(row => header.map(key => csvEscape(row[key])).join(',')),
    ].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `opanel-access-logs-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  async function clearWafAccessLogs() {
    const domainLabel = wafAccessFilters.domain || tr("all domains");
    if (!confirm(tr("Clear WAF access logs for {0}?", domainLabel))) return;
    const params = new URLSearchParams();
    if (wafAccessFilters.domain) params.set('domain', wafAccessFilters.domain);
    const suffix = params.toString() ? `?${params.toString()}` : '';
    const data = await request(`/waf/access-logs${suffix}`, { method: 'DELETE' }, tr("Clearing access logs..."));
    if (data) await loadWafAccessLogs({ ...wafAccessFilters, offset: 0 }, false);
  }

  async function loadAddons(silent = false) {
    const data = await request('/addons', { silent }, silent ? '' : tr("Loading addons..."));
    if (data) setAddonList(data.addons || []);
    return data?.addons || [];
  }

  async function installAddon(addon) {
    const data = await request(`/addons/${addon.id}/install`, { method: 'POST' }, tr("Installing {0}...", addon.name));
    if (data) {
      setNotice(tr("{0} is installing in the background. This page follows along.", addon.name));
      loadAddons(true);
    }
  }

  async function uninstallAddon(addon) {
    // Removing an addon takes its protection away, so make the admin say the
    // name rather than clicking through a generic confirm.
    const typed = window.prompt(tr("Remove {0} and stop what it is doing?\n\nType the addon name to confirm:", addon.name));
    if (typed === null) return;
    // The prompt asks for the name shown on the card ("Email"); the id ("mail")
    // is accepted too. Comparing with the id alone refused every addon whose
    // name is not its id.
    const answer = typed.trim().toLowerCase();
    if (answer !== String(addon.name || '').trim().toLowerCase() && answer !== addon.id) { setError(tr("Name did not match; nothing was removed.")); return; }
    const data = await request(`/addons/${addon.id}/uninstall`, { method: 'POST' }, tr("Removing {0}...", addon.name));
    if (data) loadAddons(true);
  }

  async function setAddonRunning(addon, running) {
    const data = await request(`/addons/${addon.id}/service`, {
      method: 'POST', body: JSON.stringify({ running }),
    }, running ? tr("Starting {0}...", addon.name) : tr("Stopping {0}...", addon.name));
    if (data) { setNotice(`${addon.name} ${running ? tr("started") : tr("stopped")}.`); loadAddons(true); }
  }

  async function loadFail2ban(silent = false) {
    const [settings, banned, log] = await Promise.all([
      request('/addons/fail2ban/settings', { silent: true }),
      request('/addons/fail2ban/banned', { silent: true }),
      request('/addons/fail2ban/log?lines=40', { silent: true }),
    ]);
    if (settings) { setF2bSettings(settings); setF2bDraft(prev => prev || settings); }
    if (banned) setF2bBanned(banned.banned || []);
    if (log) setF2bLog(log.log || '');
  }

  async function loadF2bAddon() {
    const data = await request('/addons/fail2ban', { silent: true }, '');
    setF2bAddon(data || null);
    if (data?.installed) loadFail2ban(true);
    return data;
  }

  async function setF2bRunning(running) {
    await setAddonRunning(f2bAddon, running);
    loadF2bAddon();
  }

  async function saveFail2banSettings() {
    if (!f2bDraft) return;
    const data = await request('/addons/fail2ban/settings', {
      method: 'POST', body: JSON.stringify(f2bDraft),
    }, tr("Applying Fail2ban settings..."));
    if (data) {
      setF2bSettings(data);
      setF2bDraft(data);
      setNotice(tr("Fail2ban settings applied."));
      loadFail2ban(true);
    }
  }

  async function unbanAddress(address) {
    const data = await request('/addons/fail2ban/unban', {
      method: 'POST', body: JSON.stringify({ address }),
    }, tr("Unbanning {0}...", address));
    if (data) setF2bBanned(data.banned || []);
  }

  async function loadUpdates(force = false) {
    const data = await request(`/updates/status${force ? '?refresh=true' : ''}`, {}, tr("Loading update status..."));
    if (data) setUpdatesStatus(data);
  }

  async function toggleUpdateLog() {
    if (!showUpdateLog && !updatesStatus) await loadUpdates();
    setShowUpdateLog(prev => !prev);
  }

  async function runOsUpdate() {
    if (!confirm(tr("Run apt-get update && apt-get upgrade now?"))) return;
    setOsUpdating(true);
    const data = await request('/updates/os/run', { method: 'POST' }, tr("Updating OS packages..."));
    setOsUpdating(false);
    if (data) { setNotice((data.stdout || data.stderr || tr("OS update completed.")).trim()); if (showUpdateLog) await loadUpdates(); }
  }

  async function saveOsAutoUpdate() {
    const data = await request('/updates/os/auto', { method: 'POST', body: JSON.stringify(osAutoUpdate) }, tr("Saving OS auto update..."));
    if (data) { setNotice((data.stdout || data.stderr || tr("OS auto update saved.")).trim()); if (showUpdateLog) await loadUpdates(); }
  }

  async function runPanelUpdate() {
    if (!confirm(tr("Update opanel from GitHub main now? The API may restart and this page will reload when done."))) return;
    setPanelUpdating(true);
    setShowUpdateLog(true);
    setPanelUpdateLog([]);
    const data = await request('/updates/panel/run', { method: 'POST' }, tr("Updating opanel..."));
    if (!data) {
      setPanelUpdating(false);
      return;
    }
    // Poll /updates/status every 2s until the update finishes, then reload.
    // The API restarts (twice) mid-update, so /updates/status will fail for a
    // stretch -- tolerate that, but never spin forever: give up after a run of
    // failed polls or once an overall deadline passes, and clear the spinner so
    // the page does not get stuck showing "Running ...".
    const startedAt = Date.now();
    const DEADLINE_MS = 30 * 60 * 1000;
    const MAX_CONSECUTIVE_FAILURES = 45; // ~90s of the API being unreachable
    let consecutiveFailures = 0;
    let finished = false;
    const stopPolling = () => {
      finished = true;
      if (panelUpdateInterval.current) {
        clearInterval(panelUpdateInterval.current);
        panelUpdateInterval.current = null;
      }
      setPanelUpdating(false);
    };
    const pollOnce = async () => {
      const status = await request('/updates/status', {}, null);
      if (!status) {
        consecutiveFailures += 1;
        if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES || Date.now() - startedAt > DEADLINE_MS) {
          stopPolling();
          setNotice(tr("Lost contact with the panel while it was updating. It has most likely finished — reload the page (Ctrl+Shift+R) to see the result."));
        }
        return;
      }
      consecutiveFailures = 0;
      setUpdatesStatus(status);
      if (Array.isArray(status.panel_update_log)) {
        setPanelUpdateLog(status.panel_update_log);
      } else if (typeof status.panel_update_log === 'string' && status.panel_update_log) {
        setPanelUpdateLog(status.panel_update_log.split('\n'));
      }
      const st = status.panel || {};
      const done = st.last_update_status === 'completed' || st.last_update_status === 'failed';
      if (done) {
        stopPolling();
        if (st.last_update_status === 'completed' && Number(st.progress_percent) === 100) {
          setNotice(tr("Panel update completed. Reloading to apply the new version..."));
          setTimeout(() => { window.location.reload(); }, 2000);
        } else if (st.last_update_status === 'failed') {
          setNotice((st.progress_message || st.last_update_message || tr("Panel update failed.")).trim());
        }
        return;
      }
      if (Date.now() - startedAt > DEADLINE_MS) {
        stopPolling();
        setNotice(tr("The panel update is taking longer than expected. Reload the page (Ctrl+Shift+R) to check whether it finished."));
      }
    };
    await pollOnce();
    if (panelUpdateInterval.current) clearInterval(panelUpdateInterval.current);
    if (!finished) panelUpdateInterval.current = setInterval(pollOnce, 2000);
  }

  async function savePanelAutoUpdate() {
    const data = await request('/updates/panel/auto', { method: 'POST', body: JSON.stringify(panelAutoUpdate) }, tr("Saving panel auto update..."));
    if (data) { setNotice((data.stdout || data.stderr || tr("Panel auto update saved.")).trim()); if (showUpdateLog) await loadUpdates(); }
  }

  useEffect(() => {
    if (isAuthenticated) {
      refreshAll();
    }
  }, [isAuthenticated]);

  useEffect(() => {
    if (standaloneEditor) return undefined;
    const syncPageFromLocation = () => setPage(pageFromPathname(window.location.pathname));
    syncPageFromLocation();
    window.addEventListener('popstate', syncPageFromLocation);
    return () => window.removeEventListener('popstate', syncPageFromLocation);
  }, [standaloneEditor]);

  useEffect(() => {
    if (!isAuthenticated || !standaloneEditor) return;
    setSelectedWebsiteId(standaloneEditor.websiteId);
    setFilePath(standaloneEditor.path);
    readFile(standaloneEditor.path, standaloneEditor.websiteId);
  }, [isAuthenticated, standaloneEditor]);

  useEffect(() => {
    if (!standaloneEditor || !isAuthenticated) return undefined;
    const handler = event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        writeFile();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [standaloneEditor, isAuthenticated, selectedWebsiteId, filePath, fileContent]);

  useEffect(() => {
    if (!isAuthenticated || page !== 'dashboard' || !isAdmin) return undefined;
    loadResourceUsage();
    const timer = setInterval(loadResourceUsage, 5000);
    return () => clearInterval(timer);
  }, [isAuthenticated, page, isAdmin]);

  useEffect(() => {
    if (!isAuthenticated || page !== 'services') return undefined;
    checkAllServices();
    const timer = setInterval(checkAllServices, 10000);
    return () => clearInterval(timer);
  }, [isAuthenticated, page]);

  useEffect(() => {
    if (!isAuthenticated || page !== 'wafLogs') {
      wafAccessFiltersRef.current = wafAccessFilters;
      return undefined;
    }
    const prev = wafAccessFiltersRef.current;
    const qChanged = prev.q !== wafAccessFilters.q;
    const otherChanged =
      prev.domain !== wafAccessFilters.domain ||
      prev.verdict !== wafAccessFilters.verdict ||
      prev.limit !== wafAccessFilters.limit;
    const timer = window.setTimeout(() => {
      loadWafAccessLogs({ ...wafAccessFilters, offset: 0 }, false);
    }, qChanged && !otherChanged ? 200 : 0);
    wafAccessFiltersRef.current = wafAccessFilters;
    return () => window.clearTimeout(timer);
  }, [
    isAuthenticated,
    isAdmin,
    page,
    wafAccessFilters.domain,
    wafAccessFilters.verdict,
    wafAccessFilters.q,
    wafAccessFilters.limit,
  ]);

  useEffect(() => {
    if (!currentSite) return;
    const m = currentSite.ssl_mode;
    setSslMode(m === 'manual' ? 'manual' : m === 'reuse' ? 'existing' : currentSite.ssl_wildcard ? 'wildcard' : 'letsencrypt');
    setManualSslForm({ certificate: '', private_key: '', ca_bundle: '' });
    setManualSslFiles({ certificate: null, private_key: null, ca_bundle: null });
    setWildcardSslForm({ api_token: '', email: '', provider: '' });
    setAvailableCerts([]);
    setReuseCertName(m === 'reuse' ? (currentSite.ssl_reuse_name || '') : '');
  }, [currentSite?.id]);

  useEffect(() => { if (selectedWebsiteId && page === 'backups') { listBackups(); loadBackupJobs(); } }, [selectedWebsiteId, page]);

  useEffect(() => { if (selectedWebsiteId && page === 'cron') listCron(); }, [selectedWebsiteId, page]);

  useEffect(() => { if (selectedWebsiteId && page === 'files') listFiles('public_html'); }, [selectedWebsiteId, page]);

  useEffect(() => { if (selectedBackupUserId && page === 'backups') listUserBackups(selectedBackupUserId); }, [selectedBackupUserId, page]);

  const jobsRunning = backupJobs.some(job => job.status === 'running' || job.status === 'queued');

  useEffect(() => {
    if (!isAuthenticated || page !== 'backups') return undefined;
    loadBackupJobs();
    // Closer while a bar is moving, further apart when nothing is happening:
    // a five-second bar looks stuck, and an idle page should not be asking.
    const timer = setInterval(loadBackupJobs, jobsRunning ? 2000 : 5000);
    return () => clearInterval(timer);
  }, [isAuthenticated, page, selectedWebsiteId, selectedBackupUserId, jobsRunning]);

  // When a restore finishes, what it created should show up: the new panel
  // users, their websites, and -- for this server -- the list it came from.
  const restoreJob = backupJobs.find(job => job.job_id === restoreJobId);
  const restoreJobFinished = !!restoreJob && (restoreJob.status === 'done' || restoreJob.status === 'error');
  useEffect(() => {
    if (!restoreJobFinished) return;
    loadUsers();
    refreshAll();
    if (restoreSource === 'local') loadRestoreList('local');
  }, [restoreJobFinished]);

  // This server needs nothing typed in, so its list is read as soon as the
  // step is on screen; a destination as soon as one is chosen.
  useEffect(() => {
    if (!isAuthenticated || !isAdmin || page !== 'backups' || backupTab !== 'restore') return;
    if (restoreSource === 'local') loadRestoreList('local');
    if (restoreSource === 'target' && restoreTargetId) loadRestoreList('target');
  }, [isAuthenticated, isAdmin, page, backupTab, restoreSource, restoreTargetId]);

  useEffect(() => {
    if (isAuthenticated && page === 'users') { loadUsers(); loadPlans(); if (currentUser?.role === 'reseller') loadResellerPool(); }
    if (isAuthenticated && page === 'websites' && ['admin', 'reseller'].includes(currentUser?.role) && users.length === 0) loadUsers();
    // The databases page shows an owner per row for admins, and offers to
    // hand one over, so it needs the account list too.
    if (isAuthenticated && page === 'databases' && currentUser?.role === 'admin') loadUsers();
    if (isAuthenticated && page === 'notifications') loadNotifications();
    if (isAuthenticated && page === 'php') { loadPhpConfig(); loadPhpVersions(); loadPhpExtensions(); }
    if (isAuthenticated && page === 'firewall') { loadFirewall(); loadFirewallBlocklists(); if (isAdmin) loadF2bAddon(); }
    if (isAuthenticated && page === 'waf') { loadWafRules(); loadBadBots(); }
    if (isAuthenticated && page === 'updates' && currentUser?.role === 'admin') loadUpdates();
    if (isAuthenticated && page === 'security') {
      loadTwoFactorStatus();
      loadPasskeys();
    }
    if (isAuthenticated && page === 'malware' && isAdmin) {
      loadMalwareScanStatus();
      loadMalwareScanJobs();
      loadLatestMalwareScanJob();
      loadMalwareSchedule();
      loadQuarantine();
      if (!websites.length) refreshAll();
    }
    if (isAuthenticated && page === 'addons' && isAdmin) loadAddons();
    if (isAuthenticated && page === 'mcp') loadMcp();
    if (isAuthenticated && page === 'mail') { loadMailInfo(); if (isAdmin) loadUsers(); }
    if (isAuthenticated && page === 'dns') loadDnsInfo();
    if (isAuthenticated && page === 'dashboard') loadDashboardSummary();
    if (isAuthenticated && page === 'sftp') { loadSftp(); if (isAdmin) loadUsers(); if (!websites.length) refreshAll(); }
    if (isAuthenticated && page === 'settings') { loadPanelSettings(); loadApiTokens(); loadNetworkStatus(); }
    if (isAuthenticated && page === 'backups' && currentUser?.role === 'admin') { loadUsers(); loadSftpTargets(); loadBackupSchedules(); }
  }, [isAuthenticated, page, currentUser?.role]);

  // Whether MCP is on decides if the page is offered at all, and an admin can
  // switch it on the Addons page without leaving the panel.
  useEffect(() => {
    if (isAuthenticated) loadMcpInfo();
  }, [isAuthenticated, addonList]);

  useEffect(() => {
    if (!isAuthenticated) loadDemoInfo();
  }, [isAuthenticated]);

  // Email: offered in the sidebar once the addon is installed.
  useEffect(() => {
    if (isAuthenticated) loadMailInfo();
  }, [isAuthenticated, addonList]);

  // Resource limits: whether the addon is on, then live use while a page shows it.
  useEffect(() => {
    if (isAuthenticated) loadLimitsInfo();
  }, [isAuthenticated, addonList]);
  useEffect(() => {
    if (!isAuthenticated || !limitsInfo?.installed || !['dashboard', 'users', 'usage'].includes(page)) return undefined;
    loadLimitsInfo();
    const timer = window.setInterval(loadLimitsInfo, 15000);
    return () => window.clearInterval(timer);
  }, [isAuthenticated, page, !!limitsInfo?.installed]);
  // The Resource usage page's charts: the agent adds a point every five
  // minutes, so a minute between reads is plenty.
  useEffect(() => {
    if (!isAuthenticated || page !== 'usage' || isAdmin || !limitsInfo?.installed) return undefined;
    loadLimitsHistory();
    const timer = window.setInterval(loadLimitsHistory, 60000);
    return () => window.clearInterval(timer);
  }, [isAuthenticated, page, isAdmin, currentUser?.id, !!limitsInfo?.installed]);
  // A reseller's dashboard can show its group, and customers and disk are its share.
  useEffect(() => {
    if (isAuthenticated && page === 'dashboard' && isReseller) loadResellerPool();
  }, [isAuthenticated, page, isReseller]);
  // "/" jumps to the dashboard's tool search, as it does in cPanel.
  useEffect(() => {
    if (page !== 'dashboard') return undefined;
    const onKey = event => {
      if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
      const tag = (event.target?.tagName || '').toLowerCase();
      if (['input', 'textarea', 'select'].includes(tag) || event.target?.isContentEditable) return;
      event.preventDefault();
      toolSearchRef.current?.focus();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [page]);
  useEffect(() => {
    if (page !== 'users' || pendingEditUserId == null) return;
    const user = users.find(item => item.id === pendingEditUserId);
    if (user) {
      startEditingUser(user);
      setPendingEditUserId(null);
    }
  }, [page, users, pendingEditUserId]);

  // DNS Manager, likewise; its zones load page by page on the server.
  useEffect(() => {
    if (isAuthenticated) loadDnsInfo();
  }, [isAuthenticated, addonList]);

  useEffect(() => {
    if (isAuthenticated && page === 'dns' && dnsInfo?.installed) loadDnsZones();
  }, [isAuthenticated, page, dnsPage, dnsInfo?.installed]);

  useEffect(() => {
    if (isAuthenticated && page === 'dns' && dnsInfo?.installed) syncDnsZones();
  }, [isAuthenticated, page, dnsInfo?.installed]);

  useEffect(() => {
    if (!isAuthenticated || page !== 'mail') return;
    if (mailTab === 'mailboxes') loadMailboxes();
    if (mailTab === 'forwarders') loadForwarders();
  }, [isAuthenticated, page, mailTab, mailPage, mailFilter.domain_id]);

  useEffect(() => {
    if (!isAuthenticated || !isAdmin || page !== 'mail') return;
    if (mailTab === 'relay') loadMailRelays();
    if (mailTab === 'rspamd') { loadRspamdStat(); loadRspamdHistory(1); loadRspamdLog(); }
    if (mailTab === 'server') { loadMailSettings(); loadEximLog(); }
  }, [isAuthenticated, isAdmin, page, mailTab]);

  useEffect(() => {
    if (!isAuthenticated || !isAdmin || page !== 'addons') return;
    if (addonList.some(a => a.id === 'demo' && a.installed)) { loadDemoSettings(); loadUsers(); }
  }, [isAuthenticated, isAdmin, page, addonList]);

  // Same for Notifications: the page is offered once the addon is on.
  useEffect(() => {
    if (isAuthenticated) loadNotifyInfo();
  }, [isAuthenticated, addonList]);

  // And the Malware Scanner addon.
  useEffect(() => {
    if (isAuthenticated && isAdmin) loadMalwareScanStatus(true);
  }, [isAuthenticated, isAdmin, addonList]);

  useEffect(() => {
    if (!showNotifyLog) return undefined;
    let cancelled = false;
    (async () => {
      const params = new URLSearchParams({ page: String(notifyLogPage), size: '50', status: notifyLogStatus });
      const data = await request(`/notifications/log?${params}`, {});
      if (!cancelled && data) setNotifyLog(data);
    })();
    return () => { cancelled = true; };
  }, [showNotifyLog, notifyLogPage, notifyLogStatus]);

  useEffect(() => {
    if (isAuthenticated && page === 'addons' && isAdmin && addonList.some(a => a.id === 'mcp' && a.installed)) loadMcpAllTokens();
  }, [isAuthenticated, page, isAdmin, addonList]);

  useEffect(() => {
    if (!scanJob?.job_id || !['queued', 'running'].includes(scanJob.status)) return undefined;
    setScanLoading(true);
    const poll = () => loadMalwareScanJob(scanJob.job_id);
    const timer = window.setInterval(poll, 2000);
    poll();
    return () => window.clearInterval(timer);
  }, [scanJob?.job_id, scanJob?.status]);

  useEffect(() => {
    if (!isAuthenticated || page !== 'wafLogs' || !wafAccessAutoRefresh) return undefined;
    const timer = window.setInterval(() => {
      loadWafAccessLogs({ ...wafAccessFilters, offset: 0 }, false);
    }, wafAccessAutoRefresh * 1000);
    return () => window.clearInterval(timer);
  }, [
    isAuthenticated,
    isAdmin,
    page,
    wafAccessAutoRefresh,
    wafAccessFilters.domain,
    wafAccessFilters.verdict,
    wafAccessFilters.q,
    wafAccessFilters.limit,
  ]);

  // An install is an apt run in a background thread, so the only way the page
  // learns it finished is to ask again. Polling stops as soon as nothing is busy.
  useEffect(() => {
    if (page !== 'addons' || !isAdmin) return undefined;
    if (!addonList.some(addon => addon.busy)) return undefined;
    const timer = setInterval(() => { loadAddons(true); }, 3000);
    return () => clearInterval(timer);
  }, [page, isAdmin, addonList]);


  useEffect(() => { setMobileMenuOpen(false); }, [page]);

  // The account menu closes on a click anywhere else, on Escape, and on navigation.
  useEffect(() => {
    if (!userMenuOpen) return undefined;
    const onPointer = event => { if (!userMenuRef.current?.contains(event.target)) setUserMenuOpen(false); };
    const onKey = event => { if (event.key === 'Escape') setUserMenuOpen(false); };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onPointer); document.removeEventListener('keydown', onKey); };
  }, [userMenuOpen]);

  useEffect(() => { setUserMenuOpen(false); setMalwareDetailJob(null); setShowFirewallIpList(false); setShowNotifyLog(false); window.scrollTo(0, 0); }, [page]);
  // Opening or leaving one website's WAF settings is a page change too.
  useEffect(() => { window.scrollTo(0, 0); }, [wafSiteConfig?.domain]);
  // So is opening or leaving the Firewall's address list.
  useEffect(() => { window.scrollTo(0, 0); }, [showFirewallIpList]);

  function roleLabel(role) {
    if (role === 'reseller') return tr("Reseller");
    return role === 'admin' ? tr("Admin") : tr("End user");
  }

  // The sidebar in three labelled groups -- what a site needs, what guards it,
  // and the server itself -- so nothing hides behind a collapsed "Settings".
  // Everything that is not day-to-day hosting work lives on the Settings page
  // rather than in the sidebar. Each entry: key, label, icon, one-line summary.
  const settingsGroups = [
    { key: 'security', title: tr("Security"), items: [
      ...(isAdmin ? [['firewall', tr("Firewall"), BrickWall, tr("Open ports, blocked addresses and blocklists")]] : []),
      ['waf', tr("WAF"), ShieldAlert, tr("Web application firewall for each website")],
      ['wafLogs', tr("Access logs"), ScrollText, tr("Visitors and what the WAF blocked")],
      ['security', tr("Account security"), LockKeyhole, tr("Password, two-factor authentication and passkeys")],
    ] },
    { key: 'system', title: tr("System"), items: [
      ...(isAdmin ? [['settings', tr("Panel settings"), SettingsIcon, tr("Branding, panel address and certificate")]] : []),
      ...(isAdmin ? [['services', tr("Services"), Activity, tr("Start, stop and check the server's daemons")]] : []),
      ...(isAdmin ? [['php', tr("PHP config"), Code2, tr("PHP versions, limits and extensions")]] : []),
      ...(isAdmin ? [['updates', tr("Updates"), RefreshCw, tr("Panel and system updates")]] : []),
      ...(isAdmin ? [['addons', tr("Addons"), PackageOpen, tr("Install and turn on optional features")]] : []),
    ] },
  ].filter(group => group.items.length > 0);
  const settingsItems = settingsGroups.flatMap(group => group.items);

  // An addon earns a sidebar entry only while it is turned on.
  const addonNavItems = [
    // Email is everyday work for every account, so it leads the addons.
    ...(mailInfo?.installed ? [['mail', tr("Email"), Mail]] : []),
    ...(dnsInfo?.installed ? [['dns', tr("DNS Manager"), Network]] : []),
    ...(mcpInfo?.enabled ? [['mcp', tr("AI assistants (MCP)"), Bot]] : []),
    ...(isAdmin && notifyInfo?.enabled ? [['notifications', tr("Notifications"), Bell]] : []),
    ...(isAdmin && malwareScanStatus?.enabled ? [['malware', tr("Malware Scanner"), Bug]] : []),
  ];

  // A reseller's accounts page is the same page: its customers.
  const resellerNavItems = isReseller ? [['users', tr("Customers"), Users]] : [];

  // One plain list, top to bottom: no section headings.
  const navSections = [
    { key: 'main', items: [
      ['dashboard', tr("Dashboard"), Home],
      ['websites', tr("Websites"), Globe],
      ['ssl', tr("SSL"), Lock],
      ['databases', tr("Databases"), Database],
      ['cron', tr("Cron"), Clock],
      ['files', tr("File manager"), FolderOpen],
      ['sftp', tr("SFTP accounts"), KeyRound],
      ['backups', tr("Backups"), Archive],
      ...(isAdmin ? [['users', tr("Panel users"), Users]] : []),
      ...resellerNavItems,
      ...addonNavItems,
      ['config', tr("Settings"), SettingsIcon],
    ] },
  ];

  const navItems = navSections.flatMap(section => section.items);
  // A page reached from Settings keeps Settings lit in the sidebar.
  const settingsPage = settingsItems.find(([key]) => key === page);
  const navKey = settingsPage ? 'config' : page === 'usage' ? 'dashboard' : page;
  // The scanner's page stays reachable by URL while its addon is off (it says
  // how to turn it on), so it keeps its own title then too.
  const activeNavItem = settingsPage || navItems.find(([key]) => key === navKey)
    || (page === 'malware' && isAdmin ? ['malware', tr("Malware Scanner"), Bug] : null)
    || (page === 'usage' && limitsOn && !isAdmin ? ['usage', tr("Resource usage"), Activity] : navItems[0]);

  function renderNotifications() {
    const errorMessage = formatApiError(error, '').trim();
    const noticeMessage = formatApiError(notice, '').trim();
    if (!errorMessage && !noticeMessage) return null;
    return <div className="app-toast-stack" aria-label={tr("Notifications")}>
      <NotificationToast type="error" message={errorMessage} onClose={() => setError('')} />
      <NotificationToast type="success" message={noticeMessage} onClose={() => setNotice('')} />
    </div>;
  }

  function websiteUrl(site) {
    const value = (site?.domain || '').trim();
    if (/^https?:\/\//i.test(value)) return value;
    return `${site?.ssl_enabled ? 'https' : 'http'}://${value}`;
  }

  function parentFilePath(path) {
    const parts = String(path || '').split('/').filter(Boolean);
    parts.pop();
    return parts.join('/');
  }

  function fileBreadcrumbs(path) {
    const parts = String(path || '').split('/').filter(Boolean);
    let current = '';
    return parts.map(part => {
      current = current ? `${current}/${part}` : part;
      return { label: part, path: current };
    });
  }

  function isTextEditable(item) {
    if (!item || item.is_dir) return false;
    const name = (item.name || '').toLowerCase();
    const editableDotfiles = new Set(['.env', '.env.example', '.htaccess', '.user.ini', '.gitignore', '.gitattributes']);
    return editableDotfiles.has(name) || /\.(txt|md|json|css|js|jsx|ts|tsx|html|htm|xml|yml|yaml|ini|conf|log|php|env|htaccess)$/.test(name) || !name.includes('.');
  }

  function toggleFileSelection(path) {
    setSelectedFilePaths(prev => prev.includes(path) ? prev.filter(item => item !== path) : [...prev, path]);
  }

  function toggleAllFiles() {
    setSelectedFilePaths(prev => prev.length === files.length ? [] : files.map(item => item.path));
  }

  function editorLanguage(path) {
    const name = String(path || '').toLowerCase();
    if (/\.php\d?$/.test(name) || name.endsWith('.phtml')) return 'PHP';
    if (/\.(js|jsx|ts|tsx)$/.test(name)) return 'JavaScript';
    if (/\.css$/.test(name)) return 'CSS';
    if (/\.html?$/.test(name)) return 'HTML';
    if (/\.json$/.test(name)) return 'JSON';
    if (/\.ya?ml$/.test(name)) return 'YAML';
    if (/\.(conf|ini|env|htaccess)$/.test(name)) return 'Config';
    return 'Text';
  }

  function WebsiteSelect() {
    return <select value={selectedWebsiteId} onChange={e => setSelectedWebsiteId(e.target.value)}>
      <option value="">{tr("-- Select website --")}</option>
      {websites.map(site => <option key={site.id} value={site.id}>{site.domain}</option>)}
    </select>;
  }

  function EmptyState({ icon: Icon = AlertCircle, message = tr('No data yet'), action }) {
    return <div className="empty-state">
      <Icon size={40} />
      <p>{message}</p>
      {action && <button type="button" className="mini secondary-light" onClick={action.onClick}>{action.icon ? <action.icon size={14}/> : null} {action.label}</button>}
    </div>;
  }

  function formatBytes(value) {
    const amount = Number(value);
    if (!Number.isFinite(amount) || amount < 0) return '--';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = amount;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
    return `${size >= 10 || unit === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`;
  }

  // A file's modification time (epoch seconds) at a fixed width, so the column
  // lines up: 26/09/2026 10:31 in Vietnamese, 2026-09-26 10:31 in English.
  function formatFileTime(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value) || value <= 0) return '';
    const d = new Date(value * 1000);
    const pad = n => String(n).padStart(2, '0');
    const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return currentLanguage === 'vi'
      ? `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${time}`
      : `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${time}`;
  }

  function formatPercent(value) {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return '--';
    return `${Math.round(amount)}%`;
  }

  // Memory as the Resource limits addon reports it, in MB.
  function formatMegabytes(value) {
    const mb = Number(value) || 0;
    if (mb >= 1024) return `${(mb / 1024).toFixed(mb >= 10240 ? 0 : 1)} GB`;
    return `${Math.round(mb)} MB`;
  }

  // CPU where 100% is one core: one decimal while it is small.
  function formatCpuPercent(value) {
    const cpu = Number(value) || 0;
    return `${cpu < 10 ? cpu.toFixed(1).replace(/\.0$/, '') : Math.round(cpu)}%`;
  }

  // Disk speed. A quiet site moves a few KB a second, so below 1 MB/s two
  // significant digits are kept: "0.04 MB/s" rather than a "0" that reads as
  // "not measured".
  function formatMbps(value) {
    const mbps = Number(value) || 0;
    if (mbps >= 10) return `${Math.round(mbps)} MB/s`;
    if (mbps >= 1) return `${mbps.toFixed(1).replace(/\.0$/, '')} MB/s`;
    return `${mbps > 0 ? Number(mbps.toPrecision(2)) : 0} MB/s`;
  }

  function clampPercent(value) {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return 0;
    return Math.max(0, Math.min(100, amount));
  }

  function storageLimitBytes(user) {
    if (!user) return null;
    if (user.storage_limit_bytes === null) return null;
    if (user.storage_limit_bytes !== undefined) return user.storage_limit_bytes;
    return Number(user.storage_limit_mb || 0) * 1024 * 1024;
  }

  function storageUsageText(user) {
    const limit = storageLimitBytes(user);
    // null is "not measured yet", which is not the same claim as 0 B. The
    // accounts list ships without it so the page can paint straight away.
    if (user?.storage_used_bytes == null) {
      return limit === null ? 'measuring...' : `measuring... / ${formatBytes(limit)}`;
    }
    const used = Number(user.storage_used_bytes);
    if (limit === null) return `${formatBytes(used)}`;
    const pct = limit > 0 ? Math.round((used / limit) * 100) : 0;
    return `${formatBytes(used)}/${formatBytes(limit)} ${pct}%`;
  }

  // Where the server's RAM is (operator, 2026-10-06): customers could not see
  // why their accounts' RAM did not add up to the server's. The accounts
  // together, MariaDB -- every site's database, so in no account -- the rest
  // of the system, the cache the kernel hands back when it needs to, and what
  // is free. The parts add up to the total.
  function RamBreakdown({ memory }) {
    const parts = memory?.breakdown;
    const total = Number(memory?.total) || 0;
    if (!parts || !total) return null;
    const rows = [
      ['accounts', tr("Hosting accounts"), parts.accounts],
      ['mariadb', tr("MariaDB (every site's databases)"), parts.mariadb],
      ['system', tr("System and panel"), parts.system],
      ['cache', tr("Cache (given back when needed)"), parts.cache],
      ['free', tr("Free"), parts.free],
    ].filter(row => row[2] != null);
    return <div className="ram-breakdown">
      <div className="ram-breakdown-head"><strong>{tr("Where the RAM is")}</strong><small>{formatBytes(total)}</small></div>
      <div className="ram-breakdown-bar" role="img" aria-label={rows.map(([, label, value]) => `${label}: ${formatBytes(value)}`).join(', ')}>
        {rows.map(([key, , value]) => <span key={key} className={`ram-part ram-${key}`} style={{ width: `${Math.max(0, Math.min(100, (Number(value) || 0) / total * 100))}%` }} />)}
      </div>
      <ul className="ram-breakdown-legend">
        {rows.map(([key, label, value]) => <li key={key}><i className={`ram-swatch ram-${key}`} aria-hidden="true" /><span>{label}</span><b>{formatBytes(value)}</b></li>)}
      </ul>
      <p className="hint">{tr("An account's RAM is its processes plus their file cache. MariaDB serves every site, so it is in no account: that is why the accounts do not add up to what the server uses.")}</p>
    </div>;
  }

  function ResourceCard({ icon: Icon, label, value, detail, percent }) {
    const safePercent = percent == null ? null : clampPercent(percent);
    return <article className="resource-card">
      <div className="resource-head"><span className="resource-icon"><Icon size={16}/></span><span>{label}</span></div>
      <strong>{value}</strong>
      {/* A meter without a percentage keeps an empty track, so every card's
          value and detail sit on the same lines as its neighbours'. */}
      {safePercent !== null ? <div className="resource-track"><span style={{ width: `${safePercent}%` }}></span></div> : <div className="resource-track is-empty" aria-hidden="true"></div>}
      <small>{detail}</small>
    </article>;
  }

  // --- Dashboard, laid out as cPanel's home page ------------------------------
  // Tools on the left, grouped and searchable; on the right what the account
  // (or, for an administrator, the server) is and how much of it is used.

  // One cPanel-style statistic: a label, used / limit, and a meter that turns
  // amber at 80% and red at 95%, with an icon so colour is not the only sign.
  function StatRow({ label, value, percent, tone = '' }) {
    const safePercent = percent == null ? null : clampPercent(percent);
    const level = tone || (safePercent == null ? '' : safePercent >= 95 ? 'bad' : safePercent >= 80 ? 'warn' : '');
    return <div className={`stat-row${level ? ` tone-${level}` : ''}`}>
      <span className="stat-row-label">{level ? <AlertCircle size={13} aria-hidden="true"/> : null}{label}</span>
      <strong className="stat-row-value">{value}</strong>
      {safePercent !== null && <span className={`resource-track${level ? ` tone-${level}` : ''}`} aria-hidden="true"><span style={{ width: `${safePercent}%` }}></span></span>}
    </div>;
  }

  function InfoRow({ label, value, title }) {
    return <div className="info-row"><span>{label}</span><strong title={title || (typeof value === 'string' ? value : undefined)}>{value || '—'}</strong></div>;
  }

  function openUserFromDashboard(userId) {
    setPendingEditUserId(userId);
    navigateToPage('users');
  }

  // "3 d 4 h" / "5 h 12 min": how long the server has been up.
  function formatUptime(seconds) {
    const total = Math.max(0, Number(seconds) || 0);
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    return days ? tr("{0} d {1} h", days, hours) : tr("{0} h {1} min", hours, minutes);
  }

  // Accents and case set aside, so "tuong lua" finds "Tường lửa".
  function normalizeSearch(text) {
    return String(text || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\u0111/g, 'd').replace(/\u0110/g, 'D').toLowerCase();
  }

  // The tools on the dashboard, in cPanel's groups. Each opens its page (and,
  // where there is one, its tab or its create form). An addon's tools appear
  // only while it is installed, as its sidebar entry does.
  function dashboardToolGroups() {
    const go = (target, before) => () => { if (before) before(); navigateToPage(target); };
    const groups = [];
    if (canManageUsers) {
      groups.push({ key: 'accounts', title: isReseller ? tr("Customers") : tr("Accounts"), icon: Users, tools: [
        { key: 'users', label: isReseller ? tr("Customers") : tr("Panel users"), icon: Users, run: go('users', () => setUsersTab('list')) },
        { key: 'user-add', label: isReseller ? tr("Add customer") : tr("Add User"), icon: Plus, run: go('users', () => setUsersTab('add')) },
        { key: 'packages', label: tr("Packages"), icon: PackageOpen, run: go('users', () => setUsersTab('packages')) },
      ] });
    }
    groups.push({ key: 'websites', title: tr("Websites"), icon: Globe, tools: [
      { key: 'websites', label: tr("Websites"), icon: Globe, run: go('websites') },
      { key: 'site-new', label: tr("New website"), icon: Plus, run: go('websites', () => setShowCreateSite(true)) },
      { key: 'ssl', label: tr("SSL"), icon: Lock, run: go('ssl') },
      { key: 'waf', label: tr("WAF"), icon: ShieldAlert, run: go('waf') },
      { key: 'logs', label: tr("Access logs"), icon: ScrollText, run: go('wafLogs') },
      ...(isAdmin ? [{ key: 'php', label: tr("PHP config"), icon: Code2, run: go('php') }] : []),
    ] });
    groups.push({ key: 'files', title: tr("Files"), icon: FolderOpen, tools: [
      { key: 'files', label: tr("File manager"), icon: FolderOpen, run: go('files') },
      { key: 'sftp', label: tr("SFTP accounts"), icon: KeyRound, run: go('sftp') },
      { key: 'sftp-new', label: tr("New SFTP account"), icon: Plus, run: go('sftp', () => setShowCreateSftp(true)) },
      { key: 'backups', label: tr("Backups"), icon: Archive, run: go('backups') },
    ] });
    groups.push({ key: 'databases', title: tr("Databases"), icon: Database, tools: [
      { key: 'databases', label: tr("Databases"), icon: Database, run: go('databases') },
      { key: 'db-new', label: tr("New database"), icon: Plus, run: go('databases', () => setShowCreateDb(true)) },
    ] });
    if (mailInfo?.installed) {
      groups.push({ key: 'email', title: tr("Email"), icon: Mail, tools: [
        { key: 'mailboxes', label: tr("Mailboxes"), icon: Inbox, run: go('mail', () => setMailTab('mailboxes')) },
        { key: 'forwarders', label: tr("Forwarders"), icon: Forward, run: go('mail', () => setMailTab('forwarders')) },
      ] });
    }
    if (dnsInfo?.installed) {
      groups.push({ key: 'domains', title: tr("Domains"), icon: Network, tools: [
        { key: 'dns', label: tr("DNS Manager"), icon: Network, run: go('dns') },
      ] });
    }
    groups.push({ key: 'security', title: tr("Security"), icon: ShieldCheck, tools: [
      { key: 'account-security', label: tr("Account security"), icon: LockKeyhole, run: go('security') },
      ...(isAdmin ? [{ key: 'firewall', label: tr("Firewall"), icon: BrickWall, run: go('firewall') }] : []),
      ...(isAdmin && malwareScanStatus?.enabled ? [{ key: 'malware', label: tr("Malware Scanner"), icon: Bug, run: go('malware') }] : []),
    ] });
    groups.push({ key: 'advanced', title: tr("Advanced"), icon: SettingsIcon, tools: [
      { key: 'cron', label: tr("Cron"), icon: Clock, run: go('cron') },
      ...(limitsOn && !isAdmin ? [{ key: 'usage', label: tr("Resource usage"), icon: Activity, run: go('usage') }] : []),
      ...(mcpInfo?.enabled ? [{ key: 'mcp', label: tr("AI assistants (MCP)"), icon: Bot, run: go('mcp') }] : []),
      ...(isAdmin ? [
        { key: 'services', label: tr("Services"), icon: Server, run: go('services') },
        ...(notifyInfo?.enabled ? [{ key: 'notifications', label: tr("Notifications"), icon: Bell, run: go('notifications') }] : []),
        { key: 'updates', label: tr("Updates"), icon: RefreshCw, run: go('updates') },
        { key: 'addons', label: tr("Addons"), icon: PackageOpen, run: go('addons') },
        { key: 'panel-settings', label: tr("Panel settings"), icon: SettingsIcon, run: go('settings') },
      ] : []),
    ] });
    return groups;
  }

  function toggleToolGroup(key) {
    setCollapsedTools(prev => {
      const next = prev.includes(key) ? prev.filter(item => item !== key) : [...prev, key];
      try { localStorage.setItem(DASHBOARD_COLLAPSED_KEY, JSON.stringify(next)); } catch { /* a convenience only */ }
      return next;
    });
  }

  // `order` places the panel when the page folds into one column (see
  // .cp-layout): after the statistics for a customer, lower for an admin.
  function renderToolPanel(order) {
    const query = normalizeSearch(toolQuery.trim());
    const groups = dashboardToolGroups()
      .map(group => ({ ...group, tools: query ? group.tools.filter(tool => normalizeSearch(`${tool.label} ${group.title}`).includes(query)) : group.tools }))
      .filter(group => group.tools.length > 0);
    return <section className="section dash-card tools-panel" style={{ '--cp-order': order }}>
      <label className="tools-search">
        <Search size={16} aria-hidden="true"/>
        <input ref={toolSearchRef} type="search" value={toolQuery} onChange={e => setToolQuery(e.target.value)}
          placeholder={tr("Search tools (press /)")} aria-label={tr("Search tools")} />
      </label>
      {groups.length === 0 && <p className="hint">{tr("No tool matches your search.")}</p>}
      {groups.map(group => {
        const open = !!query || !collapsedTools.includes(group.key);
        return <div className={`tool-group${open ? ' is-open' : ''}`} key={group.key}>
          <button type="button" className="tool-group-head" aria-expanded={open} onClick={() => toggleToolGroup(group.key)}>
            <span className="tool-group-icon"><group.icon size={15}/></span>
            <h3>{group.title}</h3>
            <ChevronDown size={16} className="tool-group-chevron" aria-hidden="true"/>
          </button>
          {open && <div className="tool-grid">
            {group.tools.map(tool => <button type="button" key={tool.key} className="tool-tile" onClick={tool.run}>
              <span className="tool-icon"><tool.icon size={17}/></span>
              <span className="tool-label">{tool.label}</span>
            </button>)}
          </div>}
        </div>;
      })}
    </section>;
  }

  // The administrator's view of the Resource limits addon: who uses most now.
  function renderTopAccounts() {
    const rows = Object.entries(limitsInfo?.accounts || {}).map(([id, entry]) => ({ ...entry, id: Number(id), now: entry.usage || {}, caps: entry.limits || {} }));
    if (!rows.length) return null;
    const measure = topAccountsSort === 'memory' ? row => Number(row.now.memory_mb) || 0 : row => Number(row.now.cpu_percent) || 0;
    const top = rows.sort((a, b) => measure(b) - measure(a) || (b.oom_kills_day || 0) - (a.oom_kills_day || 0)).slice(0, 5);
    const share = (used, limit) => limit > 0 ? clampPercent((Number(used) || 0) / limit * 100) : null;
    const meter = (label, text, percent, limitText) => <span className="top-accounts-meter" data-label={label}>
      <span>{text}{limitText && <small> / {limitText}</small>}</span>
      {percent !== null && <span className={`resource-track${percent >= 90 ? ' tone-bad' : percent >= 75 ? ' tone-warn' : ''}`}><span style={{ width: `${percent}%` }}></span></span>}
    </span>;
    return <section className="section dash-card" style={{ '--cp-order': 4 }}>
      <div className="dash-card-head">
        <span className="dash-card-icon"><Layers size={16}/></span><h2>{tr("Busiest accounts")}</h2>
        <div className="segmented-control compact" role="tablist" aria-label={tr("Sort by")}>
          <button type="button" role="tab" aria-selected={topAccountsSort === 'cpu'} className={topAccountsSort === 'cpu' ? 'active' : ''} onClick={() => setTopAccountsSort('cpu')}>{tr("CPU")}</button>
          <button type="button" role="tab" aria-selected={topAccountsSort === 'memory'} className={topAccountsSort === 'memory' ? 'active' : ''} onClick={() => setTopAccountsSort('memory')}>{tr("RAM")}</button>
        </div>
      </div>
      {!limitsInfo.running && <p className="hint">{tr("The resource limits agent is not running, so these figures are not current.")}</p>}
      <div className="top-accounts">
        <div className="top-accounts-row is-head" aria-hidden="true">
          <span>{tr("Account")}</span><span>{tr("CPU")}</span><span>{tr("RAM")}</span><span>{tr("Processes")}</span><span>{tr("Stopped (24 h)")}</span>
        </div>
        {top.map(row => <button type="button" className="top-accounts-row" key={row.id} onClick={() => openUserFromDashboard(row.id)} title={tr("Open {0}", row.username)}>
          <span className="top-accounts-name"><strong>{row.username}</strong>{row.role === 'reseller' && <em>{tr("Reseller")}</em>}</span>
          {meter(tr("CPU"), formatCpuPercent(row.now.cpu_percent), share(row.now.cpu_percent, row.caps.cpu_percent), row.caps.cpu_percent ? `${row.caps.cpu_percent}%` : '')}
          {meter(tr("RAM"), formatMegabytes(row.now.memory_mb), share(row.now.memory_mb, row.caps.memory_mb), row.caps.memory_mb ? formatMegabytes(row.caps.memory_mb) : '')}
          <span className="top-accounts-procs" data-label={tr("Processes")}>{row.now.processes ?? 0}{row.caps.process_limit ? <small> / {row.caps.process_limit}</small> : null}</span>
          <span className={`top-accounts-oom${row.oom_kills_day ? ' tone-bad' : ''}`} data-label={tr("Stopped (24 h)")}>{row.oom_kills_day || 0}</span>
        </button>)}
      </div>
    </section>;
  }

  function renderDashboard() {
    const cpu = resourceUsage?.cpu || {};
    const memory = resourceUsage?.memory || {};
    const disk = resourceUsage?.disk || {};
    const network = resourceUsage?.network || {};
    const networkTotal = (Number(network.rx_per_sec) || 0) + (Number(network.tx_per_sec) || 0);
    const sum = dashSummary || {};
    const server = sum.server || {};
    const sites = sum.websites || { total: websites.length, active: websites.length, suspended: 0 };
    const ssl = sum.ssl || { total: websites.length, secured: websites.filter(site => site.ssl_enabled).length, unsecured: [], unsecured_count: 0 };
    const dbCount = sum.databases?.total ?? databases.length;
    const shortDate = value => value ? new Date(value).toLocaleString([], { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';

    // How each part stands, and how loudly: ok / warn / bad / neutral. On the
    // right of the page, as a list a glance can take in.
    const status = [
      { key: 'ssl', icon: Lock, label: tr("SSL"), value: `${ssl.secured}/${ssl.total}`,
        detail: !ssl.total ? tr("No websites yet") : ssl.unsecured_count ? tr("{0} without SSL", ssl.unsecured_count) : tr("All secured"),
        tone: !ssl.total ? 'neutral' : ssl.unsecured_count ? 'warn' : 'ok' },
    ];
    if (isAdmin) {
      const services = sum.services;
      status.unshift({ key: 'services', icon: Server, label: tr("Services"),
        value: services ? `${services.running}/${services.total}` : '—',
        detail: services?.stopped?.length ? tr("Stopped: {0}", services.stopped.join(', ')) : services ? tr("All running") : tr("Checking…"),
        tone: !services ? 'neutral' : services.stopped?.length ? 'bad' : 'ok' });
      const fw = sum.firewall?.enabled;
      status.push({ key: 'firewall', icon: BrickWall, label: tr("Firewall"),
        value: fw === true ? tr("On") : fw === false ? tr("Off") : '—', detail: tr("iptables"),
        tone: fw === true ? 'ok' : fw === false ? 'bad' : 'neutral' });
      const engine = sum.waf?.engine;
      status.push({ key: 'waf', icon: ShieldAlert, label: tr("WAF"),
        value: engine === 'on' ? tr("On") : engine === 'off' ? tr("Off") : '—', detail: tr("ModSecurity engine"),
        tone: engine === 'on' ? 'ok' : engine === 'off' ? 'warn' : 'neutral' });
      const mw = sum.malware;
      const last = mw?.last_scan;
      status.push({ key: 'malware', icon: Bug, label: tr("Malware scanner"),
        value: !mw ? '—' : !mw.installed ? tr("Off") : last?.infected ? tr("{0} threat(s)", last.infected) : last ? tr("Clean") : tr("No scan yet"),
        detail: last ? shortDate(last.finished_at) : mw && !mw.installed ? tr("Not installed") : tr("Last scan"),
        tone: !mw ? 'neutral' : !mw.installed ? 'warn' : last?.infected ? 'bad' : last ? 'ok' : 'neutral' });
      const backups = sum.backups;
      status.push({ key: 'backups', icon: Archive, label: tr("Backups"),
        value: backups ? shortDate(backups.last_run_at) : '—',
        detail: !backups ? tr("Checking…") : !backups.schedules ? tr("No schedule") : backups.failed ? tr("Last run failed") : tr("{0} schedule(s)", backups.schedules),
        tone: !backups ? 'neutral' : !backups.schedules ? 'warn' : backups.failed ? 'bad' : 'ok' });
    } else {
      const wafOn = websites.filter(site => site.waf_enabled).length;
      status.push({ key: 'waf', icon: ShieldAlert, label: tr("WAF"), value: `${wafOn}/${websites.length}`,
        detail: !websites.length ? tr("No websites yet") : wafOn === websites.length ? tr("On for every website") : tr("{0} website(s) without WAF", websites.length - wafOn),
        tone: !websites.length ? 'neutral' : wafOn === websites.length ? 'ok' : 'warn' });
      status.push({ key: 'security', icon: LockKeyhole, label: tr("Account security"),
        value: currentUser?.totp_enabled ? tr("On") : tr("Off"), detail: tr("Two-factor sign-in"),
        tone: currentUser?.totp_enabled ? 'ok' : 'warn' });
    }

    // Things that want a look, most urgent first.
    const attention = [];
    if (isAdmin && sum.services?.stopped?.length) attention.push({ tone: 'bad', text: tr("Stopped: {0}", sum.services.stopped.join(', ')), action: tr("Open"), target: 'services' });
    if (isAdmin && sum.firewall?.enabled === false) attention.push({ tone: 'bad', text: tr("The firewall is off."), action: tr("Open"), target: 'firewall' });
    if (isAdmin && sum.malware?.last_scan?.infected) attention.push({ tone: 'bad', text: tr("The last malware scan found {0} threat(s).", sum.malware.last_scan.infected), action: tr("Open"), target: 'malware' });
    if (isAdmin && sum.backups?.failed) attention.push({ tone: 'bad', text: tr("{0} scheduled backup(s) failed on their last run.", sum.backups.failed), action: tr("Open"), target: 'backups' });
    if (ssl.unsecured_count) attention.push({ tone: 'warn', text: tr("{0} website(s) without SSL: {1}", ssl.unsecured_count, ssl.unsecured.join(', ') + (ssl.unsecured_count > ssl.unsecured.length ? '…' : '')), action: tr("Set up SSL"), target: 'ssl' });
    if (sites.suspended) attention.push({ tone: 'warn', text: tr("{0} website(s) suspended.", sites.suspended), action: tr("Open"), target: 'websites' });
    if (isAdmin && sum.backups && !sum.backups.schedules) attention.push({ tone: 'warn', text: tr("No scheduled backup is set up."), action: tr("Set up"), target: 'backups' });
    if (isAdmin && sum.waf?.engine === 'off') attention.push({ tone: 'warn', text: tr("The WAF engine is not installed."), action: tr("Open"), target: 'waf' });
    if (isAdmin && sum.malware && !sum.malware.installed) attention.push({ tone: 'info', text: tr("The Malware Scanner addon is not installed."), action: tr("Install"), target: 'addons' });
    if (!isAdmin && !currentUser?.totp_enabled) attention.push({ tone: 'info', text: tr("Two-factor sign-in is off for your account."), action: tr("Turn on"), target: 'security' });
    if (isAdmin && sum.updates?.update_available) attention.push({ tone: 'info', text: tr("Panel update {0} is available.", sum.updates.latest_version), action: tr("Open"), target: 'updates' });

    // Most urgent first; when nothing needs a look the administrator gets one
    // quiet line, and a customer nothing at all.
    const attentionCard = (attention.length > 0 || isAdmin) && <section className={`section dash-card cp-attention${attention.length ? '' : ' is-clear'}`} style={{ '--cp-order': 1 }}>
      {attention.length === 0
        ? <div className="attention-ok"><CheckCircle size={16}/> {dashSummary ? tr("Everything looks fine.") : tr("Checking…")}</div>
        : <>
            <div className="dash-card-head"><span className="dash-card-icon"><AlertCircle size={16}/></span><h2>{tr("Needs attention")}</h2><span className="badge warn">{attention.length}</span></div>
            <div className="attention-list">
              {attention.map((item, index) => <div className={`attention-item tone-${item.tone}`} key={index}>
                {item.tone === 'info' ? <RefreshCw size={15}/> : <AlertCircle size={15}/>}
                <span>{item.text}</span>
                <button type="button" className="mini secondary" onClick={() => navigateToPage(item.target)}>{item.action}</button>
              </div>)}
            </div>
          </>}
    </section>;

    const statusCard = <section className="section side-card" style={{ '--cp-order': isAdmin ? 3 : 5 }}>
      <h3 className="side-card-title">{tr("Status")}</h3>
      <div className="status-list">
        {status.map(card => <button type="button" key={card.key} className={`status-row tone-${card.tone}`} onClick={() => navigateToPage(card.key)}>
          <card.icon size={15}/><span>{card.label}</span><strong>{card.value}</strong><small>{card.detail}</small>
        </button>)}
      </div>
    </section>;

    if (isAdmin) {
      const accounts = sum.accounts || {};
      return <div className="dashboard cp-layout">
        <div className="cp-main">
          {attentionCard}
          <section className="section dash-card dash-resources" style={{ '--cp-order': 2 }}>
            <div className="dash-card-head"><span className="dash-card-icon"><Activity size={16}/></span><h2>{tr("Server resources")}</h2></div>
            <div className="resource-grid">
              <ResourceCard icon={Cpu} label={tr("CPU")} value={formatPercent(cpu.percent)} percent={cpu.percent} detail={cpu.load?.length ? tr("Load {0}", cpu.load.join(' / ')) : tr("{0} cores", cpu.cores || '--')} />
              <ResourceCard icon={MemoryStick} label={tr("RAM")} value={formatPercent(memory.percent)} percent={memory.percent} detail={`${formatBytes(memory.used)} / ${formatBytes(memory.total)}`} />
              <ResourceCard icon={HardDrive} label={tr("Disk")} value={formatPercent(disk.percent)} percent={disk.percent} detail={`${formatBytes(disk.used)} / ${formatBytes(disk.total)}`} />
              <ResourceCard icon={Network} label={tr("Network")} value={`${formatBytes(networkTotal)}/s`} detail={tr("Down {0}/s / Up {1}/s", formatBytes(network.rx_per_sec), formatBytes(network.tx_per_sec))} />
            </div>
            <RamBreakdown memory={memory} />
          </section>
          {limitsOn && renderTopAccounts()}
          {renderToolPanel(5)}
        </div>
        <aside className="cp-side">
          {statusCard}
          <section className="section side-card" style={{ '--cp-order': 6 }}>
            <h3 className="side-card-title">{tr("Server information")}</h3>
            <InfoRow label={tr("Hostname")} value={server.hostname} />
            <InfoRow label={tr("IP address")} value={server.ipv4} />
            <InfoRow label={tr("Operating system")} value={server.os} />
            <InfoRow label={tr("Kernel")} value={server.kernel} />
            <InfoRow label={tr("Uptime")} value={server.uptime_seconds ? formatUptime(server.uptime_seconds) : ''} />
            <InfoRow label={tr("CPU")} value={cpu.cores ? tr("{0} cores", cpu.cores) : ''} />
            <InfoRow label={tr("RAM")} value={memory.total ? formatBytes(memory.total) : ''} />
            <InfoRow label={tr("Panel version")} value={server.panel_version} />
          </section>
          <section className="section side-card" style={{ '--cp-order': 7 }}>
            <h3 className="side-card-title">{tr("Statistics")}</h3>
            <StatRow label={tr("Customers")} value={String(accounts.end_users ?? '—')} />
            <StatRow label={tr("Resellers")} value={String(accounts.resellers ?? '—')} />
            <StatRow label={tr("Websites")} value={sites.suspended ? tr("{0} ({1} suspended)", sites.total, sites.suspended) : String(sites.total)} />
            <StatRow label={tr("Databases")} value={String(dbCount)} />
            {sum.mail && <StatRow label={tr("Mail domains")} value={String(sum.mail.domains)} />}
            {sum.mail && <StatRow label={tr("Mailboxes")} value={String(sum.mail.mailboxes)} />}
            {sum.dns && <StatRow label={tr("DNS zones")} value={String(sum.dns.zones)} />}
          </section>
        </aside>
      </div>;
    }

    if (!currentUser) return null;

    // A customer or reseller: cPanel's General Information and Statistics.
    const limitEntry = limitsOn ? limitsInfo?.accounts?.[currentUser.id] : null;
    const groupScope = isReseller && dashScope === 'group' && !!limitEntry?.group_limits;
    const now = (groupScope ? limitEntry?.group_usage : limitEntry?.usage) || {};
    const caps = (groupScope ? limitEntry?.group_limits : limitEntry?.limits) || {};
    const pctOf = (used, limit) => limit > 0 ? ((Number(used) || 0) / limit) * 100 : null;
    const usedOf = (used, limit, format = value => value) => limit ? `${format(used)} / ${format(limit)}` : `${format(used)} / ∞`;
    const storageLimit = storageLimitBytes(currentUser);
    const usedBytes = Number(currentUser.storage_used_bytes) || 0;
    const siteLimit = Number(currentUser.website_limit) || 0;
    const dbLimit = Number(currentUser.database_limit) || 0;
    const mailboxLimit = Number(mailInfo?.mailbox_limit) || 0;
    const primarySite = websites[0];
    // /home/<user>/<domain> -> /home/<user>
    const homeDir = (primarySite?.root_path || '').match(/^\/[^/]+\/[^/]+/)?.[0] || '';
    const pool = resellerPool || {};
    const shareDisk = pool[pool.pool_oversell ? 'used_storage_limit_mb' : 'allocated_storage_limit_mb'];
    const stopped = (groupScope ? limitEntry?.group_oom_kills_day : limitEntry?.oom_kills_day) || 0;
    // Disk speed has two limits; the meter follows whichever is closer.
    const ioPercents = [pctOf(now.io_read_mbps, caps.io_read_mbps), pctOf(now.io_write_mbps, caps.io_write_mbps)].filter(value => value != null);

    return <div className="dashboard cp-layout">
      <div className="cp-main">
        {attentionCard}
        {renderToolPanel(3)}
      </div>
      <aside className="cp-side">
        <section className="section side-card" style={{ '--cp-order': 2 }}>
          <div className="side-card-head">
            <h3 className="side-card-title">{tr("Statistics")}</h3>
            {isReseller && limitEntry?.group_limits && <div className="segmented-control compact" role="tablist" aria-label={tr("Show")}>
              <button type="button" role="tab" aria-selected={!groupScope} className={!groupScope ? 'active' : ''} onClick={() => setDashScope('account')}>{tr("Me")}</button>
              <button type="button" role="tab" aria-selected={groupScope} className={groupScope ? 'active' : ''} onClick={() => setDashScope('group')}>{tr("Group")}</button>
            </div>}
          </div>
          {groupScope && <p className="hint">{tr("You and all your customers together.")}</p>}
          {groupScope ? <>
            <StatRow label={tr("Customers")} value={usedOf(pool.customers ?? 0, pool.pool_user_limit)} percent={pctOf(pool.customers, pool.pool_user_limit)} />
            <StatRow label={pool.pool_oversell ? tr("Disk in use") : tr("Disk handed out")} value={usedOf(shareDisk ?? 0, pool.pool_storage_limit_mb, formatMegabytes)} percent={pctOf(shareDisk, pool.pool_storage_limit_mb)} />
          </> : <>
            <StatRow label={tr("Disk usage")} value={currentUser.storage_used_bytes == null ? tr("Measuring…") : (storageLimit ? `${formatBytes(usedBytes)} / ${formatBytes(storageLimit)}` : `${formatBytes(usedBytes)} / ∞`)} percent={storageLimit ? pctOf(usedBytes, storageLimit) : null} />
            <StatRow label={tr("Websites")} value={usedOf(websites.length, siteLimit)} percent={pctOf(websites.length, siteLimit)} />
            <StatRow label={tr("Databases")} value={usedOf(databases.length, dbLimit)} percent={pctOf(databases.length, dbLimit)} />
            {mailInfo?.installed && <StatRow label={tr("Mailboxes")} value={usedOf(mailInfo.mailbox_count ?? 0, mailboxLimit)} percent={pctOf(mailInfo.mailbox_count, mailboxLimit)} />}
          </>}
          {limitEntry && <>
            <StatRow label={tr("CPU usage")} value={caps.cpu_percent ? `${formatCpuPercent(now.cpu_percent)} / ${caps.cpu_percent}%` : `${formatCpuPercent(now.cpu_percent)} / ∞`} percent={pctOf(now.cpu_percent, caps.cpu_percent)} />
            <StatRow label={tr("Physical memory")} value={usedOf(now.memory_mb ?? 0, caps.memory_mb, formatMegabytes)} percent={pctOf(now.memory_mb, caps.memory_mb)} />
            <StatRow label={tr("Processes")} value={usedOf(now.processes ?? 0, caps.process_limit)} percent={pctOf(now.processes, caps.process_limit)} />
            <StatRow label={tr("Disk read / write")} value={`${formatMbps(now.io_read_mbps)} / ${formatMbps(now.io_write_mbps)}`}
              percent={ioPercents.length ? Math.max(...ioPercents) : null} />
            <StatRow label={tr("Stopped at the RAM limit (24 h)")} value={String(stopped)} tone={stopped ? 'bad' : ''} />
            <button type="button" className="side-card-link" onClick={() => navigateToPage('usage')}><Activity size={14}/> {tr("Resource usage over time")}</button>
          </>}
        </section>
        <section className="section side-card" style={{ '--cp-order': 4 }}>
          <h3 className="side-card-title">{tr("General information")}</h3>
          <InfoRow label={tr("Account")} value={isReseller ? <>{currentUser.username} <span className="badge role-reseller">{tr("Reseller")}</span></> : currentUser.username} title={currentUser.username} />
          <InfoRow label={tr("Email")} value={currentUser.email} />
          <InfoRow label={tr("Primary domain")} value={primarySite?.domain} />
          <InfoRow label={tr("Home directory")} value={homeDir} />
          <InfoRow label={tr("Server IP")} value={server.ipv4} />
        </section>
        {statusCard}
      </aside>
    </div>;
  }

  // cPanel's "Resource Usage": the addon's history for this account (and a
  // reseller's group), a day of 5-minute points or a week of hours.
  function renderResourceUsage() {
    if (isAdmin || !limitsOn || !currentUser) return renderDashboard();
    const limitEntry = limitsInfo?.accounts?.[currentUser.id];
    const groupScope = isReseller && dashScope === 'group' && !!limitEntry?.group_limits;
    const caps = (groupScope ? limitEntry?.group_limits : limitEntry?.limits) || {};
    const reading = groupScope ? limitEntry?.group_usage : limitEntry?.usage;
    const now = reading || {};
    // No reading at all (an account the agent has not picked up yet) is "—",
    // not 0: a 0 means it was measured and nothing was used.
    const measured = !!reading;
    const stopped = (groupScope ? limitEntry?.group_oom_kills_day : limitEntry?.oom_kills_day) || 0;
    const history = limitsHistory?.[groupScope ? 'group' : 'account'] || {};
    const points = (dashRange === 'week' ? history.week : history.day) || [];
    const formatWhen = (t, full) => {
      const date = new Date(t * 1000);
      if (full) return date.toLocaleString([], { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
      return dashRange === 'week' ? date.toLocaleDateString([], { day: '2-digit', month: '2-digit' }) : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    };
    const formatCount = value => String(Math.round(Number(value) || 0));
    const common = { points, when: formatWhen, emptyText: tr("Not enough history yet: a point is added every 5 minutes."),
      limitText: tr("Limit"), unlimitedText: tr("Unlimited"), peakText: tr("Peak"), averageText: tr("Average") };
    // The limits themselves, as cPanel's Resource Usage lists them: what is
    // used now against what is allowed, a reseller's group's when it looks at
    // its group. 0 is unlimited.
    const pctOf = (used, limit) => measured && limit > 0 ? ((Number(used) || 0) / limit) * 100 : null;
    const usedOf = (used, limit, format) => `${measured ? format(used ?? 0) : '—'} / ${limit ? format(limit) : '∞'}`;
    const limitRows = [
      [tr("CPU"), usedOf(now.cpu_percent, caps.cpu_percent, formatCpuPercent), pctOf(now.cpu_percent, caps.cpu_percent)],
      [tr("RAM"), usedOf(now.memory_mb, caps.memory_mb, formatMegabytes), pctOf(now.memory_mb, caps.memory_mb)],
      [tr("Processes"), usedOf(now.processes, caps.process_limit, formatCount), pctOf(now.processes, caps.process_limit)],
      [tr("Disk read"), usedOf(now.io_read_mbps, caps.io_read_mbps, formatMbps), pctOf(now.io_read_mbps, caps.io_read_mbps)],
      [tr("Disk write"), usedOf(now.io_write_mbps, caps.io_write_mbps, formatMbps), pctOf(now.io_write_mbps, caps.io_write_mbps)],
    ];
    return <section className="section">
      <div className="section-title">
        <div><h2>{groupScope ? tr("Your group's resource usage") : tr("Resource usage")}</h2>
          <p className="hint">{tr("CPU, memory, processes and disk speed over the last day or week. The dashed line is the limit.")}</p></div>
        <div className="actions">
          {isReseller && limitEntry?.group_limits && <div className="segmented-control compact" role="tablist" aria-label={tr("Show")}>
            <button type="button" role="tab" aria-selected={!groupScope} className={!groupScope ? 'active' : ''} onClick={() => setDashScope('account')}>{tr("Me")}</button>
            <button type="button" role="tab" aria-selected={groupScope} className={groupScope ? 'active' : ''} onClick={() => setDashScope('group')}>{tr("Group")}</button>
          </div>}
          <div className="segmented-control compact" role="tablist" aria-label={tr("Period")}>
            <button type="button" role="tab" aria-selected={dashRange === 'day'} className={dashRange === 'day' ? 'active' : ''} onClick={() => setDashRange('day')}>{tr("24 hours")}</button>
            <button type="button" role="tab" aria-selected={dashRange === 'week'} className={dashRange === 'week' ? 'active' : ''} onClick={() => setDashRange('week')}>{tr("7 days")}</button>
          </div>
        </div>
      </div>
      <h3 className="usage-subtitle">{groupScope ? tr("Your group's limits and use now") : tr("Limits and use now")}
        {groupScope && <small>{tr("You and all your customers together.")}</small>}</h3>
      {!limitsInfo?.running
        ? <p className="usage-stale">{tr("The resource limits agent is not running, so these figures are not current.")}</p>
        : !measured && <p className="usage-stale">{tr("No reading for this account yet: the first one comes within a minute.")}</p>}
      <div className="usage-summary">
        {limitRows.map(([label, value, percent]) => <div className="usage-summary-item" key={label}>
          <StatRow label={label} value={value} percent={percent} />
        </div>)}
        <div className="usage-summary-item">
          <StatRow label={tr("Stopped at the RAM limit (24 h)")} value={String(stopped)} tone={stopped ? 'bad' : ''} />
        </div>
      </div>
      <p className="hint usage-note">{tr("Disk read and write count only what actually reaches the disk. Files the server already holds in memory are read without touching it, so 0 is normal for a quiet site.")}</p>
      <p className="hint usage-note">
        {measured && now.memory_anon_mb != null && <>{tr("RAM now: processes {0}, file cache and OPcache {1}.", formatMegabytes(now.memory_anon_mb), formatMegabytes(now.memory_cache_mb))}{' '}</>}
        {tr("The cache counts towards the limit but is given back when memory runs short. MariaDB serves every site and is not counted here. Adding up processes in htop or ps gives more, because OPcache's shared memory is counted again in every process.")}
      </p>
      <h3 className="usage-subtitle">{tr("Over time")}</h3>
      <div className="usage-charts">
        <UsageChart {...common} title={tr("CPU")} pick={point => point.cpu} limit={caps.cpu_percent} format={formatCpuPercent} floor={10} />
        <UsageChart {...common} title={tr("RAM")} pick={point => point.mem} limit={caps.memory_mb} format={formatMegabytes} floor={128} />
        <UsageChart {...common} title={tr("Processes")} pick={point => point.procs} limit={caps.process_limit} format={formatCount} floor={10} />
        <UsageChart {...common} title={tr("Disk read")} pick={point => point.io_r} limit={caps.io_read_mbps} format={formatMbps} floor={0.1} />
        <UsageChart {...common} title={tr("Disk write")} pick={point => point.io_w} limit={caps.io_write_mbps} format={formatMbps} floor={0.1} />
      </div>
    </section>;
  }

  function renderNginxEditor() {
    if (!nginxCustomEditing) return null;
    const fullConfig = nginxCustomEditing.mode === 'full';
    const selectedAppType = websiteSettingsForm.app_type || nginxCustomEditing.site?.app_type || 'wordpress';
    const rewriteDisabled = selectedAppType !== 'php';
    const settingsSite = nginxCustomEditing.site || {};
    const siteDomains = settingsSite.aliases || [];
    const aliasMode = aliasModes[nginxCustomEditing.id] || 'alias';
    return <section className="section nginx-modal inline-nginx-editor">
      <div className="section-title">
        <div className="nginx-config-title">
          <h2>{fullConfig ? tr("VHost Config") : tr("Website settings")} - {nginxCustomEditing.domain}</h2>
          <p className="hint">{fullConfig
            ? tr("This is read-only. opanel manages the main vhost template.")
            : tr("Managed settings rewrite the main vhost safely.")}</p>
        </div>
        <div className="actions">
          {!fullConfig && isAdmin && <button className="secondary-light" disabled={!!loading} onClick={viewFullNginxConfig}><FileText size={14}/> {tr("View all")}</button>}
          {fullConfig && <button className="secondary-light" disabled={!!loading} onClick={() => setNginxCustomEditing(prev => ({ ...prev, mode: 'settings', content: '' }))}><SettingsIcon size={14}/> {tr("Settings")}</button>}
          <button className="secondary-light" onClick={() => setNginxCustomEditing(null)}><X size={14}/> {tr("Close")}</button>
        </div>
      </div>
      {!fullConfig && <div className="website-settings-grid">
        <label><span>{tr("Website mode")}</span><select
          value={websiteSettingsForm.app_type}
          onChange={e => setWebsiteSettingsForm(prev => ({
            ...prev,
            app_type: e.target.value,
            nginx_rewrite_mode: e.target.value === 'php' ? prev.nginx_rewrite_mode || 'none' : e.target.value === 'wordpress' ? 'front_controller' : 'none',
          }))}
          disabled={!!loading}
        >
          <option value="wordpress">{tr("WordPress")}</option>
          <option value="php">{tr("PHP")}</option>
          <option value="static">{tr("Static")}</option>
        </select></label>
        {selectedAppType !== 'static' && <label><span>{tr("PHP version")}</span><select
          value={websiteSettingsForm.php_version}
          onChange={e => setWebsiteSettingsForm(prev => ({ ...prev, php_version: e.target.value }))}
          disabled={!!loading}
        >
          {phpVersionOptions(phpVersions.installed, websiteSettingsForm.php_version).map(v => <option key={v} value={v}>{tr("PHP")} {v}</option>)}
        </select></label>}
        <label><span>{tr("Webserver rewrite")}</span><select
          value={rewriteDisabled ? (selectedAppType === 'wordpress' ? 'front_controller' : 'none') : websiteSettingsForm.nginx_rewrite_mode}
          onChange={e => setWebsiteSettingsForm(prev => ({ ...prev, nginx_rewrite_mode: e.target.value }))}
          disabled={!!loading || rewriteDisabled}
        >
          {NGINX_REWRITE_MODES.map(mode => <option key={mode.value} value={mode.value}>{mode.label}</option>)}
        </select></label>
        <div className="website-settings-actions">
          <button disabled={!!loading} onClick={saveWebsiteSettings}><Save size={14}/> {tr("Save settings")}</button>
        </div>
      </div>}
      {!fullConfig && <div className="site-aliases settings-domain-manager">
        <div className="domain-manager-head">
          <h3>{tr("Domains")}</h3>
          <p className="hint">{tr("Alias serves the same app. Redirect sends visitors to")} {nginxCustomEditing.domain}.</p>
        </div>
        <div className="alias-list">
          <span className="alias-chip primary-domain"><Globe size={12}/>{nginxCustomEditing.domain}<span>{tr("Main")}</span></span>
          {siteDomains.length === 0
            ? <span className="alias-empty">{tr("No extra domains")}</span>
            : siteDomains.map(alias => <span className="alias-chip" key={alias.id}>
              <Globe size={12}/>{alias.domain}<span>{alias.mode === 'redirect' ? tr("Redirect") : tr("Alias")}</span>
              <button type="button" disabled={!!loading} title={tr("Remove {0}", alias.domain)} aria-label={tr("Remove {0}", alias.domain)} onClick={() => deleteWebsiteAlias(settingsSite, alias)}><X size={12}/></button>
            </span>)}
        </div>
        <div className="alias-form settings-domain-form">
          <input
            value={aliasDrafts[nginxCustomEditing.id] || ''}
            onChange={e => setAliasDrafts(prev => ({ ...prev, [nginxCustomEditing.id]: e.target.value }))}
            onKeyDown={e => { if (e.key === 'Enter') addWebsiteAlias(settingsSite); }}
            placeholder="domain-alias.com"
            disabled={!!loading}
          />
          <select
            value={aliasMode}
            onChange={e => setAliasModes(prev => ({ ...prev, [nginxCustomEditing.id]: e.target.value }))}
            disabled={!!loading}
          >
            <option value="alias">{tr("Alias")}</option>
            <option value="redirect">{tr("Redirect")}</option>
          </select>
          <button className="secondary-light" disabled={!!loading || !(aliasDrafts[nginxCustomEditing.id] || '').trim()} onClick={() => addWebsiteAlias(settingsSite)}><Plus size={14}/> {tr("Add domain")}</button>
        </div>
      </div>}
      {fullConfig && <div className="custom-nginx-block">
        <textarea
          className="code-editor"
          value={nginxCustomEditing.content}
          placeholder={tr("docRoot                   /home/site/{0}/public_html", nginxCustomEditing.domain)}
          spellCheck={false}
          rows={18}
          readOnly
        />
      </div>}
      <div className="actions">
        <button className="secondary-light" disabled={!!loading} onClick={() => setNginxCustomEditing(null)}>{fullConfig ? tr("Close") : tr("Cancel")}</button>
      </div>
    </section>;
  }


  function renderWebsiteTerminal() {
    if (!terminalViewer) return null;
    return <section className="section nginx-modal terminal-modal">
      <div className="section-title">
        <h2>{tr("Terminal -")} {terminalViewer.domain}</h2>
        <button className="secondary-light" onClick={() => setTerminalViewer(null)}><X size={14}/> {tr("Close")}</button>
      </div>
      <div style={{ height: '500px', marginTop: '8px' }}>
        <Terminal websiteId={terminalViewer.id} apiBase={API} />
      </div>
    </section>;
  }

  function renderWebsiteLogViewer() {
    if (!logViewer) return null;
    return <section className="section nginx-modal log-viewer">
      <div className="section-title">
        <div className="nginx-config-title">
          <h2>{tr("Webserver logs -")} {logViewer.domain}</h2>
          <p className="hint">{logViewer.path || `/var/log/nginx/${logViewer.domain}.${logViewer.kind}.log`}</p>
        </div>
        <button className="secondary-light" onClick={() => setLogViewer(null)}><X size={14}/> {tr("Close")}</button>
      </div>
      <div className="log-toolbar">
        <div className="segmented-control">
          <button className={logViewer.kind === 'access' ? 'active' : ''} disabled={!!loading} onClick={() => loadWebsiteLog(logViewer.id, 'access', logViewer.lines, logViewer.domain)}>{tr("Access")}</button>
          <button className={logViewer.kind === 'error' ? 'active' : ''} disabled={!!loading} onClick={() => loadWebsiteLog(logViewer.id, 'error', logViewer.lines, logViewer.domain)}>{tr("Errors (PHP + server)")}</button>
        </div>
        <select value={logViewer.lines} onChange={e => loadWebsiteLog(logViewer.id, logViewer.kind, Number(e.target.value), logViewer.domain)} disabled={!!loading}>
          <option value={100}>{tr("100 lines")}</option>
          <option value={200}>{tr("200 lines")}</option>
          <option value={500}>{tr("500 lines")}</option>
          <option value={1000}>{tr("1000 lines")}</option>
          <option value={2000}>{tr("2000 lines")}</option>
        </select>
        <button className="secondary" disabled={!!loading} onClick={() => loadWebsiteLog(logViewer.id, logViewer.kind, logViewer.lines, logViewer.domain)}><RefreshCw size={14}/> {tr("Refresh")}</button>
      </div>
      <pre className="log-output">{logViewer.exists
        ? (logViewer.content || tr("Log is empty."))
        : (logViewer.kind === 'error' ? tr("No errors logged yet.") : tr("Log file has not been created yet."))}</pre>
    </section>;
  }

  function renderWebsites() {
    const wpFieldsEnabled = siteType === 'wordpress' && installWordPress;
    const wsQuery = websiteSearch.trim().toLowerCase();
    const filteredWebsites = wsQuery
      ? websites.filter(s => (s.domain || '').toLowerCase().includes(wsQuery)
          || (s.aliases || []).some(a => (a.domain || '').toLowerCase().includes(wsQuery)))
      : websites;
    const createOpen = showCreateSite || websites.length === 0;
    const openCreate = () => {
      setShowCreateSite(true);
      setTimeout(() => { const el = document.getElementById('create-website-domain'); el?.scrollIntoView({ behavior: 'smooth', block: 'center' }); el?.focus(); }, 0);
    };
    return <>
      {createOpen && <section className="section create-panel">
        <div className="section-title">
          <h2>{tr("Create website")}</h2>
          {websites.length > 0 && <button type="button" className="secondary icon-only mini" onClick={() => setShowCreateSite(false)} aria-label={tr("Close")} title={tr("Close")}><X size={15}/></button>}
        </div>
        <div className="form-row create-site-row">
          <input id="create-website-domain" value={domain} onChange={e => setDomain(e.target.value)} placeholder="domain.com" />
          {canManageUsers && users.length > 0 && <select value={siteOwnerId} onChange={e => setSiteOwnerId(e.target.value)} aria-label={tr("Owner")} title={tr("Owner")}>
            <option value="">{tr("For yourself")}</option>
            {users.filter(u => u.id !== currentUser?.id && u.role !== 'admin').map(u => <option key={u.id} value={u.id}>{tr("For {0}", u.username)}</option>)}
          </select>}
          <select value={siteType} onChange={e => setSiteType(e.target.value)}>
            <option value="wordpress">{tr("WordPress")}</option>
            <option value="php">{tr("PHP")}</option>
          </select>
          <select value={phpVersion} onChange={e => setPhpVersion(e.target.value)}>
            {phpVersionOptions(phpVersions.installed, phpVersion).map(v => <option key={v} value={v}>{tr("PHP")} {v}</option>)}
          </select>
          {wpFieldsEnabled && <input value={adminEmail} onChange={e => setAdminEmail(e.target.value)} placeholder="admin@domain.com" />}
          {wpFieldsEnabled && <input value={wpAdminUser} onChange={e => setWpAdminUser(e.target.value)} placeholder={tr("WP admin user")} />}
          {wpFieldsEnabled && <input value={wpAdminPassword} onChange={e => setWpAdminPassword(e.target.value)} placeholder={tr("WP admin password")} type="password" />}
          <button disabled={!!loading || !domain} onClick={createWordPress}><Plus size={15}/> {tr("Create")}</button>
        </div>
        {siteType === 'wordpress' && <label className="check-line">
          <input type="checkbox" checked={installWordPress} onChange={e => setInstallWordPress(e.target.checked)} />
          {tr("Install WordPress (creates database, downloads WP, configures vhost)")}
        </label>}
        <label className="check-line">
          <input type="checkbox" checked={installSslAfterCreate} onChange={e => setInstallSslAfterCreate(e.target.checked)} />
          {tr("Set up SSL after creating")}
        </label>
        {installSslAfterCreate && <div className="create-ssl-box">
          <div className="segmented ssl-mode-tabs">
            <button className={createSslMode === 'letsencrypt' ? 'active' : ''} onClick={() => setCreateSslMode('letsencrypt')}><Lock size={13}/> {tr("Let's Encrypt")}</button>
            <button className={createSslMode === 'wildcard' ? 'active' : ''} onClick={() => setCreateSslMode('wildcard')}><Globe size={13}/> {dnsInfo?.installed ? tr("Wildcard") : tr("Wildcard (Cloudflare)")}</button>
            <button className={createSslMode === 'existing' ? 'active' : ''} onClick={() => { setCreateSslMode('existing'); loadCreateSslCerts(); }}><RefreshCw size={13}/> {tr("Use existing")}</button>
            <button className={createSslMode === 'manual' ? 'active' : ''} onClick={() => setCreateSslMode('manual')}><KeyRound size={13}/> {tr("Manual")}</button>
          </div>
          {createSslMode === 'letsencrypt' && <p className="hint">{tr("certbot HTTP-01 — the domain must point to this server's IP first.")}</p>}
          {createSslMode === 'wildcard' && wildcardProvider(createSslForm.provider) === 'opanel' && <div className="create-ssl-fields">
            {renderWildcardProvider('opanel', provider => setCreateSslForm(p => ({ ...p, provider })))}
            <input type="email" autoComplete="off" placeholder={tr("Contact email (optional)")} value={createSslForm.email} onChange={e => setCreateSslForm(p => ({ ...p, email: e.target.value }))} />
            <p className="hint">{tr("The challenge record goes into this server's zone for the domain, created with the website. The domain's nameservers must point at this server. One cert for the domain and *.domain; renewals need nothing.")}</p>
          </div>}
          {createSslMode === 'wildcard' && wildcardProvider(createSslForm.provider) !== 'opanel' && <div className="create-ssl-fields">
            {renderWildcardProvider('cloudflare', provider => setCreateSslForm(p => ({ ...p, provider })))}
            <input type="password" autoComplete="off" placeholder={tr("Cloudflare API Token")} value={createSslForm.api_token} onChange={e => setCreateSslForm(p => ({ ...p, api_token: e.target.value }))} />
            <input type="email" autoComplete="off" placeholder={tr("Contact email (optional)")} value={createSslForm.email} onChange={e => setCreateSslForm(p => ({ ...p, email: e.target.value }))} />
            <p className="hint">{tr("A scoped")} <strong>{tr("API Token")}</strong> {tr("(My Profile → API Tokens → Create Token),")} <strong>{tr("not")}</strong> {tr("the Global API Key. Permission")} <code>Zone → DNS → Edit</code> {tr("for the zone(s) you issue certs for. Stored encrypted for renewal. Issues one cert for the domain and *.domain — no DNS record or port 80 needed.")}</p>
          </div>}
          {createSslMode === 'existing' && <div className="create-ssl-fields">
            <div className="form-row">
              <select value={createSslForm.reuse_name} onChange={e => setCreateSslForm(p => ({ ...p, reuse_name: e.target.value }))}>
                <option value="">{tr("— pick a certificate on this server —")}</option>
                {createSslCerts.map(c => <option key={c.name} value={c.name}>{c.domains.join(', ')} ({c.source})</option>)}
              </select>
              <button className="secondary-light" type="button" onClick={loadCreateSslCerts}><RefreshCw size={13}/> {tr("Refresh")}</button>
            </div>
            <p className="hint">{!domain.trim()
              ? tr("Type the domain above first — this list shows only certificates that cover it.")
              : createSslCerts.length === 0
                ? tr("No certificate on this server covers {0}. Note a *.example.com wildcard covers x.example.com but not example.com itself. Issue a Let's Encrypt or wildcard cert first.", domain.trim().toLowerCase())
                : tr("A *.example.com wildcard issued earlier covers every x.example.com.")}</p>
          </div>}
          {createSslMode === 'manual' && <div className="create-ssl-fields">
            <textarea rows={4} placeholder={tr("-----BEGIN CERTIFICATE-----")} value={createSslForm.certificate} onChange={e => setCreateSslForm(p => ({ ...p, certificate: e.target.value }))} />
            <textarea rows={4} placeholder={tr("-----BEGIN PRIVATE KEY-----")} value={createSslForm.private_key} onChange={e => setCreateSslForm(p => ({ ...p, private_key: e.target.value }))} />
            <textarea rows={3} placeholder={tr("CA bundle (optional)")} value={createSslForm.ca_bundle} onChange={e => setCreateSslForm(p => ({ ...p, ca_bundle: e.target.value }))} />
          </div>}
        </div>}
        <p className="hint">{wpFieldsEnabled
          ? tr("WordPress will be installed and the panel will show the URL, admin account, and password after creation.")
          : tr("A virtual host will be created with public_html/ folder. Upload your PHP, HTML, or static files via File Manager.")}</p>
      </section>}
      <section className="section">
        <div className="section-title">
          <h2>{tr("Website list")}</h2>
          <div className="actions">
            <button className="secondary" disabled={!!loading} onClick={refreshAll}><RefreshCw size={15}/> {tr("Refresh")}</button>
            {!createOpen && <button type="button" onClick={openCreate}><Plus size={15}/> {tr("New website")}</button>}
          </div>
        </div>
        {websites.length > 0 && <div className="website-search">
          <Search size={15}/>
          <input value={websiteSearch} onChange={e => setWebsiteSearch(e.target.value)} placeholder={tr("Filter domains…")} />
          {websiteSearch && <button className="mini secondary-light" onClick={() => setWebsiteSearch('')}><X size={13}/></button>}
          <span className="hint">{wsQuery ? tr("{0} of {1}", filteredWebsites.length, websites.length) : (websites.length === 1 ? tr("{0} website", websites.length) : tr("{0} websites", websites.length))}</span>
        </div>}
        {websites.length === 0 && <EmptyState icon={Globe} message={tr("No websites yet.")} action={{ label: tr("New website"), icon: Plus, onClick: openCreate }} />}
        {websites.length > 0 && filteredWebsites.length === 0 && <EmptyState icon={Globe} message={tr("No domain matches “{0}”.", websiteSearch)} />}
        <div className="site-grid">
          {filteredWebsites.map(site => <div className="site-stack" key={site.id}>
          <article className="site-card">
            <div className="site-head">
              <div>
                <a className="site-link" href={websiteUrl(site)} target="_blank" rel="noopener noreferrer">{site.domain}</a>
                <small>{site.root_path}</small>
              </div>
            </div>
            <div className="site-meta">
              <span className={`badge site-ssl-badge ${site.ssl_enabled ? 'ok' : ''}`}>{site.ssl_wildcard ? tr("Wildcard") : site.ssl_mode === 'reuse' ? tr("Shared cert") : site.ssl_enabled ? tr("SSL OK") : tr("No SSL")}</span>
              <span>{({ wordpress: tr("WordPress"), php: tr("PHP"), static: tr("Static") })[site.app_type || 'wordpress'] || site.app_type}</span>
              <span>{tr("PHP")} <strong>{site.php_version}</strong></span>
              {site.app_type === 'php' && site.nginx_rewrite_mode && site.nginx_rewrite_mode !== 'none' && <span>{tr("Rewrite")} <strong>{site.nginx_rewrite_mode}</strong></span>}
              {site.waf_enabled && <span className="badge ok">{tr("WAF")}</span>}
              {(site.aliases || []).length > 0 && <span>{tr("Domains")} <strong>{(site.aliases || []).length + 1}</strong></span>}
            </div>
            <div className="site-actions" aria-label={tr("Website actions for {0}", site.domain)}>
              <div className="site-feature-actions">
                <button className="site-icon-button secondary-light" data-tooltip="Files" title={tr("Files")} aria-label={tr("Open file manager for {0}", site.domain)} disabled={!!loading} onClick={() => openWebsiteFileManager(site)}><FolderOpen size={15}/></button>
                <button className="site-icon-button secondary-light" data-tooltip="Logs" title={tr("Logs")} aria-label={tr("View logs for {0}", site.domain)} disabled={!!loading} onClick={() => openWebsiteLogs(site)}><FileText size={15}/></button>
                <button className="site-icon-button secondary-light" data-tooltip="Terminal" title={tr("Terminal")} aria-label={tr("Open terminal for {0}", site.domain)} disabled={!!loading} onClick={() => openWebsiteTerminal(site)}><TerminalIcon size={15}/></button>
                <button className="site-icon-button secondary-light" data-tooltip="Settings" title={tr("Settings")} aria-label={tr("Edit settings for {0}", site.domain)} disabled={!!loading} onClick={() => openNginxCustom(site)}><SettingsIcon size={15}/></button>
                {!site.wp_installed && <button className="site-icon-button secondary-light" data-tooltip={tr("Install WP")} title={tr("Install WordPress")} aria-label={tr("Install WordPress on {0}", site.domain)} disabled={!!loading} onClick={() => openWpInstall(site)}><Download size={15}/></button>}
                {site.wp_installed && <button className="site-icon-button secondary-light" data-tooltip={tr("Update WP")} title={tr("Update WordPress")} aria-label={tr("Update WordPress on {0}", site.domain)} disabled={!!loading} onClick={() => openWpUpdate(site)}><RefreshCw size={15}/></button>}
                <button className="site-icon-button danger" data-tooltip="Delete" title={tr("Delete")} aria-label={tr("Delete {0}", site.domain)} disabled={!!loading} onClick={() => deleteWebsite(site.id)}><Trash2 size={15}/></button>
              </div>
            </div>
          </article>
          {nginxCustomEditing?.id === site.id && renderNginxEditor()}
          {logViewer?.id === site.id && renderWebsiteLogViewer()}
          {terminalViewer?.id === site.id && renderWebsiteTerminal()}
          {wpManagerSite?.id === site.id && <div className="wp-manager-panel">
            <div className="user-edit-heading">
              <strong>{wpManagerMode === 'install' ? tr("Install WordPress on {0}", site.domain) : tr("Update WordPress on {0}", site.domain)}</strong>
              <button className="user-edit-close secondary-light" onClick={() => setWpManagerSite(null)}><X size={16}/></button>
            </div>
            {wpManagerMode === 'install' ? <div className="user-edit-grid">
              <p className="hint">{tr("Creates a database, downloads WordPress, configures vhost.")}</p>
              <label><span>{tr("Site title")}</span><input value={wpInstallForm.title} onChange={e => setWpInstallForm(prev => ({ ...prev, title: e.target.value }))} /></label>
              <label><span>{tr("Admin username")}</span><input value={wpInstallForm.admin_user} onChange={e => setWpInstallForm(prev => ({ ...prev, admin_user: e.target.value }))} /></label>
              <label><span>{tr("Admin email")}</span><input type="email" value={wpInstallForm.admin_email} onChange={e => setWpInstallForm(prev => ({ ...prev, admin_email: e.target.value }))} /></label>
              <label><span>{tr("Admin password")}</span><input type="password" value={wpInstallForm.admin_password} onChange={e => setWpInstallForm(prev => ({ ...prev, admin_password: e.target.value }))} placeholder={tr("Min 12 characters")} /></label>
              <div className="user-edit-actions">
                <button className="secondary-light" onClick={() => setWpManagerSite(null)}>{tr("Cancel")}</button>
                <button disabled={!!loading || !wpInstallForm.admin_password || wpInstallForm.admin_password.length < 12} onClick={installWpOnSite}><Download size={14}/> {tr("Install WordPress")}</button>
              </div>
            </div> : <div>
              <p className="hint">{tr("Updates WordPress core, all plugins, and all themes to the latest version.")}</p>
              <div className="user-edit-actions">
                <button className="secondary-light" onClick={() => setWpManagerSite(null)}>{tr("Cancel")}</button>
                <button disabled={!!loading} onClick={updateWpOnSite}><RefreshCw size={14}/> {tr("Update All")}</button>
              </div>
            </div>}
          </div>}
          </div>)}
        </div>
      </section>
    </>;
  }

  function renderSsl() {
    const sslLabel = currentSite?.ssl_mode === 'manual'
      ? tr("Manual SSL")
      : currentSite?.ssl_mode === 'reuse'
        ? tr("Existing certificate")
        : currentSite?.ssl_wildcard
          ? tr("Wildcard SSL")
          : currentSite?.ssl_enabled
            ? tr("SSL Enabled")
            : tr("SSL Disabled");
    const sslUpdated = currentSite?.ssl_updated_at ? new Date(currentSite.ssl_updated_at).toLocaleString() : '';
    // Unsecured sites first, then the rest by name, so the ones that need a
    // certificate are at the top of the overview.
    const sslSites = [...websites].sort((a, b) => (a.ssl_enabled === b.ssl_enabled ? (a.domain || '').localeCompare(b.domain || '') : a.ssl_enabled ? 1 : -1));
    const securedCount = websites.filter(site => site.ssl_enabled).length;
    const siteSslLabel = site => site.ssl_mode === 'manual' ? tr("Manual SSL")
      : site.ssl_mode === 'reuse' ? tr("Shared cert")
        : site.ssl_wildcard ? tr("Wildcard")
          : site.ssl_enabled ? tr("SSL OK") : tr("No SSL");
    return <>
    <section className="section" id="ssl-manage">
      <h2>{tr("SSL Certificate")}</h2>
      <WebsiteSelect />
      {currentSite && <div className="info-box" style={{marginTop:8}}>
        <strong>{currentSite.domain}</strong>
        <span className={currentSite.ssl_enabled ? 'badge ok' : 'badge'} style={{justifySelf:'start'}}>{sslLabel}</span>
        {currentSite.ssl_wildcard && <span className="badge ok" style={{justifySelf:'start'}}>*.{currentSite.domain}</span>}
        {currentSite.ssl_mode === 'reuse' && currentSite.ssl_reuse_name && <span className="hint">{currentSite.ssl_reuse_name.split(':')[1]}</span>}
        {sslUpdated && <span className="hint">{tr("Updated")} {sslUpdated}</span>}
        {currentSite.ssl_mode === 'manual' && currentSite.ssl_has_ca && <span className="badge ok" style={{justifySelf:'start'}}>{tr("CA Bundle")}</span>}
      </div>}
      <div className="segmented ssl-mode-tabs">
        <button className={sslMode === 'letsencrypt' ? 'active' : ''} onClick={() => setSslMode('letsencrypt')}><Lock size={14}/> {tr("Let's Encrypt")}</button>
        <button className={sslMode === 'wildcard' ? 'active' : ''} onClick={() => setSslMode('wildcard')}><Globe size={14}/> {dnsInfo?.installed ? tr("Wildcard") : tr("Wildcard (Cloudflare)")}</button>
        <button className={sslMode === 'existing' ? 'active' : ''} onClick={() => { setSslMode('existing'); loadAvailableCerts(); }}><RefreshCw size={14}/> {tr("Use existing")}</button>
        <button className={sslMode === 'manual' ? 'active' : ''} onClick={() => setSslMode('manual')}><KeyRound size={14}/> {tr("Manual SSL")}</button>
      </div>
      {sslMode === 'letsencrypt' ? <>
        <button className="manual-ssl-submit" disabled={!selectedWebsiteId || !!loading} onClick={() => enableSsl(selectedWebsiteId)}><Lock size={15}/> {tr("Install / Renew SSL")}</button>
        <p className="hint">{tr("certbot HTTP-01 — the domain must point to this server's IP before issuing.")}</p>
      </> : sslMode === 'wildcard' && wildcardProvider(wildcardSslForm.provider, currentSite) === 'opanel' ? <div className="manual-ssl-grid">
        <div style={{gridColumn:'1 / -1'}}>{renderWildcardProvider('opanel', provider => setWildcardSslForm(p => ({ ...p, provider })))}</div>
        <label style={{gridColumn:'1 / -1'}}>{tr("Contact email (optional)")}
          <input type="email" autoComplete="off" value={wildcardSslForm.email} onChange={e => setWildcardSslForm(p => ({ ...p, email: e.target.value }))} placeholder="admin@domain.com" />
        </label>
        <button className="manual-ssl-submit" disabled={!selectedWebsiteId || !!loading} onClick={issueWildcardSsl}><Globe size={15}/> {currentSite?.ssl_wildcard ? tr("Renew / re-issue wildcard") : tr("Issue wildcard certificate")}</button>
        <p className="hint" style={{gridColumn:'1 / -1'}}>{tr("Issues one cert for {0} and *.{1} with a DNS challenge in this server's zone for the domain (DNS Manager). The domain's nameservers must point at this server; no token, and renewals need nothing. Other websites can then pick this cert under \"Use existing\".", currentSite?.domain || tr("the domain"), currentSite?.domain || tr("domain"))}</p>
      </div> : sslMode === 'wildcard' ? <div className="manual-ssl-grid">
        {dnsInfo?.installed && <div style={{gridColumn:'1 / -1'}}>{renderWildcardProvider('cloudflare', provider => setWildcardSslForm(p => ({ ...p, provider })))}</div>}
        <label style={{gridColumn:'1 / -1'}}>{tr("Cloudflare API Token")}
          <input type="password" autoComplete="off" value={wildcardSslForm.api_token} onChange={e => setWildcardSslForm(p => ({ ...p, api_token: e.target.value }))} placeholder={currentSite?.ssl_wildcard ? tr("Stored — leave blank to reuse") : tr("Scoped API Token (not the Global API Key)")} />
        </label>
        <label style={{gridColumn:'1 / -1'}}>{tr("Contact email (optional)")}
          <input type="email" autoComplete="off" value={wildcardSslForm.email} onChange={e => setWildcardSslForm(p => ({ ...p, email: e.target.value }))} placeholder="admin@domain.com" />
        </label>
        <button className="manual-ssl-submit" disabled={!selectedWebsiteId || !!loading} onClick={issueWildcardSsl}><Globe size={15}/> {currentSite?.ssl_wildcard ? tr("Renew / re-issue wildcard") : tr("Issue wildcard certificate")}</button>
        <p className="hint" style={{gridColumn:'1 / -1'}}>{tr("Use a")} <strong>{tr("scoped API Token")}</strong> {tr("from Cloudflare (My Profile → API Tokens → Create Token → permission")} <code>Zone → DNS → Edit</code> {tr("for the zone),")} <strong>{tr("not")}</strong> {tr("the account Global API Key. It is stored encrypted and re-used for automatic renewal. Issues one cert for")} <strong>{currentSite?.domain || tr("the domain")}</strong> {tr("and")} <strong>*.{currentSite?.domain || tr("domain")}</strong> {tr("via a Cloudflare DNS challenge — no DNS record or port 80 needed. Other websites can then pick this cert under \"Use existing\".")}</p>
      </div> : sslMode === 'existing' ? <div className="manual-ssl-grid">
        <div className="form-row" style={{gridColumn:'1 / -1'}}>
          <select value={reuseCertName} onChange={e => setReuseCertName(e.target.value)}>
            <option value="">{tr("— pick a certificate on this server —")}</option>
            {availableCerts.map(c => <option key={c.name} value={c.name} disabled={!c.covers_domain}>
              {c.domains.join(', ')} · {c.source}{c.covers_domain ? '' : tr(" (does not cover this domain)")}
            </option>)}
          </select>
          <button className="secondary-light" type="button" disabled={!!loading} onClick={loadAvailableCerts}><RefreshCw size={13}/> {tr("Refresh")}</button>
        </div>
        <button className="manual-ssl-submit" disabled={!selectedWebsiteId || !reuseCertName || !!loading} onClick={reuseExistingSsl}><Lock size={15}/> {tr("Use this certificate")}</button>
        <p className="hint" style={{gridColumn:'1 / -1'}}>{availableCerts.length === 0
          ? tr("No certificate on this server. Issue a Let’s Encrypt or wildcard cert first (here or on another domain).")
          : tr("A *.example.com wildcard covers every x.example.com — issue it once, reuse it everywhere.")}</p>
      </div> : <div className="manual-ssl-grid">
        <label>
          {tr("Certificate (.crt/.pem)")}
          <input type="file" accept=".crt,.pem" onChange={e => setManualSslFiles(prev => ({ ...prev, certificate: e.target.files?.[0] || null }))} />
        </label>
        <label>
          {tr("Private key (.key/.pem)")}
          <input type="file" accept=".key,.pem" onChange={e => setManualSslFiles(prev => ({ ...prev, private_key: e.target.files?.[0] || null }))} />
        </label>
        <label>
          {tr("CA bundle (.ca/.crt/.pem)")}
          <input type="file" accept=".ca,.crt,.pem" onChange={e => setManualSslFiles(prev => ({ ...prev, ca_bundle: e.target.files?.[0] || null }))} />
        </label>
        <textarea rows={7} disabled={!!manualSslFiles.certificate} value={manualSslForm.certificate} onChange={e => setManualSslForm(prev => ({ ...prev, certificate: e.target.value }))} placeholder={tr("-----BEGIN CERTIFICATE-----")} />
        <textarea rows={7} disabled={!!manualSslFiles.private_key} value={manualSslForm.private_key} onChange={e => setManualSslForm(prev => ({ ...prev, private_key: e.target.value }))} placeholder={tr("-----BEGIN PRIVATE KEY-----")} />
        <textarea rows={7} disabled={!!manualSslFiles.ca_bundle} value={manualSslForm.ca_bundle} onChange={e => setManualSslForm(prev => ({ ...prev, ca_bundle: e.target.value }))} placeholder={tr("Optional CA bundle")} />
        <button className="manual-ssl-submit" disabled={!selectedWebsiteId || !!loading} onClick={installManualSsl}><Upload size={15}/> {tr("Install Manual SSL")}</button>
      </div>}
    </section>
    {websites.length > 0 && <section className="section">
      <div className="section-title">
        <div><h2>{tr("All websites")}</h2><p className="hint">{tr("{0} of {1} secured", securedCount, websites.length)}</p></div>
      </div>
      <div className="table ssl-overview">
        {sslSites.map(site => <div className={`row ssl-overview-row${String(site.id) === String(selectedWebsiteId) ? ' selected' : ''}`} key={site.id}>
          <span className="ssl-overview-domain"><strong>{site.domain}</strong>{site.ssl_updated_at && <small>{tr("Updated")} {new Date(site.ssl_updated_at).toLocaleDateString()}</small>}</span>
          <span className={`badge ${site.ssl_enabled ? 'ok' : 'warn'}`}>{siteSslLabel(site)}</span>
          <button type="button" className="mini secondary" onClick={() => { setSelectedWebsiteId(String(site.id)); document.getElementById('ssl-manage')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}>{site.ssl_enabled ? tr("Manage") : tr("Set up SSL")}</button>
        </div>)}
      </div>
    </section>}
    </>;
  }

  function renderDatabases() {
    const dbQuery = databaseSearch.trim().toLowerCase();
    const websiteById = new Map(websites.map(w => [w.id, w.domain]));
    const filteredDatabases = dbQuery
      ? databases.filter(db => (db.db_name || '').toLowerCase().includes(dbQuery)
          || (db.db_user || '').toLowerCase().includes(dbQuery)
          || (websiteById.get(db.website_id) || '').toLowerCase().includes(dbQuery))
      : databases;
    function copyToClipboard(text, field) {
      const doCopy = navigator.clipboard ? navigator.clipboard.writeText(text) : new Promise((resolve, reject) => {
        try { const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); resolve(); } catch(e) { reject(e); }
      });
      doCopy.then(() => { setCopiedField(field); setTimeout(() => setCopiedField(null), 2000); }).catch(() => setError(tr("Copy failed.")));
    }
    const createOpen = showCreateDb || databases.length === 0;
    const openCreate = () => {
      setShowCreateDb(true);
      setTimeout(() => { const el = document.getElementById('create-database-name'); el?.scrollIntoView({ behavior: 'smooth', block: 'center' }); el?.focus(); }, 0);
    };
    return <section className="section">
      <div className="section-title">
        <h2>{tr("Databases")}</h2>
        <div className="actions">
          <button className="secondary" disabled={!!loading} onClick={refreshAll}><RefreshCw size={15}/> {tr("Refresh")}</button>
          {!createOpen && <button type="button" onClick={openCreate}><Plus size={15}/> {tr("New database")}</button>}
        </div>
      </div>
      {createOpen && <div className="create-inline">
        <div className="create-inline-head">
          <strong>{tr("Create database")}</strong>
          {databases.length > 0 && <button type="button" className="secondary icon-only mini" onClick={() => setShowCreateDb(false)} aria-label={tr("Close")} title={tr("Close")}><X size={15}/></button>}
        </div>
      <div className="form-row">
        <input id="create-database-name" value={newDatabase.db_name} onChange={e => setNewDatabase(prev => ({ ...prev, db_name: e.target.value }))} placeholder="database_name" />
        <input value={newDatabase.db_user} onChange={e => setNewDatabase(prev => ({ ...prev, db_user: e.target.value }))} placeholder={tr("db_user (default = db_name)")} />
        <input value={newDatabase.db_password} onChange={e => setNewDatabase(prev => ({ ...prev, db_password: e.target.value }))} placeholder={tr("password (min 12 chars)")} />
        <button className="mini secondary-light" title={tr("Generate random password")} onClick={() => setNewDatabase(prev => ({ ...prev, db_password: generateRandomPassword() }))}><Dices size={13}/></button>
        <button disabled={!!loading || !newDatabase.db_name.trim()} onClick={createDatabase}><Plus size={15}/> {tr("Create database")}</button>
      </div>
      </div>}
      {createdDbInfo && <div className="info-box db-created-box">
        <div className="db-created-head"><strong>{tr("Database created successfully")}</strong><button className="mini secondary-light" onClick={() => setCreatedDbInfo(null)}><X size={13}/></button></div>
        <div className="db-created-grid">
          <label>{tr("Database")}</label><span>{createdDbInfo.db_name} <button className="mini secondary-light" title={copiedField === 'db_name' ? tr("Copied!") : tr("Copy")} onClick={() => copyToClipboard(createdDbInfo.db_name, 'db_name')}>{copiedField === 'db_name' ? <Check size={12} style={{color:'var(--success)'}}/> : <Copy size={12}/>}</button></span>
          <label>{tr("User")}</label><span>{createdDbInfo.db_user} <button className="mini secondary-light" title={copiedField === 'db_user' ? tr("Copied!") : tr("Copy")} onClick={() => copyToClipboard(createdDbInfo.db_user, 'db_user')}>{copiedField === 'db_user' ? <Check size={12} style={{color:'var(--success)'}}/> : <Copy size={12}/>}</button></span>
          <label>{tr("Password")}</label><span><code>{createdDbInfo.db_password}</code> <button className="mini secondary-light" title={copiedField === 'db_password' ? tr("Copied!") : tr("Copy")} onClick={() => copyToClipboard(createdDbInfo.db_password, 'db_password')}>{copiedField === 'db_password' ? <Check size={12} style={{color:'var(--success)'}}/> : <Copy size={12}/>}</button></span>
        </div>
      </div>}
      {databases.length === 0 && !createdDbInfo && <EmptyState icon={Database} message={tr("No databases found.")} action={{ label: tr("New database"), icon: Plus, onClick: openCreate }} />}
      {databases.length > 0 && <div className="list-search">
        <Search size={15}/>
        <input value={databaseSearch} onChange={e => setDatabaseSearch(e.target.value)} placeholder={tr("Filter databases…")} />
        {databaseSearch && <button className="mini secondary-light" onClick={() => setDatabaseSearch('')}><X size={13}/></button>}
        <span className="hint">{dbQuery ? tr("{0} of {1}", filteredDatabases.length, databases.length) : (databases.length === 1 ? tr("{0} database", databases.length) : tr("{0} databases", databases.length))}</span>
      </div>}
      {databases.length > 0 && filteredDatabases.length === 0 && <EmptyState icon={Database} message={tr("No database matches “{0}”.", databaseSearch)} />}
      <div className="table">
        {filteredDatabases.map(db => {
          return <div className="row db-row" key={db.id}>
          <span><strong>{db.db_name}</strong>{isAdmin && <small className="db-owner">{dbOwnerLabel(db)}</small>}</span>
          <span className="db-user"><small>{tr("User")}</small> {db.db_user}</span>
          <span className="db-actions">
            <button className="mini secondary" disabled={!!loading} onClick={() => openPhpMyAdmin(db.id)}>{tr("phpMyAdmin")}</button>
            <button className="mini secondary-light" disabled={!!loading} title={tr("Download SQL dump")}
                    aria-label={tr("Download SQL dump of {0}", db.db_name)}
                    onClick={() => downloadDatabase(db.id, db.db_name)}><Download size={14}/></button>
            <button className="mini secondary-light" disabled={!!loading} title={tr("Change database password")}
                    aria-label={tr("Change the password for {0}", db.db_name)}
                    onClick={() => changeDbPassword(db.id)}><KeyRound size={14}/></button>
            {isAdmin && !db.website_id && <button className="mini secondary-light" disabled={!!loading}
                    title={tr("Move to another account")}
                    aria-label={tr("Move {0} to another account", db.db_name)}
                    onClick={() => openDbOwnerModal(db)}><MoveRight size={14}/></button>}
            <button className="mini danger" disabled={!!loading} title={tr("Delete database")}
                    aria-label={tr("Delete {0}", db.db_name)}
                    onClick={() => deleteDatabase(db.id, db.db_name)}><Trash2 size={14}/></button>
          </span>
        </div>})}
      </div>
      <p className="hint">{tr("Click phpMyAdmin to sign in directly. Token expires after 60s.")}</p>
    </section>;
  }

  function cronScheduleLabel(expr) {
    const preset = CRON_PRESETS.find(([value]) => value === normalizeCron(expr));
    return preset ? tr(preset[1]) : expr;
  }

  // A preset dropdown beside the raw expression. Picking a preset fills the
  // expression; typing in it (or choosing "Custom") switches to custom.
  function renderSchedulePicker(key, value, onChange, inputId) {
    const preset = CRON_PRESETS.find(([expr]) => expr === normalizeCron(value));
    const custom = customSchedules[key] || !preset;
    return <div className="schedule-picker">
      <select value={custom ? 'custom' : preset[0]} onChange={e => {
        const next = e.target.value;
        if (next === 'custom') {
          setCustomSchedules(prev => ({ ...prev, [key]: true }));
          setTimeout(() => document.getElementById(inputId)?.focus(), 0);
          return;
        }
        setCustomSchedules(prev => ({ ...prev, [key]: false }));
        onChange(next);
      }}>
        {CRON_PRESETS.map(([expr, label]) => <option key={expr} value={expr}>{tr(label)}</option>)}
        <option value="custom">{tr("Custom…")}</option>
      </select>
      <input id={inputId} value={value} spellCheck={false} placeholder="*/15 * * * *" aria-label={tr("Cron expression")}
        onChange={e => { setCustomSchedules(prev => ({ ...prev, [key]: true })); onChange(e.target.value); }} />
    </div>;
  }

  function cronCommandTemplates(site) {
    const host = site?.domain || 'example.com';
    const base = `${site?.ssl_enabled ? 'https' : 'http'}://${host}`;
    const wordpress = !site || (site.app_type || 'wordpress') === 'wordpress';
    return [
      ...(wordpress ? [['wp-cron', 'WordPress cron (wp-cron.php)', `wget -q -O - ${base}/wp-cron.php?doing_wp_cron`]] : []),
      ...(wordpress ? [['wp-due', 'WP-CLI: run due cron events', 'wp cron event run --due-now']] : []),
      ...(wordpress ? [['wp-plugins', 'WP-CLI: update all plugins', 'wp plugin update --all']] : []),
      ...(wordpress ? [['wp-themes', 'WP-CLI: update all themes', 'wp theme update --all']] : []),
      ...(wordpress ? [['wp-core', 'WP-CLI: update WordPress core', 'wp core update']] : []),
      ['url', 'Call a URL (curl)', `curl -s ${base}/`],
      ['php', 'Run a PHP script', 'php -q cron.php'],
    ];
  }

  function renderCron() {
    const templates = cronCommandTemplates(currentSite);
    const template = templates.find(([, , command]) => command === cronCommand.trim());
    return <section className="section">
      <div className="section-title">
        <div><h2>{tr("Cron manager")}</h2></div>
        <button className="secondary" disabled={!selectedWebsiteId || !!loading} onClick={listCron}><RefreshCw size={14}/> {tr("Refresh")}</button>
      </div>
      <div className="cron-builder">
        <div className="field"><span className="field-label">{tr("Website")}</span><WebsiteSelect /></div>
        <div className="field"><span className="field-label">{tr("Schedule")}</span>{renderSchedulePicker('cron', cronSchedule, setCronSchedule, 'cron-schedule-input')}</div>
        <div className="field"><span className="field-label">{tr("Command template")}</span>
          <select value={template ? template[0] : 'custom'} onChange={e => { const picked = templates.find(([key]) => key === e.target.value); if (picked) setCronCommand(picked[2]); }}>
            {templates.map(([key, label]) => <option key={key} value={key}>{tr(label)}</option>)}
            <option value="custom">{tr("Custom command")}</option>
          </select>
        </div>
        <div className="field"><span className="field-label">{tr("Command")}</span>
          <input value={cronCommand} spellCheck={false} onChange={e => setCronCommand(e.target.value)} placeholder={tr("wget -q -O - https://example.com/wp-cron.php")} />
        </div>
        <button className="cron-add" disabled={!selectedWebsiteId || !cronCommand.trim() || !!loading} onClick={addCron}><Plus size={14}/> {tr("Add cron")}</button>
      </div>
      {selectedWebsiteId && <p className="hint">{tr("Cron runs as")} <strong>{cronUser || currentSite?.linux_user || tr("www-data")}</strong> {tr("for the selected website. Accepted commands:")} <code>wget</code>/<code>curl</code> {tr("to an http(s) URL, WP-CLI maintenance commands, or a")} <code>.php</code> {tr("file inside this website. Output is discarded automatically.")}</p>}
      <div className="cron-list">
        {selectedWebsiteId && cronItems.length === 0 && <EmptyState icon={Clock} message={tr("No cron jobs found for this website.")} />}
        {cronItems.map(item => <div className="cron-item" key={`${item.index}-${item.line}`}>
          <span className="badge">#{item.index}</span>
          <span><strong>{cronScheduleLabel(item.schedule)} <code className="cron-expr">{item.schedule}</code></strong><small>{item.command || item.line}</small></span>
          <button className="mini danger" disabled={!!loading} onClick={() => deleteCron(item.index)} aria-label={tr("Delete")} title={tr("Delete")}><Trash2 size={13}/></button>
        </div>)}
      </div>
    </section>;
  }

  function renderFiles() {
    const allSelected = files.length > 0 && selectedFilePaths.length === files.length;
    const visibleFileJobs = fileJobs.filter(job => String(job.website_id) === String(selectedWebsiteId)).slice(0, 4);
    const selectedArchive = selectedFilePaths.length === 1
      ? files.find(item => item.path === selectedFilePaths[0] && isExtractableArchive(item))
      : null;
    return <section className="section">
      <div className="section-title">
        <div><h2>{tr("File manager")}</h2></div>
        <button className="secondary" disabled={!selectedWebsiteId || !!loading} onClick={() => listFiles(fileListPath)}><RefreshCw size={14}/> {tr("Refresh")}</button>
      </div>
      <div className="file-manager">
        <div className="file-panel">
          <div className="file-controls">
            <WebsiteSelect />
            {currentSite && <div className="file-meta">
              <span>{tr("Website:")} <strong>{currentSite.domain}</strong></span>
              <span>{tr("Root:")} <strong>{currentSite.root_path}{fileListPath ? `/${fileListPath}` : ''}</strong></span>
              {currentUser && !isAdmin && <span>{tr("Storage:")} <strong>{storageUsageText(currentUser)}</strong></span>}
            </div>}
            <div className="path-pill breadcrumb-line">
              <button className="crumb" disabled={!selectedWebsiteId || fileListPath === ''} onClick={() => listFiles('')}>{tr("root")}</button>
              {fileBreadcrumbs(fileListPath).map(crumb => <button className="crumb" key={crumb.path} onClick={() => listFiles(crumb.path)}>{crumb.label}</button>)}
            </div>
            <div className="file-toolbar">
              <button className="secondary" disabled={!selectedWebsiteId || fileListPath === '' || !!loading} onClick={() => listFiles(parentFilePath(fileListPath))}>{tr("Up")}</button>
              <button className="secondary" disabled={!selectedWebsiteId || !!loading} onClick={makeFileDirectory}><Plus size={14}/> {tr("Folder")}</button>
              <button className="secondary" disabled={!selectedWebsiteId || !!loading} onClick={makeFile}><FileText size={14}/> {tr("File")}</button>
              <label className={`upload-button ${(!selectedWebsiteId || !!loading) ? 'disabled' : ''}`}>
                <Upload size={14}/> {tr("Upload")}
                <input type="file" disabled={!selectedWebsiteId || !!loading} onChange={e => { uploadSiteFile(e.target.files?.[0]); e.target.value = ''; }} />
              </label>
              <select value={archiveFormat} onChange={e => setArchiveFormat(e.target.value)} disabled={!selectedWebsiteId || !!loading}>
                <option value="zip">{tr("zip")}</option>
                <option value="tar.gz">tar.gz</option>
              </select>
              <button className="secondary" disabled={selectedFilePaths.length === 0 || !!loading} onClick={copySelectedFiles}><Copy size={14}/> {tr("Copy")}</button>
              <button className="secondary" disabled={selectedFilePaths.length === 0 || !!loading} onClick={moveSelectedFiles}><MoveRight size={14}/> {tr("Move")}</button>
              <button className="secondary" disabled={selectedFilePaths.length === 0 || !!loading} onClick={archiveSelectedFiles}><Archive size={14}/> {tr("Archive")}</button>
              <button className="secondary" disabled={!selectedArchive || !!loading} onClick={extractSelectedArchive}><PackageOpen size={14}/> {tr("Extract")}</button>
              <button className="danger" disabled={selectedFilePaths.length === 0 || !!loading} onClick={deleteSelectedFiles}><Trash2 size={14}/> {tr("Delete")}</button>
            </div>
            {visibleFileJobs.length > 0 && <div className="file-job-list">
              {visibleFileJobs.map(job => <div className={`file-job ${job.status}`} key={job.job_id}>
                <Clock size={14}/>
                <span><strong>{job.archive_path?.split('/').pop() || tr("Archive")}</strong> {job.status === 'done' ? tr("completed") : job.status === 'error' ? tr("failed") : job.status}</span>
                {job.error && <small>{job.error}</small>}
              </div>)}
            </div>}
          </div>
          <div className="file-list-header">
            <label><input type="checkbox" checked={allSelected} onChange={toggleAllFiles} disabled={files.length === 0} /> {tr("Select")}</label>
            <span>{files.length} {tr("item(s)")}</span>
          </div>
          <div className="file-list">
            {files.length === 0 && <div className="empty-box">{tr("No files in this folder.")}</div>}
            {files.map(item => <div className={`file-item ${selectedFilePaths.includes(item.path) ? 'selected' : ''}`} key={item.path}>
              <input type="checkbox" checked={selectedFilePaths.includes(item.path)} onChange={() => toggleFileSelection(item.path)} />
              <button className="file-name" onClick={() => item.is_dir ? listFiles(item.path) : (isTextEditable(item) ? openFileEditorTab(item.path) : downloadFile(item.path))}>
                {item.is_dir ? <FolderOpen size={16}/> : <FileText size={16}/>} <strong>{item.name}</strong>
              </button>
              <button type="button" className="file-mode" disabled={!!loading} title={tr("Change permissions")} aria-label={tr("Change permissions of {0}", item.name)}
                onClick={() => setChmodTarget({ path: item.path, name: item.name, is_dir: item.is_dir, mode: (item.mode || (item.is_dir ? '755' : '644')).slice(-3) })}>{item.mode || '---'}</button>
              <span className="file-size">{item.is_dir ? tr("Folder") : formatBytes(item.size)}{item.modified ? <span className="file-date-inline"> · {formatFileTime(item.modified)}</span> : null}</span>
              <span className="file-date" title={item.modified ? new Date(item.modified * 1000).toLocaleString() : ''}>{formatFileTime(item.modified)}</span>
              <div className="file-row-actions">
                {!item.is_dir && <button className="mini secondary-light" disabled={!!loading} onClick={() => downloadFile(item.path)}><Download size={13}/></button>}
                {isExtractableArchive(item) && <button className="mini secondary-light" disabled={!!loading} onClick={() => extractArchive(item)}><PackageOpen size={13}/> {tr("Extract")}</button>}
                <button className="mini secondary-light" disabled={!!loading} onClick={() => renameFileItem(item)}>{tr("Rename")}</button>
              </div>
            </div>)}
          </div>
        </div>
      </div>
    </section>;
  }

  // ISO time from the server as the file manager shows times.
  function formatIsoTime(value) {
    const ms = Date.parse(value || '');
    return Number.isFinite(ms) ? formatFileTime(ms / 1000) : '';
  }

  // DirectAdmin's restore, step by step: where the backups are, what it takes
  // to reach them, which accounts, go. OPanel and DirectAdmin archives share
  // the list; each is restored by its own importer.
  function renderRestoreWizard() {
    const allGroups = restoreGroups();
    const needle = restoreFilter.trim().toLowerCase();
    const groups = needle
      ? allGroups.filter(group => (group.account + ' ' + group.items.map(item => item.filename).join(' ')).toLowerCase().includes(needle))
      : allGroups;
    const selectable = groups.filter(group => restoreChosen(group).valid !== false);
    const pickedGroups = allGroups.filter(group => restorePicks.includes(group.key));
    const pickedHasDa = pickedGroups.some(group => group.kind === 'directadmin');
    const allPicked = selectable.length > 0 && selectable.every(group => restorePicks.includes(group.key));
    const somePicked = selectable.some(group => restorePicks.includes(group.key));
    const job = restoreJob;
    const jobActive = !!job && (job.status === 'queued' || job.status === 'running');
    const remote = restoreRemote;
    const setRemote = patch => setRestoreRemote(prev => ({ ...prev, ...patch }));
    const sources = [
      ['local', HardDrive, tr("This server"), tr("Backups the panel made, and archives uploaded for restore.")],
      ['target', Network, tr("Backup Destination"), tr("An S3 or SFTP destination saved under Backup Destination.")],
      ['remote', Server, tr("Another server"), tr("Pull backups from another server over SFTP or FTP, such as an old DirectAdmin server.")],
    ];
    const stepTwoTitle = { local: tr("Backups on this server"), target: tr("Destination"), remote: tr("Connection") }[restoreSource];

    return <div className="backup-tab-panel restore-wizard">
      <div className="backup-panel-title">
        <div><h3>{tr("Restore")}</h3><p className="hint">{tr("OPanel and DirectAdmin backups: choose where they are, pick the accounts, then restore.")}</p></div>
      </div>

      <section className="restore-step">
        <h4><span className="restore-step-no">1</span>{tr("Source")}</h4>
        <div className="restore-sources" role="radiogroup" aria-label={tr("Source")}>
          {sources.map(([id, Icon, label, hint]) => <button
            key={id}
            type="button"
            role="radio"
            aria-checked={restoreSource === id}
            className={`restore-source${restoreSource === id ? ' active' : ''}`}
            onClick={() => chooseRestoreSource(id)}
          >
            <span className="settings-tile-icon"><Icon size={18}/></span>
            <span className="settings-tile-text"><strong>{label}</strong><small>{hint}</small></span>
          </button>)}
        </div>
      </section>

      <section className="restore-step">
        <h4><span className="restore-step-no">2</span>{stepTwoTitle}</h4>
        {restoreSource === 'local' && <>
          <p className="hint">
            {tr("Read from")} <code>{restoreList?.directories?.opanel || '/var/backups/opanel/users'}</code> {tr("and")} <code>{restoreList?.directories?.directadmin || '/home/admin/opanel-backups/da'}</code>. {tr("An uploaded archive goes to the right one by its name.")}
          </p>
          <p className="hint restore-sftp-hint">
            {tr("Large backups go up over SFTP: sign in as admin on port 22, with the SFTP password set under SFTP accounts.")}<br/>
            {tr("Put them in {0} (/backups in the SFTP client), panel and DirectAdmin backups alike, then press Refresh once the upload has finished.", '/home/admin/backups')}
          </p>
          <div className="actions restore-local-actions">
            <label className="upload-button secondary">
              <Upload size={14}/> {tr("Upload backups")}
              <input type="file" multiple accept=".tar.gz,.tgz,.tar.zst,.tar.bz2,.tbz2,.tar.xz,.txz,.tar" onChange={e => { uploadRestoreArchives(e.target.files); e.target.value = ''; }} />
            </label>
            <button className="secondary" disabled={restoreListing} onClick={() => loadRestoreList('local')}><RefreshCw size={14}/> {tr("Refresh")}</button>
          </div>
        </>}
        {restoreSource === 'target' && <>
          <div className="restore-form-row">
            <select value={restoreTargetId} onChange={e => { setRestoreTargetId(e.target.value); setRestoreList(null); setRestoreListError(''); setRestorePicks([]); setRestoreChoice({}); }} aria-label={tr("Destination")}>
              <option value="">{tr("Choose a destination")}</option>
              {sftpTargets.map(target => <option key={target.id} value={target.id}>{target.name} ({target.kind === 's3' ? 'S3' : 'SFTP'})</option>)}
            </select>
            <button className="secondary" disabled={!restoreTargetId || restoreListing} onClick={() => loadRestoreList('target')}><RefreshCw size={14}/> {tr("Refresh")}</button>
          </div>
          {sftpTargets.length === 0 && <p className="hint">{tr("No destination saved yet. Add one under Backup Destination.")}</p>}
        </>}
        {restoreSource === 'remote' && <form className="restore-remote" onSubmit={e => { e.preventDefault(); loadRestoreList('remote'); }} autoComplete="off">
          <label className="field"><span className="field-label">{tr("Protocol")}</span>
            <select value={remote.protocol} onChange={e => {
              const protocol = e.target.value;
              const port = Number(remote.port);
              // Follow the protocol's usual port unless someone typed their own.
              setRemote({ protocol, port: protocol === 'sftp' ? (port === 21 ? 22 : remote.port) : (port === 22 ? 21 : remote.port) });
            }}>
              <option value="sftp">SFTP</option>
              <option value="ftp">FTP</option>
              <option value="ftps">{tr("FTPS (FTP over TLS)")}</option>
            </select>
          </label>
          <label className="field"><span className="field-label">{tr("Host")}</span><input value={remote.host} onChange={e => setRemote({ host: e.target.value })} placeholder="203.0.113.10" spellCheck={false} /></label>
          <label className="field"><span className="field-label">{tr("Port")}</span><input type="number" min="1" max="65535" value={remote.port} onChange={e => setRemote({ port: e.target.value })} /></label>
          <label className="field"><span className="field-label">{tr("Username")}</span><input value={remote.username} onChange={e => setRemote({ username: e.target.value })} spellCheck={false} /></label>
          <label className="field"><span className="field-label">{remote.protocol === 'sftp' && remote.use_key ? tr("Key passphrase") : tr("Password")}</span><input type="password" value={remote.password} onChange={e => setRemote({ password: e.target.value })} autoComplete="new-password" /></label>
          <label className="field restore-remote-wide"><span className="field-label">{tr("Folder")}</span><input value={remote.path} onChange={e => setRemote({ path: e.target.value })} placeholder="/home/admin/admin_backups" spellCheck={false} /></label>
          {remote.protocol === 'sftp' && <label className="check-line restore-remote-wide">
            <input type="checkbox" checked={remote.use_key} onChange={e => setRemote({ use_key: e.target.checked })} />
            {tr("Sign in with a private key")}
          </label>}
          {remote.protocol === 'sftp' && remote.use_key && <label className="field restore-remote-wide"><span className="field-label">{tr("Private key")}</span>
            <textarea rows={4} value={remote.private_key} onChange={e => setRemote({ private_key: e.target.value })} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" spellCheck={false} />
          </label>}
          {remote.protocol === 'ftp' && <p className="hint restore-remote-wide">{tr("Plain FTP sends the password unencrypted. Use SFTP or FTPS if the server offers it.")}</p>}
          <div className="actions restore-remote-wide">
            <button type="submit" disabled={restoreListing}><Search size={14}/> {restoreListing ? tr("Connecting...") : tr("Connect and list backups")}</button>
            <span className="hint">{tr("Used for this restore only; nothing here is saved.")}</span>
          </div>
        </form>}
        {restoreList?.host_key && <p className="hint">{tr("Server key:")} <code>{restoreList.host_key.type} {restoreList.host_key.fingerprint}</code></p>}
        {restoreListError && <div className="restore-error" role="alert"><AlertCircle size={14}/> <span>{restoreListError}</span></div>}
      </section>

      <section className="restore-step">
        <h4><span className="restore-step-no">3</span>{tr("Accounts to restore")}</h4>
        {restoreListing && <p className="hint">{tr("Reading backups...")}</p>}
        {!restoreListing && !restoreList && restoreSource !== 'local' && <p className="hint">{tr("The accounts appear here once the source has been read.")}</p>}
        {restoreList && allGroups.length === 0 && <EmptyState icon={Archive} message={tr("No backups found here.")} />}
        {restoreList && allGroups.length > 0 && <>
          <div className="restore-toolbar">
            <label className="schedule-toggle">
              <input
                type="checkbox"
                checked={allPicked}
                ref={box => { if (box) box.indeterminate = somePicked && !allPicked; }}
                onChange={e => {
                  const keys = selectable.map(group => group.key);
                  setRestorePicks(prev => e.target.checked ? [...new Set([...prev, ...keys])] : prev.filter(key => !keys.includes(key)));
                }}
              />
              <span>{tr("Select all")}</span>
            </label>
            {allGroups.length > 6 && <input className="restore-filter" value={restoreFilter} onChange={e => setRestoreFilter(e.target.value)} placeholder={tr("Filter accounts")} aria-label={tr("Filter accounts")} />}
            <span className="hint">{pickedGroups.length ? tr("{0} selected", pickedGroups.length) : tr("{0} account(s)", allGroups.length)}</span>
          </div>
          <div className="restore-accounts">
            {groups.map(group => {
              const chosen = restoreChosen(group);
              const invalid = chosen.valid === false;
              const picked = restorePicks.includes(group.key);
              const toggle = () => {
                if (invalid) return;
                setRestorePicks(prev => picked ? prev.filter(key => key !== group.key) : [...prev, group.key]);
              };
              const detail = invalid
                ? (chosen.error || tr("Invalid backup"))
                : [formatBytes(chosen.size), formatIsoTime(chosen.modified_at),
                  chosen.kind === 'opanel' && chosen.websites != null ? tr("{0} website(s)", chosen.websites) : ''].filter(Boolean).join(' · ');
              const deletable = restoreSource === 'local' && ['uploaded', 'da', 'inbox'].includes(chosen.location);
              return <div
                key={group.key}
                className={`restore-account${picked ? ' picked' : ''}${invalid ? ' invalid' : ''}`}
                role="checkbox"
                aria-checked={picked}
                aria-disabled={invalid}
                tabIndex={invalid ? -1 : 0}
                onClick={toggle}
                onKeyDown={e => { if (e.target === e.currentTarget && (e.key === ' ' || e.key === 'Enter')) { e.preventDefault(); toggle(); } }}
              >
                <input type="checkbox" checked={picked} disabled={invalid} tabIndex={-1} onChange={toggle} onClick={e => e.stopPropagation()} aria-hidden="true" />
                <span className="restore-account-main">
                  <strong>
                    {group.account || chosen.username || chosen.filename}
                    {chosen.location === 'inbox' && <span className="badge restore-kind" title="/home/admin/backups">SFTP</span>}
                  </strong>
                  <small>{detail}</small>
                </span>
                <span className={`badge${group.kind === 'opanel' ? ' ok' : ''}`}>{group.kind === 'directadmin' ? 'DirectAdmin' : 'OPanel'}</span>
                {group.items.length > 1
                  ? <select className="restore-version" value={chosen.ref} aria-label={tr("Backup to restore")}
                      onClick={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}
                      onChange={e => setRestoreChoice(prev => ({ ...prev, [group.key]: e.target.value }))}>
                      {group.items.map(item => <option key={item.ref} value={item.ref}>{item.filename}{item.modified_at ? ` · ${formatIsoTime(item.modified_at)}` : ''}</option>)}
                    </select>
                  : (group.account || chosen.username)
                    ? <span className="restore-version-name" title={chosen.ref}>{chosen.filename}</span>
                    : <span />}
                {deletable
                  ? <button type="button" className="danger restore-delete" title={tr("Delete")} aria-label={tr("Delete")} disabled={!!loading}
                      onClick={e => { e.stopPropagation(); deleteRestoreArchive(chosen); }}><Trash2 size={14}/></button>
                  : <span className="restore-delete-spacer" />}
              </div>;
            })}
            {groups.length === 0 && <p className="hint">{tr("No account matches this filter.")}</p>}
          </div>
        </>}
      </section>

      <section className="restore-step">
        <h4><span className="restore-step-no">4</span>{tr("Restore")}</h4>
        {pickedHasDa && <label className="check-line">
          <input type="checkbox" checked={restoreOverwrite} onChange={e => setRestoreOverwrite(e.target.checked)} />
          {tr("DirectAdmin backups: overwrite a user or website that is already on this server")}
        </label>}
        <div className="actions">
          <button disabled={!!loading || jobActive || pickedGroups.length === 0} onClick={runRestore}>
            <RotateCcw size={14}/> {pickedGroups.length ? tr("Restore {0} account(s)", pickedGroups.length) : tr("Restore")}
          </button>
          {!pickedGroups.length && !jobActive && <span className="hint">{tr("Pick at least one account above.")}</span>}
        </div>
        {job && <div className={`backup-job ${job.status}`}>
          <Clock size={14}/>
          <span>
            <strong>{jobActive ? tr("Restoring") : job.status === 'done' ? tr("Restore finished") : tr("Restore finished with errors")}</strong>
            <small className="restore-job-message">{job.message}</small>
            {jobActive && <span className="job-progress">
              <span className={`progress-bar${job.progress_percent == null ? ' indeterminate' : ''}`}>
                <span className="progress-bar-fill" style={job.progress_percent == null ? undefined : { width: `${job.progress_percent}%` }} />
              </span>
              {job.progress_percent != null && <small className="job-progress-pct">{Math.round(job.progress_percent)}%</small>}
            </span>}
          </span>
          <span className={job.status === 'done' ? 'badge ok' : job.status === 'error' ? 'badge bad' : 'badge'}>{job.status}</span>
        </div>}
      </section>
    </div>;
  }

  function renderBackups() {
    const selectedBackupUser = users.find(user => String(user.id) === String(selectedBackupUserId));
    const userNameById = id => users.find(user => String(user.id) === String(id))?.username || `User #${id}`;
    const scheduleUserLabel = item => {
      if (item.all_users) return tr("All users");
      const ids = (item.user_ids && item.user_ids.length > 0) ? item.user_ids : (item.user_id ? [item.user_id] : []);
      return ids.length ? ids.map(userNameById).join(', ') : 'No users';
    };
    const jobTitle = job => ({ site_backup: tr("Website backup"), user_backup: tr("Full user backup"), sftp_backup: tr("SFTP backup") }[job.kind] || tr("Backup task"));
    const jobDetail = job => job.error || job.remote_file || job.backup_file || job.message || job.status;
    const jobTimestamp = job => {
      const stamp = job.finished_at || job.started_at || job.created_at || '';
      if (!stamp) return 'No timestamp';
      const date = new Date(stamp);
      return Number.isNaN(date.getTime()) ? stamp : new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Asia/Ho_Chi_Minh',
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
      }).format(date).replace(',', '');
    };
    const backupTabs = isAdmin
      ? [
        ['website', tr("Backup website"), Globe],
        ['user', tr("Backup user"), Users],
        ['restore', tr("Restore"), RotateCcw],
        ['schedule', tr("Scheduled backups"), Clock],
        ['destination', tr("Backup Destination"), Network],
        ['logs', tr("Backup logs"), FileText],
      ]
      : [
        ['website', tr("Backup website"), Globe],
        ['logs', tr("Backup logs"), FileText],
      ];
    const activeBackupTab = backupTabs.some(([id]) => id === backupTab) ? backupTab : 'website';

    return <section className="section backups-page">
      <h2>{tr("Backups")}</h2>
      <div className="segmented-control backup-tabs" role="tablist" aria-label={tr("Backup sections")}>
        {backupTabs.map(([id, label, Icon]) => <button
          key={id}
          type="button"
          role="tab"
          aria-selected={activeBackupTab === id}
          className={activeBackupTab === id ? 'active' : ''}
          onClick={() => setBackupTab(id)}
        ><Icon size={14}/>{label}</button>)}
      </div>

      {activeBackupTab === 'logs' && <div className="backup-tab-panel backup-logs-panel">
        <div className="backup-panel-title">
          <div><h3>{tr("Backup logs")}</h3><p className="hint">{tr("Recent queued, running, completed, and failed backup tasks.")}</p></div>
          <button className="secondary" disabled={!!loading} onClick={() => loadBackupJobs(true)}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        {backupJobs.length === 0 && <EmptyState icon={FileText} message={tr("No backup logs found.")} />}
        {backupJobs.length > 0 && <div className="backup-job-list">
          {backupJobs.map(job => <div className={`backup-job ${job.status}`} key={job.job_id}>
            <Clock size={14}/>
            <span>
              <strong>{jobTitle(job)}</strong>
              <small>{jobDetail(job)}</small>
              {(job.status === 'running' || job.status === 'queued') && <span className="job-progress">
                <span className={`progress-bar${job.progress_percent == null ? ' indeterminate' : ''}`}>
                  <span className="progress-bar-fill" style={job.progress_percent == null ? undefined : { width: `${job.progress_percent}%` }} />
                </span>
                {/* No number when nothing countable is happening -- taring one
                    site's tree has no milestones to report. */}
                {job.progress_percent != null && <small className="job-progress-pct">{Math.round(job.progress_percent)}%</small>}
              </span>}
              <small className="backup-job-time">{jobTimestamp(job)}</small>
            </span>
            <span className={job.status === 'done' ? 'badge ok' : job.status === 'error' ? 'badge bad' : 'badge'}>{job.status}</span>
          </div>)}
        </div>}
      </div>}

      {activeBackupTab === 'website' && <div className="backup-tab-panel">
        <div className="backup-panel-title">
          <div><h3>{tr("Backup website")}</h3><p className="hint">{tr("Backups include website source files and a database SQL export.")}</p></div>
        </div>
        <WebsiteSelect />
        <div className="actions backup-toolbar">
          <button disabled={!selectedWebsiteId || !!loading} onClick={createBackup}><Plus size={14}/> {tr("Create backup")}</button>
          <button className="secondary" disabled={!selectedWebsiteId || !!loading} onClick={refreshBackupArea}><RefreshCw size={14}/> {tr("Refresh")}</button>
          <label className="upload-button secondary">
            <Upload size={14}/> {tr("Upload backup")}
            <input type="file" accept=".tar.gz,application/gzip" onChange={e => { uploadBackup(e.target.files?.[0]); e.target.value = ''; }} />
          </label>
        </div>
        {backups.length === 0 && selectedWebsiteId && <EmptyState icon={Archive} message={tr("No backups found for this website.")} action={{ label: tr("Create backup"), icon: Plus, onClick: () => { if (!loading) createBackup(); } }} />}
        <div className="backup-list">
          {backups.map(file => <div className="backup-item" key={file}>
            <span>{file.split('/').pop()}</span>
            <div className="actions">
              <button className="secondary" disabled={!!loading} onClick={() => downloadBackup(file)}><Download size={14}/> {tr("Download")}</button>
              <button className="secondary" disabled={!!loading} onClick={() => restoreBackup(file)}><RotateCcw size={14}/> {tr("Restore")}</button>
              <button className="danger" disabled={!!loading} onClick={() => deleteBackup(file)}><Trash2 size={14}/></button>
            </div>
          </div>)}
        </div>
      </div>}

      {isAdmin && activeBackupTab === 'user' && <div className="backup-tab-panel">
        <div className="backup-panel-title">
          <div><h3>{tr("Backup user")}</h3><p className="hint">{tr("Includes the panel user, all owned websites, source files, database dumps, and restore metadata.")}</p></div>
          <button className="secondary" disabled={!!loading} onClick={refreshUserBackupArea}><RefreshCw size={14}/> {tr("Reload")}</button>
        </div>
        <div className="sftp-run-row user-backup-row backup-run-row">
          <select value={selectedBackupUserId} onChange={e => setSelectedBackupUserId(e.target.value)}>
            <option value="">{tr("Select user")}</option>
            {users.map(user => <option key={user.id} value={user.id}>{user.username}</option>)}
          </select>
          <select value={selectedSftpTargetId} onChange={e => setSelectedSftpTargetId(e.target.value)}>
            <option value="">{tr("Local only")}</option>
            {sftpTargets.map(target => <option key={target.id} value={target.id}>{target.name}</option>)}
          </select>
          <button disabled={!selectedBackupUserId || !!loading} onClick={createUserBackup}><Archive size={14}/> {tr("Create backup")}</button>
        </div>
        {selectedBackupUser && <p className="hint">{tr("Current user:")} <strong>{selectedBackupUser.username}</strong></p>}
        <div className="actions backup-subactions">
          <button className="secondary" disabled={!selectedBackupUserId || !!loading} onClick={() => listUserBackups()}><RefreshCw size={14}/> {tr("Refresh list")}</button>
        </div>
        {selectedBackupUserId && userBackups.length === 0 && <EmptyState icon={Archive} message={tr("No user backups found.")} />}
        <div className="backup-list">
          {userBackups.map(file => <div className="backup-item" key={file}>
            <span>{file.split('/').pop()}</span>
            <div className="actions">
              <button className="secondary" disabled={!!loading} onClick={() => downloadUserBackup(file)}><Download size={14}/> {tr("Download")}</button>
              <button className="secondary" disabled={!!loading} onClick={() => restoreUserBackup(file)}><RotateCcw size={14}/> {tr("Restore user")}</button>
              <button className="danger" disabled={!!loading} onClick={() => deleteUserBackup(file)}><Trash2 size={14}/></button>
            </div>
          </div>)}
        </div>

      </div>}

      {isAdmin && activeBackupTab === 'restore' && renderRestoreWizard()}

      {isAdmin && activeBackupTab === 'schedule' && <div className="backup-tab-panel">
        <div className="backup-panel-title">
          <div><h3>{tr("Scheduled backups")}</h3><p className="hint">{tr("Runs a full user backup on a schedule, with an optional off-server destination. A daily schedule rotates through seven files named for the day —")} <code>username-monday.tar.gz</code> {tr("and so on — so you keep a week and the eighth day overwrites the first. With a destination, that week is kept there: each archive is removed from this server once it has uploaded, and stays here only if the upload fails.")}</p></div>
          <button className="secondary" disabled={!!loading} onClick={refreshScheduledBackupArea}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        <div className="backup-schedule-builder">
          <div className="field"><span className="field-label">{tr("Accounts")}</span>
            <label className="check-line">
              <input type="checkbox" checked={!!newBackupSchedule.all_users} onChange={e => setNewBackupSchedule(prev => ({ ...prev, all_users: e.target.checked }))} />
              {tr("All users")}
            </label>
            {!newBackupSchedule.all_users && <select multiple value={newBackupSchedule.user_ids || []} onChange={e => setNewBackupSchedule(prev => ({ ...prev, user_ids: Array.from(e.target.selectedOptions, option => option.value) }))}>
              {users.map(user => <option key={user.id} value={String(user.id)}>{user.username}</option>)}
            </select>}
          </div>
          <div className="field"><span className="field-label">{tr("Schedule")}</span>{renderSchedulePicker('backup', newBackupSchedule.schedule, value => setNewBackupSchedule(prev => ({ ...prev, schedule: value })), 'backup-schedule-input')}</div>
          <div className="field"><span className="field-label">{tr("Destination")}</span>
            <select value={newBackupSchedule.target_id} onChange={e => setNewBackupSchedule(prev => ({ ...prev, target_id: e.target.value }))}>
              <option value="">{tr("Local only")}</option>
              {sftpTargets.map(target => <option key={target.id} value={target.id}>{target.name}</option>)}
            </select>
          </div>
          <button className="backup-schedule-add" disabled={(!newBackupSchedule.all_users && (!newBackupSchedule.user_ids || newBackupSchedule.user_ids.length === 0)) || !!loading} onClick={createBackupSchedule}><Clock size={14}/> {tr("Schedule")}</button>
        </div>
        <div className="backup-list">
          {backupSchedules.map(item => {
            const scheduleTarget = sftpTargets.find(target => target.id === item.target_id);
            return <div className="backup-item" key={item.id}>
              <span>{scheduleUserLabel(item)} - {cronScheduleLabel(item.schedule)}{scheduleTarget ? ` - ${scheduleTarget.name}` : ''}
                {(() => {
                  // A run started from this row reports on this row. Sending
                  // someone to another tab to find out whether their click did
                  // anything is how it reads as doing nothing.
                  const live = backupJobs.find(job => job.schedule_id === item.id
                    && (job.status === 'running' || job.status === 'queued'));
                  if (!live) return <small>{item.last_status}: {item.last_message || tr("not run yet")}</small>;
                  return <>
                    <small>{live.message || tr("Running")}</small>
                    <span className="job-progress">
                      <span className={`progress-bar${live.progress_percent == null ? ' indeterminate' : ''}`}>
                        <span className="progress-bar-fill" style={live.progress_percent == null ? undefined : { width: `${live.progress_percent}%` }} />
                      </span>
                      {live.progress_percent != null && <small className="job-progress-pct">{Math.round(live.progress_percent)}%</small>}
                    </span>
                  </>;
                })()}
              </span>
              <div className="actions">
                <button className="secondary" disabled={!!loading} onClick={() => runBackupScheduleNow(item, scheduleUserLabel(item))}><Play size={14}/> {tr("Run now")}</button>
                <button className="danger" disabled={!!loading} onClick={() => deleteBackupSchedule(item.id)}><Trash2 size={14}/></button>
              </div>
            </div>;
          })}
        </div>
      </div>}

      {isAdmin && activeBackupTab === 'destination' && <div className="backup-tab-panel">
        <div className="backup-panel-title">
          <div><h3>{tr("Backup Destination")}</h3><p className="hint">{tr("Where off-server backup copies are sent. SFTP, or any S3-compatible object storage.")}</p></div>
          <button className="secondary" disabled={!!loading} onClick={loadSftpTargets}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        <div className="sftp-form sftp-target-form">
          <input value={newSftpTarget.name} onChange={e => setNewSftpTarget(prev => ({ ...prev, name: e.target.value }))} placeholder={tr("Destination name")} />
          <select value={newSftpTarget.kind} onChange={e => setNewSftpTarget(prev => ({ ...prev, kind: e.target.value }))}>
            <option value="sftp">{tr("SFTP")}</option>
            <option value="s3">{tr("S3 compatible")}</option>
          </select>

          {newSftpTarget.kind === 'sftp' ? <>
            <input value={newSftpTarget.host} onChange={e => setNewSftpTarget(prev => ({ ...prev, host: e.target.value }))} placeholder={tr("Host")} />
            <input value={newSftpTarget.port} onChange={e => setNewSftpTarget(prev => ({ ...prev, port: e.target.value }))} placeholder="22" inputMode="numeric" />
            <input value={newSftpTarget.username} onChange={e => setNewSftpTarget(prev => ({ ...prev, username: e.target.value }))} placeholder={tr("Username")} />
            <input value={newSftpTarget.password} onChange={e => setNewSftpTarget(prev => ({ ...prev, password: e.target.value }))} placeholder={tr("Password")} type="password" />
            <input value={newSftpTarget.remote_path} onChange={e => setNewSftpTarget(prev => ({ ...prev, remote_path: e.target.value }))} placeholder="/backups/opanel" />
            <textarea value={newSftpTarget.private_key} onChange={e => setNewSftpTarget(prev => ({ ...prev, private_key: e.target.value }))} placeholder={tr("Private key (optional)")} rows={4} />
          </> : <>
            <input value={newSftpTarget.s3_bucket} onChange={e => setNewSftpTarget(prev => ({ ...prev, s3_bucket: e.target.value }))} placeholder={tr("Bucket")} />
            <input value={newSftpTarget.s3_endpoint} onChange={e => setNewSftpTarget(prev => ({ ...prev, s3_endpoint: e.target.value }))} placeholder={tr("Endpoint - leave empty for AWS")} />
            <input value={newSftpTarget.s3_region} onChange={e => setNewSftpTarget(prev => ({ ...prev, s3_region: e.target.value }))} placeholder={tr("us-east-1")} />
            <input value={newSftpTarget.s3_access_key} onChange={e => setNewSftpTarget(prev => ({ ...prev, s3_access_key: e.target.value }))} placeholder={tr("Access key")} />
            <input value={newSftpTarget.s3_secret_key} onChange={e => setNewSftpTarget(prev => ({ ...prev, s3_secret_key: e.target.value }))} placeholder={tr("Secret key")} type="password" />
            <input value={newSftpTarget.remote_path} onChange={e => setNewSftpTarget(prev => ({ ...prev, remote_path: e.target.value }))} placeholder={tr("Key prefix, e.g. opanel/backups")} />
            <label className="check-line">
              <input type="checkbox" checked={!!newSftpTarget.s3_use_path_style}
                onChange={e => setNewSftpTarget(prev => ({ ...prev, s3_use_path_style: e.target.checked }))} />
              {tr("Path-style addressing (MinIO, Ceph)")}
            </label>
          </>}

          <button disabled={!!loading || !newSftpTarget.name || (newSftpTarget.kind === 's3'
            ? (!newSftpTarget.s3_bucket || !newSftpTarget.s3_access_key || !newSftpTarget.s3_secret_key)
            : (!newSftpTarget.host || !newSftpTarget.username || (!newSftpTarget.password && !newSftpTarget.private_key)))}
            onClick={createSftpTarget}><Plus size={14}/> {tr("Save destination")}</button>
        </div>
        {sftpTargets.length === 0 && <EmptyState icon={Network} message={tr("No backup destinations found.")} />}
        <div className="backup-list">
          {sftpTargets.map(target => <div className="backup-item" key={target.id}>
            <span>
              <span className="badge">{target.kind === 's3' ? 'S3' : tr("SFTP")}</span>{' '}
              {target.name} &mdash; {target.kind === 's3'
                ? `${target.s3_bucket}/${target.remote_path.replace(/^\/+/, '')}${target.s3_endpoint ? ` @ ${target.s3_endpoint}` : ''}`
                : `${target.username}@${target.host}:${target.remote_path}`}
            </span>
            <span className="backup-item-actions">
              {target.kind === 's3' && <button className="secondary" disabled={!!loading} onClick={() => loadTargetObjects(target.id)}>
                {targetObjects.id === target.id ? tr("Hide files") : tr("Files")}
              </button>}
              {target.kind === 's3' && <button className="secondary" disabled={!!loading} onClick={() => testBackupTarget(target.id)}>{tr("Test")}</button>}
              <button className="danger" disabled={!!loading} onClick={() => deleteSftpTarget(target.id)}><Trash2 size={14}/></button>
            </span>
          </div>)}
          {targetObjects.id && <div className="backup-list target-objects">
            {targetObjects.items.length === 0
              ? <p className="hint">{tr("Nothing in")} {targetObjects.bucket} {tr("under this prefix yet.")}</p>
              : targetObjects.items.map(item => <div className="backup-item" key={item.key}>
                  <span>{item.name} <small>{formatBytes(item.size)} &middot; {item.modified.slice(0, 19).replace('T', ' ')}</small></span>
                  <button className="danger" disabled={!!loading}
                    onClick={() => deleteTargetObject(targetObjects.id, item.key)}><Trash2 size={14}/></button>
                </div>)}
          </div>}
        </div>
      </div>}
    </section>;
  }

  // Fail2ban is managed where the rest of the firewall is: its bans are
  // firewall rules, and its Never ban list sits next to Allow IP.
  function renderFirewallFail2ban() {
    const addon = f2bAddon;
    const badge = !addon ? null
      : addon.busy ? <span className="badge warn">{addon.busy_action === 'uninstall' ? tr("Removing") : tr("Installing")}</span>
        : !addon.installed ? <span className="badge">{tr("Not installed")}</span>
          : addon.running ? <span className="badge ok">{tr("Running")}</span>
            : <span className="badge warn">{tr("Stopped")}</span>;
    return <section className="section firewall-fail2ban">
      <div className="section-title">
        <div><h2 className="firewall-fail2ban-title">Fail2ban {badge}</h2>
          <p className="hint">{tr("Bans an address at the firewall after repeated failed logins.")}</p></div>
        <div className="actions">
          {addon?.installed && !addon.busy && (addon.running
            ? <button className="secondary-light" disabled={!!loading} onClick={() => setF2bRunning(false)}><Square size={14}/> {tr("Stop")}</button>
            : <button className="secondary" disabled={!!loading} onClick={() => setF2bRunning(true)}><Play size={14}/> {tr("Start")}</button>)}
          <button className="secondary" disabled={!!loading} onClick={() => loadF2bAddon()}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
      </div>
      {addon === null && <p className="hint">{tr("Loading…")}</p>}
      {addon && !addon.installed && !addon.busy && <div className="info-box firewall-fail2ban-missing">
        <AlertCircle size={14}/> <span>{tr("Fail2ban is not installed. Install it on the Addons page to ban addresses that keep failing to sign in.")}</span>
        <button type="button" className="mini" onClick={() => navigateToPage('addons')}><PackageOpen size={13}/> {tr("Open Addons")}</button>
      </div>}
      {addon?.last_error && <p className="hint addon-error"><AlertCircle size={13}/> {addon.last_error}</p>}
      {addon?.installed && !addon.running && <p className="hint">{tr("Fail2ban is stopped: nothing is being banned. Start it to protect SSH and the panel login.")}</p>}
      {addon?.installed && renderAddonFail2ban(addon)}
    </section>;
  }

  function renderAddonFail2ban(addon) {
    const draft = f2bDraft || {};
    const dirty = f2bSettings && JSON.stringify(draft) !== JSON.stringify(f2bSettings);
    const setDraft = (patch) => setF2bDraft(prev => ({ ...(prev || f2bSettings || {}), ...patch }));
    return <>
      <div className="addon-panel">
        <div className="addon-panel-head">
          <strong>{tr("Ban rules")}</strong>
          <span className="hint">{tr("Applied to both jails.")}</span>
        </div>
        <div className="firewall-form addon-form">
          <label>
            <span>{tr("Failures before a ban")}</span>
            <input type="number" min="1" max="100" value={draft.maxretry ?? 5}
              onChange={e => setDraft({ maxretry: Number(e.target.value) })} />
          </label>
          <label>
            <span>{tr("Counted within (seconds)")}</span>
            <input type="number" min="10" value={draft.findtime ?? 600}
              onChange={e => setDraft({ findtime: Number(e.target.value) })} />
          </label>
          <label>
            <span>{tr("Ban lasts (seconds)")}</span>
            <input type="number" min="-1" value={draft.bantime ?? 3600}
              onChange={e => setDraft({ bantime: Number(e.target.value) })} />
          </label>
          <label>
            <span>{tr("Watch SSH")}</span>
            <select value={draft.jail_sshd ? 'on' : 'off'} onChange={e => setDraft({ jail_sshd: e.target.value === 'on' })}>
              <option value="on">{tr("On")}</option><option value="off">{tr("Off")}</option>
            </select>
          </label>
          <label>
            <span>{tr("Watch panel logins")}</span>
            <select value={draft.jail_panel ? 'on' : 'off'} onChange={e => setDraft({ jail_panel: e.target.value === 'on' })}>
              <option value="on">{tr("On")}</option><option value="off">{tr("Off")}</option>
            </select>
          </label>
          <label className="addon-form-wide">
            <span>{tr("Never ban")}</span>
            <input type="text" placeholder="203.0.113.7 198.51.100.0/24" value={draft.ignoreip ?? ''}
              onChange={e => setDraft({ ignoreip: e.target.value })} />
          </label>
        </div>
        <p className="hint">{tr("Ban lasts")} <code>-1</code> {tr("to ban permanently. Loopback is always exempt. Put your own address in Never ban so a wrong rule cannot lock you out of the panel.")}</p>
        <div className="actions">
          <button disabled={!!loading || !dirty} onClick={saveFail2banSettings}><Save size={14}/> {tr("Apply settings")}</button>
          {dirty && <button className="secondary-light" disabled={!!loading}
            onClick={() => setF2bDraft(f2bSettings)}><RotateCcw size={14}/> {tr("Discard changes")}</button>}
        </div>
      </div>

      <div className="addon-panel">
        <div className="addon-panel-head">
          <strong>{tr("Banned right now")}</strong>
          <button className="secondary-light" disabled={!!loading} onClick={() => loadFail2ban()}><RefreshCw size={13}/> {tr("Refresh")}</button>
        </div>
        {f2bBanned.length === 0
          ? <p className="hint">{tr("Nothing is banned.")}</p>
          : <table className="table addon-ban-table"><thead><tr><th>{tr("Address")}</th><th>{tr("Jail")}</th><th></th></tr></thead>
              <tbody>{f2bBanned.map(entry => <tr key={`${entry.jail}-${entry.address}`}>
                <td><code>{entry.address}</code></td>
                <td>{entry.jail}</td>
                <td className="row-actions"><button className="secondary-light" disabled={!!loading}
                  onClick={() => unbanAddress(entry.address)}><Check size={13}/> {tr("Unban")}</button></td>
              </tr>)}</tbody></table>}
      </div>

      {f2bLog && <div className="addon-panel">
        <div className="addon-panel-head"><strong>{tr("Recent activity")}</strong></div>
        <pre className="addon-log">{f2bLog}</pre>
      </div>}
    </>;
  }

  function mcpClientSnippets(token) {
    const bearer = `Bearer ${token || '<your-token>'}`;
    return [
      ['Claude Code', `claude mcp add --transport http opanel ${mcpEndpoint} --header "Authorization: ${bearer}"`],
      ['Cursor (~/.cursor/mcp.json)', JSON.stringify({ mcpServers: { opanel: { url: mcpEndpoint, headers: { Authorization: bearer } } } }, null, 2)],
      ['VS Code (.vscode/mcp.json)', JSON.stringify({ servers: { opanel: { type: 'http', url: mcpEndpoint, headers: { Authorization: bearer } } } }, null, 2)],
    ];
  }

  function renderMcpTokenRows(tokens, { showOwner = false, fromAddonPanel = false } = {}) {
    const now = Date.now();
    return <div className="table">
      {tokens.map(t => {
        const expired = t.expires_at && new Date(t.expires_at).getTime() <= now;
        return <div className="row" key={t.id}>
          <div className="token-info">
            <strong>{t.name}{showOwner && t.username ? ` — ${t.username}` : ''}</strong>
            <small>{tr("Prefix:")} {t.prefix}{tr("… | Created:")} {t.created_at ? new Date(t.created_at).toLocaleDateString() : '—'}
              {' | '}{tr("Expires:")} {t.expires_at ? new Date(t.expires_at).toLocaleDateString() : tr("never")}
              {' | '}{tr("Last used:")} {t.last_used_at ? new Date(t.last_used_at).toLocaleString() : tr("never")}</small>
          </div>
          {expired
            ? <span className="badge warn">{tr("Expired")}</span>
            : <span className={t.can_write ? 'badge warn' : 'badge ok'}>{t.can_write ? tr("Read + actions") : tr("Read-only")}</span>}
          <button className="mini danger" disabled={!!loading} onClick={() => revokeMcpToken(t, fromAddonPanel)}><Trash2 size={14}/> {tr("Revoke")}</button>
        </div>;
      })}
    </div>;
  }

  // A label, the value in a code block of its own, and a copy button:
  // stacked on a phone, the button beside the label on a wider screen.
  function renderCopyBlock(label, text, { multiline = false, copiedMessage } = {}) {
    return <div className="copy-block">
      <div className="copy-block-head">
        <span>{label}</span>
        <button type="button" className="mini secondary" onClick={() => { copyToClipboard(text); setNotice(copiedMessage || tr("Copied to clipboard.")); }}><Copy size={13}/> {tr("Copy")}</button>
      </div>
      {multiline ? <pre className="copy-block-code">{text}</pre> : <code className="copy-block-code">{text}</code>}
    </div>;
  }

  function renderSftp() {
    const info = sftpInfo || {};
    const host = info.host || window.location.hostname;
    const port = info.port || 22;
    const accounts = info.accounts || [];
    const endUsers = users.filter(user => user.role !== 'admin');
    const ownerId = isAdmin ? Number(sftpForm.owner_id) || null : currentUser?.id;
    const owner = isAdmin ? endUsers.find(user => user.id === ownerId) : currentUser;
    const ownerSites = websites.filter(site => ownerId && site.owner_id === ownerId);
    const prefix = owner ? `${owner.username.toLowerCase()}_` : '';
    const canCreate = !!owner && /^[a-z0-9]{1,16}$/.test(sftpForm.suffix.trim().toLowerCase()) && sftpForm.password.length >= 12;
    const createOpen = showCreateSftp || (!isAdmin && sftpInfo && accounts.length === 0);
    return <>
      <section className="section">
        <div className="section-title">
          <div><h2>{tr("SFTP connection")}</h2>
            <p className="hint">{tr("Use FileZilla, WinSCP, Cyberduck or any SFTP client. SSH shells are not available; each login only sees its own folder.")}</p></div>
          <button className="secondary" disabled={!!loading} onClick={loadSftp}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        <div className="sftp-connect-grid">
          {renderCopyBlock(tr("Host"), host)}
          {renderCopyBlock(tr("Port"), String(port))}
          {info.primary && renderCopyBlock(tr("Username"), info.primary.username)}
        </div>
        {info.primary && <div className="info-box sftp-primary">
          <strong>{tr("Your main login")}</strong>
          <p className="hint">{tr("Password: the same as your panel password. It opens at / — your whole account, one folder per website (for example /{0}/public_html).", websites[0]?.domain || 'example.com')}</p>
          <div className="actions"><button className="mini secondary" onClick={openProfileModal}><KeyRound size={13}/> {tr("Change password")}</button></div>
        </div>}
        {info.primary && renderCopyBlock(tr("Command line"), `sftp -P ${port} ${info.primary.username}@${host}`)}
      </section>

      <section className="section">
        <div className="section-title">
          <div><h2>{tr("Extra SFTP accounts")}</h2>
            <p className="hint">{tr("A separate login and password for one folder — for a developer or designer who should see one website and nothing else. Up to {0} per account.", info.max_accounts || 10)}</p></div>
          <div className="actions">
            {!createOpen && <button type="button" onClick={() => setShowCreateSftp(true)}><Plus size={15}/> {tr("New SFTP account")}</button>}
          </div>
        </div>

        {createOpen && <div className="create-inline">
          <div className="create-inline-head">
            <strong>{tr("New SFTP account")}</strong>
            {(accounts.length > 0 || isAdmin) && <button type="button" className="secondary icon-only mini" onClick={() => setShowCreateSftp(false)} aria-label={tr("Close")} title={tr("Close")}><X size={15}/></button>}
          </div>
          <div className="sftp-create-grid">
            {isAdmin && <div className="field"><span className="field-label">{tr("Hosting account")}</span>
              <select value={sftpForm.owner_id} onChange={e => setSftpForm(prev => ({ ...prev, owner_id: e.target.value, website_id: '' }))}>
                <option value="">{tr("-- Select account --")}</option>
                {endUsers.map(user => <option key={user.id} value={user.id}>{user.username}</option>)}
              </select>
            </div>}
            <div className="field"><span className="field-label">{tr("Name")}</span>
              <div className="prefixed-input"><span>{prefix || '…_'}</span>
                <input value={sftpForm.suffix} maxLength={16} placeholder="dev" onChange={e => setSftpForm(prev => ({ ...prev, suffix: e.target.value.toLowerCase().replace(/[^a-z0-9]/g, '') }))} />
              </div>
            </div>
            <div className="field"><span className="field-label">{tr("Folder")}</span>
              <select value={sftpForm.website_id} disabled={!owner} onChange={e => setSftpForm(prev => ({ ...prev, website_id: e.target.value }))}>
                <option value="">{tr("Whole account")}</option>
                {ownerSites.map(site => <option key={site.id} value={site.id}>{site.domain}</option>)}
              </select>
            </div>
            <div className="field"><span className="field-label">{tr("Subfolder (optional)")}</span>
              <input value={sftpForm.subpath} placeholder={sftpForm.website_id ? 'public_html' : ''} onChange={e => setSftpForm(prev => ({ ...prev, subpath: e.target.value }))} />
            </div>
            <div className="field"><span className="field-label">{tr("Password")}</span>
              <div className="password-with-generate">
                <input value={sftpForm.password} placeholder={tr("Min 12 characters")} onChange={e => setSftpForm(prev => ({ ...prev, password: e.target.value }))} />
                <button type="button" className="secondary icon-only" title={tr("Generate random password")} aria-label={tr("Generate random password")} onClick={() => setSftpForm(prev => ({ ...prev, password: generateRandomPassword() }))}><Dices size={15}/></button>
              </div>
            </div>
            <button className="sftp-create-submit" disabled={!canCreate || !!loading} onClick={createSftpAccount}><Plus size={14}/> {tr("Create")}</button>
          </div>
          <p className="hint">{tr("Copy the password now — it is not shown again. Files uploaded through this login belong to the hosting account, so the website can use them.")}</p>
        </div>}

        {sftpInfo && accounts.length === 0 && !createOpen && <EmptyState icon={KeyRound} message={tr("No extra SFTP accounts yet.")} />}
        {accounts.length > 0 && <div className="table">
          {accounts.map(account => <div className="row sftp-row" key={account.id}>
            <span className="sftp-row-name"><strong>{account.username}</strong>{isAdmin && account.owner && <small>{tr("Account")}: {account.owner}</small>}</span>
            <span className="sftp-row-folder"><code>{account.directory}</code>{account.domain && <small>{account.domain}</small>}</span>
            <span className="row-actions">
              <button className="mini secondary" disabled={!!loading} onClick={() => setSftpPasswordFor({ account, password: generateRandomPassword() })}><KeyRound size={13}/> {tr("Change password")}</button>
              <button className="mini danger" disabled={!!loading} onClick={() => deleteSftpAccount(account)} aria-label={tr("Delete {0}", account.username)} title={tr("Delete")}><Trash2 size={13}/></button>
            </span>
          </div>)}
        </div>}
      </section>
    </>;
  }

  // --- Email page ---
  function mailDomainOptions(placeholder) {
    return <>
      {placeholder && <option value="">{placeholder}</option>}
      {(mailInfo?.domains || []).map(d => <option key={d.id} value={String(d.id)}>{d.domain}{isAdmin && d.owner ? ` (${d.owner})` : ''}</option>)}
    </>;
  }

  function renderMailFilter(onSearch) {
    return <div className="mail-filter">
      <select value={mailFilter.domain_id} aria-label={tr("Domain")} onChange={e => { setMailFilter(prev => ({ ...prev, domain_id: e.target.value })); setMailPage(1); }}>
        {mailDomainOptions(tr("All domains"))}
      </select>
      <form className="mail-search" onSubmit={e => { e.preventDefault(); setMailPage(1); onSearch(); }}>
        <input value={mailFilter.q} placeholder={tr("Search")} aria-label={tr("Search")} onChange={e => setMailFilter(prev => ({ ...prev, q: e.target.value }))} />
        <button type="submit" className="secondary icon-only" aria-label={tr("Search")} title={tr("Search")}><Search size={14}/></button>
      </form>
    </div>;
  }

  function renderMailPager(list) {
    const pages = Math.max(1, Math.ceil((list?.total || 0) / (list?.per_page || 50)));
    if (pages <= 1) return null;
    return <div className="firewall-ip-pager">
      <button className="mini secondary" disabled={mailPage <= 1} onClick={() => setMailPage(p => Math.max(1, p - 1))}>{tr("Previous")}</button>
      <span className="hint">{tr("Page {0} of {1}", mailPage, pages)}</span>
      <button className="mini secondary" disabled={mailPage >= pages} onClick={() => setMailPage(p => p + 1)}>{tr("Next")}</button>
    </div>;
  }

  function renderMailboxes() {
    const info = mailInfo;
    const list = mailboxList;
    const items = list?.items || [];
    const limit = Number(info.mailbox_limit) || 0;
    const atLimit = !isAdmin && limit > 0 && info.mailbox_count >= limit;
    const local = mailboxForm.local_part.trim().toLowerCase();
    const canCreate = !!mailboxForm.domain_id && /^[a-z0-9]([a-z0-9._+-]{0,62}[a-z0-9_+-])?$/.test(local) && !local.includes('..')
      && mailboxForm.password.length >= 8 && /[A-Za-z]/.test(mailboxForm.password) && /\d/.test(mailboxForm.password);
    const openCreate = () => {
      setShowCreateMailbox(true);
      setMailboxForm(prev => ({
        ...prev,
        domain_id: prev.domain_id || mailFilter.domain_id || String(info.domains[0]?.id || ''),
        password: prev.password || mailPassword(),
        quota_mb: prev.quota_mb === '' ? String(info.default_quota_mb || 1024) : prev.quota_mb,
      }));
    };
    return <div className="mail-tab">
      <div className="mail-toolbar">
        {renderMailFilter(() => loadMailboxes(1))}
        {!showCreateMailbox && <button type="button" disabled={atLimit} title={atLimit ? tr("Mailbox limit reached") : ''} onClick={openCreate}><Plus size={15}/> {tr("New mailbox")}</button>}
      </div>
      {atLimit && <p className="hint">{tr("You have used all {0} of your mailboxes. Delete one, or ask your provider for more.", limit)}</p>}
      {showCreateMailbox && <div className="create-inline">
        <div className="create-inline-head">
          <strong>{tr("New mailbox")}</strong>
          <button type="button" className="secondary icon-only mini" onClick={() => setShowCreateMailbox(false)} aria-label={tr("Close")} title={tr("Close")}><X size={15}/></button>
        </div>
        <div className="mail-create-grid">
          <div className="field mail-address-field"><span className="field-label">{tr("Address")}</span>
            <div className="mail-address-input">
              <input value={mailboxForm.local_part} placeholder="info" autoComplete="off" spellCheck={false} aria-label={tr("Mailbox name")}
                onChange={e => setMailboxForm(prev => ({ ...prev, local_part: e.target.value.toLowerCase().replace(/[^a-z0-9._+-]/g, '') }))} />
              <span>@</span>
              <select value={mailboxForm.domain_id} aria-label={tr("Domain")} onChange={e => setMailboxForm(prev => ({ ...prev, domain_id: e.target.value }))}>{mailDomainOptions(tr("Choose a domain"))}</select>
            </div>
          </div>
          <div className="field"><span className="field-label">{tr("Password")}</span>
            <div className="password-with-generate">
              <input value={mailboxForm.password} autoComplete="new-password" spellCheck={false} placeholder={tr("8+ characters, letters and digits")} onChange={e => setMailboxForm(prev => ({ ...prev, password: e.target.value }))} />
              <button type="button" className="secondary icon-only" title={tr("Generate random password")} aria-label={tr("Generate random password")} onClick={() => setMailboxForm(prev => ({ ...prev, password: mailPassword() }))}><Dices size={15}/></button>
              <button type="button" className="secondary icon-only" title={tr("Copy")} aria-label={tr("Copy")} onClick={() => { copyToClipboard(mailboxForm.password); setNotice(tr("Copied to clipboard.")); }}><Copy size={15}/></button>
            </div>
          </div>
          <div className="field mail-quota-field"><span className="field-label">{tr("Size (MB)")}{isAdmin && <em> {tr("0 = unlimited")}</em>}</span>
            <input type="number" min={isAdmin ? 0 : 1} max={isAdmin ? 1048576 : info.max_user_quota_mb} value={mailboxForm.quota_mb} onChange={e => setMailboxForm(prev => ({ ...prev, quota_mb: e.target.value }))} />
          </div>
          <button disabled={!canCreate || !!loading} onClick={createMailbox}><Plus size={14}/> {tr("Create")}</button>
        </div>
        <p className="hint">{tr("Copy the password now — it is not shown again. It signs in to webmail and to any mail app, with the full address as the username.")}</p>
      </div>}
      {list === null && <p className="hint">{tr("Loading…")}</p>}
      {list && items.length === 0 && <EmptyState icon={Inbox} message={mailFilter.q || mailFilter.domain_id ? tr("No mailbox matches.") : tr("No mailboxes yet.")} />}
      {items.length > 0 && <div className="table">
        {items.map(box => {
          const percent = box.quota_mb && box.used_mb != null ? Math.min(100, Math.round(box.used_mb / box.quota_mb * 100)) : null;
          return <div className="row mail-row" key={box.id}>
            <span className="mail-row-name">
              <strong>{box.address}</strong>
              <small>
                {!box.enabled && <span className="badge warn">{tr("Suspended")}</span>}
                {isAdmin && box.owner ? <span>{tr("Account")}: {box.owner}</span> : null}
              </small>
            </span>
            <span className="mail-row-usage">
              <small>{box.used_mb != null ? tr("{0} MB", box.used_mb) : '—'} / {box.quota_mb ? tr("{0} MB", box.quota_mb) : tr("unlimited")}</small>
              {percent != null && <span className={`mail-meter ${percent >= 90 ? 'bad' : percent >= 75 ? 'warn' : ''}`}><span style={{ width: `${percent}%` }} /></span>}
            </span>
            <span className="row-actions">
              <button className="mini" disabled={!!loading || !box.enabled} onClick={() => openWebmail(box)} title={tr("Open this mailbox in webmail, no password needed")}><Mail size={13}/> {tr("Webmail")}</button>
              <button className="mini secondary" disabled={!!loading} onClick={() => setMailboxEdit({ box, password: '', quota_mb: String(box.quota_mb) })}><Pencil size={13}/> {tr("Edit")}</button>
              <button className="mini secondary" disabled={!!loading} onClick={() => setMailboxEnabled(box, !box.enabled)}>{box.enabled ? <><Ban size={13}/> {tr("Suspend")}</> : <><Play size={13}/> {tr("Resume")}</>}</button>
              <button className="mini danger" disabled={!!loading} onClick={() => deleteMailbox(box)} aria-label={tr("Delete {0}", box.address)} title={tr("Delete")}><Trash2 size={13}/></button>
            </span>
          </div>;
        })}
      </div>}
      {renderMailPager(list)}
    </div>;
  }

  function renderForwarders() {
    const info = mailInfo;
    const list = forwarderList;
    const items = list?.items || [];
    const local = forwarderForm.local_part.trim().toLowerCase();
    const canCreate = !!forwarderForm.domain_id && /^[a-z0-9]([a-z0-9._+-]{0,62}[a-z0-9_+-])?$/.test(local) && splitAddresses(forwarderForm.destinations).length > 0;
    return <div className="mail-tab">
      <div className="mail-toolbar">
        {renderMailFilter(() => loadForwarders(1))}
        {!showCreateForwarder && <button type="button" onClick={() => { setShowCreateForwarder(true); setForwarderForm(prev => ({ ...prev, domain_id: prev.domain_id || mailFilter.domain_id || String(info.domains[0]?.id || '') })); }}><Plus size={15}/> {tr("New forwarder")}</button>}
      </div>
      {showCreateForwarder && <div className="create-inline">
        <div className="create-inline-head">
          <strong>{tr("New forwarder")}</strong>
          <button type="button" className="secondary icon-only mini" onClick={() => setShowCreateForwarder(false)} aria-label={tr("Close")} title={tr("Close")}><X size={15}/></button>
        </div>
        <div className="mail-create-grid">
          <div className="field mail-address-field"><span className="field-label">{tr("Address")}</span>
            <div className="mail-address-input">
              <input value={forwarderForm.local_part} placeholder="sales" autoComplete="off" spellCheck={false} aria-label={tr("Forwarder name")}
                onChange={e => setForwarderForm(prev => ({ ...prev, local_part: e.target.value.toLowerCase().replace(/[^a-z0-9._+-]/g, '') }))} />
              <span>@</span>
              <select value={forwarderForm.domain_id} aria-label={tr("Domain")} onChange={e => setForwarderForm(prev => ({ ...prev, domain_id: e.target.value }))}>{mailDomainOptions(tr("Choose a domain"))}</select>
            </div>
          </div>
          <div className="field mail-destinations-field"><span className="field-label">{tr("Forward to")}</span>
            <textarea rows={2} value={forwarderForm.destinations} spellCheck={false} placeholder={tr("one or more addresses, separated by commas")} onChange={e => setForwarderForm(prev => ({ ...prev, destinations: e.target.value }))} />
          </div>
          <button disabled={!canCreate || !!loading} onClick={createForwarder}><Plus size={14}/> {tr("Create")}</button>
        </div>
        <p className="hint">{tr("If a mailbox has the same address, it keeps a copy of each message as well.")}</p>
      </div>}
      {list === null && <p className="hint">{tr("Loading…")}</p>}
      {list && items.length === 0 && <EmptyState icon={Forward} message={mailFilter.q || mailFilter.domain_id ? tr("No forwarder matches.") : tr("No forwarders yet.")} />}
      {items.length > 0 && <div className="table">
        {items.map(item => <div className="row mail-row" key={item.id}>
          <span className="mail-row-name"><strong>{item.address}</strong>{item.keeps_copy && <small><span className="badge">{tr("Keeps a copy")}</span></small>}</span>
          <span className="mail-row-destinations"><MoveRight size={13}/> <span>{item.destinations.join(', ')}</span></span>
          <span className="row-actions">
            <button className="mini secondary" disabled={!!loading} onClick={() => setForwarderEdit({ item, destinations: item.destinations.join(', ') })}><Pencil size={13}/> {tr("Edit")}</button>
            <button className="mini danger" disabled={!!loading} onClick={() => deleteForwarder(item)} aria-label={tr("Delete {0}", item.address)} title={tr("Delete")}><Trash2 size={13}/></button>
          </span>
        </div>)}
      </div>}
      {renderMailPager(list)}
    </div>;
  }

  function renderMailDomains() {
    const info = mailInfo;
    const domains = info.domains || [];
    const candidates = info.candidates || [];
    return <div className="mail-tab">
      <div className="create-inline">
        <div className="create-inline-head"><strong>{tr("Turn on email for a domain")}</strong></div>
        <div className="mail-create-grid">
          {isAdmin
            ? <div className="field"><span className="field-label">{tr("Domain")}</span>
                <input list="mail-domain-candidates" value={mailDomainForm.domain} placeholder="example.com" spellCheck={false}
                  onChange={e => setMailDomainForm(prev => ({ ...prev, domain: e.target.value.trim().toLowerCase() }))} />
                <datalist id="mail-domain-candidates">{candidates.map(name => <option key={name} value={name} />)}</datalist>
              </div>
            : <div className="field"><span className="field-label">{tr("Domain")}</span>
                <select value={mailDomainForm.domain} onChange={e => setMailDomainForm(prev => ({ ...prev, domain: e.target.value }))}>
                  <option value="">{tr("Choose one of your websites")}</option>
                  {candidates.map(name => <option key={name} value={name}>{name}</option>)}
                </select>
              </div>}
          {isAdmin && <div className="field"><span className="field-label">{tr("Account")}</span>
            <select value={mailDomainForm.owner_id} onChange={e => setMailDomainForm(prev => ({ ...prev, owner_id: e.target.value }))}>
              <option value="">{tr("The website's owner")}</option>
              {users.map(user => <option key={user.id} value={user.id}>{user.username}</option>)}
            </select>
          </div>}
          <button disabled={!mailDomainForm.domain.trim() || !!loading} onClick={addMailDomain}><Plus size={14}/> {tr("Turn on email")}</button>
        </div>
        {!isAdmin && candidates.length === 0 && <p className="hint">{tr("Every domain of your websites already has email. Add a website or a domain alias to use another one.")}</p>}
        <p className="hint">{tr("Mail for a domain arrives here once its MX record points at {0}; the DNS records to add are shown next.", info.hostname || '—')}</p>
      </div>
      {domains.length === 0 && <EmptyState icon={Mail} message={tr("No mail domains yet.")} />}
      {domains.length > 0 && <div className="table">
        {domains.map(d => {
          const draft = String(catchAllDraft[d.id] ?? d.catch_all ?? '');
          return <div className="row mail-domain-row" key={d.id}>
            <span className="mail-row-name">
              <strong>{d.domain}</strong>
              <small>{tr("{0} mailboxes · {1} forwarders", d.mailboxes, d.forwarders)}{isAdmin && d.owner ? ` · ${tr("Account")}: ${d.owner}` : ''}</small>
            </span>
            <span className="mail-catchall">
              <span className="field-label">{tr("Catch-all")}</span>
              <span className="mail-catchall-input">
                <input value={draft} placeholder={tr("Off: unknown addresses are refused")} spellCheck={false} aria-label={tr("Catch-all for {0}", d.domain)}
                  onChange={e => setCatchAllDraft(prev => ({ ...prev, [d.id]: e.target.value }))} />
                {draft.trim() !== (d.catch_all || '') && <button className="mini" disabled={!!loading} onClick={() => saveCatchAll(d)} aria-label={tr("Save")} title={tr("Save")}><Save size={13}/></button>}
              </span>
            </span>
            <label className="check-line mail-webmail-host" title={tr("Serve webmail at webmail.{0} with its own certificate", d.domain)}>
              <input type="checkbox" checked={!!d.webmail_host} disabled={!!loading} onChange={e => toggleWebmailHost(d, e.target.checked)} />
              <span>webmail.{d.domain}</span>
            </label>
            <span className="row-actions">
              <button className="mini secondary" disabled={!!loading} onClick={() => openMailDns(d)}><Globe size={13}/> {tr("DNS records")}</button>
              <button className="mini danger" disabled={!!loading} onClick={() => deleteMailDomain(d)} aria-label={tr("Delete {0}", d.domain)} title={tr("Delete")}><Trash2 size={13}/></button>
            </span>
          </div>;
        })}
      </div>}
    </div>;
  }

  function renderDnsRecordEditor(rows, onChange, { domain = '', template = false } = {}) {
    const setRow = (index, patch) => onChange(rows.map((row, i) => i === index ? { ...row, ...patch } : row));
    return <div className="dns-editor">
      {rows.map((row, index) => <div className={`dns-editor-row${row.type === 'MX' ? ' with-priority' : ''}`} key={index}>
        <select value={row.type} aria-label={tr("Type")} onChange={e => setRow(index, { type: e.target.value })}>
          {['TXT', 'CNAME', 'MX', 'A', 'AAAA'].map(type => <option key={type} value={type}>{type}</option>)}
        </select>
        <input value={row.name} placeholder="@" aria-label={tr("Name")} spellCheck={false} onChange={e => setRow(index, { name: e.target.value })} />
        {row.type === 'MX' && <input type="number" min="0" max="65535" value={row.priority ?? ''} placeholder="10" aria-label={tr("Priority")} onChange={e => setRow(index, { priority: e.target.value })} />}
        <input className="dns-editor-value" value={row.value} placeholder={template ? tr("Value ({domain} = the domain)") : tr("Value")} aria-label={tr("Value")} spellCheck={false} onChange={e => setRow(index, { value: e.target.value })} />
        <button type="button" className="mini danger-light icon-only" aria-label={tr("Remove")} title={tr("Remove")} onClick={() => onChange(rows.filter((_, i) => i !== index))}><Trash2 size={13}/></button>
      </div>)}
      <div className="actions dns-editor-actions">
        <button type="button" className="mini secondary" disabled={rows.length >= (template ? 10 : 20)} onClick={() => onChange([...rows, { type: 'TXT', name: '@', value: '', priority: '' }])}><Plus size={13}/> {tr("Add a record")}</button>
      </div>
      <p className="hint">{template
        ? tr("Names are relative to each domain that uses the relay: @ is the domain itself, brevo1._domainkey a name under it. {domain} in a value becomes the domain name.")
        : tr("Names are relative to {0}: @ is the domain itself.", domain)}</p>
    </div>;
  }

  function renderMailDns() {
    const { domain, records, relay, canCustomize } = mailDns;
    const form = dnsCustomForm;
    const statusLabel = { ok: tr("Found"), missing: tr("Missing"), different: tr("Different"), unknown: tr("Not checked") };
    const statusClass = { ok: 'ok', missing: 'bad', different: 'warn', unknown: '' };
    const titles = {
      mx: tr("Receiving mail (MX)"),
      spf: tr("Allowed senders (SPF)"),
      dkim: tr("Signature key (DKIM)"),
      dmarc: tr("Policy (DMARC)"),
      webmail: tr("Webmail address (optional)"),
    };
    const titleFor = record => titles[record.key]
      || (record.source === 'relay' ? tr("Asked for by the relay {0}", record.relay) : tr("Extra record"));
    const suggested = key => (records || []).find(r => r.key === key)?.suggested || '';
    return <section className="section mail-dns-page">
      <div className="section-title">
        <div className="waf-detail-title">
          <button className="secondary" onClick={() => setMailDns(null)}><ArrowLeft size={14}/> {tr("Email")}</button>
          <div><h2>{tr("DNS records for {0}", domain.domain)}</h2>
            <p className="hint">{tr("Add these at the DNS provider of {0}. A change can take a few hours to be seen everywhere.", domain.domain)}</p></div>
        </div>
        <div className="actions">
          <button className="secondary" disabled={!!loading || records === null} onClick={() => openMailDns(domain)}><RefreshCw size={14}/> {tr("Check again")}</button>
          <button className="secondary-light" disabled={!!loading} onClick={() => rotateMailDkim(domain)}><KeyRound size={14}/> {tr("New DKIM key")}</button>
        </div>
      </div>
      {relay && <div className="mail-relay-card">
        <div className="mail-relay-card-head"><Send size={15}/><strong>{tr("Outgoing mail")}</strong></div>
        {isAdmin && <select value={relay.choice} disabled={!!loading} aria-label={tr("Outgoing mail")} onChange={e => saveDomainRelay(e.target.value)}>
          <option value="">{tr("Server default")}</option>
          <option value="direct">{tr("Direct, without a relay")}</option>
          {relay.options.map(option => <option key={option.id} value={option.id}>{tr("Relay: {0}", option.name)}</option>)}
        </select>}
        <p className="hint">{relay.effective_name
          ? tr("Mail from {0} leaves through the relay {1}; the records it asks for are listed below.", domain.domain, relay.effective_name)
          : tr("Mail from {0} is delivered directly from this server.", domain.domain)}</p>
      </div>}
      {mailDns.dnsZone && <div className="info-box dns-managed-box">
        <Network size={14}/>
        <span>{tr("DNS Manager on this server holds the zone {0} and keeps these records in it, taking back the ones email no longer needs. \"In the zone\" is what the zone holds; the other badge is what public DNS answers, which matches once the domain's nameservers point here.", mailDns.dnsZone.name)}</span>
        <button type="button" className="mini secondary" onClick={() => openDnsZone(mailDns.dnsZone)}>{tr("Open zone")}</button>
      </div>}
      {records === null && <p className="hint">{tr("Checking DNS…")}</p>}
      {records && <div className="mail-dns-list">
        {records.map(record => <div className="mail-dns-record" key={record.key}>
          <div className="mail-dns-head">
            <strong>{titleFor(record)}</strong>
            <span className="mail-dns-type"><code>{record.type}</code>{record.priority != null && <small>{tr("priority {0}", record.priority)}</small>}</span>
            {isAdmin && (record.custom || record.source === 'custom') && <span className="badge">{tr("Custom")}</span>}
            {mailDns.dnsZone && record.in_zone != null && <span className={`badge mail-dns-zone ${record.in_zone ? 'ok' : 'warn'}`}
              title={tr("DNS Manager's zone on this server")}>{record.in_zone ? tr("In the zone") : tr("Not in the zone")}</span>}
            <span className={`badge mail-dns-status ${statusClass[record.status] || ''}`} title={tr("What public DNS answers now")}>{statusLabel[record.status] || record.status}</span>
          </div>
          {renderCopyBlock(tr("Name"), record.name)}
          {renderCopyBlock(tr("Value"), record.value, { multiline: record.key === 'dkim' })}
          {record.status === 'different' && (record.found || []).length > 0 && <p className="hint">{tr("Found now:")} <code>{record.found.join(' | ')}</code></p>}
          {record.key === 'webmail' && <p className="hint">{tr("Only needed for webmail.{0}; turn that on in the Domains tab once this record is in place.", domain.domain)}</p>}
        </div>)}
      </div>}
      {records && form && canCustomize && <div className="create-inline mail-dns-custom">
        <div className="create-inline-head"><strong>{tr("Customize the mail records")}</strong></div>
        <p className="hint">{tr("For this domain only: its own SPF and DMARC, and records another service asks for. Records every domain on a relay needs belong in that relay's DNS template (Relays tab). Customers see these records and publish them; they cannot change them.")}</p>
        <label className="field"><span className="field-label">SPF</span>
          <input value={form.spf} placeholder={suggested('spf')} spellCheck={false} onChange={e => setDnsCustomForm(prev => ({ ...prev, spf: e.target.value }))} /></label>
        <label className="field"><span className="field-label">DMARC</span>
          <input value={form.dmarc} placeholder={suggested('dmarc')} spellCheck={false} onChange={e => setDnsCustomForm(prev => ({ ...prev, dmarc: e.target.value }))} /></label>
        <div className="field"><span className="field-label">{tr("Extra records")}</span>
          {renderDnsRecordEditor(form.records, rows => setDnsCustomForm(prev => ({ ...prev, records: rows })), { domain: domain.domain })}</div>
        <div className="actions"><button type="button" disabled={!!loading} onClick={saveDnsCustom}><Save size={14}/> {tr("Save records")}</button></div>
      </div>}
    </section>;
  }

  function relayTlsLabel(tls) {
    return { starttls: 'STARTTLS', ssl: 'SSL/TLS', none: tr("no TLS") }[tls] || tls;
  }

  function renderRelayForm() {
    const f = relayForm;
    const set = patch => setRelayForm(prev => ({ ...prev, ...patch }));
    const canSave = f.name.trim() && f.host.trim() && (!f.username.trim() || f.password || f.password_set);
    return <div className="mail-tab">
      <div className="create-inline mail-relay-form">
        <div className="create-inline-head">
          <strong>{f.id ? tr("Edit relay {0}", f.name) : tr("New relay")}</strong>
          <button type="button" className="secondary icon-only mini" onClick={() => setRelayForm(null)} aria-label={tr("Close")} title={tr("Close")}><X size={15}/></button>
        </div>
        <div className="mail-settings-grid">
          <label className="field"><span className="field-label">{tr("Name")}</span><input value={f.name} placeholder="Brevo" onChange={e => set({ name: e.target.value })} /></label>
          <label className="field"><span className="field-label">{tr("Relay host")}</span><input value={f.host} placeholder="smtp-relay.brevo.com" spellCheck={false} onChange={e => set({ host: e.target.value })} /></label>
          <label className="field"><span className="field-label">{tr("Port")}</span><input type="number" min="1" max="65535" value={f.port}
            onChange={e => set({ port: e.target.value, ...(e.target.value === '465' ? { tls: 'ssl' } : f.tls === 'ssl' ? { tls: 'starttls' } : {}) })} /></label>
          <label className="field"><span className="field-label">TLS</span>
            <select value={f.tls} onChange={e => set({ tls: e.target.value })}>
              <option value="starttls">STARTTLS</option>
              <option value="ssl">SSL/TLS</option>
              <option value="none">{tr("None (private network only)")}</option>
            </select></label>
          <label className="field"><span className="field-label">{tr("Username")}</span><input value={f.username} autoComplete="off" spellCheck={false} onChange={e => set({ username: e.target.value })} /></label>
          <label className="field"><span className="field-label">{tr("Password")}</span><input type="password" value={f.password} autoComplete="new-password"
            placeholder={f.password_set ? tr("Saved — leave empty to keep") : ''} onChange={e => set({ password: e.target.value })} /></label>
        </div>
        <div className="mail-relay-template">
          <strong>{tr("Mail DNS template")}</strong>
          <p className="hint">{tr("What every domain that sends through this relay must publish. Customers see it on their domain's DNS records page and set up their domain from it.")}</p>
          <label className="field"><span className="field-label">{tr("SPF for this relay")} <em>{tr("added to the SPF record of every domain that uses it")}</em></span>
            <input value={f.spf_include} placeholder="include:spf.brevo.com" spellCheck={false} onChange={e => set({ spf_include: e.target.value })} /></label>
          <div className="field"><span className="field-label">{tr("DNS records the relay asks for")}</span>
            {renderDnsRecordEditor(f.dns_records, rows => set({ dns_records: rows }), { template: true })}</div>
        </div>
        {!f.id && <label className="check-line"><input type="checkbox" checked={!!f.make_default} onChange={e => set({ make_default: e.target.checked })} /> {tr("Use it as the default relay")}</label>}
        <p className="hint">{tr("587 uses STARTTLS and 465 SSL/TLS, and the relay's certificate must be valid. Leave the username empty for a relay that knows this server by its address.")}</p>
        <div className="actions">
          <button type="button" className="secondary-light" onClick={() => setRelayForm(null)}>{tr("Cancel")}</button>
          <button type="button" disabled={!canSave || !!loading} onClick={saveRelay}><Save size={14}/> {tr("Save relay")}</button>
        </div>
      </div>
    </div>;
  }

  function renderMailRelays() {
    if (relayForm) return renderRelayForm();
    const data = mailRelays;
    const relays = data?.relays || [];
    return <div className="mail-tab">
      <p className="hint">{tr("A relay (smarthost) sends this server's outgoing mail for it: needed where the provider blocks port 25, and it can help mail reach the inbox. Each domain uses the default relay unless its DNS page picks another one or direct delivery.")}</p>
      <div className="mail-toolbar">
        <label className="field mail-default-relay"><span className="field-label">{tr("Default relay")}</span>
          <select value={data?.default_relay || ''} disabled={!data || !!loading} onChange={e => setDefaultRelay(e.target.value)}>
            <option value="">{tr("None: deliver directly")}</option>
            {relays.map(relay => <option key={relay.id} value={relay.id}>{relay.name}</option>)}
          </select></label>
        <button type="button" onClick={() => editRelay(null)}><Plus size={15}/> {tr("New relay")}</button>
      </div>
      {data === null && <p className="hint">{tr("Loading…")}</p>}
      {data && relays.length === 0 && <EmptyState icon={Send} message={tr("No relay yet: mail leaves this server directly.")} />}
      {relays.length > 0 && <div className="table">
        {relays.map(relay => <div className="row mail-relay-row" key={relay.id}>
          <span className="mail-row-name">
            <strong>{relay.name}{relay.default && <span className="badge ok">{tr("Default")}</span>}</strong>
            <small>{relay.host}:{relay.port} · {relayTlsLabel(relay.tls)}{relay.username ? ` · ${relay.username}` : ` · ${tr("no login")}`}</small>
          </span>
          <span className="mail-relay-meta">
            {relay.spf_include && <small><code>{relay.spf_include}</code></small>}
            {(relay.dns_records || []).length > 0 && <small>{tr("{0} DNS records", relay.dns_records.length)}</small>}
            {(relay.domains || []).length > 0 && <small>{tr("Chosen by {0}", relay.domains.join(', '))}</small>}
          </span>
          <span className="row-actions">
            <button className="mini secondary" disabled={!!loading} onClick={() => editRelay(relay)}><Pencil size={13}/> {tr("Edit")}</button>
            <button className="mini danger" disabled={!!loading} onClick={() => deleteRelay(relay)} aria-label={tr("Delete {0}", relay.name)} title={tr("Delete")}><Trash2 size={13}/></button>
          </span>
        </div>)}
      </div>}
    </div>;
  }

  function rspamdActionLabel(action) {
    return {
      'no action': tr("Delivered"),
      'add header': tr("Marked as spam"),
      'rewrite subject': tr("Subject marked"),
      greylist: tr("Greylisted"),
      'soft reject': tr("Deferred"),
      reject: tr("Rejected"),
    }[action] || action;
  }

  function renderLogViewer(lines, query, setQuery, reload) {
    return <>
      <div className="mail-toolbar mail-log-toolbar">
        <form className="mail-search" onSubmit={e => { e.preventDefault(); reload(query); }}>
          <input value={query.q} placeholder={tr("Filter, e.g. an address or a message ID")} aria-label={tr("Filter")} onChange={e => setQuery(prev => ({ ...prev, q: e.target.value }))} />
          <button type="submit" className="secondary icon-only" aria-label={tr("Search")} title={tr("Search")}><Search size={14}/></button>
        </form>
        <select value={query.lines} aria-label={tr("Lines")} onChange={e => { const next = { ...query, lines: Number(e.target.value) }; setQuery(next); reload(next); }}>
          {[200, 500, 1000, 3000].map(n => <option key={n} value={n}>{tr("Last {0} lines", n)}</option>)}
        </select>
        <button type="button" className="secondary" disabled={!!loading} onClick={() => reload(query)}><RefreshCw size={14}/> {tr("Refresh")}</button>
      </div>
      {lines === null ? <p className="hint">{tr("Loading…")}</p>
        : lines.length ? <pre className="mail-log">{lines.join('\n')}</pre>
          : <p className="hint">{query.q ? tr("No line matches.") : tr("The log is empty.")}</p>}
    </>;
  }

  function renderMailRspamd() {
    const stat = rspamdStat;
    const list = rspamdHistory;
    const pages = Math.max(1, Math.ceil((list?.total || 0) / (list?.per_page || 50)));
    const actionClass = { reject: 'bad', 'soft reject': 'warn', greylist: 'warn', 'add header': 'warn', 'rewrite subject': 'warn', 'no action': 'ok' };
    const cards = [[tr("Scanned"), stat?.scanned], [tr("Spam"), stat?.spam], [tr("Ham"), stat?.ham], [tr("Learned"), stat?.learned]];
    return <div className="mail-tab">
      <div className="mail-stat-grid">
        {cards.map(([label, value]) => <div className="mail-stat" key={label}><small>{label}</small><strong>{value ?? '—'}</strong></div>)}
        {stat && Object.entries(stat.actions || {}).filter(([, count]) => count > 0).map(([action, count]) =>
          <div className="mail-stat" key={action}><small>{rspamdActionLabel(action)}</small><strong>{count}</strong></div>)}
      </div>
      <div className="segmented-control" role="tablist" aria-label="Rspamd">
        {[['history', tr("Scan history")], ['log', tr("Log")]].map(([id, label]) => <button key={id} type="button" role="tab"
          aria-selected={rspamdView === id} className={rspamdView === id ? 'active' : ''} onClick={() => setRspamdView(id)}>{label}</button>)}
      </div>
      {rspamdView === 'history' && <>
        <div className="mail-toolbar">
          <form className="mail-search" onSubmit={e => { e.preventDefault(); loadRspamdHistory(1); }}>
            <input value={rspamdFilter.q} placeholder={tr("Sender, recipient, subject or IP")} aria-label={tr("Search")} onChange={e => setRspamdFilter(prev => ({ ...prev, q: e.target.value }))} />
            <button type="submit" className="secondary icon-only" aria-label={tr("Search")} title={tr("Search")}><Search size={14}/></button>
          </form>
          <select value={rspamdFilter.action} aria-label={tr("Result")} onChange={e => loadRspamdHistory(1, e.target.value)}>
            <option value="">{tr("Every result")}</option>
            {['no action', 'add header', 'rewrite subject', 'greylist', 'soft reject', 'reject'].map(action => <option key={action} value={action}>{rspamdActionLabel(action)}</option>)}
          </select>
          <button type="button" className="secondary" disabled={!!loading} onClick={() => { loadRspamdStat(); loadRspamdHistory(rspamdFilter.page); }}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        {list === null && <p className="hint">{tr("Loading…")}</p>}
        {list && list.items.length === 0 && <EmptyState icon={ShieldCheck} message={rspamdFilter.q || rspamdFilter.action ? tr("No scanned message matches.") : tr("No message has been scanned yet.")} />}
        {list && list.items.length > 0 && <div className="table">
          {list.items.map((item, index) => <div className="row rspamd-row" key={`${item.time}-${index}`}>
            <span className="rspamd-when">
              <small>{item.time ? new Date(item.time).toLocaleString() : ''}</small>
              <span className={`badge ${actionClass[item.action] || ''}`}>{rspamdActionLabel(item.action)}</span>
            </span>
            <span className="mail-row-name">
              <strong title={item.subject}>{item.subject || tr("(no subject)")}</strong>
              <small>{item.from || '—'} → {item.to.join(', ') || '—'}</small>
              {item.ip && <small>{item.ip}{item.user ? ` · ${tr("signed in as {0}", item.user)}` : ''}</small>}
            </span>
            <span className="rspamd-score"><strong>{item.score}</strong><small>/ {item.required}</small></span>
            <span className="rspamd-symbols">{item.symbols.slice(0, 10).map(symbol =>
              <code key={symbol.name} className={symbol.score > 0 ? 'pos' : symbol.score < 0 ? 'neg' : ''} title={String(symbol.score)}>{symbol.name}{symbol.score ? ` ${symbol.score > 0 ? '+' : ''}${symbol.score}` : ''}</code>)}</span>
          </div>)}
        </div>}
        {pages > 1 && <div className="firewall-ip-pager">
          <button className="mini secondary" disabled={rspamdFilter.page <= 1} onClick={() => loadRspamdHistory(rspamdFilter.page - 1)}>{tr("Previous")}</button>
          <span className="hint">{tr("Page {0} of {1}", rspamdFilter.page, pages)}</span>
          <button className="mini secondary" disabled={rspamdFilter.page >= pages} onClick={() => loadRspamdHistory(rspamdFilter.page + 1)}>{tr("Next")}</button>
        </div>}
        <p className="hint">{tr("Rspamd keeps the last 200 scans. A message sent by a signed-in mailbox is not scanned.")}</p>
      </>}
      {rspamdView === 'log' && renderLogViewer(rspamdLog, rspamdLogQuery, setRspamdLogQuery, loadRspamdLog)}
    </div>;
  }

  function renderMailServer() {
    const f = mailSettingsForm;
    const set = patch => setMailSettingsForm(prev => ({ ...prev, ...patch }));
    return <div className="mail-tab">
      {!f ? <p className="hint">{tr("Loading…")}</p> : <div className="create-inline mail-settings">
        <div className="create-inline-head"><strong>{tr("Mail server")}</strong></div>
        <p className="hint">{tr("Server name: {0}. Messages waiting to be sent: {1}.", mailSettings?.hostname || '—', mailSettings?.queue ?? '—')}</p>
        <div className="mail-settings-grid">
          <label className="field"><span className="field-label">{tr("Recipients per mailbox per hour")} <em>{tr("0 = no limit")}</em></span><input type="number" min="0" value={f.auth_rate_per_hour ?? 300} onChange={e => set({ auth_rate_per_hour: e.target.value })} /></label>
          <label className="field"><span className="field-label">{tr("Messages per website account per hour")} <em>{tr("0 = no limit")}</em></span><input type="number" min="0" value={f.local_rate_per_hour ?? 300} onChange={e => set({ local_rate_per_hour: e.target.value })} /></label>
          <label className="field"><span className="field-label">{tr("Largest message (MB)")}</span><input type="number" min="1" max="200" value={f.max_message_mb ?? 50} onChange={e => set({ max_message_mb: e.target.value })} /></label>
          <label className="field"><span className="field-label">{tr("New mailbox size (MB)")}</span><input type="number" min="1" value={f.default_quota_mb ?? 1024} onChange={e => set({ default_quota_mb: e.target.value })} /></label>
          <label className="field"><span className="field-label">{tr("Spam score: move to Junk")}</span><input type="number" min="1" max="100" step="0.5" value={f.spam_header_score ?? 6} onChange={e => set({ spam_header_score: e.target.value })} /></label>
          <label className="field"><span className="field-label">{tr("Spam score: refuse")}</span><input type="number" min="1" max="100" step="0.5" value={f.spam_reject_score ?? 15} onChange={e => set({ spam_reject_score: e.target.value })} /></label>
        </div>
        <label className="check-line"><input type="checkbox" checked={!!f.greylisting} onChange={e => set({ greylisting: e.target.checked })} />
          {tr("Greylisting: doubtful senders are asked to retry a few minutes later")}</label>
        <div className="actions"><button type="button" disabled={!!loading} onClick={saveMailSettings}><Save size={14}/> {tr("Apply mail settings")}</button></div>
      </div>}
      <h3 className="mail-subhead">{tr("Exim log")}</h3>
      {renderLogViewer(eximLog, eximLogQuery, setEximLogQuery, loadEximLog)}
    </div>;
  }

  function renderMailClientHelp() {
    const client = mailInfo?.client || {};
    return <section className="section">
      <div className="section-title">
        <div><h2>{tr("Mail app settings")}</h2>
          <p className="hint">{tr("For Outlook, Thunderbird, Apple Mail or a phone. The username is the full email address, the password the mailbox's own.")}</p></div>
      </div>
      <div className="sftp-connect-grid">
        {renderCopyBlock(tr("Server (IMAP, POP3 and SMTP)"), client.host || '—')}
      </div>
      <ul className="mail-client-ports">
        <li><strong>IMAP</strong> {tr("port {0}, SSL/TLS", client.imap_port || 993)}</li>
        <li><strong>POP3</strong> {tr("port {0}, SSL/TLS", client.pop3_port || 995)}</li>
        <li><strong>SMTP</strong> {tr("port {0}, SSL/TLS — or {1} with STARTTLS", client.smtp_port || 465, client.submission_port || 587)}</li>
      </ul>
    </section>;
  }

  function dnsTtlLabel(seconds) {
    const n = Number(seconds) || 0;
    if (n >= 86400 && n % 86400 === 0) return tr("{0} d", n / 86400);
    if (n >= 3600 && n % 3600 === 0) return tr("{0} h", n / 3600);
    if (n >= 60 && n % 60 === 0) return tr("{0} min", n / 60);
    return tr("{0} s", n);
  }

  function dnsTypeHint(type) {
    const ip4 = dnsZone?.addresses?.ipv4?.[0] || dnsInfo?.addresses?.ipv4?.[0];
    const ip6 = dnsZone?.addresses?.ipv6?.[0] || dnsInfo?.addresses?.ipv6?.[0];
    return {
      A: ip4 ? tr("The IPv4 address the name points to. This server: {0}.", ip4) : tr("The IPv4 address the name points to."),
      AAAA: ip6 ? tr("The IPv6 address the name points to. This server: {0}.", ip6) : tr("The IPv6 address the name points to."),
      CNAME: tr("Makes the name an alias of another host. A name with a CNAME can have no other record."),
      MX: tr("A mail server for the domain; the lowest priority is tried first."),
      TXT: tr("Text such as SPF, DKIM or a site verification. A long value is split into strings for you."),
      NS: tr("Hands a subdomain to other nameservers."),
      SRV: tr("Where a service runs: weight port target, with the priority in its own field. The name is like _sip._tcp."),
      CAA: tr("Which certificate authorities may issue for the domain, for example issue letsencrypt.org."),
    }[type] || '';
  }

  function renderDnsRecordFields(form, set, { lockType = false } = {}) {
    const hasPriority = ['MX', 'SRV'].includes(form.type);
    const current = Number(form.ttl) || 0;
    const ttls = !current || DNS_TTLS.includes(current) ? DNS_TTLS : [...DNS_TTLS, current].sort((a, b) => a - b);
    return <div className={`dns-record-fields${hasPriority ? ' with-priority' : ''}`}>
      <label className="field"><span className="field-label">{tr("Type")}</span>
        <select value={form.type} disabled={lockType} onChange={e => set({ type: e.target.value })}>
          {DNS_TYPES.map(type => <option key={type} value={type}>{type}</option>)}
        </select></label>
      <label className="field"><span className="field-label">{tr("Name")}</span>
        <input value={form.name} placeholder="@" spellCheck={false} autoCapitalize="off" onChange={e => set({ name: e.target.value })} /></label>
      {hasPriority && <label className="field"><span className="field-label">{tr("Priority")}</span>
        <input type="number" min="0" max="65535" value={form.priority} placeholder="10" onChange={e => set({ priority: e.target.value })} /></label>}
      <label className="field dns-value-field"><span className="field-label">{tr("Value")}</span>
        {form.type === 'TXT'
          ? <textarea rows={2} value={form.value} placeholder={DNS_PLACEHOLDERS.TXT} spellCheck={false} onChange={e => set({ value: e.target.value.replace(/[\r\n]+/g, ' ') })} />
          : <input value={form.value} placeholder={DNS_PLACEHOLDERS[form.type]} spellCheck={false} autoCapitalize="off" onChange={e => set({ value: e.target.value })} />}</label>
      <label className="field"><span className="field-label">TTL</span>
        <select value={form.ttl} onChange={e => set({ ttl: e.target.value })}>
          <option value="">{tr("Default ({0})", dnsTtlLabel(dnsInfo?.default_ttl || 3600))}</option>
          {ttls.map(ttl => <option key={ttl} value={String(ttl)}>{dnsTtlLabel(ttl)}</option>)}
        </select></label>
    </div>;
  }

  // One line: the nameservers a domain's registrar is given.
  function renderDnsNameservers() {
    const names = dnsInfo.nameservers || [];
    return <div className="dns-ns-line">
      <span className="dns-ns-label"><Network size={14}/> {tr("Nameservers")}</span>
      {names.length > 0
        ? <>
          {names.map(name => <code key={name}>{name}</code>)}
          <button type="button" className="mini secondary icon-only" aria-label={tr("Copy")} title={tr("Copy")}
            onClick={() => { copyToClipboard(names.join('\n')); setNotice(tr("Copied to clipboard.")); }}><Copy size={13}/></button>
        </>
        : <span className="hint">{isAdmin ? tr("Not set yet: open Settings.") : tr("The administrator has not set the nameservers yet.")}</span>}
    </div>;
  }

  function renderDnsPager() {
    const list = dnsZones;
    const pages = Math.max(1, Math.ceil((list?.total || 0) / (list?.per_page || 50)));
    if (pages <= 1) return null;
    return <div className="firewall-ip-pager">
      <button className="mini secondary" disabled={dnsPage <= 1} onClick={() => setDnsPage(p => Math.max(1, p - 1))}>{tr("Previous")}</button>
      <span className="hint">{tr("Page {0} of {1}", dnsPage, pages)}</span>
      <button className="mini secondary" disabled={dnsPage >= pages} onClick={() => setDnsPage(p => p + 1)}>{tr("Next")}</button>
    </div>;
  }

  function renderDnsZones() {
    const list = dnsZones;
    const items = list?.items || [];
    return <div className="mail-tab">
      {renderDnsNameservers()}
      <form className="mail-search dns-search" onSubmit={e => { e.preventDefault(); setDnsPage(1); loadDnsZones(1); }}>
        <input value={dnsQuery} placeholder={tr("Search domains")} aria-label={tr("Search domains")} onChange={e => setDnsQuery(e.target.value)} />
        <button type="submit" className="secondary icon-only" aria-label={tr("Search")} title={tr("Search")}><Search size={14}/></button>
      </form>
      {list === null && <p className="hint">{tr("Loading…")}</p>}
      {list && items.length === 0 && <EmptyState icon={Network} message={dnsQuery.trim() ? tr("No domain matches the search.") : tr("No domains yet.")} />}
      {items.length > 0 && <div className="table">
        {items.map(zone => <div className="row dns-zone-row" key={zone.id}>
          <span className="mail-row-name">
            <strong>{zone.name}</strong>
            <small>{[isAdmin && zone.owner ? `${tr("Account")}: ${zone.owner}` : '', zone.created_at ? `${tr("Added")} ${new Date(zone.created_at).toLocaleDateString()}` : '', zone.on_panel === false ? tr("No longer on the panel") : ''].filter(Boolean).join(' · ')}</small>
          </span>
          <span className="row-actions">
            <button className="mini secondary" disabled={!!loading} onClick={() => openDnsZone(zone)}><Pencil size={13}/> {tr("Records")}</button>
          </span>
        </div>)}
      </div>}
      {renderDnsPager()}
    </div>;
  }

  function renderDnsSettings() {
    const f = dnsSettingsForm;
    const set = patch => setDnsSettingsForm(prev => ({ ...prev, ...patch }));
    const address = [dnsInfo.addresses?.ipv4?.[0], dnsInfo.addresses?.ipv6?.[0]].filter(Boolean).join(' / ');
    return <section className="section dns-page">
      <div className="section-title">
        <div className="waf-detail-title">
          <button className="secondary" onClick={() => setDnsTab('zones')}><ArrowLeft size={14}/> {tr("DNS Manager")}</button>
          <div><h2>{tr("DNS settings")}</h2>
            <p className="hint">{tr("The nameservers and default TTL of every zone on this server.")}</p></div>
        </div>
      </div>
      {!f ? <p className="hint">{tr("Loading…")}</p> : <div className="create-inline">
        <div className="mail-settings-grid">
          <label className="field"><span className="field-label">{tr("Nameserver {0}", 1)}</span>
            <input value={f.ns1} placeholder="ns1.example.com" spellCheck={false} onChange={e => set({ ns1: e.target.value })} /></label>
          <label className="field"><span className="field-label">{tr("Nameserver {0}", 2)}</span>
            <input value={f.ns2} placeholder="ns2.example.com" spellCheck={false} onChange={e => set({ ns2: e.target.value })} /></label>
          <label className="field"><span className="field-label">{tr("Zone contact (hostmaster)")}</span>
            <input value={f.hostmaster} placeholder="hostmaster@example.com" spellCheck={false} onChange={e => set({ hostmaster: e.target.value })} /></label>
          <label className="field"><span className="field-label">{tr("Default TTL")}</span>
            <select value={String(f.default_ttl)} onChange={e => set({ default_ttl: e.target.value })}>
              {DNS_TTLS.map(ttl => <option key={ttl} value={String(ttl)}>{dnsTtlLabel(ttl)}</option>)}
            </select></label>
        </div>
        <p className="hint">{tr("A changed nameserver is written into the NS and SOA records of every zone. Register both names as glue (child nameserver) records at the registrar of their domain, pointing at this server.")} {address && tr("This server: {0}.", address)}</p>
        <div className="actions"><button type="button" disabled={!f.ns1.trim() || !f.ns2.trim() || !!loading} onClick={saveDnsSettings}><Save size={14}/> {tr("Save settings")}</button></div>
      </div>}
    </section>;
  }

  function renderDnsZone() {
    const { zone, records } = dnsZone;
    const delegation = dnsDelegation;
    const statusLabel = { ok: tr("Served from here"), missing: tr("Not delegated"), different: tr("Other nameservers"), unknown: tr("Not checked") };
    const statusClass = { ok: 'ok', missing: 'bad', different: 'warn', unknown: '' };
    const term = dnsRecordFilter.trim().toLowerCase();
    const shown = (records || []).filter(r => !term || `${r.name} ${r.type} ${r.value}`.toLowerCase().includes(term));
    const form = dnsRecordForm;
    const edit = dnsRecordEdit;
    const isEditing = record => edit && edit.old.name === record.name && edit.old.type === record.type && edit.old.content === record.content;
    const delegationText = !delegation ? tr("Checking the nameservers…")
      : delegation.status === 'ok' ? tr("{0} is answered by this server.", zone.name)
      : delegation.status === 'different' ? tr("{0} uses other nameservers now ({1}). Set {2} at its registrar.", zone.name, delegation.found.join(', '), delegation.expected.join(', '))
      : delegation.status === 'missing' ? tr("Set {0} as the nameservers of {1} at its registrar. Until then these records are not used.", delegation.expected.join(', '), zone.name)
      : tr("The nameservers of {0} could not be looked up.", zone.name);
    return <section className="section dns-zone-page">
      <div className="section-title">
        <div className="waf-detail-title">
          <button className="secondary" onClick={() => { setDnsZone(null); loadDnsZones(); }}><ArrowLeft size={14}/> {tr("DNS Manager")}</button>
          <div><h2>{zone.name}</h2>
            <p className="hint">{[isAdmin && zone.owner ? `${tr("Account")}: ${zone.owner}` : '', records ? tr("{0} records", records.length) : ''].filter(Boolean).join(' · ')}</p></div>
        </div>
        <div className="actions">
          <button className="secondary" disabled={!!loading} onClick={() => openDnsZone(zone)}><RefreshCw size={14}/> {tr("Refresh")}</button>
          <button className="secondary-light" disabled={!!loading || records === null} onClick={restoreDnsDefaults}><RotateCcw size={14}/> {tr("Restore panel records")}</button>
          {zone.on_panel === false && <button className="danger" disabled={!!loading} onClick={() => deleteDnsZone(zone)}><Trash2 size={14}/> {tr("Delete zone")}</button>}
        </div>
      </div>
      <div className="dns-delegation">
        <span className={`badge ${statusClass[delegation?.status] || ''}`}>{delegation ? statusLabel[delegation.status] || delegation.status : '…'}</span>
        <span className="hint">{delegationText}</span>
      </div>
      <div className="create-inline dns-record-form">
        <div className="create-inline-head"><strong>{tr("Add a record")}</strong></div>
        {renderDnsRecordFields(form, patch => setDnsRecordForm(prev => ({ ...prev, ...patch })))}
        <p className="hint">{dnsTypeHint(form.type)} {tr("Names are relative to {0}: @ is the domain itself.", zone.name)}</p>
        <div className="actions"><button type="button" disabled={!form.value.trim() || !!loading || records === null} onClick={addDnsRecord}><Plus size={14}/> {tr("Add record")}</button></div>
      </div>
      <div className="mail-toolbar">
        <strong>{tr("Records")}</strong>
        <div className="mail-search"><input value={dnsRecordFilter} placeholder={tr("Filter records")} aria-label={tr("Filter records")} onChange={e => setDnsRecordFilter(e.target.value)} /></div>
      </div>
      {records === null && <p className="hint">{tr("Loading…")}</p>}
      {records && <div className="table dns-records">
        <div className="row dns-record-row dns-record-head" aria-hidden="true">
          <span>{tr("Name")}</span><span>{tr("Type")}</span><span>TTL</span><span>{tr("Value")}</span><span/>
        </div>
        {shown.map(record => isEditing(record)
          ? <div className="row dns-record-edit" key={`${record.name}|${record.type}|${record.content}`}>
              {renderDnsRecordFields(edit.form, patch => setDnsRecordEdit(prev => ({ ...prev, form: { ...prev.form, ...patch } })), { lockType: true })}
              <div className="actions">
                <button type="button" className="secondary-light" onClick={() => setDnsRecordEdit(null)}>{tr("Cancel")}</button>
                <button type="button" disabled={!String(edit.form.value).trim() || !!loading} onClick={saveDnsRecordEdit}><Save size={14}/> {tr("Save")}</button>
              </div>
            </div>
          : <div className="row dns-record-row" key={`${record.name}|${record.type}|${record.content}`}>
              <span className="dns-record-name" title={record.fqdn}>{record.name}</span>
              <span><code className="dns-type">{record.type}</code></span>
              <span className="dns-record-ttl">{dnsTtlLabel(record.ttl)}</span>
              <span className="dns-record-value">{record.priority != null && <small title={tr("Priority")}>{record.priority}</small>}<code>{record.value}</code>
                {record.mail && <span className="badge dns-mail-badge" title={tr("Kept in step with the email of {0}", record.mail)}><Mail size={11}/> {tr("Email")}</span>}</span>
              <span className="row-actions">
                {record.locked
                  ? <span className="dns-locked" title={tr("Follows DNS Manager's nameserver settings")}><Lock size={13}/></span>
                  : <>
                    <button type="button" className="mini secondary icon-only" disabled={!!loading} aria-label={tr("Edit")} title={tr("Edit")}
                      onClick={() => setDnsRecordEdit({ old: record, form: { type: record.type, name: record.name, value: record.value, priority: record.priority ?? '', ttl: String(record.ttl || '') } })}><Pencil size={13}/></button>
                    <button type="button" className="mini danger-light icon-only" disabled={!!loading} aria-label={tr("Delete")} title={tr("Delete")} onClick={() => deleteDnsRecord(record)}><Trash2 size={13}/></button>
                  </>}
              </span>
            </div>)}
        {shown.length === 0 && <p className="hint">{tr("No record matches the filter.")}</p>}
      </div>}
    </section>;
  }

  function renderDns() {
    const info = dnsInfo;
    if (!info) return <section className="section"><h2>{tr("DNS Manager")}</h2><p className="hint">{tr("Loading…")}</p></section>;
    if (!info.installed) return <section className="section">
      <h2>{tr("DNS Manager")}</h2>
      <div className="info-box"><AlertCircle size={14}/> {isAdmin ? tr("The DNS Manager addon is not installed. Install it on the Addons page.") : tr("DNS Manager is not available on this server.")}</div>
      {isAdmin && <div className="actions"><button onClick={() => navigateToPage('addons')}><PackageOpen size={14}/> {tr("Open Addons")}</button></div>}
    </section>;
    if (dnsZone) return renderDnsZone();
    if (isAdmin && dnsTab === 'settings') return renderDnsSettings();
    return <section className="section dns-page">
      <div className="section-title">
        <div><h2>{tr("DNS Manager")}</h2>
          <p className="hint">{isAdmin ? tr("Every domain on the panel, answered by this server.") : tr("The DNS records of your domains, answered by this server.")}</p></div>
        <div className="actions">
          {isAdmin && <button type="button" className="secondary" onClick={() => setDnsTab('settings')}><SettingsIcon size={14}/> {tr("Settings")}</button>}
          <button type="button" className="secondary" disabled={!!loading} onClick={() => { loadDnsInfo(); loadDnsZones(); }}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
      </div>
      {renderDnsZones()}
    </section>;
  }

  function renderMail() {
    const info = mailInfo;
    if (!info) return <section className="section"><h2>{tr("Email")}</h2><p className="hint">{tr("Loading…")}</p></section>;
    if (!info.installed) return <section className="section">
      <h2>{tr("Email")}</h2>
      <div className="info-box"><AlertCircle size={14}/> {isAdmin ? tr("The Email addon is not installed. Install it on the Addons page.") : tr("Email is not available on this server.")}</div>
      {isAdmin && <div className="actions"><button onClick={() => navigateToPage('addons')}><PackageOpen size={14}/> {tr("Open Addons")}</button></div>}
    </section>;
    if (mailDns) return renderMailDns();
    const domains = info.domains || [];
    const limit = Number(info.mailbox_limit) || 0;
    const tabs = [
      ['mailboxes', tr("Mailboxes"), Inbox], ['forwarders', tr("Forwarders"), Forward], ['domains', tr("Domains"), Globe],
      // Server-wide: relays, the spam filter and the mail server itself.
      ...(isAdmin ? [['relay', tr("Relays"), Send], ['rspamd', 'Rspamd', ShieldCheck], ['server', tr("Server"), Server]] : []),
    ];
    const serverTabs = ['relay', 'rspamd', 'server'];
    const activeTab = domains.length || serverTabs.includes(mailTab) ? mailTab : 'domains';
    return <>
      <section className="section mail-page">
        <div className="section-title">
          <div><h2>{tr("Email")}</h2>
            <p className="hint">{isAdmin
              ? tr("Mailboxes, forwarders and DNS records of every mail domain on this server.")
              : limit ? tr("{0} of {1} mailboxes used.", info.mailbox_count, limit) : tr("{0} mailboxes.", info.mailbox_count)}</p></div>
          <div className="actions">
            <button type="button" className="secondary" onClick={() => window.open(info.webmail_url, '_blank', 'noopener')}><ExternalLink size={14}/> {tr("Webmail")}</button>
            <button type="button" className="secondary" disabled={!!loading} onClick={refreshMail}><RefreshCw size={14}/> {tr("Refresh")}</button>
          </div>
        </div>
        <div className="segmented-control backup-tabs" role="tablist" aria-label={tr("Email sections")}>
          {tabs.map(([id, label, Icon]) => <button key={id} type="button" role="tab" aria-selected={activeTab === id}
            className={activeTab === id ? 'active' : ''} disabled={!domains.length && !['domains', ...serverTabs].includes(id)}
            onClick={() => { setMailTab(id); setMailPage(1); }}><Icon size={14}/>{label}</button>)}
        </div>
        {activeTab === 'mailboxes' && renderMailboxes()}
        {activeTab === 'forwarders' && renderForwarders()}
        {activeTab === 'domains' && renderMailDomains()}
        {activeTab === 'relay' && renderMailRelays()}
        {activeTab === 'rspamd' && renderMailRspamd()}
        {activeTab === 'server' && renderMailServer()}
      </section>
      {domains.length > 0 && !serverTabs.includes(activeTab) && renderMailClientHelp()}
    </>;
  }

  function renderAddonMail(addon) {
    const open = tab => { setMailTab(tab); navigateToPage('mail'); };
    return <div className="addon-panel">
      <div className="addon-panel-head"><strong>{tr("Mailboxes, relays, the spam filter and server settings")}</strong></div>
      {!addon.running && <p className="hint">{tr("The mail server is stopped: no mail is received or sent, and webmail is off.")}</p>}
      <div className="actions">
        <button type="button" className="mini" onClick={() => open('mailboxes')}><Mail size={13}/> {tr("Open Email")}</button>
        <button type="button" className="mini secondary" onClick={() => open('relay')}><Send size={13}/> {tr("Relays")}</button>
        <button type="button" className="mini secondary" onClick={() => open('rspamd')}><ShieldCheck size={13}/> Rspamd</button>
        <button type="button" className="mini secondary" onClick={() => open('server')}><Server size={13}/> {tr("Server")}</button>
      </div>
    </div>;
  }

  function renderSettingsHub() {
    return <section className="section settings-hub">
      <div className="section-title">
        <div><h2>{tr("Settings")}</h2><p className="hint">{tr("Security, server and panel configuration.")}</p></div>
      </div>
      {settingsGroups.map(group => <div className="settings-hub-group" key={group.key}>
        <h3>{group.title}</h3>
        <div className="settings-hub-grid">
          {group.items.map(([key, label, Icon, summary]) => <button key={key} type="button" className="settings-tile" onClick={() => navigateToPage(key)}>
            <span className="settings-tile-icon"><Icon size={18}/></span>
            <span className="settings-tile-text"><strong>{label}</strong><small>{summary}</small></span>
          </button>)}
        </div>
      </div>)}
    </section>;
  }

  // Labels live here rather than in the API so the i18n check sees them.
  function notifyEventLabels() {
    return {
      backup_failed: tr("Scheduled backup failed or warned"),
      malware_found: tr("Malware found"),
      ssl_expiring: tr("SSL certificate expiring (renewal failing)"),
      service_status: tr("Service stopped / running again (web server, MariaDB, Redis, panel)"),
      disk_low: tr("Disk over 90% / 95%"),
      storage_quota: tr("An account at 90% / 100% of its storage"),
      resource_limit: tr("An account's processes stopped at its memory limit"),
      update_available: tr("New OPanel release"),
      update_result: tr("Panel update finished or failed"),
      login_lockout: tr("Address locked out after repeated failed sign-ins"),
      da_import_done: tr("DirectAdmin import finished"),
    };
  }

  function notifyUserEventLabels() {
    return {
      login_new_ip: tr("Sign-in from a new address"),
      account_security: tr("Password, 2FA, passkey or token changes"),
      backup_job: tr("Backups and restores I started have finished"),
    };
  }

  function renderNotifyLog() {
    const list = notifyLog || { items: [], total: 0, page: 1, size: 50 };
    const pages = Math.max(1, Math.ceil((list.total || 0) / (list.size || 50)));
    const statusLabel = { pending: tr("Waiting"), sending: tr("Sending"), sent: tr("Sent"), failed: tr("Failed") };
    const statusClass = { sent: 'ok', failed: 'bad', pending: 'warn', sending: 'warn' };
    return <section className="section notify-log-page">
      <div className="section-title">
        <div className="waf-detail-title">
          <button className="secondary" onClick={() => setShowNotifyLog(false)}><ArrowLeft size={14}/> {tr("Notifications")}</button>
          <div><h2>{tr("Send log")}</h2><p className="hint">{tr("Every email and Telegram message of the last 30 days.")}</p></div>
        </div>
        <select value={notifyLogStatus} aria-label={tr("Show")} onChange={e => { setNotifyLogStatus(e.target.value); setNotifyLogPage(1); }}>
          <option value="">{tr("All")}</option><option value="sent">{tr("Sent")}</option>
          <option value="failed">{tr("Failed")}</option><option value="pending">{tr("Waiting")}</option>
        </select>
      </div>
      {list.items.length > 0 ? <ul className="notify-log-list">
        {list.items.map(item => <li key={item.id}>
          <span className={`badge ${statusClass[item.status] || ''}`}>{statusLabel[item.status] || item.status}</span>
          <small>{item.created_at ? new Date(item.created_at).toLocaleString() : ''}</small>
          <span className="notify-log-subject" title={item.subject}>{item.subject}</span>
          <small className="notify-log-to" title={item.recipient}>{item.channel === 'email' ? '✉' : '✈'} {item.recipient}</small>
          {item.last_error && <small className="notify-log-error" title={item.last_error}>{item.last_error}</small>}
        </li>)}
      </ul> : <p className="hint">{notifyLog === null ? tr("Loading…") : tr("Nothing has been sent yet.")}</p>}
      {pages > 1 && <div className="firewall-ip-pager">
        <button className="mini secondary" disabled={list.page <= 1} onClick={() => setNotifyLogPage(p => Math.max(1, p - 1))}>{tr("Previous")}</button>
        <span className="hint">{tr("Page {0} of {1}", list.page, pages)}</span>
        <button className="mini secondary" disabled={list.page >= pages} onClick={() => setNotifyLogPage(p => p + 1)}>{tr("Next")}</button>
      </div>}
    </section>;
  }

  function renderNotificationCenter() {
    const enabled = !!notifyInfo?.enabled;
    if (showNotifyLog && isAdmin) return renderNotifyLog();
    const f = notifyForm;
    const setF = patch => setNotifyForm(prev => ({ ...(prev || {}), ...patch }));
    const prefs = notifyPrefs;
    const adminLabels = notifyEventLabels();
    const userLabels = notifyUserEventLabels();
    return <>
      <section className="section">
        <div className="section-title">
          <div>
            <h2>{tr("Notifications")}</h2>
            <p className="hint">{tr("Email and Telegram alerts for administrators. Hosting customers are not notified.")}</p>
          </div>
          <button className="secondary" disabled={!!loading} onClick={loadNotifications}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        {notifyInfo === null && <p className="hint">{tr("Loading…")}</p>}
        {notifyInfo !== null && !enabled && <div className="info-box"><AlertCircle size={14}/> {isAdmin
          ? <>{tr("Notifications are off. Install the Notifications addon on the Addons page first.")}
              {' '}<button className="mini" onClick={() => navigateToPage('addons')}><PackageOpen size={13}/> {tr("Open Addons")}</button></>
          : tr("Notifications are not enabled on this panel. Ask your administrator to turn them on.")}</div>}
      </section>

      {enabled && isAdmin && f && <section className="section">
        <div className="section-title">
          <div><h2>{tr("Channels")}</h2><p className="hint">{tr("Server-wide. Your own account's alerts go out through these too.")}</p></div>
          <button className="secondary" onClick={() => { setNotifyLog(null); setNotifyLogPage(1); setShowNotifyLog(true); }}><ScrollText size={14}/> {tr("Send log")}</button>
        </div>
        <div className="notify-grid">
          <div className="notify-card">
            <div className="notify-card-head">
              <h3>{tr("Email (SMTP)")}</h3>
              <label className="notify-switch"><input type="checkbox" checked={!!f.email_enabled} onChange={e => setF({ email_enabled: e.target.checked })} /> {tr("On")}</label>
            </div>
            <div className="notify-form">
              <label className="wide"><span>{tr("SMTP host")}</span><input value={f.smtp_host || ''} placeholder="smtp.gmail.com" onChange={e => setF({ smtp_host: e.target.value })} /></label>
              <label><span>{tr("Port")}</span><input type="number" value={f.smtp_port || 587} onChange={e => setF({ smtp_port: e.target.value })} /></label>
              <label><span>{tr("Security")}</span><select value={f.smtp_security || 'starttls'} onChange={e => setF({ smtp_security: e.target.value, smtp_port: e.target.value === 'ssl' ? 465 : e.target.value === 'starttls' ? 587 : f.smtp_port })}>
                <option value="starttls">STARTTLS (587)</option><option value="ssl">SSL/TLS (465)</option><option value="none">{tr("None")}</option>
              </select></label>
              <label><span>{tr("Username")}</span><input value={f.smtp_username || ''} autoComplete="off" onChange={e => setF({ smtp_username: e.target.value })} /></label>
              <label><span>{tr("Password")}</span><input type="password" value={f.smtp_password || ''} autoComplete="new-password"
                placeholder={notifySettings?.smtp_password_set ? tr("Saved — leave blank to keep") : ''} onChange={e => setF({ smtp_password: e.target.value })} /></label>
              <label><span>{tr("From address")}</span><input value={f.from_address || ''} placeholder="panel@example.com" onChange={e => setF({ from_address: e.target.value })} /></label>
              <label><span>{tr("From name")}</span><input value={f.from_name || ''} onChange={e => setF({ from_name: e.target.value })} /></label>
            </div>
            <div className="notify-test">
              <input value={notifyTest.email} placeholder={tr("Send a test to…")} onChange={e => setNotifyTest(prev => ({ ...prev, email: e.target.value }))} />
              <button className="secondary" disabled={!!loading || !notifyTest.email} onClick={() => sendNotifyTest('email')}>{tr("Send test")}</button>
            </div>
            <p className="hint">{tr("Uses the saved settings. Many VPS providers block port 25; use 587 or 465.")}</p>
          </div>

          <div className="notify-card">
            <div className="notify-card-head">
              <h3>{tr("Telegram")}</h3>
              <label className="notify-switch"><input type="checkbox" checked={!!f.telegram_enabled} onChange={e => setF({ telegram_enabled: e.target.checked })} /> {tr("On")}</label>
            </div>
            <div className="notify-form">
              <label className="wide"><span>{tr("Bot token (from @BotFather)")}</span><input type="password" value={f.telegram_bot_token || ''} autoComplete="off"
                placeholder={notifySettings?.telegram_bot_token_set ? tr("Saved — leave blank to keep") : '123456789:AA…'} onChange={e => setF({ telegram_bot_token: e.target.value })} /></label>
              {notifySettings?.telegram_bot_username && <p className="hint wide">{tr("Bot:")} <a href={`https://t.me/${notifySettings.telegram_bot_username}`} target="_blank" rel="noopener noreferrer">@{notifySettings.telegram_bot_username}</a></p>}
              <label className="wide"><span>{tr("Admin chat IDs")}</span><input value={f.admin_telegram_chats || ''} placeholder="123456789, -1001234567890"
                onChange={e => setF({ admin_telegram_chats: e.target.value })} /></label>
            </div>
            <p className="hint">{tr("A group works too: add the bot to it and use the group's ID (it starts with -100). Link your own chat below.")}</p>
            <div className="notify-test">
              <input value={notifyTest.telegram} placeholder={tr("Chat ID for a test")} onChange={e => setNotifyTest(prev => ({ ...prev, telegram: e.target.value }))} />
              <button className="secondary" disabled={!!loading || !notifyTest.telegram} onClick={() => sendNotifyTest('telegram')}>{tr("Send test")}</button>
            </div>
          </div>
        </div>

        <div className="notify-grid">
          <div className="notify-card">
            <h3>{tr("Admin recipients")}</h3>
            <div className="notify-form">
              <label className="wide"><span>{tr("Admin email addresses")}</span><input value={f.admin_emails || ''} placeholder="noc@example.com, boss@example.com"
                onChange={e => setF({ admin_emails: e.target.value })} /></label>
              <label><span>{tr("Message language")}</span><select value={f.language || 'vi'} onChange={e => setF({ language: e.target.value })}>
                <option value="vi">Tiếng Việt</option><option value="en">English</option></select></label>
            </div>
            <p className="hint">{tr("Blank: every administrator account with a real email address.")}</p>
          </div>
          <div className="notify-card">
            <h3>{tr("Admin events")}</h3>
            <div className="notify-events">
              {(notifySettings?.admin_event_keys || []).map(key => <label key={key}>
                <input type="checkbox" checked={f.admin_events?.[key] !== false}
                  onChange={e => setF({ admin_events: { ...(f.admin_events || {}), [key]: e.target.checked } })} /> {adminLabels[key] || key}
              </label>)}
            </div>
          </div>
        </div>
        <div className="notify-actions">
          <button disabled={!!loading} onClick={() => saveNotifySettings()}><Save size={14}/> {tr("Save")}</button>
          {notifySettings?.smtp_password_set && <button className="secondary-light" disabled={!!loading} onClick={() => saveNotifySettings({ clear_smtp_password: true })}>{tr("Forget SMTP password")}</button>}
        </div>
      </section>}

      {enabled && prefs && <section className="section">
        <div className="section-title">
          <div><h2>{tr("My notifications")}</h2><p className="hint">{tr("About your own administrator account.")}</p></div>
          <button className="secondary" disabled={!!loading || !(prefs.email_available || prefs.telegram_linked)} onClick={sendMyNotifyTest}>{tr("Send me a test")}</button>
        </div>
        <div className="notify-grid">
          <div className="notify-card">
            <div className="notify-card-head">
              <h3>{tr("Email")}</h3>
              <label className="notify-switch"><input type="checkbox" disabled={!prefs.email_available} checked={!!prefs.email_enabled && prefs.email_available}
                onChange={e => saveNotifyPrefs({ email_enabled: e.target.checked })} /> {tr("On")}</label>
            </div>
            {!prefs.email_available ? <p className="hint">{tr("Email is not set up on this panel.")}</p>
              : prefs.email_usable ? <p className="hint">{tr("Sent to {0}.", prefs.email)}</p>
              : <p className="hint notify-warn">{tr("Your account email ({0}) is a placeholder. Set a real address in your profile to receive email.", prefs.email)}</p>}
          </div>
          <div className="notify-card">
            <div className="notify-card-head">
              <h3>{tr("Telegram")}</h3>
              {prefs.telegram_linked && <label className="notify-switch"><input type="checkbox" checked={!!prefs.telegram_enabled}
                onChange={e => saveNotifyPrefs({ telegram_enabled: e.target.checked })} /> {tr("On")}</label>}
            </div>
            {!prefs.telegram_available ? <p className="hint">{tr("Telegram is not set up on this panel.")}</p>
              : prefs.telegram_linked ? <div className="notify-test"><span className="badge ok">{tr("Linked")}</span>
                  <button className="mini secondary-light" disabled={!!loading} onClick={unlinkTelegram}>{tr("Unlink")}</button></div>
              : notifyLink ? <>
                  <p className="hint">{tr("In Telegram, press Start in the chat with @{0}, then come back and check.", prefs.telegram_bot_username)}</p>
                  <div className="notify-test">
                    <button className="secondary" onClick={() => window.open(notifyLink.url, '_blank', 'noopener')}>{tr("Open Telegram")}</button>
                    <button disabled={!!loading} onClick={verifyTelegramLink}>{tr("I pressed Start — check")}</button>
                  </div>
                </>
              : <button className="secondary" disabled={!!loading} onClick={startTelegramLink}>{tr("Link Telegram")}</button>}
          </div>
        </div>
        <div className="notify-card">
          <h3>{tr("What to send me")}</h3>
          <div className="notify-events">
            {(prefs.event_keys || []).map(key => {
              const locked = (prefs.unmutable || []).includes(key);
              return <label key={key}>
                <input type="checkbox" disabled={locked} checked={locked || prefs.events?.[key] !== false}
                  onChange={e => saveNotifyPrefs({ events: { ...(prefs.events || {}), [key]: e.target.checked } })} /> {userLabels[key] || key}
              </label>;
            })}
          </div>
        </div>
      </section>}
    </>;
  }

  function renderMcp() {
    const enabled = !!mcpInfo?.enabled;
    return <>
      <section className="section">
        <div className="section-title">
          <div>
            <h2>{tr("AI assistants (MCP)")}</h2>
            <p className="hint">{tr("Connect Claude Code, Cursor, VS Code or another MCP client to this panel. A token acts as your account: it sees your websites, databases and backups")}{isAdmin ? tr(" (as an administrator, every account’s)") : ''} {tr("and nothing else. With actions allowed it can also edit your websites’ files, so an assistant can build and fix your sites; deleting a file always asks you first in the client.")}</p>
          </div>
          <button className="secondary" disabled={!!loading} onClick={loadMcp}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        {mcpInfo === null && <p className="hint">{tr("Loading…")}</p>}
        {mcpInfo !== null && !enabled && <div className="info-box"><AlertCircle size={14}/> {isAdmin
          ? <>{tr("MCP is not running on this panel. Install or start the")} <strong>{tr("MCP server")}</strong> {tr("addon on the Addons page first.")}
              {' '}<button className="mini" onClick={() => navigateToPage('addons')}><PackageOpen size={13}/> {tr("Open Addons")}</button></>
          : tr("MCP is not enabled on this panel. Ask your administrator to turn it on.")}</div>}
        {enabled && renderCopyBlock(tr("Endpoint"), mcpEndpoint)}
      </section>

      {enabled && mcpCreated && <section className="section token-created-notice">
        <div className="section-title">
          <div><h2>{tr("Token created.")}</h2><p className="hint">{tr("Copy it now — it is shown only once.")}</p></div>
          <button className="secondary" onClick={() => setMcpCreated(null)}>{tr("Dismiss")}</button>
        </div>
        {renderCopyBlock(tr("Token"), mcpCreated.token)}
        {mcpClientSnippets(mcpCreated.token).map(([label, text]) => <React.Fragment key={label}>
          {renderCopyBlock(label, text, { multiline: true, copiedMessage: tr("{0} setup copied.", label) })}
        </React.Fragment>)}
      </section>}

      {enabled && <section className="section">
        <h2>{tr("New token")}</h2>
        <div className="token-create-form mcp-token-form">
          <label><span>{tr("Name")}</span><input value={mcpForm.name} maxLength={64} placeholder={tr("Claude Code on my laptop")}
            onChange={e => setMcpForm(prev => ({ ...prev, name: e.target.value }))} /></label>
          <label><span>{tr("Expires")}</span><select value={mcpForm.expires_days}
            onChange={e => setMcpForm(prev => ({ ...prev, expires_days: Number(e.target.value) }))}>
            {[30, 90, 180, 365].map(days => <option key={days} value={days}>{days} {tr("days")}</option>)}
          </select></label>
          <button disabled={!!loading || !mcpForm.name.trim()} onClick={createMcpToken}><Plus size={14}/> {tr("Create token")}</button>
        </div>
        <label className="option-card">
          <input type="checkbox" checked={mcpForm.can_write}
            onChange={e => setMcpForm(prev => ({ ...prev, can_write: e.target.checked }))} />
          <span>
            <strong>{tr("Allow actions")}</strong>
            <small>{tr("Write and delete files, run backups, issue certificates, switch the WAF")}{isAdmin ? tr(", restart services, block and unblock IPs, add WAF rules") : ''}. {tr("Without it the token can only read.")}</small>
          </span>
        </label>
        <p className="hint">{tr("Up to")} {mcpInfo?.max_tokens || 10} {tr("tokens per account.")}</p>
      </section>}

      {enabled && <section className="section">
        <h2>{tr("Your tokens")}</h2>
        {mcpTokens.length === 0 ? <p className="hint">{tr("No MCP tokens yet.")}</p> : renderMcpTokenRows(mcpTokens)}
      </section>}

      {enabled && !mcpCreated && <section className="section">
        <div><h2>{tr("Connecting a client")}</h2>
          <p className="hint">{tr("Replace <your-token> with a token from above. The client must trust this panel’s HTTPS certificate; a self-signed one is refused.")}</p></div>
        {mcpClientSnippets('').map(([label, text]) => <React.Fragment key={label}>
          {renderCopyBlock(label, text, { multiline: true, copiedMessage: tr("{0} setup copied.", label) })}
        </React.Fragment>)}
      </section>}
    </>;
  }

  function renderAddonMcp(addon) {
    if (!addon.installed) return null;
    return <div className="addon-panel">
      <div className="addon-panel-head">
        <strong>{tr("Tokens on this panel")}</strong>
        <div className="actions">
          <button className="mini secondary-light" disabled={!!loading} onClick={loadMcpAllTokens}><RefreshCw size={13}/> {tr("Refresh")}</button>
          <button className="mini" onClick={() => navigateToPage('mcp')}><Bot size={13}/> {tr("Create my token")}</button>
        </div>
      </div>
      <p className="hint">{tr("Endpoint:")} <code>{mcpEndpoint}</code></p>
      {mcpAllTokens.length === 0
        ? <p className="hint">{tr("No account has created an MCP token yet.")}</p>
        : renderMcpTokenRows(mcpAllTokens, { showOwner: true, fromAddonPanel: true })}
    </div>;
  }

  function renderAddonDemo(addon) {
    if (!demoSettings) return <p className="hint">{tr("Loading…")}</p>;
    const rows = demoForm.accounts;
    const setRow = (index, patch) => setDemoForm(prev => ({
      ...prev, accounts: prev.accounts.map((row, i) => i === index ? { ...row, ...patch } : row),
    }));
    const choosable = users.filter(u => u.id !== currentUser?.id);
    return <div className="addon-panel demo-settings">
      <div className="addon-panel-head"><strong>{tr("Demo accounts")}</strong></div>
      <p className="hint">{tr("Each demo account can open every page its role allows and change nothing. Use accounts made for the demo, on a server with sample data only.")}</p>
      {rows.length === 0 && <p className="hint">{tr("No demo account yet.")}</p>}
      {rows.map((row, index) => <div className="demo-account-row" key={index}>
        <select value={row.user_id} onChange={e => setRow(index, { user_id: e.target.value })} aria-label={tr("Account")}>
          <option value="">{tr("Choose an account")}</option>
          {choosable.map(u => <option key={u.id} value={String(u.id)}>{u.username} ({roleLabel(u.role)})</option>)}
        </select>
        <input value={row.password} onChange={e => setRow(index, { password: e.target.value })}
          placeholder={tr("Public password (12+ characters)")} aria-label={tr("Public password")} spellCheck={false} />
        <button type="button" className="mini secondary-light icon-only" aria-label={tr("Generate")} title={tr("Generate")}
          onClick={() => setRow(index, { password: randomDemoPassword() })}><Dices size={14}/></button>
        <button type="button" className="mini danger-light icon-only" aria-label={tr("Remove")} title={tr("Remove")}
          onClick={() => setDemoForm(prev => ({ ...prev, accounts: prev.accounts.filter((_, i) => i !== index) }))}><Trash2 size={13}/></button>
      </div>)}
      <div className="actions demo-settings-actions">
        <button type="button" className="mini secondary" disabled={rows.length >= 5}
          onClick={() => setDemoForm(prev => ({ ...prev, accounts: [...prev.accounts, { user_id: '', password: randomDemoPassword() }] }))}>
          <Plus size={13}/> {tr("Add a demo account")}</button>
      </div>
      <label className="check-line">
        <input type="checkbox" checked={demoForm.show_on_login} onChange={e => setDemoForm(prev => ({ ...prev, show_on_login: e.target.checked }))} />
        {tr("Show the demo accounts on the login page, with a one-click sign-in")}
      </label>
      {!addon.running && <p className="hint">{tr("Demo mode is stopped: demo accounts cannot sign in.")}</p>}
      <div className="actions">
        <button type="button" disabled={!!loading || rows.some(r => r.user_id && r.password.length < 12)} onClick={saveDemoSettings}>
          <Save size={14}/> {tr("Save demo accounts")}</button>
      </div>
    </div>;
  }

  function renderAddons() {
    if (!isAdmin) return <section className="section"><h2>{tr("Addons")}</h2><p className="hint">{tr("No permission.")}</p></section>;
    return <section className="section">
      <div className="section-title">
        <div>
          <h2>{tr("Addons")}</h2>
          <p className="hint">{tr("Optional components this panel release can install for you. Each one ships with the panel, so a new addon arrives with a panel update.")}</p>
        </div>
        <button className="secondary-light" disabled={!!loading} onClick={() => loadAddons()}><RefreshCw size={14}/> {tr("Refresh")}</button>
      </div>

      {addonList.length === 0 && <p className="hint">{tr("No addons in this release.")}</p>}

      <div className="addon-grid">
        {addonList.map(addon => {
          const open = addonOpen === addon.id;
          const stateBadge = addon.busy
            ? <span className="badge warn">{addon.busy_action === 'uninstall' ? tr("Removing") : tr("Installing")}</span>
            : !addon.installed
              ? <span className="badge">{tr("Not installed")}</span>
              : addon.running
                ? <span className="badge ok">{tr("Running")}</span>
                : <span className="badge warn">{tr("Stopped")}</span>;
          return <div className="addon-card" key={addon.id}>
            <div className="addon-card-head">
              <div className="addon-card-title">
                <PackageOpen size={16}/>
                <strong>{addon.name}</strong>
                {addon.version && <span className="hint addon-version">v{addon.version}</span>}
              </div>
              {stateBadge}
            </div>
            <p className="addon-summary">{tr(addon.summary)}</p>
            {addon.installed && addon.enabled === false &&
              <p className="hint">{tr("Installed but not set to start on boot.")}</p>}
            {addon.last_error && <p className="hint addon-error"><AlertCircle size={13}/> {addon.last_error}</p>}
            {addon.detail && !addon.last_error && <p className="hint">{addon.detail}</p>}
            {addon.installed_at && <p className="hint">{tr("Installed")} {addon.installed_at}{addon.installed_by ? tr(" by {0}", addon.installed_by) : ''}</p>}

            <div className="actions addon-actions">
              {!addon.installed && <button disabled={!!loading || addon.busy} onClick={() => installAddon(addon)}>
                <Download size={14}/> {tr("Install")}</button>}
              {addon.installed && !addon.running && <button className="secondary" disabled={!!loading || addon.busy}
                onClick={() => setAddonRunning(addon, true)}><Play size={14}/> {tr("Start")}</button>}
              {addon.installed && addon.running && <button className="secondary-light" disabled={!!loading || addon.busy}
                onClick={() => setAddonRunning(addon, false)}><Square size={14}/> {tr("Stop")}</button>}
              {addon.installed && <button className="secondary-light" disabled={!!loading || addon.busy}
                onClick={() => setAddonOpen(open ? '' : addon.id)}>
                <SettingsIcon size={14}/> {open ? tr("Hide") : tr("Manage")}</button>}
              {addon.installed && <button className="danger-light" disabled={!!loading || addon.busy}
                onClick={() => uninstallAddon(addon)}><Trash2 size={14}/> {tr("Remove")}</button>}
            </div>

            {open && <div className="addon-detail">
              <p className="addon-description">{tr(addon.description)}</p>
              {(addon.notes || []).length > 0 && <ul className="addon-notes">
                {addon.notes.map((note, idx) => <li key={idx}>{tr(note)}</li>)}
              </ul>}
              {addon.id === 'fail2ban' && addon.installed && <div className="addon-panel">
                <div className="addon-panel-head"><strong>{tr("Ban rules, banned addresses and activity")}</strong>
                  <button className="mini" onClick={() => navigateToPage('firewall')}><Shield size={13}/> {tr("Open Firewall")}</button></div>
              </div>}
              {addon.id === 'mcp' && renderAddonMcp(addon)}
              {addon.id === 'demo' && addon.installed && renderAddonDemo(addon)}
              {addon.id === 'mail' && addon.installed && renderAddonMail(addon)}
              {addon.id === 'dns' && addon.installed && <div className="addon-panel">
                <div className="addon-panel-head"><strong>{tr("Zones, records and nameservers")}</strong>
                  <button className="mini" disabled={!addon.running} onClick={() => navigateToPage('dns')}><Network size={13}/> {tr("Open DNS Manager")}</button></div>
                {!addon.running && <p className="hint">{tr("PowerDNS is stopped: the domains hosted here do not resolve.")}</p>}
              </div>}
              {addon.id === 'malware' && addon.installed && <div className="addon-panel">
                <div className="addon-panel-head"><strong>{tr("Scans, schedules, real-time protection and quarantine")}</strong>
                  <button className="mini" disabled={!addon.running} onClick={() => navigateToPage('malware')}><Bug size={13}/> {tr("Open Malware Scanner")}</button></div>
              </div>}
              {addon.id === 'notifications' && addon.installed && <div className="addon-panel">
                <div className="addon-panel-head"><strong>{tr("Channels, recipients and events")}</strong>
                  <button className="mini" onClick={() => navigateToPage('notifications')}><Bell size={13}/> {tr("Open Notifications")}</button></div>
              </div>}
            </div>}
          </div>;
        })}
      </div>
    </section>;
  }

  function renderServices() {
    return <section className="section">
      <div className="section-title">
        <div><h2>{tr("Services")}</h2><p className="hint">{tr("Auto-refreshes every 10s")}</p></div>
        <button className="secondary" disabled={!!loading} onClick={checkAllServices}><RefreshCw size={15}/> {tr("Refresh")}</button>
      </div>
      <div className="service-grid">
        {serviceNames.map(name => {
          const state = serviceStates[name];
          const text = `${state?.stdout || ''} ${state?.stderr || ''}`;
          const active = text.includes('active (running)');
          const inactive = text.includes('inactive') || text.includes('failed');
          return <div className="service-card" key={name}>
            <div><strong>{name}</strong><span className={active ? 'badge ok' : inactive ? 'badge bad' : 'badge'}>{active ? tr("Running") : inactive ? tr("Stopped") : '...'}</span></div>
            {isAdmin && <div className="service-actions">
              <button className="secondary" disabled={active} onClick={() => runServiceAction(name, 'start')}><Play size={13}/> {tr("Start")}</button>
              {!['opanel-api', 'redis-server'].includes(name) && <button className="danger-light" disabled={inactive} onClick={() => runServiceAction(name, 'stop')}><Square size={13}/> {tr("Stop")}</button>}
              <button className="secondary" onClick={() => runServiceAction(name, 'restart')}><RotateCcw size={13}/> {tr("Restart")}</button>
            </div>}
          </div>;
        })}
      </div>
    </section>;
  }

  // One table for every installed PHP version: a column per version, and a
  // last column that installs the extension wherever it is still missing.
  function renderPhpExtensions() {
    const data = phpExtensions;
    const versions = data?.versions || [];
    return <div className="user-create-card php-ext-card" style={{ marginTop: 16 }}>
      <div className="php-ext-head">
        <h3>{tr("PHP extensions")}</h3>
        <p className="hint">{tr("Server-wide: every website on a PHP version gets its extensions. Installing or removing one restarts OpenLiteSpeed.")}</p>
      </div>
      {!data ? <p className="hint">{tr("Loading…")}</p>
        : versions.length === 0 ? <p className="hint">{tr("No PHP version is installed.")}</p>
        : <>
          <div className="php-ext-table-wrap">
            <table className="php-ext-table">
              <thead><tr>
                <th>{tr("Extension")}</th>
                {versions.map(v => <th key={v}>{tr("PHP")} {v}</th>)}
                <th aria-label={tr("Install all")}></th>
              </tr></thead>
              <tbody>
                {data.extensions.map(ext => {
                  const missingOn = versions.filter(v => ext.states[v]?.state === 'available');
                  return <tr key={ext.name}>
                    <td><code>{ext.name}</code></td>
                    {versions.map(v => {
                      const cell = ext.states[v] || { state: 'missing' };
                      if (cell.state === 'installed') {
                        return <td key={v}><span className={`php-ext-installed ${cell.loaded ? '' : 'not-loaded'}`}
                          title={cell.loaded ? '' : tr("Installed, not loaded")}>{tr("Installed")}</span>
                          {!ext.core && <button className="php-ext-remove" disabled={!!loading} title={tr("Remove from PHP {0}", v)}
                            aria-label={tr("Remove {0} from PHP {1}", ext.name, v)} onClick={() => changePhpExtension(ext.name, v, 'remove')}><X size={12}/></button>}
                        </td>;
                      }
                      if (cell.state === 'available') {
                        return <td key={v}><button className="mini secondary-light php-ext-install" disabled={!!loading}
                          title={tr("Install on PHP {0}", v)} aria-label={tr("Install {0} on PHP {1}", ext.name, v)}
                          onClick={() => changePhpExtension(ext.name, v, 'install')}><Download size={14}/></button></td>;
                      }
                      return <td key={v}><span className="php-ext-na" title={tr("Not in repository")}>—</span></td>;
                    })}
                    <td className="php-ext-all">{missingOn.length > 0 && <button className="mini secondary" disabled={!!loading}
                      onClick={() => installPhpExtensionEverywhere(ext.name, missingOn)}>{tr("Install all")}</button>}</td>
                  </tr>;
                })}
              </tbody>
            </table>
          </div>
          <details className="php-modules">
            <summary>{tr("Modules each PHP version loads")}</summary>
            {versions.map(v => <div className="php-module-row" key={v}>
              <strong>{tr("PHP")} {v}</strong>
              <div className="php-module-chips">{(data.modules?.[v] || []).map(m => <code key={m}>{m}</code>)}</div>
            </div>)}
          </details>
        </>}
    </div>;
  }

  function renderPhpConfig() {
    if (!isAdmin) return <section className="section"><h2>{tr("PHP config")}</h2><p className="hint">{tr("You do not have permission to edit PHP config.")}</p></section>;
    const notInstalled = sortPhpVersions(phpVersions.supported.filter(v => !phpVersions.installed.includes(v)));
    return <section className="section">
      <div className="section-title">
        <div><h2>{tr("PHP Configuration")}</h2></div>
      </div>
      <div className="user-create-card">
        <label><span>{tr("PHP version")}</span><select value={phpConfig.php_version} onChange={e => { const v = e.target.value; setPhpConfig(prev => ({ ...prev, php_version: v })); loadPhpConfig(v); }}>
          {phpVersionOptions(phpVersions.installed, phpConfig.php_version).map(v => <option key={v} value={v}>{tr("PHP")} {v}</option>)}
        </select></label>
        <label><span>display_errors</span><select value={phpConfig.display_errors} onChange={e => setPhpConfig(prev => ({ ...prev, display_errors: e.target.value }))}>
          <option value="Off">{tr("Off (production)")}</option><option value="On">{tr("On (debug)")}</option>
        </select></label>
        <label className="php-opcache-toggle"><span>{tr("OPcache")}</span>
          <button
            type="button"
            className={phpConfig.opcache_enable ? 'toggle-on' : 'secondary'}
            aria-pressed={!!phpConfig.opcache_enable}
            disabled={!!loading}
            onClick={() => setPhpConfig(prev => ({ ...prev, opcache_enable: !prev.opcache_enable }))}
            title={tr("OPcache is {0} for PHP {1}. Press Save to apply.", phpConfig.opcache_enable ? tr("on") : tr("off"), phpConfig.php_version)}
          >
            <Zap size={14}/> {phpConfig.opcache_enable ? tr("Enabled") : tr("Disabled")}
          </button>
        </label>
        <label><span>max_execution_time</span><input type="number" value={phpConfig.max_execution_time} onChange={e => setPhpConfig(prev => ({ ...prev, max_execution_time: e.target.value }))} /></label>
        <label><span>max_input_time</span><input type="number" value={phpConfig.max_input_time} onChange={e => setPhpConfig(prev => ({ ...prev, max_input_time: e.target.value }))} /></label>
        <label><span>max_input_vars</span><input type="number" value={phpConfig.max_input_vars} onChange={e => setPhpConfig(prev => ({ ...prev, max_input_vars: e.target.value }))} /></label>
        <label><span>memory_limit</span><input value={phpConfig.memory_limit} onChange={e => setPhpConfig(prev => ({ ...prev, memory_limit: e.target.value }))} placeholder="1024M" /></label>
        <label><span>post_max_size</span><input value={phpConfig.post_max_size} onChange={e => setPhpConfig(prev => ({ ...prev, post_max_size: e.target.value }))} placeholder="1024M" /></label>
        <label><span>upload_max_filesize</span><input value={phpConfig.upload_max_filesize} onChange={e => setPhpConfig(prev => ({ ...prev, upload_max_filesize: e.target.value }))} placeholder="1024M" /></label>
        <button className="secondary-light" disabled={!!loading} onClick={restorePhpDefaults}><RotateCcw size={14}/> {tr("Restore defaults")}</button>
        <button className="secondary-light" disabled={!!loading} onClick={loadPhpTuning}><Zap size={14}/> {tr("Auto-tune")}</button>
        <button disabled={!!loading} onClick={updatePhpConfig}>{tr("Save")}</button>
      </div>
      {phpTuning && phpTuning.recommendation && <div className="user-create-card" style={{ marginTop: 16, borderColor: 'var(--accent)' }}>
        <h3>{tr("⚡ Auto-tune Recommendation")}</h3>
        <p className="hint">{tr("Based on")} {phpTuning.recommendation.ram_mb} {tr("MB RAM,")} {phpTuning.recommendation.cores} {tr("CPU cores,")} {phpTuning.recommendation.is_ssd ? tr("SSD") : tr("HDD")} {tr("storage")}</p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 24px', margin: '12px 0', fontSize: '0.9em' }}>
          <div><strong>memory_limit:</strong> {phpTuning.recommendation.memory_limit}</div>
          <div><strong>{tr("OPcache memory:")}</strong> {phpTuning.recommendation.opcache_memory_consumption} {tr("MB")}</div>
          <div><strong>{tr("OPcache files:")}</strong> {phpTuning.recommendation.opcache_max_accelerated_files}</div>
          <div><strong>{tr("OPcache JIT:")}</strong> {phpTuning.recommendation.opcache_jit} ({phpTuning.recommendation.opcache_jit_buffer_size}{tr("MB)")}</div>
          <div><strong>{tr("PHP workers, whole server:")}</strong> {phpTuning.recommendation.lsapi_children} <small>{tr("(3 per CPU core an account may use, split over its sites)")}</small></div>
          <div><strong>{tr("LSAPI idle:")}</strong> {phpTuning.recommendation.lsapi_max_idle}{tr("s, max idle children:")} {phpTuning.recommendation.lsapi_max_idle_children}</div>
          <div><strong>upload_max:</strong> {phpTuning.recommendation.upload_max_filesize}</div>
          <div><strong>post_max:</strong> {phpTuning.recommendation.post_max_size}</div>
        </div>
        <button disabled={!!loading} onClick={applyPhpTuning}><Zap size={14}/> {tr("Apply Auto-tune to PHP")} {phpConfig.php_version}</button>
        <button className="secondary-light" style={{ marginLeft: 8 }} onClick={() => setPhpTuning(null)}>{tr("Dismiss")}</button>
      </div>}
      {renderPhpExtensions()}
      {notInstalled.length > 0 && <div className="user-create-card" style={{ marginTop: 16 }}>
        <h3>{tr("Install PHP")}</h3>
        <div className="php-install-grid">
          {notInstalled.map(v => <button key={v} className="secondary" disabled={!!loading} onClick={() => installPhpVersion(v)}>{tr("+ PHP")} {v}</button>)}
        </div>
      </div>}
    </section>;
  }

  // A sub-page of Firewall, like a malware scan's detail: same URL, a back button.
  function renderFirewallAddresses() {
    const list = firewallIpList || { items: [], total: 0, page: 1, size: 50, counts: firewallStatus?.ip_rule_counts || {} };
    const counts = list.counts || {};
    const pages = Math.max(1, Math.ceil((list.total || 0) / (list.size || 50)));
    return <section className="section firewall-ip-page">
      <div className="section-title">
        <div className="waf-detail-title">
          <button className="secondary" onClick={() => setShowFirewallIpList(false)}><ArrowLeft size={14}/> {tr("Firewall")}</button>
          <div><h2>{tr("Blocked and allowed addresses")}</h2><p className="hint">{tr("{0} blocked · {1} allowed", counts.blocked || 0, counts.allowed || 0)}</p></div>
        </div>
      </div>
      <div className="firewall-ip-toolbar">
        <input value={firewallIpQuery} autoFocus aria-label={tr("Search addresses")} placeholder={tr("Search an IP, network or reason")}
               onChange={e => { setFirewallIpQuery(e.target.value); setFirewallIpPage(1); }} />
        <select value={firewallIpAction} aria-label={tr("Show")} onChange={e => { setFirewallIpAction(e.target.value); setFirewallIpPage(1); }}>
          <option value="">{tr("All ({0})", (counts.blocked || 0) + (counts.allowed || 0))}</option>
          <option value="deny">{tr("Blocked ({0})", counts.blocked || 0)}</option>
          <option value="allow">{tr("Allowed ({0})", counts.allowed || 0)}</option>
        </select>
      </div>
      {list.items.length > 0 ? <ul className="firewall-ip-list">
        {list.items.map(rule => <li key={rule.id}>
          <span className={`badge ${rule.action === 'deny' ? 'bad' : 'ok'}`}>{rule.action === 'deny' ? tr("Blocked") : tr("Allowed")}</span>
          <code>{rule.network}{rule.port ? ` :${rule.port}/${String(rule.protocol || 'tcp').toUpperCase()}` : ''}</code>
          <span className="firewall-ip-note" title={rule.note || ''}>{rule.note || '—'}</span>
          <small title={rule.created_at ? new Date(rule.created_at).toLocaleString() : ''}>{[rule.source === 'mcp' ? 'MCP' : rule.source === 'panel' ? tr("Panel") : '', rule.created_at ? new Date(rule.created_at).toLocaleDateString() : ''].filter(Boolean).join(' · ')}</small>
          <button className="mini secondary-light" disabled={!!loading} onClick={() => deleteFirewallRule(rule.id, rule.network)}>{rule.action === 'deny' ? tr("Unblock") : tr("Remove")}</button>
        </li>)}
      </ul> : <p className="hint">{firewallIpList === null ? tr("Loading…") : firewallIpQuery.trim() ? tr("No address matches “{0}”.", firewallIpQuery.trim()) : tr("No address is blocked or allowed by a panel rule. Blocklists and Fail2ban bans are listed on their own.")}</p>}
      {pages > 1 && <div className="firewall-ip-pager">
        <button className="mini secondary" disabled={list.page <= 1} onClick={() => setFirewallIpPage(p => Math.max(1, p - 1))}>{tr("Previous")}</button>
        <span className="hint">{tr("Page {0} of {1} · {2} addresses", list.page, pages, list.total)}</span>
        <button className="mini secondary" disabled={list.page >= pages} onClick={() => setFirewallIpPage(p => p + 1)}>{tr("Next")}</button>
      </div>}
    </section>;
  }

  function renderFirewall() {
    if (!isAdmin) return <section className="section"><h2>{tr("Firewall")}</h2><p className="hint">{tr("No permission.")}</p></section>;
    if (showFirewallIpList) return renderFirewallAddresses();
    const firewallText = firewallStatus?.stdout || firewallStatus?.stderr || tr("Click Refresh to load status.");
    const blocklistText = firewallBlocklists?.stdout || firewallBlocklists?.stderr || tr("No blocklist status loaded.");
    const blocklistUrls = parseFirewallBlocklistUrls(blocklistText);
    const openPorts = Array.isArray(firewallStatus?.open_ports) ? firewallStatus.open_ports : [];
    const ipCounts = firewallStatus?.ip_rule_counts || {};
    return <>
      <section className="section">
        <div className="section-title">
          <div><h2>{tr("Firewall (iptables)")}</h2><p className="hint">{tr("Keep SSH and web ports allowed before enabling.")}</p></div>
          <div className="actions">
            <button className="secondary" disabled={!!loading} onClick={loadFirewall}><RefreshCw size={14}/> {tr("Refresh")}</button>
            <button className="secondary" disabled={!!loading} onClick={reloadFirewall}>{tr("Reload")}</button>
            <button className="danger-light" disabled={!!loading} onClick={disableFirewall}>{tr("Disable")}</button>
            <button disabled={!!loading} onClick={enableFirewall}><Shield size={14}/> {tr("Enable")}</button>
          </div>
        </div>
        <div className="info-box firewall-open-ports">
          <strong>{tr("Open ports")}</strong>
          {openPorts.length > 0 ? <div className="firewall-port-list">
            {openPorts.map(item => <span className="firewall-port-chip" key={`${item.protocol}-${item.port}-${item.zone}-${item.rule || ''}`}>
              <code>{item.port}/{String(item.protocol || 'tcp').toUpperCase()}</code>
              <small>{item.zone || tr("UserZone")}{item.source && item.source !== 'Anywhere' ? tr(" from {0}", item.source) : ''}</small>
            </span>)}
          </div> : <p className="hint">{tr("No open port rules found in OPANEL chains.")}</p>}
          {firewallStatus && <p className="hint">{firewallStatus.default_deny
            ? tr("Every other incoming port is closed. Open a port below to make another service reachable.")
            : tr("Ports not listed here are not blocked: this firewall blocks the addresses and rules you add.")}</p>}
        </div>
        <div className="firewall-ip-summary">
          <span><strong>{tr("Addresses")}</strong> {tr("{0} blocked · {1} allowed", ipCounts.blocked || 0, ipCounts.allowed || 0)}</span>
          <button className="secondary" disabled={!firewallStatus} onClick={() => { setFirewallIpList(null); setFirewallIpQuery(''); setFirewallIpAction(''); setFirewallIpPage(1); setShowFirewallIpList(true); }}><Ban size={14}/> {tr("View list")}</button>
        </div>
        <details className="raw-output firewall-status">
          <summary>{tr("iptables status")}</summary>
          <div className="raw-output-body">
          <pre>{firewallText}</pre>
          <div className="firewall-delete-inline">
            <label><span>{tr("Delete UserZone #")}</span><input value={firewallDeleteNumber} onChange={e => setFirewallDeleteNumber(e.target.value)} placeholder="12" inputMode="numeric" /></label>
            <button className="danger" disabled={!!loading || !firewallDeleteNumber} onClick={() => deleteFirewallRule()}>{tr("Delete")}</button>
          </div>
          </div>
        </details>
      </section>
      <div className="firewall-rule-forms">
      <section className="section">
        <h2>{tr("Open port")}</h2>
        <div className="firewall-form">
          <label><span>{tr("Port")}</span><input value={firewallPort} onChange={e => setFirewallPort(e.target.value)} placeholder="80" inputMode="numeric" /></label>
          <label><span>{tr("Protocol")}</span><select value={firewallProtocol} onChange={e => setFirewallProtocol(e.target.value)}><option value="tcp">{tr("TCP")}</option><option value="udp">{tr("UDP")}</option></select></label>
          <button disabled={!!loading || !firewallPort} onClick={openFirewallPort}>{tr("Open port")}</button>
        </div>
      </section>
      <section className="section">
        <h2>{tr("Allow IP")}</h2>
        <div className="firewall-form">
          <label><span>{tr("IP / CIDR")}</span><input value={firewallAllowIp} onChange={e => setFirewallAllowIp(e.target.value)} placeholder="1.2.3.4" /></label>
          <label><span>{tr("Port (optional)")}</span><input value={firewallAllowPort} onChange={e => setFirewallAllowPort(e.target.value)} placeholder="22" inputMode="numeric" /></label>
          <label><span>{tr("Protocol")}</span><select value={firewallAllowProtocol} onChange={e => setFirewallAllowProtocol(e.target.value)}><option value="tcp">{tr("TCP")}</option><option value="udp">{tr("UDP")}</option></select></label>
          <button disabled={!!loading || !firewallAllowIp} onClick={allowFirewallIp}>{tr("Allow")}</button>
        </div>
      </section>
      <section className="section">
        <h2>{tr("Block IP")}</h2>
        <div className="firewall-form">
          <label><span>{tr("IP / CIDR")}</span><input value={firewallBlockIp} onChange={e => setFirewallBlockIp(e.target.value)} placeholder="5.6.7.8" /></label>
          <label><span>{tr("Port (optional)")}</span><input value={firewallBlockPort} onChange={e => setFirewallBlockPort(e.target.value)} placeholder={tr("All ports")} inputMode="numeric" /></label>
          <label><span>{tr("Protocol")}</span><select value={firewallBlockProtocol} onChange={e => setFirewallBlockProtocol(e.target.value)}><option value="tcp">{tr("TCP")}</option><option value="udp">{tr("UDP")}</option></select></label>
          <label className="firewall-note-field"><span>{tr("Reason (optional)")}</span><input value={firewallBlockNote} maxLength={120} onChange={e => setFirewallBlockNote(e.target.value)} placeholder={tr("e.g. scanner hitting wp-login")} /></label>
          <button className="danger" disabled={!!loading || !firewallBlockIp} onClick={blockFirewallIp}>{tr("Block")}</button>
        </div>
      </section>
      </div>
      {renderFirewallFail2ban()}
      <section className="section">
        <div className="section-title">
          <div><h2>{tr("Blocklist URLs")}</h2><p className="hint">{tr("TXT files are fetched daily at 01:00 and enforced by ipset, so large lists do not create thousands of firewall rules.")}</p></div>
          <button className="secondary" disabled={!!loading} onClick={loadFirewallBlocklists}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        <div className="firewall-form firewall-blocklist-form">
          <label><span>{tr("TXT URL")}</span><input value={firewallBlocklistUrl} onChange={e => setFirewallBlocklistUrl(e.target.value)} placeholder="https://example.com/blocklist.txt" /></label>
          <button disabled={!!loading || !firewallBlocklistUrl.trim()} onClick={addFirewallBlocklistUrl}><Plus size={14}/> {tr("Add URL")}</button>
          <button className="secondary-light" disabled={!!loading} onClick={updateFirewallBlocklistsNow}><RefreshCw size={14}/> {tr("Update now")}</button>
        </div>
        {blocklistUrls.length > 0 && <div className="table firewall-blocklist-table">
          {blocklistUrls.map(url => <div className="firewall-rule" key={url}>
            <span>{url}</span>
            <div className="firewall-rule-actions"><button className="danger" disabled={!!loading} onClick={() => deleteFirewallBlocklistUrl(url)}><Trash2 size={14}/> {tr("Delete")}</button></div>
          </div>)}
        </div>}
        <details className="raw-output firewall-status"><summary>{tr("Blocklist status")}</summary><pre>{blocklistText}</pre></details>
      </section>
    </>;
  }

  function renderWaf() {
    // The status probe prints "installed" or "not-installed"; that is all the page needs to say.
    const wafProbe = `${wafRules.status?.stdout || ''}`;
    const wafEngine = /not-installed/.test(wafProbe) ? 'off' : /\binstalled\b/.test(wafProbe) ? 'on' : 'unknown';
    const selectedSite = websites.find(site => String(site.id) === String(selectedWafWebsiteId));
    const groupedRules = (wafSiteConfig?.default_rules || wafRules.default_rule_definitions || []).reduce((groups, rule) => {
      const category = rule.category || 'General';
      groups[category] = groups[category] || [];
      groups[category].push(rule);
      return groups;
    }, {});
    return <>
      {!wafSiteConfig && <>
        {isAdmin && <section className="section">
          <div className="section-title">
            <div>
              <h2>{tr("Global bad bot")} <span className="badge">{badBots.patterns.length}</span></h2>
              <p className="hint">{tr("One bot per line, applied to every website. A request whose User-Agent contains the text gets 403 — plain text, no regex.")}</p>
            </div>
            <button className="secondary" disabled={!!loading} onClick={loadBadBots}><RefreshCw size={14}/> {tr("Refresh")}</button>
          </div>
          <textarea className="code-editor badbot-input" value={badBotText} onChange={e => setBadBotText(e.target.value)}
            spellCheck={false}
            placeholder={tr("AhrefsBot\nSemrushBot\nMJ12bot\nGPTBot\nBytespider")} />
          <div className="actions"><button disabled={!!loading} onClick={saveBadBots}><Shield size={14}/> {tr("Save and apply to all websites")}</button></div>
        </section>}

        <section className="section">
          <div className="section-title">
            <div>
              <h2>{isAdmin ? tr("Websites") : tr("Your websites")} {isAdmin && <span className={wafEngine === 'on' ? 'badge ok' : wafEngine === 'off' ? 'badge warn' : 'badge'}>{wafEngine === 'on' ? tr("WAF engine on") : wafEngine === 'off' ? tr("WAF engine off") : tr("WAF engine: checking…")}</span>}</h2>
              <p className="hint">{tr("Open a website to configure its rules and bad bots.")}</p>
            </div>
            {isAdmin && <button className="secondary" disabled={!!loading} onClick={loadWafRules}><RefreshCw size={14}/> {tr("Refresh")}</button>}
          </div>
          {websites.length === 0
            ? <EmptyState icon={Globe} message={tr("No websites yet.")} />
            : <div className="waf-site-table">
                {websites.map(site => <button key={site.id} type="button" className="waf-site-row"
                  disabled={!!loading} onClick={() => loadWebsiteWafConfig(site.id)}>
                  <span className="waf-site-domain">{site.domain}</span>
                  <span className="waf-site-badges">
                    <span className={site.waf_enabled ? 'badge ok' : 'badge'}>{site.waf_enabled ? tr("WAF on") : tr("WAF off")}</span>
                  </span>
                  <span className="waf-site-open">{tr("Configure")}</span>
                </button>)}
              </div>}
        </section>
      </>}

      {wafSiteConfig && <>
        <section className="section">
          <div className="section-title waf-detail-head">
            <div className="waf-detail-title">
              <button className="secondary-light" onClick={closeWafSiteConfig}><ArrowLeft size={14}/> {tr("All websites")}</button>
              <div><h2>{wafSiteConfig.domain}</h2><p className="hint">{tr("WAF configuration for this website.")}</p></div>
            </div>
            <div className="waf-detail-actions">
              <span className={selectedSite?.waf_enabled ? 'badge ok' : 'badge'}>{selectedSite?.waf_enabled ? tr("WAF on") : tr("WAF off")}</span>
              <button disabled={!!loading} onClick={() => selectedSite && toggleWebsiteWaf(selectedSite)}>
                <Shield size={14}/> {selectedSite?.waf_enabled ? tr("Disable WAF") : tr("Enable WAF")}
              </button>
            </div>
          </div>
        </section>

        <section className="section">
          <div className="section-title">
            <div>
              <h2>{tr("Bad bot")}</h2>
              <p className="hint">
                {wafSiteConfig.bot_blocking_enabled
                  ? tr("Global list ({0}) plus anything below.", (wafSiteConfig.global_bad_bots || []).length)
                  : tr("Off — the global list is ignored on this website.")}
              </p>
            </div>
            <span className={wafSiteConfig.bot_blocking_enabled ? 'badge ok' : 'badge'}>
              {wafSiteConfig.bot_blocking_enabled ? tr("On") : tr("Off")}
            </span>
          </div>
          <label className="check-line">
            <input type="checkbox" checked={!!wafSiteConfig.bot_blocking_enabled}
              onChange={e => setWafSiteConfig(prev => ({ ...prev, bot_blocking_enabled: e.target.checked }))} />
            {tr("Block bad bots on this website")}
          </label>
          <label className="badbot-site-extra"><span>{tr("Extra bots, this website only")}</span>
            <textarea className="code-editor badbot-input" value={wafBotExtra} onChange={e => setWafBotExtra(e.target.value)}
              spellCheck={false} placeholder={tr("ScrapyBot\nSomeOtherBot")} />
          </label>
          <div className="actions"><button disabled={!!loading} onClick={saveWebsiteWafRules}><Shield size={14}/> {tr("Save bad bot config")}</button></div>
        </section>

        <section className="section waf-rules-grid">
          <div className="waf-rule-panel">
            <div className="section-title"><h2>{tr("Default rules")}</h2></div>
            <div className="waf-default-groups">
              {Object.entries(groupedRules).map(([category, rules]) => <div className="waf-rule-group" key={category}>
                <h3>{tr(category)}</h3>
                {rules.map(rule => <label className="waf-rule-toggle" key={rule.id}>
                  <input type="checkbox" checked={!!rule.enabled} onChange={e => toggleWafDefaultRule(rule.id, e.target.checked)} />
                  <span><strong>{tr(rule.title)}</strong><small>{tr(rule.description)}</small></span>
                </label>)}
              </div>)}
            </div>
          </div>
          <div className="waf-rule-panel">
            <div className="section-title"><h2>{tr("Custom rules")}</h2></div>
            {isAdmin
              ? <>
                  <textarea className="code-editor" value={wafCustomRules} onChange={e => setWafCustomRules(e.target.value)} rows={14} spellCheck={false} placeholder={tr("SecRule ...")} />
                  <p className="hint">{tr("Saved into")} {wafSiteConfig.rules_file}</p>
                </>
              : <>
                  {wafCustomRules
                    ? <pre className="code-editor waf-custom-readonly">{wafCustomRules}</pre>
                    : <p className="hint">{tr("No custom rules on this website.")}</p>}
                  <p className="hint">{tr("Custom rules are written by an administrator. Everything else on this page is yours to change.")}</p>
                </>}
            <div className="actions"><button disabled={!!loading} onClick={saveWebsiteWafRules}>{tr("Save website WAF rules")}</button></div>
          </div>
        </section>
      </>}
    </>;
  }

  function renderWafAccessLogs() {
    // The API scopes every report to the caller's own domains, so an end user
    // sees what was blocked on their sites and nothing from anyone else's.
    const entries = wafAccessLogs.entries || [];
    const total = Number(wafAccessLogs.total || 0);
    const limit = Number(wafAccessFilters.limit || wafAccessLogs.limit || 50);
    const offset = Number(wafAccessFilters.offset || wafAccessLogs.offset || 0);
    const currentPage = Math.floor(offset / limit) + 1;
    const pageCount = Math.max(1, Math.ceil(total / limit));
    const domains = wafAccessLogs.domains?.length ? wafAccessLogs.domains : websites.map(site => site.domain);
    return <>
      <section className="access-log-hero">
        <div>
          <p className="eyebrow">{tr("Protected Traffic")}</p>
          <h1>{tr("Access Logs")}</h1>
        </div>
        <div className="access-log-hero-actions">
          <button className="secondary-light icon-only" onClick={() => loadWafAccessLogs()} disabled={!!loading} aria-label={tr("Refresh logs")} title={tr("Refresh logs")}><RefreshCw size={16}/></button>
          <button className="secondary-light icon-only" onClick={() => navigateToPage('waf')} aria-label={tr("Open WAF settings")} title={tr("Open WAF settings")}><ExternalLink size={16}/></button>
        </div>
      </section>
      <section className="section access-log-section">
        <div className="access-log-toolbar">
          <div className="access-log-title">
            <strong>{tr("Access Logs")}</strong>
            <span>{formatLogCount(total)} {tr("entries")}</span>
            <button className="secondary-light" onClick={exportWafAccessLogs} disabled={!entries.length}><Download size={14}/> {tr("Export")}</button>
            <button className="danger-light" onClick={clearWafAccessLogs} disabled={!!loading || total === 0}><Trash2 size={14}/> {tr("Clear")}</button>
          </div>
          <div className="access-log-filters">
            <select value={wafAccessFilters.domain} onChange={e => setWafAccessFilter('domain', e.target.value)}>
              <option value="">{tr("All websites")}</option>
              {domains.map(domain => <option key={domain} value={domain}>{domain}</option>)}
            </select>
            <select value={wafAccessFilters.verdict} onChange={e => setWafAccessFilter('verdict', e.target.value)}>
              <option value="">{tr("All verdicts")}</option>
              <option value="block">{tr("Block")}</option>
              <option value="allow">{tr("Allow")}</option>
            </select>
            <input value={wafAccessFilters.q} onChange={e => setWafAccessFilter('q', e.target.value)} placeholder={tr("Filter logs")} />
            <select value={limit} onChange={e => setWafAccessFilter('limit', Number(e.target.value))}>
              {[25, 50, 100, 250, 500].map(size => <option key={size} value={size}>{size} {tr("/ page")}</option>)}
            </select>
            <select value={wafAccessAutoRefresh} onChange={e => setWafAccessAutoRefresh(Number(e.target.value))} title={tr("Auto refresh")}>
              <option value={0}>{tr("Auto refresh off")}</option>
              <option value={5}>{tr("Refresh 5s")}</option>
              <option value={10}>{tr("Refresh 10s")}</option>
              <option value={30}>{tr("Refresh 30s")}</option>
            </select>
          </div>
        </div>
        <div className="access-log-table-wrap">
          <table className="access-log-table">
            <thead>
              <tr>
                <th>{tr("Verdict")}</th>
                <th>{tr("Time")}</th>
                <th>{tr("Site")}</th>
                <th>{tr("Method")}</th>
                <th>{tr("Path")}</th>
                <th>{tr("IP")}</th>
                <th>{tr("Reason")}</th>
                <th>{tr("Status")}</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, index) => <tr key={`${entry.domain}-${entry.timestamp}-${entry.ip}-${entry.path}-${index}`}>
                <td><span className={`verdict-pill ${entry.verdict === 'block' ? 'block' : 'allow'}`}>{entry.verdict === 'block' ? tr("Block") : tr("Allow")}</span></td>
                <td><span>{entry.time || entry.timestamp || '-'}</span><small>{formatLogDuration(entry.duration_ms)}</small></td>
                <td><span>{entry.site || entry.domain}</span><small>{entry.domain}</small></td>
                <td>{entry.method || '-'}</td>
                <td><span className="access-log-path">{entry.path || '-'}</span></td>
                <td><span>{entry.ip || '-'}</span><small>{entry.country || ''}</small></td>
                <td>{entry.reason || (entry.verdict === 'block' ? tr("Blocked") : tr("Allowed"))}</td>
                <td>{entry.status || '-'}</td>
              </tr>)}
            </tbody>
          </table>
          {entries.length === 0 && <EmptyState icon={Shield} message={tr("No access log entries match the current filters.")} />}
        </div>
        <div className="access-log-footer">
          <span>{tr("Page")} {currentPage} {tr("of")} {pageCount}</span>
          <div className="actions">
            <button className="secondary-light" disabled={offset <= 0 || !!loading} onClick={() => loadWafAccessLogs({ ...wafAccessFilters, offset: Math.max(0, offset - limit) })}>{tr("Previous")}</button>
            <button className="secondary-light" disabled={offset + limit >= total || !!loading} onClick={() => loadWafAccessLogs({ ...wafAccessFilters, offset: offset + limit })}>{tr("Next")}</button>
          </div>
        </div>
      </section>
    </>;
  }

  function renderUpdates() {
    if (!isAdmin) return <section className="section"><h2>{tr("Updates")}</h2><p className="hint">{tr("No permission.")}</p></section>;
    const statusText = updatesStatus?.stdout || updatesStatus?.stderr || tr("Click View logs to load update logs.");
    const panelUpdate = updatesStatus?.panel || {};
    // Three states, not two. `update_available` is null when the check could
    // not tell -- an unreadable VERSION on the branch, or a box that has not
    // yet recorded the commit it installed -- and "unknown" must not be
    // dressed up as "up to date".
    const hasUpdate = panelUpdate.update_available;
    const panelBadge = hasUpdate === true ? tr("Update available")
      : hasUpdate === false ? 'Up to date'
      : panelUpdate.latest_commit ? tr("Tracking main") : tr("Unknown");
    const panelBadgeClass = hasUpdate === true ? 'badge warn'
      : hasUpdate === false ? 'badge ok' : 'badge';
    const currentPanelVersion = panelUpdate.current_version || appVersion || 'unknown';
    const latestPanelVersion = panelUpdate.latest_version || '';
    const newerVersion = Boolean(latestPanelVersion) && latestPanelVersion !== currentPanelVersion;
    const latestPanelRef = panelUpdate.latest_commit ? panelUpdate.latest_commit.slice(0, 12) : 'unknown';
    return <>
      <section className="section">
        <div className="section-title">
          <div><h2>{tr("Updates")}</h2><p className="hint">{tr("OS packages use apt; panel updates pull from GitHub")} <code>main</code>.</p></div>
          <button className="secondary-light" disabled={!!loading} onClick={toggleUpdateLog}>{showUpdateLog ? <X size={14}/> : <FileText size={14}/>} {showUpdateLog ? tr("Hide logs") : tr("View logs")}</button>
        </div>
        <div className="info-box update-version-box">
          <div className="update-version-head"><strong>{tr("Panel source")}</strong><span className={panelBadgeClass}>{panelBadge}</span></div>
          <div className="update-version-grid">
            <span>{tr("Current")} <strong>v{currentPanelVersion}</strong></span>
            {newerVersion && <span>{tr("Available")} <strong>v{latestPanelVersion}</strong></span>}
            <span>{tr("Branch")} <strong>{panelUpdate.update_branch || tr("main")}</strong></span>
            <span>{tr("Latest commit")} <strong>{latestPanelRef}</strong></span>
            <span>{tr("Checked")} <strong>{panelUpdate.last_checked_at || tr("never")}</strong></span>
            <span>{tr("State file")} <strong>{panelUpdate.state_file || '/var/lib/opanel/update-status.json'}</strong></span>
          </div>
          {panelUpdate.check_error && <p className="hint">{tr("Main branch check failed:")} {panelUpdate.check_error}</p>}
          {hasUpdate !== true && hasUpdate !== false && !panelUpdate.check_error &&
            <p className="hint">{tr("Cannot tell yet whether a release is waiting. Run one panel update to record the installed commit, after which this compares exactly.")}</p>}
          {panelUpdate.last_update_status && <p className="hint">{tr("Last update:")} {panelUpdate.last_update_status}{panelUpdate.last_update_ref ? ` (${panelUpdate.last_update_ref})` : ''}{panelUpdate.last_update_finished_at ? tr(" at {0}", panelUpdate.last_update_finished_at) : ''}</p>}
        </div>
        <div className="actions">
          <button className="secondary-light" disabled={!!loading} onClick={() => loadUpdates(true)}><RefreshCw size={14}/> {tr("Refresh status")}</button>
          <button className="secondary" disabled={!!loading || osUpdating} onClick={runOsUpdate}><RefreshCw size={14} className={osUpdating ? 'spin' : ''}/> {osUpdating ? tr("Updating OS...") : tr("Update OS now")}</button>
          <button disabled={!!loading || panelUpdating} onClick={runPanelUpdate}><RotateCcw size={14} className={panelUpdating ? 'spin' : ''}/> {panelUpdating ? tr("Updating panel...") : tr("Update panel now")}</button>
        </div>
        {showUpdateLog && <div className="info-box firewall-status update-log-box">
          <div className="update-log-head"><strong>{tr("Update logs")}</strong><button className="secondary-light" disabled={!!loading} onClick={() => loadUpdates(true)}><RefreshCw size={13}/> {tr("Refresh")}</button></div>
          <pre>{statusText}</pre>
        </div>}
        {panelUpdate.last_update_status !== 'completed' && panelUpdate.last_update_status !== 'failed' && (panelUpdating || (Boolean(panelUpdate.progress_percent) && panelUpdate.last_update_status)) && (
          <div className="info-box firewall-status update-progress-box">
            <div className="update-progress-row">
              <span className={panelUpdate.last_update_status === 'failed' ? 'badge bad' : 'badge ok'}>
                {panelUpdating ? tr("Running") : (panelUpdate.last_update_status === 'failed' ? tr("Failed") : (panelUpdate.last_update_status || tr("Idle")))}
              </span>
              <span className="update-progress-phase">{panelUpdate.progress_phase || ''}</span>
              <span className="update-progress-pct">{Number(panelUpdate.progress_percent) || 0}%</span>
            </div>
            <div className="progress-bar"><div className="progress-bar-fill" style={{ width: `${Number(panelUpdate.progress_percent) || 0}%` }} /></div>
            {panelUpdate.progress_message && <p className="hint update-progress-msg">{panelUpdate.progress_message}</p>}
            {panelUpdateLog.length > 0 && (
              <pre className="update-progress-log">{panelUpdateLog.join('\n')}</pre>
            )}
          </div>
        )}
      </section>
      <section className="section">
        <h2>{tr("Auto Update OS")}</h2>
        <div className="firewall-form updates-os-form">
          <label><span>{tr("Enabled")}</span><select value={osAutoUpdate.enabled ? 'on' : 'off'} onChange={e => setOsAutoUpdate(prev => ({ ...prev, enabled: e.target.value === 'on' }))}><option value="on">{tr("On")}</option><option value="off">{tr("Off")}</option></select></label>
          <label><span>{tr("Mode")}</span><select value={osAutoUpdate.mode} onChange={e => setOsAutoUpdate(prev => ({ ...prev, mode: e.target.value }))}><option value="security">{tr("Security")}</option><option value="all">{tr("All packages")}</option></select></label>
          <label><span>{tr("Auto reboot")}</span><select value={osAutoUpdate.auto_reboot ? 'on' : 'off'} onChange={e => setOsAutoUpdate(prev => ({ ...prev, auto_reboot: e.target.value === 'on' }))}><option value="off">{tr("Off")}</option><option value="on">{tr("On")}</option></select></label>
          <button disabled={!!loading} onClick={saveOsAutoUpdate}>{tr("Save OS auto update")}</button>
        </div>
      </section>
    </>;
  }

  function renderSecurity() {
    const enabled = Boolean(twoFactorStatus?.enabled || currentUser?.totp_enabled);
    const pk = passkeyStatus || {};
    const keys = pk.passkeys || [];
    // Independent. Sign-in prefers the passkey; the code is what gets the
    // owner in when the passkey cannot be used.
    const canEnableTotp = pk.can_enable_totp !== undefined ? pk.can_enable_totp : !enabled;
    return <>
      <section className="section">
        <div className="section-title">
          <div>
            <h2>{tr("Passkey")}</h2>
            <p className="hint">
              {keys.length
                ? <>{tr("Tried first when you sign in.")} <strong>{keys.length}</strong> {tr("registered")}
                    {enabled ? tr(", with your authenticator code as the fallback.") : '.'}</>
                : tr("Sign in with a fingerprint, face, screen lock, or security key.")}
            </p>
          </div>
          <button className="secondary" disabled={!!loading} onClick={loadPasskeys}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        {pk.available === false && <p className="hint">{pk.unavailable_reason}</p>}
        {keys.length > 0 && <div className="table">
          {keys.map(item => <div className="row passkey-row" key={item.id}>
            <span>
              <strong>{item.name}</strong>
              <small className="db-owner">
                {tr("Added")} {item.created_at ? new Date(item.created_at).toLocaleDateString() : tr("recently")}
                {item.last_used_at ? tr(" · last used {0}", new Date(item.last_used_at).toLocaleDateString()) : tr(" · not used yet")}
              </small>
            </span>
            <button className="mini danger" disabled={!!loading}
                    title={tr("Remove this passkey")} aria-label={tr("Remove the passkey {0}", item.name)}
                    onClick={() => removePasskey(item)}><Trash2 size={14}/></button>
          </div>)}
        </div>}
        {pk.available !== false && (pk.can_add_passkey !== false) && <div className="passkey-add">
          <input value={passkeyName} onChange={e => setPasskeyName(e.target.value)}
                 placeholder={tr("Name this passkey (e.g. Work laptop)")} maxLength={64} />
          <button disabled={!!loading} onClick={registerPasskey}><Shield size={14}/> {tr("Add passkey")}</button>
        </div>}
        {keys.length > 0 && !enabled &&
          <p className="hint">{tr("Add an authenticator app below as a fallback, in case you cannot use this passkey.")}</p>}
      </section>
      <section className="section">
        <div className="section-title">
          <div><h2>{tr("Google Authenticator 2FA")}</h2><p className="hint">{tr("Current status:")} <strong>{enabled ? tr("Enabled") : tr("Disabled")}</strong></p></div>
          <button className="secondary" disabled={!!loading} onClick={loadTwoFactorStatus}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        {!enabled && keys.length > 0 && <p className="hint">{tr("Used when a passkey is not available on the device you are signing in from.")}</p>}
        {!enabled && canEnableTotp && <div className="security-grid">
          <div className="info-box">
            <strong>{tr("Setup")}</strong>
            {twoFactorSetup?.qr_data_url ? <img className="qr-code" src={twoFactorSetup.qr_data_url} alt={tr("2FA QR code")} /> : <p className="hint">{tr("No setup code generated.")}</p>}
            {twoFactorSetup?.secret && <code className="secret-text">{twoFactorSetup.secret}</code>}
            <div className="actions">
              <button disabled={!!loading} onClick={setupTwoFactorAuth}><Shield size={14}/> {tr("Generate QR")}</button>
            </div>
          </div>
          <div className="info-box">
            <strong>{tr("Verify")}</strong>
            <input value={twoFactorCode} onChange={e => setTwoFactorCode(e.target.value)} placeholder="123456" inputMode="numeric" />
            <button disabled={!!loading || !twoFactorSetup || !twoFactorCode} onClick={enableTwoFactorAuth}><Lock size={14}/> {tr("Enable 2FA")}</button>
          </div>
        </div>}
        {enabled && <div className="security-grid one">
          <div className="info-box">
            <strong>{tr("Disable 2FA")}</strong>
            <input value={twoFactorCode} onChange={e => setTwoFactorCode(e.target.value)} placeholder="123456" inputMode="numeric" />
            <button className="danger" disabled={!!loading || !twoFactorCode} onClick={disableTwoFactorAuth}>{tr("Disable 2FA")}</button>
          </div>
        </div>}
      </section>
    </>;
  }

  function renderMalwareScanner() {
    if (!isAdmin) return <section className="section"><h2>{tr("Malware Scanner")}</h2><p className="hint">{tr("No permission.")}</p></section>;
    const mw = malwareScanStatus || {};
    const mwActive = Boolean(mw.active);
    const mwInstalled = Boolean(mw.installed);
    const mwEnabled = Boolean(mw.enabled);
    const activeScanJob = scanJob || scanResults || {};
    const scanRunning = ['queued', 'running'].includes(scanJob?.status);
    const scanJobTitle = job => (job.domains && job.domains.length > 0)
      ? (job.domains.length === 1 ? job.domains[0] : `${job.domains.length} websites`)
      : (job.scope === 'all' ? tr("All websites")
        : job.scope === 'system' ? tr("Full server ({0})", job.scan_root || '/')
        : tr("Scan job"));
    const scanJobStamp = job => {
      const stamp = job.finished_at || job.updated_at || job.started_at || job.created_at || '';
      if (!stamp) return 'No timestamp';
      const date = new Date(stamp);
      return Number.isNaN(date.getTime()) ? stamp : new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Asia/Ho_Chi_Minh',
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
      }).format(date).replace(',', '');
    };
    const scanJobDetail = job => `${job.scanned || 0}/${job.total_files || job.scanned || 0} files, ${job.infected || 0} threats, ${job.errors || 0} errors`;
    const scanJobMeta = job => `${scanJobStamp(job)} / ${scanJobDetail(job)}`;
    const scanJobBadgeClass = job => {
      if (job.status === 'done') return 'badge ok';
      if (job.status === 'infected') return 'badge danger';
      if (['error', 'interrupted'].includes(job.status)) return 'badge bad';
      return 'badge warn';
    };
    if (malwareDetailJob) {
      const job = malwareDetailJob;
      const started = job.started_at ? new Date(job.started_at) : null;
      const finished = job.finished_at ? new Date(job.finished_at) : null;
      const seconds = started && finished ? Math.max(0, Math.round((finished - started) / 1000)) : null;
      const duration = seconds == null ? '—' : seconds >= 3600 ? `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m` : seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
      return <section className="section scan-detail">
        <div className="section-title">
          <div className="waf-detail-title">
            <button className="secondary" onClick={() => setMalwareDetailJob(null)}><ArrowLeft size={14}/> {tr("Malware scanner")}</button>
            <div><h2>{scanJobTitle(job)}</h2><p className="hint">{scanJobStamp(job)}</p></div>
          </div>
          <span className={scanJobBadgeClass(job)}>{job.status}</span>
        </div>
        <div className="scan-detail-stats">
          <div><small>{tr("Files scanned")}</small><strong>{job.scanned || 0}{job.total_files ? ` / ${job.total_files}` : ''}</strong></div>
          <div><small>{tr("Threats found")}</small><strong className={job.infected > 0 ? 'text-danger' : ''}>{job.infected || 0}</strong></div>
          <div><small>{tr("Errors")}</small><strong>{job.errors || 0}</strong></div>
          <div><small>{tr("Duration")}</small><strong>{duration}</strong></div>
        </div>
        {job.message && <p className="hint">{job.message}</p>}
        {job.error && <p className="hint" style={{ color: 'var(--danger)' }}>{job.error}</p>}
        <h3>{tr("Threats")}</h3>
        {job.threats && job.threats.length > 0
          ? <div className="scan-threat-list">
              {job.threats.map((t, i) => <div key={i} className="scan-threat-item">
                <strong>{t.signature}</strong>
                <span>{t.domain ? `${t.domain}: ` : ''}{t.path}</span>
                {(t.quarantined || quarantine.some(q => q.original_path === t.path))
                  ? <span className="badge ok">{tr("Quarantined")}</span>
                  : <button className="secondary-light" disabled={!!loading} onClick={() => quarantineThreat(t.path, t.signature)}>{tr("Quarantine")}</button>}
              </div>)}
            </div>
          : <div className="quarantine-empty"><CheckCircle size={16}/> {tr("No threats in this scan.")}</div>}
        {job.log && job.log.length > 0 && <details className="raw-output" open={job.infected > 0 || job.status === 'error'}>
          <summary>{tr("Scan log")}</summary>
          <pre className="malware-scan-log">{job.log.join('\n')}</pre>
        </details>}
      </section>;
    }
    return <>
      {isAdmin && <section className="section">
        <div className="section-title">
          <div>
            <h2>{tr("Malware Scanner")}</h2>
            <p className="hint badge-row">
              {mwActive ? <span className="badge ok">{tr("Active")}</span>
                : mwEnabled && mwInstalled ? <span className="badge warn">{tr("Enabled — clamd not running")}</span>
                : mwEnabled && !mwInstalled ? <span className="badge warn">{tr("Installing ClamAV...")}</span>
                : mwInstalled && !mwEnabled ? <span className="badge">{tr("Installed — scanning disabled")}</span>
                : <span className="badge">{tr("Not installed")}</span>}
              {mwInstalled && <span className="badge">{tr("Engine:")} {mw.engine || tr("ClamAV")}{mw.lmd_version ? tr(" (LMD {0})", mw.lmd_version) : ''}</span>}
              {mwInstalled && mw.signature_filter === 'on' && mw.signatures_total > 0 && <span className="badge" title={tr("clam-juice keeps the signatures a Linux web server needs and drops Windows, macOS and Office malware, so clamd uses far less memory.")}>{tr("Signatures: {0} of {1} (clam-juice)", Number(mw.signatures_kept).toLocaleString(), Number(mw.signatures_total).toLocaleString())}</span>}
              {mwInstalled && mw.signature_filter === 'pending' && <span className="badge">{tr("Filtering signatures...")}</span>}
              {mwInstalled && mw.signature_filter === 'failed' && <span className="badge warn">{tr("Signature filter failed: full databases in use")}</span>}
              {mw.realtime_active && <span className="badge ok">{tr("Real-time on")}</span>}
            </p>
          </div>
          <button className="secondary" disabled={!!loading} onClick={loadMalwareScanStatus}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        <div className="info-box">
          <p className="hint">{mw.detail || tr("Checking status...")}</p>
          {!mwEnabled && <p className="hint" style={{marginTop:8}}>{tr("The Malware Scanner is an addon. Install or start it on the Addons page; stopping or removing it there frees the memory ClamAV uses.")}</p>}
          {mwInstalled && mwActive && <p className="hint" style={{marginTop:8}}>{tr("Uploaded files are scanned automatically. Scheduled scans default to incremental (files changed recently) once a full baseline scan has run, with a full scan at least weekly.")}</p>}
          <div className="actions" style={{marginTop:12}}>
            {!mwEnabled && <button disabled={!!loading} onClick={() => navigateToPage('addons')}><PackageOpen size={14}/> {tr("Open Addons")}</button>}
            {mwActive && <button className="secondary" disabled={!!loading} onClick={updateMalwareSignatures}><RefreshCw size={14}/> {tr("Update signatures")}</button>}
          </div>
        </div>

        {mwActive && !mw.lmd_installed && <div className="info-box">
          <div className="malware-scan-head">
            <div>
              <strong>{tr("Linux Malware Detect")} <span className="badge">{tr("Not installed")}</span></strong>
              <p className="hint">{tr("This box runs ClamAV only. Add LMD for its web-focused signatures (PHP shells, injected malware ClamAV misses) and the incremental")} <code>/home</code> {tr("scan. It reuses the running clamd, so no extra daemon. Fresh installs get it automatically.")}</p>
            </div>
            <button disabled={!!loading} onClick={installLmd}><Shield size={14}/> {tr("Add Linux Malware Detect")}</button>
          </div>
        </div>}

        {mwActive && mw.lmd_installed && <div className="info-box">
          <div className="malware-scan-head">
            <div>
              <strong>{tr("Real-time protection")} {mw.realtime_enabled && !mw.realtime_active
                ? <span className="badge danger">{tr("Not running")}</span>
                : <span className={mw.realtime_enabled ? 'badge ok' : 'badge'}>{mw.realtime_enabled ? tr("On") : tr("Off")}</span>}</strong>
              <p className="hint">{tr("Watches every file under")} <code>/home</code> {tr("plus the temp directories")} <code>/tmp</code>, <code>/var/tmp</code>, <code>/dev/shm</code>, {tr("and scans new/changed files within seconds — instead of only on schedule. Costs RAM per watched file; hits are surfaced, not auto-quarantined.")}</p>
              {mw.realtime_enabled && !mw.realtime_active && <p className="hint" style={{color:'var(--danger)'}}>
                {tr("Turned on here, but the monitor service is not running — nothing is being watched right now. Turn it off and on again to restart it.")}
              </p>}
            </div>
            {mw.realtime_enabled
              ? <button className="danger" disabled={!!loading} onClick={() => toggleMalwareRealtime(false)}>{tr("Turn off")}</button>
              : <button disabled={!!loading} onClick={() => toggleMalwareRealtime(true)}><Shield size={14}/> {tr("Turn on")}</button>}
          </div>
        </div>}

        {mwInstalled && <div className="info-box malware-scan-panel">
          {!mw.clamd_running && <div className="malware-daemon-warning">
            <p className="hint">{tr("ClamAV daemon is not running. Start it to enable scanning.")}</p>
            <button disabled={!!loading} onClick={startClamavDaemon}><Shield size={14}/> {tr("Start ClamAV")}</button>
          </div>}
          {mw.clamd_running && <div className="malware-scan-runner">
            <div className="malware-scan-head">
              <div>
                <strong>{tr("Run a scan now")}</strong>
                <p className="hint">{tr("One website, every website (")}<code>/home</code>{tr("), or the whole server (")}<code>/</code>{tr("). Scheduled scans are set up further down.")}</p>
              </div>
              <button className="secondary" disabled={!!loading} onClick={loadMalwareScanJobs}><RefreshCw size={14}/> {tr("History")}</button>
            </div>
            <div className="malware-scan-controls">
              <select value={scanTargetWebsiteId} onChange={e => { setScanTargetWebsiteId(e.target.value); setScanResults(null); setScanJob(null); }}>
                <option value="">{tr("-- Select target --")}</option>
                <option value="all">{tr("All websites (/home)")}</option>
                <option value="system">{tr("Full server (/)")}</option>
                {websites.map(w => <option key={w.id} value={w.id}>{w.domain}</option>)}
              </select>
              <button disabled={!!loading || scanRunning || !scanTargetWebsiteId} onClick={runMalwareScan}>
                {scanRunning || scanLoading ? <><RefreshCw size={14} className="spin"/> Scanning...</> : <><Search size={14}/> {tr("Scan Now")}</>}
              </button>
            </div>
          </div>}
          {scanJobs.length > 0 && <div className="scan-history-wrap">
            <div className="scan-history-head">
              <strong>{tr("Scan history")}</strong>
              <span>{scanJobs.length} {tr("saved")}</span>
            </div>
            <div className="scan-history-list">
              {scanJobs.slice(0, 8).map(job => <button
                key={job.job_id}
                className={`scan-history-item ${job.status}${activeScanJob.job_id === job.job_id ? ' active' : ''}`}
                onClick={() => openMalwareScanDetail(job)}
                disabled={!!loading}
                type="button"
              >
                <Clock size={14}/>
                <span className="scan-history-main">
                  <strong>{scanJobTitle(job)}</strong>
                  <small>{scanJobMeta(job)}</small>
                </span>
                <span className={scanJobBadgeClass(job)}>{job.status}</span>
              </button>)}
            </div>
          </div>}
          {(scanJob || scanResults) && <div className="scan-status-panel">
            <div className="progress-bar">
              <div className="progress-bar-fill" style={{width: `${Number(activeScanJob.progress_percent) || 0}%`}} />
            </div>
            <div className="scan-status-summary">
              <span><strong>{tr("Progress")}</strong>{Number(activeScanJob.progress_percent) || 0}%</span>
              <span><strong>{tr("Files scanned")}</strong>{activeScanJob.scanned || 0}/{activeScanJob.total_files || activeScanJob.scanned || 0}</span>
              <span><strong>{tr("Threats found")}</strong>{activeScanJob.infected > 0
                ? <span className="badge danger">{activeScanJob.infected}</span>
                : <span className="badge ok">0</span>}
              </span>
              <span><strong>{tr("Errors")}</strong>{activeScanJob.errors || 0}</span>
            </div>
            {activeScanJob.message && <p className="hint">{activeScanJob.message}</p>}
            {activeScanJob.threats && activeScanJob.threats.length > 0 && <div className="scan-threat-list">
              {activeScanJob.threats.map((t, i) => <div key={i} className="scan-threat-item">
                <strong>{t.signature}</strong>
                <span>{t.domain ? `${t.domain}: ` : ''}{t.path}</span>
                {(t.quarantined || quarantine.some(q => q.original_path === t.path))
                  ? <span className="badge ok">{tr("Quarantined")}</span>
                  : <button className="secondary-light" disabled={!!loading} onClick={() => quarantineThreat(t.path, t.signature)}>{tr("Quarantine")}</button>}
              </div>)}
            </div>}
            {activeScanJob.log && activeScanJob.log.length > 0 && <pre className="malware-scan-log">{activeScanJob.log.join('\n')}</pre>}
          </div>}
        </div>}

        {mwActive && <div className="info-box">
          <div className="malware-scan-head">
            <div>
              <strong>{tr("Quarantine")}{quarantine.length > 0 && <span className="badge" style={{marginLeft:6}}>{quarantine.length}</span>}</strong>
              <p className="hint">
                {mw.auto_quarantine
                  ? tr("A file a scan flags as malware is moved here automatically and stops being served at once. Restore anything that was a false positive.")
                  : tr("Auto-quarantine is off — scans only report threats. Turn it on below, or use “Quarantine” on a scan result to move a file here manually.")}
              </p>
            </div>
            <button className="secondary" disabled={!!loading} onClick={loadQuarantine}><RefreshCw size={14}/> {tr("Refresh")}</button>
          </div>
          <label className="check-line" style={{marginBottom: 12}}>
            <input type="checkbox" checked={!!mw.auto_quarantine} disabled={!!loading}
              onChange={e => toggleAutoQuarantine(e.target.checked)} />
            {tr("Automatically quarantine detected malware")}
          </label>
          {quarantine.length === 0
            ? <div className="quarantine-empty"><CheckCircle size={16}/> {tr("Nothing in quarantine")}{mw.auto_quarantine ? tr(" — flagged files will show up here for review.") : '.'}</div>
            : <div className="quarantine-list">
                {quarantine.map(q => {
                  const m = /^\/home\/[^/]+\/([^/]+)\/(.+)$/.exec(q.original_path || '');
                  return <div key={q.id} className="quarantine-item">
                    <div className="quarantine-info">
                      <div className="quarantine-line">
                        <span className="badge danger">{q.signature || tr("flagged file")}</span>
                        {m && <span className="quarantine-site">{m[1]}</span>}
                        <span className="quarantine-meta">{scanJobStamp({ finished_at: q.quarantined_at })} · {formatBytes(q.size || 0)}</span>
                      </div>
                      <code className="quarantine-path" title={q.original_path}>{q.original_path}</code>
                    </div>
                    <div className="quarantine-actions">
                      <button className="secondary-light" disabled={!!loading} onClick={() => restoreQuarantine(q.id, q.original_path)}><RotateCcw size={13}/> {tr("Restore")}</button>
                      <button className="danger-light" disabled={!!loading} onClick={() => deleteQuarantine(q.id, q.original_path)}><Trash2 size={13}/> {tr("Delete")}</button>
                    </div>
                  </div>;
                })}
              </div>}
        </div>}
      </section>}

      {/* Schedules belong to a running scanner; while the addon is off the
          page is only the way to the Addons page. */}
      {isAdmin && mwEnabled && [
        {
          scope: 'system',
          title: tr("Full server scan"),
          root: '/',
          intro: tr("Walks the whole VPS as root, so malware parked in /tmp, /root or a home folder outside public_html is found too. Skips /proc, /sys, /dev, /run, /snap, the ClamAV signature database and panel backup archives. The first run can take hours; best run weekly or monthly."),
        },
        {
          scope: 'web',
          title: tr("Website scan (/home)"),
          root: '/home',
          intro: tr("Scans every website’s files under /home. With Linux Malware Detect installed this runs incrementally (only files changed in the last few days) between full runs, so a daily schedule stays cheap."),
        },
      ].map(({ scope, title, root, intro }) => {
        const sched = malwareSchedules[scope] || {};
        const setField = (patch) => setMalwareSchedules(prev => ({ ...prev, [scope]: { ...prev[scope], ...patch } }));
        return <section className="section" key={scope}>
          <div className="section-title">
            <div>
              <h2>{title}</h2>
              <p className="hint">{intro}</p>
            </div>
            <button className="secondary" disabled={!!loading} onClick={loadMalwareSchedule}><RefreshCw size={14}/> {tr("Refresh")}</button>
          </div>
          <div className="info-box">
            <strong>{tr("Scheduled scan")} <code>{root}</code></strong>
            <p className="hint">{tr("Runs automatically on the server clock, even when nobody is logged in to the panel. Use “Run a scan now” above for an on-demand scan.")}</p>
            <div className="scan-schedule-form">
              <label><span>{tr("Enabled")}</span>
                <select value={sched.enabled ? 'on' : 'off'} onChange={e => setField({ enabled: e.target.value === 'on' })}>
                  <option value="off">{tr("Off")}</option>
                  <option value="on">{tr("On")}</option>
                </select>
              </label>
              <label><span>{tr("Frequency")}</span>
                <select value={sched.frequency || 'weekly'} onChange={e => setField({ frequency: e.target.value })}>
                  <option value="hourly">{tr("Hourly")}</option>
                  <option value="daily">{tr("Daily")}</option>
                  <option value="weekly">{tr("Weekly")}</option>
                  <option value="monthly">{tr("Monthly")}</option>
                </select>
              </label>
              {sched.frequency === 'weekly' && <label><span>{tr("Day of week")}</span>
                <select value={Number(sched.weekday) || 0} onChange={e => setField({ weekday: Number(e.target.value) })}>
                  {WEEKDAY_LABELS.map((label, index) => <option key={label} value={index}>{tr(label)}</option>)}
                </select>
              </label>}
              {sched.frequency === 'monthly' && <label><span>{tr("Day of month")}</span>
                <input type="number" min="1" max="28" value={Number(sched.day) || 1} onChange={e => setField({ day: Number(e.target.value) })} />
              </label>}
              {sched.frequency !== 'hourly' && <label><span>{tr("Hour")}</span>
                <input type="number" min="0" max="23" value={Number(sched.hour) || 0} onChange={e => setField({ hour: Number(e.target.value) })} />
              </label>}
              <label><span>{tr("Minute")}</span>
                <input type="number" min="0" max="59" value={Number(sched.minute) || 0} onChange={e => setField({ minute: Number(e.target.value) })} />
              </label>
              <button disabled={!!loading} onClick={() => saveMalwareSchedule(scope)}><Clock size={14}/> {tr("Save schedule")}</button>
            </div>
            {sched.last_run_at && <p className="hint" style={{marginTop:8}}>
              {tr("Last scheduled run:")} {sched.last_run_at} — <span className={sched.last_status === 'infected' ? 'badge danger' : sched.last_status === 'error' ? 'badge bad' : 'badge ok'}>{sched.last_status || tr("unknown")}</span> {sched.last_message || ''}
            </p>}
          </div>
        </section>;
      })}
    </>;
  }

  function renderPanelSettings() {
    if (!isAdmin) return <section className="section"><h2>{tr("Settings")}</h2><p className="hint">{tr("No permission.")}</p></section>;
    // Tabs, one form at a time (operator, 2026-09-30): the page had grown into
    // unrelated forms stacked up. The admin's own email and password are in
    // Profile, in the account menu.
    const tabs = [
      ['general', tr("General"), SettingsIcon],
      ['brand', tr("Brand assets"), Image],
      ['api', tr("API Tokens"), KeyRound],
    ];
    const activeTab = tabs.some(([id]) => id === panelSettingsTab) ? panelSettingsTab : 'general';
    return <section className="section panel-settings-page">
      <div className="segmented-control backup-tabs" role="tablist" aria-label={tr("Panel settings sections")}>
        {tabs.map(([id, label, Icon]) => <button key={id} type="button" role="tab" aria-selected={activeTab === id}
          className={activeTab === id ? 'active' : ''} onClick={() => setPanelSettingsTab(id)}><Icon size={14}/>{label}</button>)}
      </div>
      {activeTab === 'general' && <div className="backup-tab-panel" role="tabpanel">
        <div className="backup-panel-title">
          <div><h3>{tr("General")}</h3><p className="hint">{tr("Panel name, hostname and the server's addresses.")}</p></div>
          <button className="secondary" disabled={!!loading} onClick={() => { loadPanelSettings(); loadNetworkStatus(); }}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        <div className="panel-settings-grid panel-settings-compact">
          <label><span>{tr("Panel name")}</span><input value={panelSettingsForm.app_name} onChange={e => setPanelSettingsForm(prev => ({ ...prev, app_name: e.target.value }))} placeholder={tr("OPanel")} /></label>
          <label><span>{tr("Panel hostname")}</span><input value={panelSettingsForm.panel_hostname} onChange={e => setPanelSettingsForm(prev => ({ ...prev, panel_hostname: e.target.value }))} placeholder="panel.domain.com" /></label>
          <label className="check-line panel-ssl-status"><input type="checkbox" checked={!!panelSettingsForm.ssl_enabled} onChange={e => setPanelSettingsForm(prev => ({ ...prev, ssl_enabled: e.target.checked }))} /> {tr("Panel SSL")}</label>
          <button disabled={!!loading || !panelSettingsForm.app_name || !panelSettingsForm.panel_hostname} onClick={savePanelSettings}><SettingsIcon size={14}/> {tr("Save settings")}</button>
        </div>
        <div className="backup-subtitle">
          <h3>{tr("Server network")}</h3>
          <p className="hint">{tr("Addresses this server answers on. Detected live, so an IPv6 block added later shows up here.")}</p>
        </div>
        <div className="info-box">
          <div className="network-address-row">
            <strong>{tr("IPv4")}</strong>
            {(networkStatus.ipv4 || []).length > 0
              ? (networkStatus.ipv4 || []).map(address => <code key={address}>{address}</code>)
              : <span className="hint">{tr("None detected")}</span>}
          </div>
          <div className="network-address-row">
            <strong>{tr("IPv6")}</strong>
            {(networkStatus.ipv6 || []).length > 0
              ? (networkStatus.ipv6 || []).map(address => <code key={address}>{address}</code>)
              : <span className="hint">{tr("None detected")}</span>}
            {networkStatus.ipv6_available
              ? (networkStatus.ipv6_enabled ? <span className="badge ok">{tr("Enabled")}</span> : <span className="badge">{tr("Disabled")}</span>)
              : <span className="badge warn">{tr("Not available")}</span>}
          </div>
          <div className="actions" style={{marginTop:12}}>
            {networkStatus.ipv6_enabled
              ? <button className="danger" disabled={!!loading} onClick={() => toggleIpv6(false)}>{tr("Disable IPv6")}</button>
              : <button className="secondary" disabled={!!loading || !networkStatus.ipv6_available} onClick={() => toggleIpv6(true)}><Network size={14}/> {tr("Enable IPv6")}</button>}
          </div>
          <p className="hint" style={{marginTop:8}}>
            {networkStatus.ipv6_available
              ? tr("Websites and the panel listen on both protocols while this is on. Point an AAAA record at the address above.")
              : tr("No global IPv6 address is configured on this server yet. Add one at your provider, then refresh.")}
          </p>
        </div>
      </div>}
      {activeTab === 'brand' && <div className="backup-tab-panel" role="tabpanel">
        <div className="backup-panel-title">
          <div><h3>{tr("Brand assets")}</h3><p className="hint">{tr("Upload PNG, JPG, WEBP, or ICO files up to 1 MB.")}</p></div>
        </div>
        <div className="brand-asset-grid">
          <div className="brand-asset-card">
            <div className="brand-preview">{renderBrandMark('settings-brand-mark')}</div>
            <label><span>{tr("Logo")}</span><input type="file" accept="image/png,image/jpeg,image/webp,image/x-icon" onChange={e => setPanelLogoFile(e.target.files?.[0] || null)} /></label>
            <button className="secondary" disabled={!!loading || !panelLogoFile} onClick={() => uploadPanelAsset('logo')}><Upload size={14}/> {tr("Upload logo")}</button>
          </div>
          <div className="brand-asset-card">
            <div className="brand-preview favicon-preview">{panelSettings.favicon_url ? <img src={panelSettings.favicon_url} alt="" /> : <Image size={28}/>}</div>
            <label><span>{tr("Favicon")}</span><input type="file" accept="image/png,image/jpeg,image/webp,image/x-icon" onChange={e => setPanelFaviconFile(e.target.files?.[0] || null)} /></label>
            <button className="secondary" disabled={!!loading || !panelFaviconFile} onClick={() => uploadPanelAsset('favicon')}><Upload size={14}/> {tr("Upload favicon")}</button>
          </div>
        </div>
      </div>}
      {activeTab === 'api' && <div className="backup-tab-panel" role="tabpanel">
        <div className="backup-panel-title">
          <div><h3>{tr("API Tokens")}</h3><p className="hint">{tr("Provisioning tokens for WHMCS or external billing systems.")}</p></div>
          <button className="secondary" disabled={!!loading} onClick={loadApiTokens}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        {createdToken && <div className="token-created-notice">
          <p><strong>{tr("Token created!")}</strong> {tr("Copy it now — it will not be shown again.")}</p>
          <div className="token-copy-row">
            <code>{createdToken.token}</code>
            <button className="mini" onClick={() => { copyToClipboard(createdToken.token); setNotice(tr("Copied to clipboard.")); }}><Copy size={14}/> {tr("Copy")}</button>
          </div>
          <button className="mini secondary-light" onClick={() => setCreatedToken(null)}>{tr("Dismiss")}</button>
        </div>}
        <div className="token-create-form">
          <label><span>{tr("Name")}</span><input value={apiTokenForm.name} onChange={e => setApiTokenForm(prev => ({ ...prev, name: e.target.value }))} placeholder={tr("WHMCS Production")} /></label>
          <label><span>{tr("Expires (days)")}</span><input type="number" min="1" max="3650" value={apiTokenForm.expires_days} onChange={e => setApiTokenForm(prev => ({ ...prev, expires_days: parseInt(e.target.value) || 365 }))} /></label>
          <label><span>{tr("IP allowlist")}</span><input value={apiTokenForm.ip_allowlist} onChange={e => setApiTokenForm(prev => ({ ...prev, ip_allowlist: e.target.value }))} placeholder={tr("Optional, comma-separated")} /></label>
          <button disabled={!!loading || !apiTokenForm.name.trim()} onClick={createApiToken}><Plus size={14}/> {tr("Create token")}</button>
        </div>
        {apiTokens.length === 0 && <p className="hint">{tr("No API tokens yet.")}</p>}
        {apiTokens.length > 0 && <div className="table">
          {apiTokens.map(t => <div className="row" key={t.id}>
            <div className="token-info">
              <strong>{t.name}</strong>
              <small>{t.scopes.join(', ')}</small>
              <small>{tr("Prefix:")} {t.prefix}{tr("... | Created:")} {t.created_at ? new Date(t.created_at).toLocaleDateString() : '—'}{t.last_used_at ? tr(" | Last used: {0}", new Date(t.last_used_at).toLocaleDateString()) : ''}</small>
            </div>
            <span className="badge ok">{tr("Active")}</span>
            <button className="mini danger" disabled={!!loading} onClick={() => deleteApiToken(t)}><Trash2 size={14}/> {tr("Delete")}</button>
          </div>)}
        </div>}
      </div>}
    </section>;
  }

  // A reseller's share of the server: what is handed out against what it has.
  function renderResellerPool() {
    if (!isReseller || !resellerPool) return null;
    // Overselling counts the disk the accounts use; otherwise what was handed out.
    const oversell = !!resellerPool.pool_oversell;
    const rows = [
      [tr("Customers"), resellerPool.customers, resellerPool.pool_user_limit],
      [tr("Disk (MB)"), resellerPool[oversell ? 'used_storage_limit_mb' : 'allocated_storage_limit_mb'], resellerPool.pool_storage_limit_mb],
    ];
    return <section className="section">
      <div className="section-title"><div><h2>{tr("Your share")}</h2><p className="hint">{oversell
        ? tr("The disk your account and your customers' accounts use together must stay within the share the administrator gave you. The disk limits you give customers may add up to more.")
        : tr("Your disk limit plus every customer's must fit in the share the administrator gave you. Websites, databases and mailboxes are yours to set for each customer.")}</p></div></div>
      <div className="reseller-pool">
        {rows.map(([label, used, total]) => <div className="reseller-pool-item" key={label}>
          <span>{label}</span>
          <strong>{used == null ? tr("unlimited") : used} / {total ? total : tr("unlimited")}</strong>
        </div>)}
      </div>
    </section>;
  }

  function resellerName(id) {
    return users.find(u => u.id === id)?.username || `#${id}`;
  }

  // A reseller's share: customers and disk, what a reseller is sold by.
  // Websites, databases and mailboxes are its own to give its customers.
  function renderPoolInputs(form, setForm) {
    const fields = [
      ['pool_user_limit', tr("Customers")],
      ['pool_storage_limit_mb', tr("Disk (MB)")],
    ];
    return <fieldset className="form-section">
      <legend>{tr("Reseller share")}</legend>
      <p className="hint">{form.pool_oversell
        ? tr("Reseller share, overselling: number of customers and disk. The disk its accounts use together must stay within it; the disk limits it gives customers may add up to more. 0 = unlimited.")
        : tr("Reseller share: number of customers and disk. Its own disk limit and all its customers' must fit inside it. 0 = unlimited.")}</p>
      <div className="form-grid">
        {fields.map(([field, label]) => <label key={field}><span>{label}</span><input type="number" min="0" value={form[field] ?? 0} onChange={e => setForm(prev => ({ ...prev, [field]: e.target.value }))} /></label>)}
      </div>
      <label className="check-line"><input type="checkbox" checked={!!form.pool_oversell} onChange={e => setForm(prev => ({ ...prev, pool_oversell: e.target.checked }))} /> {tr("Allow overselling (count what is used, as cPanel and DirectAdmin do)")}</label>
    </fieldset>;
  }

  // CPU, RAM, processes and disk speed, for an account (prefix '') or a
  // reseller's whole group ('group_'). 0 is unlimited.
  function renderLimitInputs(form, setForm, prefix = '') {
    const labels = {
      cpu_percent: tr("CPU (%)"), memory_mb: tr("RAM (MB)"), process_limit: tr("Processes"),
      io_read_mbps: tr("Disk read (MB/s)"), io_write_mbps: tr("Disk write (MB/s)"),
    };
    return <fieldset className="form-section">
      <legend>{prefix ? tr("Group limits") : tr("Resource limits")}</legend>
      <p className="hint">{prefix
        ? tr("Group limits: this reseller's own account and all its customers together. 0 = unlimited.")
        : tr("Resource limits: 0 = unlimited. CPU 100% is one core.")}</p>
      <div className="form-grid five">
        {RL_FIELDS.map(field => <label key={prefix + field}><span>{labels[field]}</span>
          <input type="number" min="0" value={form[prefix + field] ?? 0} onChange={e => setForm(prev => ({ ...prev, [prefix + field]: e.target.value, _planId: '' }))} /></label>)}
      </div>
    </fieldset>;
  }

  // The account fields an administrator or reseller fills in, shared by the
  // Add form and the editor so the two cannot drift apart: who the account is,
  // what its package allows, a reseller's share and the resource limits.
  function renderUserFields(form, setForm, { creating = false, user = null } = {}) {
    const set = (field, value, keepPlan = false) => setForm(prev => ({ ...prev, [field]: value, ...(keepPlan ? {} : { _planId: '' }) }));
    const resellers = users.filter(u => u.role === 'reseller');
    const self = !creating && user?.id === currentUser?.id;
    const pickPlan = planId => {
      const plan = plans.find(p => String(p.id) === planId);
      if (plan) setForm(prev => ({ ...prev, _planId: planId, website_limit: plan.website_limit, storage_limit_mb: plan.storage_limit_mb, database_limit: plan.database_limit ?? prev.database_limit, mailbox_limit: plan.mailbox_limit ?? prev.mailbox_limit, ...limitValues(plan) }));
      else setForm(prev => ({ ...prev, _planId: '' }));
    };
    return <div className="form-sections">
      <fieldset className="form-section">
        <legend>{tr("Account")}</legend>
        <div className="form-grid account">
          {creating && <label><span>{tr("Username")}</span><input value={form.username} autoComplete="off" spellCheck={false} onChange={e => set('username', e.target.value.toLowerCase(), true)} placeholder={tr("johndoe")} /></label>}
          <label><span>{tr("Email")}</span><input type="email" value={form.email} autoComplete="off" onChange={e => set('email', e.target.value, true)} placeholder="user@domain.com" /></label>
          {creating
            ? <label><span>{tr("Password")}</span><input type="password" value={form.password} autoComplete="new-password" onChange={e => set('password', e.target.value, true)} placeholder={tr("Min 12 characters")} /></label>
            : <label><span>{tr("New password")} <em>{tr("(leave empty to keep)")}</em></span><input type="password" value={form._password || ''} autoComplete="new-password" onChange={e => set('_password', e.target.value, true)} placeholder={tr("Min 12 characters")} /></label>}
          {isAdmin && <label><span>{tr("Role")}</span><select value={form.role} disabled={self} onChange={e => set('role', e.target.value, true)}>
            <option value="end_user">{tr("End user")}</option><option value="reseller">{tr("Reseller")}</option><option value="admin">{tr("Admin")}</option>
          </select></label>}
          {isAdmin && form.role === 'end_user' && (resellers.length > 0 || form.reseller_id) && <label><span>{tr("Reseller")}</span><select value={form.reseller_id || ''} onChange={e => set('reseller_id', e.target.value, true)}>
            <option value="">{tr("None (yours)")}</option>
            {resellers.map(u => <option key={u.id} value={u.id}>{u.username}</option>)}
          </select></label>}
          {form.role !== 'admin' && <label><span>{tr("Package")}</span><select value={form._planId || ''} onChange={e => pickPlan(e.target.value)}>
            <option value="">{tr("Custom")}</option>
            {plans.filter(p => p.active).map(p => <option key={p.id} value={p.id}>{p.name} ({p.website_limit} {tr("sites,")} {p.storage_limit_mb > 0 ? tr("{0} MB", p.storage_limit_mb) : tr("unlimited")})</option>)}
          </select></label>}
        </div>
      </fieldset>
      {form.role !== 'admin' && <fieldset className="form-section">
        <legend>{tr("Hosting limits")}</legend>
        <p className="hint">{tr("0 = unlimited. Choosing a package fills these in.")}</p>
        <div className="form-grid">
          <label><span>{tr("Websites")}</span><input type="number" min="0" max="1000" value={form.website_limit} onChange={e => set('website_limit', e.target.value)} /></label>
          <label><span>{tr("Disk (MB)")}</span><input type="number" min="0" max="1048576" value={form.storage_limit_mb} onChange={e => set('storage_limit_mb', e.target.value)} /></label>
          <label><span>{tr("Databases")}</span><input type="number" min="0" max="1000" value={form.database_limit ?? 10} onChange={e => set('database_limit', e.target.value)} /></label>
          {mailInfo?.installed && <label><span>{tr("Mailboxes")}</span><input type="number" min="0" max="10000" value={form.mailbox_limit ?? 10} onChange={e => set('mailbox_limit', e.target.value)} /></label>}
        </div>
      </fieldset>}
      {isAdmin && form.role === 'reseller' && renderPoolInputs(form, setForm)}
      {limitsOn && form.role !== 'admin' && renderLimitInputs(form, setForm)}
      {limitsOn && isAdmin && form.role === 'reseller' && renderLimitInputs(form, setForm, 'group_')}
    </div>;
  }

  function renderUsers() {
    if (!canManageUsers) return <section className="section"><h2>{tr("Users")}</h2><p className="hint">{tr("No permission.")}</p></section>;
    return <>
      <section className="section">
        <div className="section-title">
          <div>{isReseller
            ? <><h2>{tr("Customers")}</h2><p className="hint">{tr("Your customers' accounts, your packages, and new customers.")}</p></>
            : <><h2>{tr("Panel Users")}</h2><p className="hint">{tr("Manage panel accounts, hosting packages, and create new users.")}</p></>}</div>
          <button className="secondary" disabled={!!loading} onClick={() => { loadUsers(); if (isReseller) loadResellerPool(); }}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        <div className="tab-bar">
          <button className={usersTab === 'list' ? 'tab active' : 'tab'} onClick={() => setUsersTab('list')}><Users size={14}/> {tr("List Users")}</button>
          <button className={usersTab === 'packages' ? 'tab active' : 'tab'} onClick={() => setUsersTab('packages')}><PackageOpen size={14}/> {tr("Packages")}</button>
          <button className={usersTab === 'add' ? 'tab active' : 'tab'} onClick={() => setUsersTab('add')}><Plus size={14}/> {tr("Add User")}</button>
        </div>
      </section>
      {renderResellerPool()}
      {usersTab === 'list' && renderUsersListTab()}
      {usersTab === 'packages' && renderUsersPackagesTab()}
      {usersTab === 'add' && renderUsersAddTab()}
    </>;
  }

  // Disk used against the account's limit: a figure and, with a limit, a meter.
  function renderUserDisk(user) {
    const limit = storageLimitBytes(user);
    // null is "not measured yet", which is not the same claim as 0 B: the list
    // ships without it so the page paints straight away, and /users/usage
    // fills it in.
    if (user?.storage_used_bytes == null) {
      return <span className="users-figure is-pending">{tr("Measuring…")}{limit ? <small> / {formatBytes(limit)}</small> : null}</span>;
    }
    const used = Number(user.storage_used_bytes);
    const pct = limit ? clampPercent((used / limit) * 100) : null;
    return <>
      <span className="users-figure">{formatBytes(used)}<small> / {limit ? formatBytes(limit) : tr("unlimited")}</small></span>
      {pct !== null && <span className={`resource-track${pct >= 90 ? ' tone-bad' : pct >= 75 ? ' tone-warn' : ''}`}><span style={{ width: `${pct}%` }}></span></span>}
    </>;
  }

  // CPU and RAM now, against the account's limits (Resource limits addon).
  function renderUserResources(user) {
    const entry = limitsInfo?.accounts?.[user.id];
    const usage = entry?.usage;
    if (!usage) return <span className="users-figure is-pending">—</span>;
    const limits = entry.limits || {};
    return <span className="users-figure">
      {formatCpuPercent(usage.cpu_percent)}{limits.cpu_percent ? <small>/{limits.cpu_percent}%</small> : null}
      <small> · </small>{formatMegabytes(usage.memory_mb)}{limits.memory_mb ? <small>/{formatMegabytes(limits.memory_mb)}</small> : null}
      <small className="users-sub">{tr("{0} processes", usage.processes ?? 0)}</small>
    </span>;
  }

  function renderUsersListTab() {
    const query = userSearch.trim().toLowerCase();
    const shown = query
      ? users.filter(user => user.username.toLowerCase().includes(query) || (user.email || '').toLowerCase().includes(query))
      : users;
    return <>
      <section className="section">
        {users.length > 0 && <div className="users-toolbar">
          <label className="users-search">
            <Search size={15}/>
            <input type="search" value={userSearch} onChange={e => setUserSearch(e.target.value)} placeholder={tr("Search by username or email")} aria-label={tr("Search by username or email")} />
          </label>
          <span className="users-count">{tr("{0} of {1}", shown.length, users.length)}</span>
        </div>}
        {users.length === 0 && <EmptyState icon={Users} message={tr("No users found.")} />}
        {users.length > 0 && shown.length === 0 && <p className="hint">{tr("No account matches your search.")}</p>}
        {shown.length > 0 && <div className={`users-table${limitsOn ? ' with-resources' : ''}`}>
          <div className="users-row users-head" aria-hidden="true">
            <span>{tr("Account")}</span><span>{tr("Role")}</span><span>{tr("Disk")}</span>{limitsOn && <span>{tr("CPU / RAM")}</span>}<span></span>
          </div>
          {shown.map(user => {
            const self = user.id === currentUser?.id;
            const editing = editingUser?.id === user.id;
            return <div className={`users-item${editing ? ' is-editing' : ''}${user.is_active ? '' : ' is-suspended'}`} key={user.id}>
              <div className="users-row">
                <div className="users-account">
                  <span className="users-avatar" aria-hidden="true">{(user.username || '?').slice(0, 1).toUpperCase()}</span>
                  <div><strong>{user.username}</strong><small>{user.email || '—'}</small></div>
                </div>
                <div className="users-meta">
                  <div className="users-badges" data-label={tr("Role")}>
                    <span className={`badge role-${user.role}`}>{roleLabel(user.role)}</span>
                    <span className={`badge ${user.is_active ? 'ok' : 'warn'}`}>{user.is_active ? tr("Active") : tr("Suspended")}</span>
                    {isAdmin && user.reseller_id && <span className="badge" title={tr("Reseller")}>{tr("via {0}", resellerName(user.reseller_id))}</span>}
                  </div>
                  <div className="users-cell" data-label={tr("Disk")}>{renderUserDisk(user)}</div>
                  {limitsOn && <div className="users-cell" data-label={tr("CPU / RAM")} title={tr("Resource use now")}>{renderUserResources(user)}</div>}
                </div>
                <div className="users-actions">
                  <button type="button" className={`mini ${editing ? '' : 'secondary-light'}`} disabled={!!loading} onClick={() => editing ? cancelEditingUser() : startEditingUser(user)}><Pencil size={14}/> {tr("Edit")}</button>
                  <button type="button" className="mini secondary-light" disabled={!!loading} onClick={() => quickLoginUser(user)}><LogIn size={14}/> {tr("Login as")}</button>
                  {!self && user.totp_enabled && <button type="button" className="mini secondary-light icon-button" disabled={!!loading} onClick={() => resetUserTwoFactor(user)} title={tr("Reset 2FA")} aria-label={tr("Reset 2FA")}><KeyRound size={14}/></button>}
                  {!self && <button type="button" className={`mini secondary-light icon-button${user.is_active ? ' is-suspend' : ''}`} disabled={!!loading} onClick={() => toggleUserActive(user)} title={user.is_active ? tr("Suspend") : tr("Unsuspend")} aria-label={user.is_active ? tr("Suspend") : tr("Unsuspend")}>{user.is_active ? <Ban size={14}/> : <CheckCircle size={14}/>}</button>}
                  {!self && <button type="button" className="mini danger icon-button" disabled={!!loading} onClick={() => deletePanelUser(user)} title={tr("Delete")} aria-label={tr("Delete")}><Trash2 size={14}/></button>}
                </div>
              </div>
              {editing && <div className="user-edit-panel">
                <div className="user-edit-heading">
                  <div><strong>{tr("Edit")} {user.username}</strong><small>
                    {self ? tr("Role is locked for the active admin session.") : tr("Role changes sign the user out of existing sessions.")}
                    {editingUserForm.role === 'admin' ? tr(" Admin accounts bypass website and storage limits.") : ''}
                  </small></div>
                  <button type="button" className="user-edit-close secondary-light" onClick={cancelEditingUser} aria-label={tr("Close user editor")} title={tr("Close user editor")}><X size={16}/></button>
                </div>
                {renderUserFields(editingUserForm, setEditingUserForm, { user })}
                <div className="user-edit-actions">
                  <button type="button" className="secondary-light" onClick={cancelEditingUser}>{tr("Cancel")}</button>
                  <button type="button" disabled={!!loading || !editingUserForm.email.trim()} onClick={updatePanelUser}><Save size={14}/> {tr("Save changes")}</button>
                </div>
              </div>}
            </div>;
          })}
        </div>}
      </section>
      {isAdmin && users.length > 0 && websites.length > 0 && <section className="section">
        <div className="section-title"><div><h2>{tr("Assign domain to user")}</h2><p className="hint">{tr("Give a website to another account.")}</p></div></div>
        <div className="assign-row">
          <select value={assignWebsiteId} onChange={e => setAssignWebsiteId(e.target.value)} aria-label={tr("Select domain")}>
            <option value="">{tr("Select domain")}</option>
            {websites.map(site => <option key={site.id} value={site.id}>{site.domain}</option>)}
          </select>
          <select value={assignUserId} onChange={e => setAssignUserId(e.target.value)} aria-label={tr("Select user")}>
            <option value="">{tr("Select user")}</option>
            {users.map(user => <option key={user.id} value={user.id}>{user.username} ({roleLabel(user.role)})</option>)}
          </select>
          <button type="button" disabled={!assignWebsiteId || !assignUserId || !!loading} onClick={assignDomainToUser}>{tr("Assign")}</button>
        </div>
      </section>}
    </>;
  }

  // A package's limits as short chips: what it gives at a glance.
  function planChips(plan) {
    const disk = plan.storage_limit_mb > 0
      ? (plan.storage_limit_mb >= 1024 ? tr("{0} GB", (plan.storage_limit_mb / 1024).toFixed(plan.storage_limit_mb % 1024 ? 1 : 0)) : tr("{0} MB", plan.storage_limit_mb))
      : tr("Unlimited disk");
    const chips = [
      [Globe, plan.website_limit ? tr("{0} website(s)", plan.website_limit) : tr("Unlimited websites")],
      [HardDrive, disk],
      [Database, plan.database_limit ? tr("{0} database(s)", plan.database_limit) : tr("Unlimited databases")],
    ];
    if (mailInfo?.installed) chips.push([Mail, plan.mailbox_limit ? tr("{0} mailbox(es)", plan.mailbox_limit) : tr("Unlimited mailboxes")]);
    if (limitsOn && plan.cpu_percent) chips.push([Cpu, `CPU ${plan.cpu_percent}%`]);
    if (limitsOn && plan.memory_mb) chips.push([MemoryStick, `RAM ${formatMegabytes(plan.memory_mb)}`]);
    return <div className="plan-chips">{chips.map(([Icon, text]) => <span className="plan-chip" key={text}><Icon size={13}/>{text}</span>)}</div>;
  }

  // The slug is made from the name once, on creation: WHMCS finds a package
  // by it, so renaming a package must leave it alone.
  function renderPlanFields(form, setForm, { creating = false } = {}) {
    const number = field => e => setForm(prev => ({ ...prev, [field]: parseInt(e.target.value) || 0 }));
    return <div className="form-sections">
      <fieldset className="form-section">
        <legend>{tr("Package")}</legend>
        <p className="hint">{tr("0 = unlimited.")}</p>
        <div className="form-grid">
          <label className="span-all"><span>{tr("Name")}</span><input value={form.name} onChange={e => { const name = e.target.value; setForm(prev => ({ ...prev, name, ...(creating ? { slug: name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') } : {}) })); }} placeholder={tr("Starter")} /></label>
          <label><span>{tr("Websites")}</span><input type="number" min="0" value={form.website_limit} onChange={number('website_limit')} /></label>
          <label><span>{tr("Disk (MB)")}</span><input type="number" min="0" value={form.storage_limit_mb} onChange={number('storage_limit_mb')} /></label>
          <label><span>{tr("Databases")}</span><input type="number" min="0" value={form.database_limit} onChange={number('database_limit')} /></label>
          {mailInfo?.installed && <label><span>{tr("Mailboxes")}</span><input type="number" min="0" value={form.mailbox_limit} onChange={number('mailbox_limit')} /></label>}
        </div>
      </fieldset>
      {limitsOn && renderLimitInputs(form, setForm)}
    </div>;
  }

  function renderUsersPackagesTab() {
    return <>
      <section className="section">
        <div className="section-title">
          <div><h2>{tr("New package")}</h2><p className="hint">{isReseller ? tr("Your own packages: only you see them, to fill in your customers' limits.") : tr("Manage provisioning plans for WHMCS and billing systems.")}</p></div>
        </div>
        <div className="user-edit-panel is-form">
          {renderPlanFields(newPlan, setNewPlan, { creating: true })}
          <div className="user-edit-actions">
            <button type="button" disabled={!!loading || !newPlan.name.trim()} onClick={createPlan}><Plus size={14}/> {tr("Add package")}</button>
          </div>
        </div>
      </section>
      <section className="section">
        <div className="section-title">
          <div><h2>{tr("Hosting Packages")}</h2></div>
          <button type="button" className="secondary" disabled={!!loading} onClick={loadPlans}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        {plans.length === 0 && <p className="hint">{tr("No packages yet. Create one above.")}</p>}
        {plans.length > 0 && <div className="users-table plans">
          {plans.map(plan => {
            const editing = editingPlan?.id === plan.id;
            return <div className={`users-item${editing ? ' is-editing' : ''}`} key={plan.id}>
              <div className="users-row plan-row">
                <div className="users-account">
                  <span className="users-avatar" aria-hidden="true"><PackageOpen size={16}/></span>
                  <div><strong>{plan.name}</strong>{planChips(plan)}</div>
                </div>
                <span className={plan.active ? 'badge ok' : 'badge'}>{plan.active ? tr("Active") : tr("Inactive")}</span>
                <div className="users-actions">
                  <button type="button" className={`mini ${editing ? '' : 'secondary-light'}`} onClick={() => editing ? setEditingPlan(null) : startEditingPlan(plan)}><Pencil size={14}/> {tr("Edit")}</button>
                  <button type="button" className="mini danger icon-button" onClick={() => deletePlanItem(plan)} title={tr("Delete")} aria-label={tr("Delete")}><Trash2 size={14}/></button>
                </div>
              </div>
              {editing && <div className="user-edit-panel">
                <div className="user-edit-heading">
                  <div><strong>{tr("Edit")} {plan.name}</strong></div>
                  <button type="button" className="user-edit-close secondary-light" onClick={() => setEditingPlan(null)} aria-label={tr("Close")} title={tr("Close")}><X size={16}/></button>
                </div>
                {renderPlanFields(editingPlanForm, setEditingPlanForm)}
                <label className="check-line"><input type="checkbox" checked={editingPlanForm.active} onChange={e => setEditingPlanForm(prev => ({ ...prev, active: e.target.checked }))} /> {tr("Active")}</label>
                <div className="user-edit-actions">
                  <button type="button" className="secondary-light" onClick={() => setEditingPlan(null)}>{tr("Cancel")}</button>
                  <button type="button" disabled={!!loading} onClick={updatePlan}><Save size={14}/> {tr("Save")}</button>
                </div>
              </div>}
            </div>;
          })}
        </div>}
      </section>
    </>;
  }

  function renderUsersAddTab() {
    return <section className="section">
      <div className="section-title">
        <div><h2>{isReseller ? tr("Add customer") : tr("Add panel user")}</h2><p className="hint">{tr("Panel username is also the Linux user. Select a package to auto-fill limits.")}</p></div>
      </div>
      <div className="user-edit-panel is-form">
        {renderUserFields(newUser, setNewUser, { creating: true })}
        <div className="user-edit-actions">
          <button type="button" disabled={!!loading || !newUser.username || !newUser.password} onClick={createUser}><Plus size={14}/> {isReseller ? tr("Create customer") : tr("Create user")}</button>
        </div>
      </div>
    </section>;
  }

  function renderStandaloneEditor() {
    const editorLineCount = Math.max(1, String(fileContent || '').split('\n').length);
    const editorMode = editorLanguage(filePath);
    const siteLabel = currentSite?.domain || (selectedWebsiteId ? `Website #${selectedWebsiteId}` : 'Website');
    return <main className="standalone-editor-page">
      <header className="standalone-editor-top">
        <div className="standalone-editor-title">
          <strong>{filePath || tr("No file selected")}</strong>
          <span>{siteLabel}</span>
        </div>
        <div className="standalone-editor-actions">
          <span className="editor-chip">{editorMode}</span>
          <span className="editor-chip">{editorLineCount} {tr("line(s)")}</span>
          <span className="editor-chip">{tr("Ln")} {editorCursor.line}{tr(", Col")} {editorCursor.column}</span>
          <button className="secondary" disabled={!selectedWebsiteId || !!loading} onClick={() => readFile(filePath)}><RefreshCw size={14}/> {tr("Reload")}</button>
          <button disabled={!selectedWebsiteId || !!loading} onClick={writeFile}>{tr("Save")}</button>
          <button disabled={!selectedWebsiteId || !filePath || !!loading} onClick={() => downloadFile(filePath)}><Download size={14}/></button>
          <button className="secondary-light" onClick={() => window.close()}><X size={14}/> {tr("Close")}</button>
        </div>
      </header>
      {loading && <div className="loading">{loading}</div>}
      {renderNotifications()}
      <section className="standalone-editor-body">
        <CodeEditor
          value={fileContent}
          mode={editorMode}
          disabled={!selectedWebsiteId}
          theme={theme}
          onChange={setFileContent}
          onCursorChange={setEditorCursor}
        />
      </section>
    </main>;
  }

  function renderPage() {
    if (page === 'websites') return renderWebsites();
    if (page === 'ssl') return renderSsl();
    if (page === 'databases') return renderDatabases();
    if (page === 'cron') return renderCron();
    if (page === 'files') return renderFiles();
    if (page === 'backups') return renderBackups();
    if (page === 'security') return renderSecurity();
    if (page === 'malware') return renderMalwareScanner();
    if (page === 'php') return renderPhpConfig();
    if (page === 'firewall') return renderFirewall();
    if (page === 'waf') return renderWaf();
    if (page === 'wafLogs') return renderWafAccessLogs();
    if (page === 'updates') return renderUpdates();
    // Admin only, and guarded here too so a bookmarked /services does not
    // paint a page whose every request will 403.
    if (page === 'addons') return isAdmin ? renderAddons() : renderDashboard();
    if (page === 'mcp') return renderMcp();
    if (page === 'mail') return renderMail();
    if (page === 'dns') return renderDns();
    if (page === 'notifications') return isAdmin ? renderNotificationCenter() : renderDashboard();
    if (page === 'config') return renderSettingsHub();
    if (page === 'sftp') return renderSftp();
    if (page === 'services') return isAdmin ? renderServices() : renderDashboard();
    if (page === 'settings') return renderPanelSettings();
    if (page === 'users') return renderUsers();
    if (page === 'usage') return renderResourceUsage();
    return renderDashboard();
  }

  // Login screen
  if (bootstrapping) {
    return <main className="login-page">
      <button type="button" className="theme-toggle-btn" onClick={toggleTheme} aria-label={tr("Toggle dark mode")} title={tr("Toggle dark mode")}>{theme === 'dark' ? <Sun size={16}/> : <Moon size={16}/>}</button>
      {renderLanguageToggle('lang-toggle-btn')}
      <section className="login-card">
        <div className="login-brand">{renderBrandMark('login-brand-mark')}<div><p className="eyebrow">{panelSettings.app_name || tr("opanel")}</p><h1>{tr("Loading…")}</h1></div></div>
      </section>
    </main>;
  }

  if (!isAuthenticated) {
    return <main className="login-page">
      <button type="button" className="theme-toggle-btn" onClick={toggleTheme} aria-label={tr("Toggle dark mode")} title={tr("Toggle dark mode")}>{theme === 'dark' ? <Sun size={16}/> : <Moon size={16}/>}</button>
      {renderLanguageToggle('lang-toggle-btn')}
      <section className="login-card">
        <div className="login-brand">
          {renderBrandMark('login-brand-mark')}
          <div>
            <p className="eyebrow">{tr("Server Management Panel")}</p>
            <h1>{panelSettings.app_name || tr("opanel")}</h1>
          </div>
        </div>
        <div className="login-form">
          <input value={username} onChange={e => setUsername(e.target.value)} placeholder={tr("Username")} autoComplete="username" />
          <input value={password} onChange={e => setPassword(e.target.value)} placeholder={tr("Password")} type="password" autoComplete="current-password" onKeyDown={e => { if (e.key === 'Enter') login(); }} />
          {needsTwoFactor && <input value={otpCode} onChange={e => setOtpCode(e.target.value)} placeholder={tr("Authentication code")} inputMode="numeric" autoComplete="one-time-code" onKeyDown={e => { if (e.key === 'Enter') login(); }} />}
          <button disabled={!!loading || !username || !password} onClick={() => login()}>{loading ? tr("Logging in...") : tr("Login")}</button>
        </div>
        {demoInfo?.accounts?.length > 0 && <div className="demo-login">
          <div className="demo-login-head"><Eye size={15}/><strong>{tr("Demo")}</strong></div>
          <p className="hint">{tr("Read-only: look around freely, nothing you change is saved.")}</p>
          {demoInfo.accounts.map(account => <div className="demo-login-row" key={account.username}>
            <span className="demo-login-cred">
              <small>{account.role === 'admin' ? tr("Administrator") : tr("Hosting customer")}</small>
              <code>{account.username}</code><span aria-hidden="true">/</span><code>{account.password}</code>
            </span>
            <button type="button" className="secondary" disabled={!!loading}
              onClick={() => { setUsername(account.username); setPassword(account.password); login(account); }}>
              <LogIn size={14}/> {tr("Sign in")}</button>
          </div>)}
        </div>}
      </section>
      {renderNotifications()}
    </main>;
  }

  if (standaloneEditor) return renderStandaloneEditor();

  const ActiveIcon = activeNavItem?.[2] || Home;

  return <main className="app-shell">
    <section className="layout">
      {mobileMenuOpen && <div className="mobile-nav-backdrop" onClick={() => setMobileMenuOpen(false)} aria-hidden="true"></div>}
      <aside className={`sidebar ${mobileMenuOpen ? 'open' : ''}`} role="navigation" aria-label={tr("Main navigation")}>
        <div className="sidebar-head">
          <div className="sidebar-brand">
            {renderBrandMark()}
            <div>
              <strong>{panelSettings.app_name || tr("opanel")}</strong>
              <small>{tr("Server Panel")}</small>
            </div>
          </div>
          <button className="sidebar-close" onClick={() => setMobileMenuOpen(false)} aria-label={tr("Close menu")}><X size={18}/></button>
        </div>
        <nav className="sidebar-nav">
          {navSections.map(section => <div className="sidebar-section" key={section.key}>
            {section.title && <p className="sidebar-section-title">{section.title}</p>}
            {section.items.map(([key, label, Icon]) => <button key={key} type="button" className={navKey === key ? 'active' : ''} onClick={() => navigateToPage(key)} aria-current={navKey === key ? 'page' : undefined}>
              <Icon size={16}/><span>{label}</span>
            </button>)}
          </div>)}
        </nav>
        {appVersion && <div className="sidebar-version">v{appVersion}</div>}
      </aside>
      <div className="content">
        <section className="topbar">
          <button className="mobile-nav-toggle" onClick={() => setMobileMenuOpen(o => !o)} aria-expanded={mobileMenuOpen} aria-label={tr("Toggle navigation")}>
            <Menu size={20}/><span><ActiveIcon size={17}/>{activeNavItem?.[1] || tr("Menu")}</span>
          </button>
          <div className="page-title">
            {settingsPage
              ? <h1 className="page-crumbs"><button type="button" onClick={() => navigateToPage('config')}>{tr("Settings")}</button><span aria-hidden="true">›</span>{settingsPage[1]}</h1>
              : <h1>{page === 'usage' && limitsOn && !isAdmin ? tr("Resource usage") : activeNavItem?.[1] || panelSettings.app_name || tr("opanel")}</h1>}
          </div>
          <div className="top-actions">
            {renderLanguageToggle('secondary compact-btn top-lang')}
            <button className="secondary compact-btn icon-only" onClick={toggleTheme} aria-label={tr("Toggle dark mode")} title={tr("Toggle dark mode")}>{theme === 'dark' ? <Sun size={15}/> : <Moon size={15}/>}</button>
            <div className="user-menu" ref={userMenuRef}>
              <button type="button" className="user-menu-trigger" onClick={() => setUserMenuOpen(open => !open)} aria-haspopup="menu" aria-expanded={userMenuOpen} title={tr("Logged in as")}>
                <span className="user-avatar" aria-hidden="true">{(currentUser?.username || username || '?').slice(0, 1).toUpperCase()}</span>
                <span className="user-menu-name">{currentUser?.username || username}</span>
                <ChevronDown size={14} className="user-menu-chevron"/>
              </button>
              {userMenuOpen && <div className="user-menu-panel" role="menu">
                <div className="user-menu-head">
                  <strong>{currentUser?.username || username}</strong>
                  <small>{currentUser?.email || roleLabel(currentUser?.role)}</small>
                </div>
                <button type="button" role="menuitem" onClick={() => { setUserMenuOpen(false); openProfileModal(); }}><KeyRound size={15}/>{tr("Profile")}</button>
                <button type="button" role="menuitem" onClick={() => { setUserMenuOpen(false); navigateToPage('security'); }}><LockKeyhole size={15}/>{tr("Account security")}</button>
                {currentUser?.impersonator && <button type="button" role="menuitem" onClick={() => { setUserMenuOpen(false); returnToImpersonator(); }}><ArrowLeft size={15}/>{tr("Back to {0}", currentUser.impersonator)}</button>}
                <button type="button" role="menuitem" className="user-menu-logout" onClick={() => { setUserMenuOpen(false); logout(); }}><LogOut size={15}/>{tr("Logout")}</button>
              </div>}
            </div>
          </div>
        </section>
        <div className="content-body">
          {currentUser?.demo && <div className="demo-banner" role="status"><Eye size={15}/> <span>{tr("You are viewing a read-only demo: you can open every page, and nothing you change is saved.")}</span></div>}
          {currentUser?.impersonator && <div className="impersonation-banner" role="status">
            <LogIn size={15}/> <span>{tr("You are logged in as {0}.", currentUser.username)}</span>
            <button type="button" className="mini" disabled={!!loading} onClick={returnToImpersonator}><ArrowLeft size={14}/> {tr("Back to {0}", currentUser.impersonator)}</button>
          </div>}
          {renderPage()}
          {loading && <div className="loading"><span></span>{loading}</div>}
        </div>
      </div>
    </section>
    {showProfileModal && <div className="modal-overlay" onClick={() => setShowProfileModal(false)}>
      <div className="modal-card" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>{tr("Profile Settings")}</h3>
          <button className="secondary-light" onClick={() => setShowProfileModal(false)}><X size={16}/></button>
        </div>
        <div className="modal-body">
          <div className="modal-section">
            <h4>{tr("Email")}</h4>
            <div className="profile-email-row">
              <input type="email" value={profileForm.email} onChange={e => setProfileForm(prev => ({ ...prev, email: e.target.value }))} placeholder="admin@domain.com" />
              <button disabled={!!loading || !profileForm.email.trim() || profileForm.email === currentUser?.email} onClick={updateMyEmail}><Save size={14}/> {tr("Save")}</button>
            </div>
            <p className="hint">{tr("Used for Let's Encrypt SSL notifications and panel communication.")}</p>
          </div>
          <div className="modal-section">
            <h4>{tr("Change Password")}</h4>
            <label><span>{tr("New password")}</span><input type="password" value={profileForm.password} onChange={e => setProfileForm(prev => ({ ...prev, password: e.target.value }))} placeholder={tr("Min 12 characters")} /></label>
            <label><span>{tr("Current password")}</span><input type="password" value={profileForm.current_password} onChange={e => setProfileForm(prev => ({ ...prev, current_password: e.target.value }))} placeholder={tr("Required to confirm")} /></label>
            {currentUser?.totp_enabled && <label><span>{tr("2FA code")}</span><input value={profileForm.code} onChange={e => setProfileForm(prev => ({ ...prev, code: e.target.value }))} placeholder={tr("6-digit code")} maxLength={6} /></label>}
            <button disabled={!!loading || !profileForm.password || profileForm.password.length < 12} onClick={changeMyPasswordFromProfile}><KeyRound size={14}/> {tr("Change password")}</button>
          </div>
        </div>
      </div>
    </div>}
    {dbOwnerModal && <div className="modal-overlay" onClick={() => setDbOwnerModal(null)}>
      <div className="modal-card" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>{tr("Move database")}</h3>
          <button className="secondary-light" onClick={() => setDbOwnerModal(null)} aria-label={tr("Close")}><X size={16}/></button>
        </div>
        <div className="modal-body">
          <div className="modal-section">
            <div className="move-db-summary">
              <label>{tr("Database")}</label><strong>{dbOwnerModal.db.db_name}</strong>
              <label>{tr("Currently")}</label>
              <span>{users.find(u => u.id === dbOwnerModal.db.owner_id)?.username || tr("user #{0}", dbOwnerModal.db.owner_id)}</span>
            </div>
            <label>
              <span>{tr("Move to")}</span>
              <select value={dbOwnerModal.ownerId}
                      onChange={e => setDbOwnerModal(prev => ({ ...prev, ownerId: e.target.value }))}>
                {dbOwnerChoices(dbOwnerModal.db).map(user => (
                  <option key={user.id} value={user.id}>{user.username}</option>
                ))}
              </select>
            </label>
            <p className="hint">{tr("The database, its MySQL user and its password do not change, so anything already connected keeps working.")}</p>
          </div>
          <div className="modal-actions">
            <button className="secondary-light" onClick={() => setDbOwnerModal(null)}>{tr("Cancel")}</button>
            <button disabled={!!loading || !dbOwnerModal.ownerId} onClick={submitDbOwnerChange}><MoveRight size={14}/> {tr("Move database")}</button>
          </div>
        </div>
      </div>
    </div>}
    {mailboxEdit && <div className="modal-overlay" onClick={() => setMailboxEdit(null)}>
      <div className="modal-card" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>{mailboxEdit.box.address}</h3>
          <button className="secondary-light" onClick={() => setMailboxEdit(null)} aria-label={tr("Close")}><X size={16}/></button>
        </div>
        <div className="modal-body">
          <label className="field"><span className="field-label">{tr("New password")} <small>{tr("(leave empty to keep)")}</small></span>
            <div className="password-with-generate">
              <input value={mailboxEdit.password} autoComplete="new-password" spellCheck={false} onChange={e => setMailboxEdit(prev => ({ ...prev, password: e.target.value }))} />
              <button type="button" className="secondary icon-only" title={tr("Generate random password")} aria-label={tr("Generate random password")} onClick={() => setMailboxEdit(prev => ({ ...prev, password: mailPassword() }))}><Dices size={15}/></button>
              <button type="button" className="secondary icon-only" title={tr("Copy")} aria-label={tr("Copy")} onClick={() => { copyToClipboard(mailboxEdit.password); setNotice(tr("Copied to clipboard.")); }}><Copy size={15}/></button>
            </div>
          </label>
          <label className="field"><span className="field-label">{tr("Size (MB)")}{isAdmin && <em> {tr("0 = unlimited")}</em>}</span>
            <input type="number" min={isAdmin ? 0 : 1} value={mailboxEdit.quota_mb} onChange={e => setMailboxEdit(prev => ({ ...prev, quota_mb: e.target.value }))} />
          </label>
          <p className="hint">{tr("A new password takes effect at once: mail apps using the old one must be updated.")}</p>
        </div>
        <div className="modal-actions">
          <button className="secondary-light" onClick={() => setMailboxEdit(null)}>{tr("Cancel")}</button>
          <button disabled={!!loading || (mailboxEdit.password !== '' && (mailboxEdit.password.length < 8 || !/[A-Za-z]/.test(mailboxEdit.password) || !/\d/.test(mailboxEdit.password)))} onClick={saveMailboxEdit}><Save size={14}/> {tr("Save")}</button>
        </div>
      </div>
    </div>}
    {forwarderEdit && <div className="modal-overlay" onClick={() => setForwarderEdit(null)}>
      <div className="modal-card" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>{forwarderEdit.item.address}</h3>
          <button className="secondary-light" onClick={() => setForwarderEdit(null)} aria-label={tr("Close")}><X size={16}/></button>
        </div>
        <div className="modal-body">
          <label className="field"><span className="field-label">{tr("Forward to")}</span>
            <textarea rows={3} value={forwarderEdit.destinations} spellCheck={false} onChange={e => setForwarderEdit(prev => ({ ...prev, destinations: e.target.value }))} />
          </label>
          <p className="hint">{tr("One or more addresses, separated by commas.")}</p>
        </div>
        <div className="modal-actions">
          <button className="secondary-light" onClick={() => setForwarderEdit(null)}>{tr("Cancel")}</button>
          <button disabled={!!loading || splitAddresses(forwarderEdit.destinations).length === 0} onClick={saveForwarderEdit}><Save size={14}/> {tr("Save")}</button>
        </div>
      </div>
    </div>}
    {sftpPasswordFor && <div className="modal-overlay" onClick={() => setSftpPasswordFor(null)}>
      <div className="modal-card" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>{tr("New password")} — {sftpPasswordFor.account.username}</h3>
          <button className="secondary-light" onClick={() => setSftpPasswordFor(null)} aria-label={tr("Close")}><X size={16}/></button>
        </div>
        <div className="modal-body">
          <div className="password-with-generate">
            <input value={sftpPasswordFor.password} onChange={e => setSftpPasswordFor(prev => ({ ...prev, password: e.target.value }))} />
            <button type="button" className="secondary icon-only" title={tr("Generate random password")} aria-label={tr("Generate random password")} onClick={() => setSftpPasswordFor(prev => ({ ...prev, password: generateRandomPassword() }))}><Dices size={15}/></button>
            <button type="button" className="secondary icon-only" title={tr("Copy")} aria-label={tr("Copy")} onClick={() => { copyToClipboard(sftpPasswordFor.password); setNotice(tr("Copied to clipboard.")); }}><Copy size={15}/></button>
          </div>
          <p className="hint">{tr("Copy it before saving — it is not shown again. Open sessions of this login keep running until they disconnect.")}</p>
        </div>
        <div className="modal-actions">
          <button className="secondary-light" onClick={() => setSftpPasswordFor(null)}>{tr("Cancel")}</button>
          <button disabled={!!loading || sftpPasswordFor.password.length < 12} onClick={saveSftpPassword}><Save size={14}/> {tr("Save")}</button>
        </div>
      </div>
    </div>}
    {chmodTarget && (() => {
      // Three-digit octal mode edited as a grid of checkboxes. The server
      // refuses what is unsafe (execute bits on files, world-writable
      // anything), so those boxes are locked here rather than failing later.
      const digits = (chmodTarget.mode.padStart(3, '0').slice(-3)).split('').map(d => parseInt(d, 8) || 0);
      const valid = /^[0-7]{3}$/.test(chmodTarget.mode);
      const setBit = (who, bit, on) => {
        const next = [...digits];
        next[who] = on ? (next[who] | bit) : (next[who] & ~bit);
        setChmodTarget(prev => ({ ...prev, mode: next.join('') }));
      };
      const locked = (who, bit) => (bit === 2 && who === 2) || (!chmodTarget.is_dir && bit === 1) || (chmodTarget.is_dir && who === 0 && bit === 1);
      const presets = chmodTarget.is_dir ? ['755', '750', '711', '700'] : ['644', '640', '600', '444'];
      return <div className="modal-overlay" onClick={() => setChmodTarget(null)}>
        <div className="modal-card chmod-card" onClick={e => e.stopPropagation()}>
          <div className="modal-header">
            <h3>{tr("Permissions")} — {chmodTarget.name}</h3>
            <button className="secondary-light" onClick={() => setChmodTarget(null)} aria-label={tr("Close")}><X size={16}/></button>
          </div>
          <div className="modal-body">
            <table className="chmod-grid">
              <thead><tr><th></th><th>{tr("Read")}</th><th>{tr("Write")}</th><th>{tr("Execute")}</th></tr></thead>
              <tbody>
                {[tr("Owner"), tr("Group"), tr("Public")].map((label, who) => <tr key={label}>
                  <th scope="row">{label}</th>
                  {[4, 2, 1].map(bit => <td key={bit}>
                    <input type="checkbox" checked={(digits[who] & bit) !== 0} disabled={locked(who, bit)}
                      onChange={e => setBit(who, bit, e.target.checked)} aria-label={`${label} ${bit === 4 ? tr("Read") : bit === 2 ? tr("Write") : tr("Execute")}`} />
                  </td>)}
                </tr>)}
              </tbody>
            </table>
            <div className="chmod-mode-row">
              <label><span>{tr("Numeric mode")}</span>
                <input value={chmodTarget.mode} maxLength={3} inputMode="numeric" onChange={e => setChmodTarget(prev => ({ ...prev, mode: e.target.value.replace(/[^0-7]/g, '').slice(0, 3) }))} />
              </label>
              <div className="chmod-presets">
                {presets.map(mode => <button key={mode} type="button" className={`mini ${chmodTarget.mode === mode ? 'toggle-on' : 'secondary'}`} onClick={() => setChmodTarget(prev => ({ ...prev, mode }))}>{mode}</button>)}
              </div>
            </div>
            <p className="hint">{chmodTarget.is_dir
              ? tr("Folders need execute for anyone who may read them, and cannot be writable by everyone.")
              : tr("Files cannot be executable or writable by everyone. 644 is the usual choice; 600 keeps a file private to the site.")}</p>
          </div>
          <div className="modal-actions">
            <button className="secondary-light" onClick={() => setChmodTarget(null)}>{tr("Cancel")}</button>
            <button disabled={!!loading || !valid} onClick={saveFilePermissions}><Save size={14}/> {tr("Apply")}</button>
          </div>
        </div>
      </div>;
    })()}
    {renderNotifications()}
  </main>;
}

createRoot(document.getElementById('root')).render(<App />);
