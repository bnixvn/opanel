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
import { Activity, Archive, ArrowLeft, Bot, BrickWall, Bug, Check, CheckCircle, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Layers, Lock, LockKeyhole, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, PackageOpen, Pencil, Save, ScrollText, Search, Server, Settings as SettingsIcon, Shield, ShieldAlert, ShieldCheck, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Zap, ExternalLink, Ban } from 'lucide-react';
import { Terminal } from './components/Terminal';
import { LANGUAGES, currentLanguage, nextLanguage, setLanguage, tr } from './i18n';
import './style.css';
import './brand.css';
import './ui.css';
import './file-manager.css';

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
const normalizeCron = value => String(value || '').trim().split(/\s+/).join(' ');
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
  settings: '/settings',
  security: '/security',
  malware: '/malware',
  php: '/php',
  firewall: '/firewall',
  waf: '/waf',
  wafLogs: '/waf-logs',
  updates: '/updates',
  addons: '/addons',
  mcp: '/mcp',
  services: '/services',
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
  const [otpCode, setOtpCode] = useState('');
  const [needsTwoFactor, setNeedsTwoFactor] = useState(false);
  const [page, setPage] = useState(() => pageFromPathname(window.location.pathname));
  const [domain, setDomain] = useState('');
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
  const [restoreBackups, setRestoreBackups] = useState([]);
  const [restorePicks, setRestorePicks] = useState([]);
  const [remoteBackups, setRemoteBackups] = useState([]);
  const [remoteBackupErrors, setRemoteBackupErrors] = useState([]);
  const [restoreBackupDir, setRestoreBackupDir] = useState('');
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
  const [daBackups, setDaBackups] = useState([]);
  const [daBackupDir, setDaBackupDir] = useState('');
  const [daImportJobs, setDaImportJobs] = useState([]);
  const [daPicks, setDaPicks] = useState([]);
  const [daOverwrite, setDaOverwrite] = useState(false);
  const [selectedWebsiteId, setSelectedWebsiteId] = useState(() => standaloneEditor?.websiteId || '');
  const [sslMode, setSslMode] = useState('letsencrypt');
  const [manualSslForm, setManualSslForm] = useState({ certificate: '', private_key: '', ca_bundle: '' });
  const [manualSslFiles, setManualSslFiles] = useState({ certificate: null, private_key: null, ca_bundle: null });
  const [wildcardSslForm, setWildcardSslForm] = useState({ api_token: '', email: '' });
  const [availableCerts, setAvailableCerts] = useState([]);   // SSL page: certs on the box covering currentSite
  const [reuseCertName, setReuseCertName] = useState('');
  // Create-website SSL section
  const [createSslMode, setCreateSslMode] = useState('letsencrypt'); // letsencrypt|wildcard|existing|manual
  const [createSslForm, setCreateSslForm] = useState({ api_token: '', email: '', reuse_name: '', certificate: '', private_key: '', ca_bundle: '' });
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
  const [newUser, setNewUser] = useState({ username: '', email: '', password: '', role: 'end_user', website_limit: 5, storage_limit_mb: 1024 });
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
  const [mcpTokens, setMcpTokens] = useState([]);
  const [mcpAllTokens, setMcpAllTokens] = useState([]);
  const [mcpForm, setMcpForm] = useState({ name: '', can_write: false, expires_days: 90 });
  const [mcpCreated, setMcpCreated] = useState(null);
  // Profile modal
  const [showProfileModal, setShowProfileModal] = useState(false);
  const [profileForm, setProfileForm] = useState({ email: '', password: '', current_password: '', code: '' });
  // Users page tabs
  const [usersTab, setUsersTab] = useState('list');
  // Hosting plans
  const [plans, setPlans] = useState([]);
  const [editingPlan, setEditingPlan] = useState(null);
  const [editingPlanForm, setEditingPlanForm] = useState({ name: '', website_limit: 1, storage_limit_mb: 1024, php_version: '8.4', app_type: 'php', auto_ssl: false, active: true });
  const [newPlan, setNewPlan] = useState({ slug: '', name: '', website_limit: 1, storage_limit_mb: 1024, php_version: '8.4', app_type: 'php', auto_ssl: false });
  // WordPress manager
  const [wpManagerSite, setWpManagerSite] = useState(null);
  const [wpManagerMode, setWpManagerMode] = useState('install'); // 'install' | 'update'
  const [wpInstallForm, setWpInstallForm] = useState({ admin_user: 'admin', admin_email: '', admin_password: '', title: '' });
  const noticeTimer = useRef(null);
  const isAdmin = currentUser?.role === 'admin';
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
    setRestoreBackups([]);
    setRestoreBackupDir('');
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

  async function login() {
    try {
      setError('');
      setLoading(tr("Logging in..."));
      const body = new URLSearchParams({ username, password });
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
        const retry = new URLSearchParams({ username, password });
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

  async function createUser() {
    const data = await request('/users', { method: 'POST', body: JSON.stringify({ ...newUser, website_limit: Number(newUser.website_limit), storage_limit_mb: Number(newUser.storage_limit_mb) }) }, tr("Creating user..."));
    if (data) {
      setNotice(tr("Created user {0}", data.username));
      setNewUser({ username: '', email: '', password: '', role: 'end_user', website_limit: 5, storage_limit_mb: 1024 });
      await loadUsers();
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
    if (editingUser.id !== currentUser?.id) payload.role = editingUserForm.role;
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
    const data = await request('/plans', { method: 'POST', body: JSON.stringify(newPlan) }, tr("Creating plan..."));
    if (data?.id) {
      setNotice(tr("Plan \"{0}\" created.", data.name));
      setNewPlan({ slug: '', name: '', website_limit: 1, storage_limit_mb: 1024, php_version: '8.4', app_type: 'php', auto_ssl: false });
      loadPlans();
    }
  }

  function startEditingPlan(plan) {
    setEditingPlan(plan);
    setEditingPlanForm({ name: plan.name, website_limit: plan.website_limit, storage_limit_mb: plan.storage_limit_mb, php_version: plan.php_version, app_type: plan.app_type, auto_ssl: plan.auto_ssl, active: plan.active });
  }

  async function updatePlan() {
    if (!editingPlan) return;
    const data = await request(`/plans/${editingPlan.id}`, { method: 'PATCH', body: JSON.stringify(editingPlanForm) }, tr("Updating plan..."));
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

  async function loadMalwareScanStatus() {
    const data = await request('/panel-settings/malware-scan', {}, tr("Loading malware scan status..."));
    if (data) setMalwareScanStatus(data);
  }

  async function toggleMalwareScan(enable) {
    if (enable && !malwareScanStatus?.installed) {
      if (!confirm(tr("ClamAV is not installed on this server. It will be installed now (may take 1-2 minutes). Continue?"))) return;
    }
    const data = await request('/panel-settings/malware-scan/toggle', {
      method: 'POST',
      body: JSON.stringify({ enabled: enable }),
    }, enable ? tr("Enabling malware scanning...") : tr("Disabling malware scanning..."));
    if (data) {
      setPanelSettings(data);
      setNotice(data.message || tr("Malware scanning {0}.", enable ? tr("enabled") : tr("disabled")));
      await loadMalwareScanStatus();
    }
  }

  async function toggleMalwareRealtime(enable) {
    if (enable && !confirm(
      tr("Turn on real-time protection?\n\n")
      + tr("• Linux Malware Detect watches every file under /home (inotify) and scans new or changed files within seconds.\n")
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

  async function applySslForNewSite(id, siteDomain) {
    if (createSslMode === 'wildcard') {
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
    const token = String(wildcardSslForm.api_token || '').trim();
    if (!token && !currentSite?.ssl_wildcard) { setError(tr("Enter a Cloudflare API token (Zone → DNS → Edit).")); return; }
    const body = { provider: 'cloudflare' };
    if (token) body.api_token = token;
    const email = String(wildcardSslForm.email || '').trim();
    if (email) body.email = email;
    const data = await request(`/websites/${selectedWebsiteId}/ssl/wildcard`, { method: 'POST', body: JSON.stringify(body) }, tr("Issuing wildcard certificate via Cloudflare DNS..."));
    if (data) { setNotice(tr("Wildcard certificate issued for {0} and *.{1}.", data.domain, data.domain)); setWildcardSslForm({ api_token: '', email: '' }); refreshAll(); }
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
    await loadRestoreBackups();
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

  async function restoreSelectedBackups() {
    const picked = restoreRows().filter(item => restorePicks.includes(item.pick));
    if (picked.length === 0) return;
    const names = picked.map(item => `  - ${item.account || item.username || '?'}: ${item.filename}${item.source === 's3' ? ` (from ${item.target})` : ''}`).join('\n');
    const warning = picked.length === 1
      ? tr("This overwrites that account's sites and databases with what is in the archive.")
      : tr("These run one after another. Each overwrites that account's sites and databases with what is in the archive.");
    if (!confirm(tr("Restore {0} backup(s)?\n\n{1}\n\n{2}", picked.length, names, warning))) return;
    const data = await request('/maintenance/user-restore-batch', {
      method: 'POST', body: JSON.stringify(splitRestorePicks(picked.map(item => item.pick))),
    }, tr("Queueing restore..."));
    if (data) {
      setRestorePicks([]);
      setNotice(tr("Restore of {0} backup(s) started. Watch Backup logs for progress.", picked.length));
      loadBackupJobs();
    }
  }

  async function describeRestoreBackups(items) {
    // The slow half: finding a manifest in an archive written before the
    // manifest moved to the front means decompressing all of it. The list is
    // already on screen, so this fills in behind it without a spinner.
    const files = items.filter(item => item.websites == null).map(item => item.backup_file);
    if (files.length === 0) return;
    // A few at a time, so rows fill in as the answers come back and one slow
    // archive does not hold up the other sixteen. Archives written before the
    // manifest moved to the front cost a full decompress to read -- 24 seconds
    // for a 5 GB one -- and they age out as the rotation overwrites them.
    for (let start = 0; start < files.length; start += 3) {
      const batch = files.slice(start, start + 3);
      const data = await request('/maintenance/user-restore-backups/describe', {
        method: 'POST', body: JSON.stringify({ backup_files: batch }),
      }, '');
      if (!data?.items) continue;
      const byFile = new Map(data.items.map(row => [row.backup_file, row]));
      setRestoreBackups(prev => prev.map(item => byFile.has(item.backup_file)
        ? { ...item, ...byFile.get(item.backup_file) } : item));
    }
  }

  async function loadRemoteBackups() {
    const data = await request('/maintenance/user-restore-remote', {}, '');
    if (!data) return;
    setRemoteBackups(data.items || []);
    setRemoteBackupErrors(data.errors || []);
  }

  // Local archives and the ones still on a destination, in one list. The
  // id is what a tick records: a path for something already here, and
  // "s3:<target>:<key>" for something that still has to come down.
  function restoreRows() {
    const local = restoreBackups.map(item => ({ ...item, pick: item.backup_file }));
    const remote = remoteBackups.map(item => ({
      ...item,
      pick: `s3:${item.target_id}:${item.key}`,
      websites: null,
      valid: null,
    }));
    return [...local, ...remote];
  }

  function splitRestorePicks(picks) {
    const backup_files = picks.filter(pick => !pick.startsWith('s3:'));
    const remote_items = picks.filter(pick => pick.startsWith('s3:')).map(pick => {
      const rest = pick.slice(3);
      const cut = rest.indexOf(':');
      return { target_id: Number(rest.slice(0, cut)), key: rest.slice(cut + 1) };
    });
    return { backup_files, remote_items };
  }

  async function loadRestoreBackups() {
    const data = await request('/maintenance/user-restore-backups');
    if (data?.items) {
      setRestoreBackups(data.items);
      describeRestoreBackups(data.items);
    }
    if (data?.directory) setRestoreBackupDir(data.directory);
    loadRemoteBackups();
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
      await loadRestoreBackups();
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
      await loadRestoreBackups();
    }
  }

  async function deleteRestoreBackup(file) {
    if (!confirm(tr("Delete this restore backup?\n{0}", file))) return;
    const data = await request(`/maintenance/user-restore-backups?backup_file=${encodeURIComponent(file)}`, { method: 'DELETE' }, tr("Deleting restore backup..."));
    if (data) {
      await loadRestoreBackups();
      await listUserBackups();
    }
  }

  async function uploadUserBackups(files) {
    const selectedFiles = Array.from(files || []);
    if (selectedFiles.length === 0) return;
    const form = new FormData();
    selectedFiles.forEach(file => form.append('files', file));
    try {
      setError(''); setLoading(tr("Uploading full user backups..."));
      const csrfToken = readCookie('opanel_csrf');
      const headers = csrfToken ? { 'X-CSRF-Token': csrfToken } : {};
      const res = await fetch(`${API}/maintenance/user-restore-backups/upload`, {
        method: 'POST',
        credentials: 'include',
        headers,
        body: form,
      });
      const responseText = await res.text();
      let data;
      try { data = responseText ? JSON.parse(responseText) : {}; } catch { data = { detail: responseText || tr("HTTP {0}", res.status) }; }
      if (!res.ok) { if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, tr("Upload failed."))); return; }
      setNotice(tr("Uploaded {0} full user backup file(s).", data.items?.length || selectedFiles.length));
      await loadRestoreBackups();
      await listUserBackups();
    } catch (err) { setError(tr("Full user backup upload failed.")); }
    finally { setLoading(''); }
  }

  async function loadDaBackups() {
    try {
      setError('');
      const res = await fetch(`${API}/maintenance/da-backups`, { credentials: 'include' });
      const data = await res.json();
      if (!res.ok) { if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, tr("Failed to load DA backups."))); return; }
      setDaBackups(data.items || []);
      setDaPicks(prev => prev.filter(name => (data.items || []).some(item => item.filename === name)));
      setDaBackupDir(data.directory || '');
    } catch (err) { setError(tr("Failed to load DA backups.")); }
  }

  async function loadDaImportJobs() {
    try {
      setError('');
      const res = await fetch(`${API}/maintenance/da-import/jobs`, { credentials: 'include' });
      const data = await res.json();
      if (!res.ok) { if (handleAuthExpired(res.status, data.detail)) return; return; }
      setDaImportJobs(data.jobs || []);
    } catch {}
  }

  async function uploadDaBackups(selectedFiles) {
    if (!selectedFiles || !selectedFiles.length) return;
    try {
      setError(''); setLoading(tr("Uploading DA backups..."));
      const csrfToken = readCookie('opanel_csrf');
      const headers = csrfToken ? { 'X-CSRF-Token': csrfToken } : {};
      const form = new FormData();
      Array.from(selectedFiles).forEach(file => form.append('files', file));
      const res = await fetch(`${API}/maintenance/da-backups/upload`, {
        method: 'POST', credentials: 'include', headers, body: form,
      });
      const data = await res.json();
      if (!res.ok) { if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, tr("Upload failed."))); return; }
      setNotice(tr("Uploaded {0} DA backup(s).", data.items?.length || selectedFiles.length));
      await loadDaBackups();
    } catch (err) { setError(tr("DA backup upload failed.")); }
    finally { setLoading(''); }
  }

  async function deleteDaBackup(backupFile) {
    if (!confirm(tr("Delete this DA backup?"))) return;
    try {
      setError('');
      const csrfToken = readCookie('opanel_csrf');
      const headers = csrfToken ? { 'X-CSRF-Token': csrfToken } : {};
      const url = new URL(`${API}/maintenance/da-backups`, window.location.origin);
      url.searchParams.set('backup_file', backupFile);
      const res = await fetch(url, { method: 'DELETE', credentials: 'include', headers });
      const data = await res.json();
      if (!res.ok) { if (handleAuthExpired(res.status, data.detail)) return; setError(formatApiError(data.detail, tr("Delete failed."))); return; }
      setNotice(tr("Deleted DA backup: {0}", data.deleted));
      await loadDaBackups();
    } catch (err) { setError(tr("Failed to delete DA backup.")); }
  }

  async function startDaImport(files) {
    if (!files.length) return;
    const names = files.map(name => `  - ${name}`).join('\n');
    const effect = daOverwrite
      ? tr("Overwrite is on. A user or website already on this server is replaced by what the archive carries: its files are copied over the ones there, its databases are re-imported, and the panel user gets a new password. Websites the account has that the archive does not mention are kept.")
      : tr("Overwrite is off. An archive whose user or domains are already on this server stops without touching them; the others still import.");
    const order = files.length > 1 ? tr("\n\nThey run one after another on the server, so you can leave this page.") : '';
    if (!confirm(tr("Import {0} DirectAdmin backup(s)?\n\n{1}\n\nThis creates panel users, websites, databases and OLS vhosts.\n\n{2}{3}", files.length, names, effect, order))) return;
    const data = await request('/maintenance/da-import-batch', {
      method: 'POST', body: JSON.stringify({ backup_files: files, overwrite: daOverwrite }),
    }, tr("Queueing DA import..."));
    if (!data) return;
    setDaPicks(prev => prev.filter(name => !files.includes(name)));
    const queued = data.jobs?.length || 0;
    const skipped = data.skipped || [];
    setNotice(tr("Queued {0} DA import(s).{1}", queued, skipped.length ? tr(" Already queued, not added again: {0}.", skipped.join(', ')) : ''));
    await loadDaImportJobs();
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

  async function loadPhpExtensions(version = phpConfig.php_version) {
    setPhpExtensions(prev => (prev && prev.php_version === version ? prev : null));
    // Silent: a version that is not installed has nothing to list, not an error banner.
    const data = await request(`/maintenance/php/extensions?php_version=${encodeURIComponent(version)}`, { silent: true });
    setPhpExtensions(data || { php_version: version, extensions: [], modules: [], unavailable: true });
  }

  async function changePhpExtension(ext, action) {
    const version = phpExtensions?.php_version || phpConfig.php_version;
    const question = action === 'install'
      ? tr("Install {0} for PHP {1}? Every website on PHP {1} gets it, and OpenLiteSpeed restarts.", ext.package, version)
      : tr("Remove {0} from PHP {1}? Websites on PHP {1} that use it will stop working, and OpenLiteSpeed restarts.", ext.package, version);
    if (!confirm(question)) return;
    const data = await request(`/maintenance/php/extensions/${encodeURIComponent(version)}/${encodeURIComponent(ext.name)}/${action}`, { method: 'POST' },
      action === 'install' ? tr("Installing {0}...", ext.package) : tr("Removing {0}...", ext.package));
    if (data) {
      setNotice(action === 'install' ? tr("{0} installed for PHP {1}.", ext.name, version) : tr("{0} removed from PHP {1}.", ext.name, version));
      await loadPhpExtensions(version);
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
    if (typed.trim().toLowerCase() !== addon.id) { setError(tr("Name did not match; nothing was removed.")); return; }
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
    setWildcardSslForm({ api_token: '', email: '' });
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

  // A batch of DA imports can run for hours, so follow the queue for as long
  // as anything is in it rather than for a fixed number of polls.
  const daQueued = Object.fromEntries(daImportJobs
    .filter(job => job.status === 'running' || job.status === 'queued')
    .map(job => [job.backup_file, job.status]));
  const daImportsRunning = Object.keys(daQueued).length > 0;
  const daImportsWereRunning = useRef(false);

  useEffect(() => {
    if (!isAuthenticated || page !== 'backups' || !daImportsRunning) return undefined;
    const timer = setInterval(loadDaImportJobs, 3000);
    return () => clearInterval(timer);
  }, [isAuthenticated, page, daImportsRunning]);

  useEffect(() => {
    if (daImportsWereRunning.current && !daImportsRunning) setNotice(tr("DA import queue finished. See Import Jobs for each archive."));
    daImportsWereRunning.current = daImportsRunning;
  }, [daImportsRunning]);

  useEffect(() => {
    if (isAuthenticated && page === 'users') { loadUsers(); loadPlans(); }
    // The databases page shows an owner per row for admins, and offers to
    // hand one over, so it needs the account list too.
    if (isAuthenticated && page === 'databases' && currentUser?.role === 'admin') loadUsers();
    if (isAuthenticated && page === 'php') { loadPhpConfig(); loadPhpVersions(); loadPhpExtensions(); }
    if (isAuthenticated && page === 'firewall') { loadFirewall(); loadFirewallBlocklists(); }
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
    if (isAuthenticated && page === 'dashboard') loadDashboardSummary();
    if (isAuthenticated && page === 'sftp') { loadSftp(); if (isAdmin) loadUsers(); if (!websites.length) refreshAll(); }
    if (isAuthenticated && page === 'settings') { loadPanelSettings(); loadApiTokens(); loadNetworkStatus(); }
    if (isAuthenticated && page === 'backups' && currentUser?.role === 'admin') { loadUsers(); loadSftpTargets(); loadBackupSchedules(); loadRestoreBackups(); loadDaBackups(); loadDaImportJobs(); }
  }, [isAuthenticated, page, currentUser?.role]);

  // Whether MCP is on decides if the page is offered at all, and an admin can
  // switch it on the Addons page without leaving the panel.
  useEffect(() => {
    if (isAuthenticated) loadMcpInfo();
  }, [isAuthenticated, addonList]);

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

  // Fail2ban's own panels are only worth fetching once it is actually there.
  useEffect(() => {
    if (page !== 'addons' || !isAdmin) return;
    const f2b = addonList.find(addon => addon.id === 'fail2ban');
    if (f2b && f2b.installed) loadFail2ban(true);
  }, [page, isAdmin, addonList.find(a => a.id === 'fail2ban')?.installed]);

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

  useEffect(() => { setUserMenuOpen(false); setMalwareDetailJob(null); setShowFirewallIpList(false); window.scrollTo(0, 0); }, [page]);
  // Opening or leaving one website's WAF settings is a page change too.
  useEffect(() => { window.scrollTo(0, 0); }, [wafSiteConfig?.domain]);
  // So is opening or leaving the Firewall's address list.
  useEffect(() => { window.scrollTo(0, 0); }, [showFirewallIpList]);

  function roleLabel(role) {
    return role === 'admin' ? tr("Admin") : tr("End user");
  }

  // The sidebar in three labelled groups -- what a site needs, what guards it,
  // and the server itself -- so nothing hides behind a collapsed "Settings".
  const navSections = [
    { key: 'home', items: [['dashboard', tr("Dashboard"), Home]] },
    { key: 'hosting', title: tr("Hosting"), items: [
      ['websites', tr("Websites"), Globe],
      ['ssl', tr("SSL"), Lock],
      ['databases', tr("Databases"), Database],
      ['cron', tr("Cron"), Clock],
      ['files', tr("File manager"), FolderOpen],
      ['sftp', tr("SFTP accounts"), KeyRound],
      ['backups', tr("Backups"), Archive],
    ] },
    { key: 'security', title: tr("Security"), items: [
      ...(isAdmin ? [['firewall', tr("Firewall"), BrickWall]] : []),
      ['waf', tr("WAF"), ShieldAlert],
      ...(isAdmin ? [['malware', tr("Malware scanner"), Bug]] : []),
      ['wafLogs', tr("Access logs"), ScrollText],
      ['security', tr("Account security"), LockKeyhole],
    ] },
    { key: 'system', title: tr("System"), items: [
      ...(isAdmin ? [['services', tr("Services"), Activity]] : []),
      ...(isAdmin ? [['php', tr("PHP config"), Code2]] : []),
      ...(isAdmin ? [['users', tr("Panel users"), Users]] : []),
      ...(isAdmin ? [['settings', tr("Panel settings"), SettingsIcon]] : []),
      ...(isAdmin ? [['updates', tr("Updates"), RefreshCw]] : []),
      ...(isAdmin ? [['addons', tr("Addons"), PackageOpen]] : []),
      // Offered to a customer only once an admin has turned MCP on.
      ...((isAdmin || mcpInfo?.enabled) ? [['mcp', tr("AI assistants (MCP)"), Bot]] : []),
    ] },
  ].filter(section => section.items.length > 0);

  const navItems = navSections.flatMap(section => section.items);
  const activeNavItem = navItems.find(([key]) => key === page) || navItems[0];

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

  function renderDashboard() {
    const cpu = resourceUsage?.cpu || {};
    const memory = resourceUsage?.memory || {};
    const disk = resourceUsage?.disk || {};
    const network = resourceUsage?.network || {};
    const networkTotal = (Number(network.rx_per_sec) || 0) + (Number(network.tx_per_sec) || 0);
    const sum = dashSummary || {};
    const sites = sum.websites || { total: websites.length, active: websites.length, suspended: 0 };
    const ssl = sum.ssl || { total: websites.length, secured: websites.filter(site => site.ssl_enabled).length, unsecured: [], unsecured_count: 0 };
    const dbCount = sum.databases?.total ?? databases.length;
    const shortDate = value => value ? new Date(value).toLocaleString([], { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';

    // What each status card says, and how loudly: ok / warn / bad / neutral.
    // A customer's plan-usage card above already counts websites and
    // databases, so their cards start at SSL.
    const cards = [
      ...(isAdmin ? [{ key: 'websites', icon: Globe, label: tr("Websites"), value: String(sites.total),
        detail: sites.suspended ? tr("{0} suspended", sites.suspended) : sites.total ? tr("All running") : tr("No websites yet"),
        tone: sites.suspended ? 'warn' : 'ok' }] : []),
      { key: 'ssl', icon: Lock, label: tr("SSL"), value: `${ssl.secured}/${ssl.total}`,
        detail: ssl.unsecured_count ? tr("{0} without SSL", ssl.unsecured_count) : tr("All secured"),
        tone: ssl.unsecured_count ? 'warn' : 'ok' },
      ...(isAdmin ? [{ key: 'databases', icon: Database, label: tr("Databases"), value: String(dbCount), detail: tr("MariaDB"), tone: 'neutral' }] : []),
    ];
    if (isAdmin) {
      const backups = sum.backups;
      cards.push({ key: 'backups', icon: Archive, label: tr("Backups"),
        value: backups ? shortDate(backups.last_run_at) : '—',
        detail: !backups ? tr("Checking…") : !backups.schedules ? tr("No schedule") : backups.failed ? tr("Last run failed") : tr("{0} schedule(s)", backups.schedules),
        tone: !backups ? 'neutral' : !backups.schedules ? 'warn' : backups.failed ? 'bad' : 'ok' });
      const fw = sum.firewall?.enabled;
      cards.push({ key: 'firewall', icon: BrickWall, label: tr("Firewall"),
        value: fw === true ? tr("On") : fw === false ? tr("Off") : '—', detail: tr("iptables"),
        tone: fw === true ? 'ok' : fw === false ? 'bad' : 'neutral' });
      const engine = sum.waf?.engine;
      cards.push({ key: 'waf', icon: ShieldAlert, label: tr("WAF"),
        value: engine === 'on' ? tr("On") : engine === 'off' ? tr("Off") : '—', detail: tr("ModSecurity engine"),
        tone: engine === 'on' ? 'ok' : engine === 'off' ? 'warn' : 'neutral' });
      const mw = sum.malware;
      const last = mw?.last_scan;
      cards.push({ key: 'malware', icon: Bug, label: tr("Malware scanner"),
        value: !mw ? '—' : !mw.installed ? tr("Off") : last?.infected ? tr("{0} threat(s)", last.infected) : last ? tr("Clean") : tr("No scan yet"),
        detail: last ? shortDate(last.finished_at) : mw && !mw.installed ? tr("Not installed") : tr("Last scan"),
        tone: !mw ? 'neutral' : !mw.installed ? 'warn' : last?.infected ? 'bad' : last ? 'ok' : 'neutral' });
      const services = sum.services;
      cards.push({ key: 'services', icon: Activity, label: tr("Services"),
        value: services ? `${services.running}/${services.total}` : '—',
        detail: services?.stopped?.length ? tr("Stopped: {0}", services.stopped.join(', ')) : services ? tr("All running") : tr("Checking…"),
        tone: !services ? 'neutral' : services.stopped?.length ? 'bad' : 'ok' });
    } else {
      const wafOn = websites.filter(site => site.waf_enabled).length;
      cards.push({ key: 'waf', icon: ShieldAlert, label: tr("WAF"), value: `${wafOn}/${websites.length}`,
        detail: wafOn === websites.length ? tr("On for every website") : tr("{0} website(s) without WAF", websites.length - wafOn),
        tone: wafOn === websites.length ? 'ok' : 'warn' });
      cards.push({ key: 'security', icon: LockKeyhole, label: tr("Account security"),
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
    if (isAdmin && sum.malware && !sum.malware.installed) attention.push({ tone: 'warn', text: tr("The malware scanner is not installed."), action: tr("Open"), target: 'malware' });
    if (!isAdmin && !currentUser?.totp_enabled) attention.push({ tone: 'info', text: tr("Two-factor sign-in is off for your account."), action: tr("Turn on"), target: 'security' });
    if (isAdmin && sum.updates?.update_available) attention.push({ tone: 'info', text: tr("Panel update {0} is available.", sum.updates.latest_version), action: tr("Open"), target: 'updates' });

    const quickActions = [
      { key: 'site', icon: Plus, label: tr("New website"), run: () => { setShowCreateSite(true); navigateToPage('websites'); }, primary: true },
      { key: 'db', icon: Database, label: tr("New database"), run: () => { setShowCreateDb(true); navigateToPage('databases'); } },
      { key: 'ssl', icon: Lock, label: tr("Set up SSL"), run: () => navigateToPage('ssl') },
      { key: 'backup', icon: Archive, label: tr("Back up a website"), run: () => navigateToPage('backups') },
      { key: 'sftp', icon: KeyRound, label: tr("New SFTP account"), run: () => { setShowCreateSftp(true); navigateToPage('sftp'); } },
      ...(isAdmin ? [{ key: 'user', icon: Users, label: tr("Panel users"), run: () => navigateToPage('users') }] : []),
    ];

    return <div className="dashboard">
      {isAdmin && <section className="section dash-card dash-resources">
        <div className="dash-card-head"><span className="dash-card-icon"><Activity size={16}/></span><h2>{tr("Server resources")}</h2></div>
        <div className="resource-grid">
          <ResourceCard icon={Cpu} label={tr("CPU")} value={formatPercent(cpu.percent)} percent={cpu.percent} detail={cpu.load?.length ? tr("Load {0}", cpu.load.join(' / ')) : tr("{0} cores", cpu.cores || '--')} />
          <ResourceCard icon={MemoryStick} label={tr("RAM")} value={formatPercent(memory.percent)} percent={memory.percent} detail={`${formatBytes(memory.used)} / ${formatBytes(memory.total)}`} />
          <ResourceCard icon={HardDrive} label={tr("Disk")} value={formatPercent(disk.percent)} percent={disk.percent} detail={`${formatBytes(disk.used)} / ${formatBytes(disk.total)}`} />
          <ResourceCard icon={Network} label={tr("Network")} value={`${formatBytes(networkTotal)}/s`} detail={tr("Down {0}/s / Up {1}/s", formatBytes(network.rx_per_sec), formatBytes(network.tx_per_sec))} />
        </div>
      </section>}
      {!isAdmin && currentUser && (() => {
        // A customer's counterpart to the server meters: how much of the plan is used.
        const storageLimit = storageLimitBytes(currentUser);
        const siteLimit = Number(currentUser.website_limit) || 0;
        const dbLimit = Number(currentUser.database_limit) || 0;
        const usedBytes = Number(currentUser.storage_used_bytes) || 0;
        const pct = (used, limit) => limit > 0 ? (used / limit) * 100 : null;
        return <section className="section dash-card dash-resources" style={{ '--meter-cols': 3 }}>
          <div className="dash-card-head"><span className="dash-card-icon"><Activity size={16}/></span><h2>{tr("Plan usage")}</h2></div>
          <div className="resource-grid">
            <ResourceCard icon={HardDrive} label={tr("Storage")} value={storageLimit ? formatPercent(pct(usedBytes, storageLimit)) : formatBytes(usedBytes)} percent={storageLimit ? pct(usedBytes, storageLimit) : null} detail={storageLimit ? tr("{0} of {1}", formatBytes(usedBytes), formatBytes(storageLimit)) : tr("unlimited")} />
            <ResourceCard icon={Globe} label={tr("Websites")} value={siteLimit ? `${websites.length} / ${siteLimit}` : String(websites.length)} percent={pct(websites.length, siteLimit)} detail={siteLimit ? tr("{0} of {1}", websites.length, siteLimit) : tr("unlimited")} />
            <ResourceCard icon={Database} label={tr("Databases")} value={dbLimit ? `${databases.length} / ${dbLimit}` : String(databases.length)} percent={pct(databases.length, dbLimit)} detail={dbLimit ? tr("{0} of {1}", databases.length, dbLimit) : tr("unlimited")} />
          </div>
        </section>;
      })()}

      {/* State, not navigation: each card says how something stands and opens its page. */}
      <div className={`status-grid${cards.length > 4 ? ' many' : ''}`} style={{ '--status-cols': Math.min(4, cards.length) }}>
        {cards.map(card => <button type="button" key={card.key} className={`status-card tone-${card.tone}`} onClick={() => navigateToPage(card.key === 'security' ? 'security' : card.key)}>
          <span className="status-card-head"><card.icon size={15}/><span>{card.label}</span></span>
          <strong>{card.value}</strong>
          <small>{card.detail}</small>
        </button>)}
      </div>

      <div className="dash-bottom">
        <section className="section dash-card">
          <div className="dash-card-head"><span className="dash-card-icon"><AlertCircle size={16}/></span><h2>{tr("Needs attention")}</h2></div>
          {attention.length === 0
            ? <div className="attention-ok"><CheckCircle size={16}/> {dashSummary ? tr("Everything looks fine.") : tr("Checking…")}</div>
            : <div className="attention-list">
                {attention.map((item, index) => <div className={`attention-item tone-${item.tone}`} key={index}>
                  {item.tone === 'info' ? <RefreshCw size={15}/> : <AlertCircle size={15}/>}
                  <span>{item.text}</span>
                  <button type="button" className="mini secondary" onClick={() => navigateToPage(item.target)}>{item.action}</button>
                </div>)}
              </div>}
        </section>
        <section className="section dash-card">
          <div className="dash-card-head"><span className="dash-card-icon"><Zap size={16}/></span><h2>{tr("Quick actions")}</h2></div>
          <div className="quick-actions">
            {quickActions.map(action => <button type="button" key={action.key} className={action.primary ? '' : 'secondary'} onClick={action.run}><action.icon size={15}/> {action.label}</button>)}
          </div>
        </section>
      </div>
    </div>;
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
            <button className={createSslMode === 'wildcard' ? 'active' : ''} onClick={() => setCreateSslMode('wildcard')}><Globe size={13}/> {tr("Wildcard (Cloudflare)")}</button>
            <button className={createSslMode === 'existing' ? 'active' : ''} onClick={() => { setCreateSslMode('existing'); loadCreateSslCerts(); }}><RefreshCw size={13}/> {tr("Use existing")}</button>
            <button className={createSslMode === 'manual' ? 'active' : ''} onClick={() => setCreateSslMode('manual')}><KeyRound size={13}/> {tr("Manual")}</button>
          </div>
          {createSslMode === 'letsencrypt' && <p className="hint">{tr("certbot HTTP-01 — the domain must point to this server's IP first.")}</p>}
          {createSslMode === 'wildcard' && <div className="create-ssl-fields">
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
        <button className={sslMode === 'wildcard' ? 'active' : ''} onClick={() => setSslMode('wildcard')}><Globe size={14}/> {tr("Wildcard (Cloudflare)")}</button>
        <button className={sslMode === 'existing' ? 'active' : ''} onClick={() => { setSslMode('existing'); loadAvailableCerts(); }}><RefreshCw size={14}/> {tr("Use existing")}</button>
        <button className={sslMode === 'manual' ? 'active' : ''} onClick={() => setSslMode('manual')}><KeyRound size={14}/> {tr("Manual SSL")}</button>
      </div>
      {sslMode === 'letsencrypt' ? <>
        <button className="manual-ssl-submit" disabled={!selectedWebsiteId || !!loading} onClick={() => enableSsl(selectedWebsiteId)}><Lock size={15}/> {tr("Install / Renew SSL")}</button>
        <p className="hint">{tr("certbot HTTP-01 — the domain must point to this server's IP before issuing.")}</p>
      </> : sslMode === 'wildcard' ? <div className="manual-ssl-grid">
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
        ['da-import', tr("Import DA Backups"), Download],
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

      {isAdmin && activeBackupTab === 'restore' && <div className="backup-tab-panel">
        <div className="backup-panel-title">
          <div>
            <h3>{tr("Restore")}</h3>
            <p className="hint">{tr("Every full-user backup on this server: the ones the panel made, and anything uploaded to")} {restoreBackupDir || '/var/backups/opanel/users/restore'}{tr(". Tick the ones to restore and run them in one go.")}</p>
          </div>
          <div className="actions">
            <button className="secondary" disabled={!!loading} onClick={loadRestoreBackups}><RefreshCw size={14}/> {tr("Refresh")}</button>
            <label className="upload-button secondary">
              <Upload size={14}/> {tr("Upload backups")}
              <input type="file" multiple accept=".tar.gz,application/gzip" onChange={e => { uploadUserBackups(e.target.files); e.target.value = ''; }} />
            </label>
          </div>
        </div>

        {remoteBackupErrors.map(message => <div className="info-box" key={message}><AlertCircle size={14}/> {message}</div>)}
        {restoreRows().length === 0 && <EmptyState icon={Archive} message={tr("No backups found to restore.")} />}

        {restoreRows().length > 0 && <>
          <div className="restore-toolbar">
            <label className="schedule-toggle">
              <input
                type="checkbox"
                checked={restorePicks.length > 0 && restorePicks.length === restoreRows().filter(item => item.valid !== false).length}
                ref={box => { if (box) box.indeterminate = restorePicks.length > 0 && restorePicks.length < restoreRows().filter(item => item.valid !== false).length; }}
                onChange={e => setRestorePicks(e.target.checked ? restoreRows().filter(item => item.valid !== false).map(item => item.pick) : [])}
              />
              <span>{tr("Select all restorable")}</span>
            </label>
            <span className="hint">{restorePicks.length ? tr("{0} selected", restorePicks.length) : (restoreRows().length === 1 ? tr("{0} backup", restoreRows().length) : tr("{0} backups", restoreRows().length))}</span>
            <button disabled={!!loading || restorePicks.length === 0} onClick={restoreSelectedBackups}>
              <RotateCcw size={14}/> {tr("Restore selected")}
            </button>
          </div>

          <div className="backup-list">
            {restoreRows().map(item => {
              const picked = restorePicks.includes(item.pick);
              return <div className={`backup-item restore-row${picked ? ' picked' : ''}`} key={item.pick}>
                <label className="restore-pick">
                  <input
                    type="checkbox"
                    checked={picked}
                    disabled={item.valid === false}
                    onChange={() => setRestorePicks(prev => picked ? prev.filter(f => f !== item.pick) : [...prev, item.pick])}
                  />
                </label>
                <span>
                  {item.filename || item.backup_file.split('/').pop()}
                  <small>
                    {item.valid === false
                      ? (item.error || tr("Invalid backup"))
                      : item.source === 's3'
                        ? `${item.account || tr("unknown user")} - ${formatBytes(item.size)} - ${item.bucket}/${item.key}`
                        : `${item.account || item.username || tr("unknown user")} - ${item.websites == null ? 'reading...' : item.websites + tr(" website(s)")} - ${formatBytes(item.size)}${item.modified_at ? ' - ' + new Date(item.modified_at).toLocaleString() : ''}`}
                  </small>
                </span>
                <span className={`badge${item.source === 'account' ? ' ok' : ''}`}>
                  {item.source === 's3' ? item.target : item.source === 'uploaded' ? tr("Uploaded") : tr("On server")}
                </span>
                <div className="actions">
                  {item.source !== 's3' && <>
                    <button className="secondary" disabled={!!loading} onClick={() => downloadUserBackup(item.backup_file)}><Download size={14}/> {tr("Download")}</button>
                    <button className="danger" disabled={!!loading} onClick={() => deleteRestoreBackup(item.backup_file)}><Trash2 size={14}/></button>
                  </>}
                </div>
              </div>;
            })}
          </div>
        </>}

      </div>}

      {isAdmin && activeBackupTab === 'da-import' && <div className="backup-tab-panel">
        <div className="backup-panel-title">
          <div><h3>{tr("Import DirectAdmin Backups")}</h3><p className="hint">{tr("Upload and import DirectAdmin user backups (")}{daBackupDir || '/home/admin/opanel-backups/da'}{tr("). Archives are extracted, users/websites/databases created, and OLS vhosts configured automatically.")}</p></div>
          <div className="actions">
            <button className="secondary" disabled={!!loading} onClick={() => { loadDaBackups(); loadDaImportJobs(); }}><RefreshCw size={14}/> {tr("Refresh")}</button>
            <label className="upload-button">
              <Upload size={14}/> {tr("Upload archive")}
              <input type="file" multiple accept=".tar.gz,.tar.bz2,.tar.xz,.tar.zst,.tar,.tgz,.tbz2,.txz" onChange={e => { uploadDaBackups(e.target.files); e.target.value = ''; }} />
            </label>
          </div>
        </div>

        <h4 style={{margin: '1rem 0 0.5rem'}}>{tr("Archives")}</h4>
        {daBackups.length === 0 && <EmptyState icon={Archive} message={tr("No DA backup archives found. Upload a DirectAdmin backup to get started.")} />}
        {daBackups.length > 0 && <div className="restore-toolbar">
          <label className="schedule-toggle">
            <input
              type="checkbox"
              checked={daPicks.length > 0 && daPicks.length === daBackups.length}
              ref={box => { if (box) box.indeterminate = daPicks.length > 0 && daPicks.length < daBackups.length; }}
              onChange={e => setDaPicks(e.target.checked ? daBackups.map(item => item.filename) : [])}
            />
            <span>{tr("Select all")}</span>
          </label>
          <label className="schedule-toggle" title={tr("Replace a panel user or website that is already on this server instead of stopping on it")}>
            <input type="checkbox" checked={daOverwrite} onChange={e => setDaOverwrite(e.target.checked)} />
            <span>{tr("Overwrite existing")}</span>
          </label>
          <span className="hint">{daPicks.length ? tr("{0} selected", daPicks.length) : (daBackups.length === 1 ? tr("{0} archive", daBackups.length) : tr("{0} archives", daBackups.length))}</span>
          <button disabled={!!loading || daPicks.length === 0} onClick={() => startDaImport(daPicks)}>
            <RotateCcw size={14}/> {tr("Import selected")}
          </button>
        </div>}
        <div className="backup-list">
          {daBackups.map(item => {
            const picked = daPicks.includes(item.filename);
            return <div className={`backup-item restore-row${picked ? ' picked' : ''}`} key={item.filename}>
              <label className="restore-pick">
                <input
                  type="checkbox"
                  checked={picked}
                  onChange={() => setDaPicks(prev => picked ? prev.filter(f => f !== item.filename) : [...prev, item.filename])}
                />
              </label>
              <span>{item.filename}<small>{formatBytes(item.size)}</small></span>
              <span>{daQueued[item.filename] && <span className="badge">{daQueued[item.filename] === 'running' ? tr("Running") : tr("Queued")}</span>}</span>
              <div className="actions">
                <button disabled={!!loading} onClick={() => startDaImport([item.filename])}><RotateCcw size={14}/> {tr("Import")}</button>
                <button className="danger" disabled={!!loading} onClick={() => deleteDaBackup(item.filename)}><Trash2 size={14}/></button>
              </div>
            </div>;
          })}
        </div>

        {daImportJobs.length > 0 && <>
          <h4 style={{margin: '1.5rem 0 0.5rem'}}>{tr("Import Jobs")}</h4>
          <div className="backup-list">
            {daImportJobs.map(job => <div className="backup-item" key={job.job_id}>
              <span>
                {job.backup_file}
                <span className={job.status === 'done' ? 'badge ok' : job.status === 'error' ? 'badge bad' : 'badge'} style={{marginLeft: '0.5rem'}}>{job.status}</span>
                {job.overwrite && <span className="badge" style={{marginLeft: '0.5rem'}}>{tr("overwrite")}</span>}
                <small>{job.message || '...'}</small>
                {job.summary && <small style={{whiteSpace: 'pre-wrap'}}>
                  {tr("Domains:")} {job.summary.imported_domains?.join(', ') || tr("none")}{job.summary.subdomains?.length ? tr(" | Subdomains: {0}", job.summary.subdomains.length) : ''}{job.summary.databases?.length ? tr(" | DBs: {0}", job.summary.databases.length) : ''}{job.summary.ssl_enabled_domains?.length ? tr(" | SSL: {0}", job.summary.ssl_enabled_domains.join(', ')) : ''}
                </small>}
                {job.summary?.warnings?.map((warning, i) => <small key={i}>{warning}</small>)}
                {job.error && <small style={{color: 'var(--danger)'}}>{job.error}</small>}
              </span>
            </div>)}
          </div>
        </>}
      </div>}

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
              {addon.id === 'fail2ban' && renderAddonFail2ban(addon)}
              {addon.id === 'mcp' && renderAddonMcp(addon)}
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

  function renderPhpExtensions() {
    const version = phpConfig.php_version;
    // Descriptions live here rather than in the API so the i18n check sees them.
    const describe = {
      apcu: tr("In-memory user cache (APCu)"), curl: tr("HTTP requests (cURL)"),
      igbinary: tr("Compact serializer, used by Redis"), imagick: tr("Image processing with ImageMagick"),
      imap: tr("Read mailboxes over IMAP / POP3"), intl: tr("Internationalization (ICU)"),
      ldap: tr("LDAP directory access"), mailparse: tr("Parse email messages"),
      memcached: tr("Memcached client"), msgpack: tr("MessagePack serializer"),
      mysql: tr("MySQL / MariaDB (mysqli, PDO)"), opcache: tr("Opcode cache"),
      pgsql: tr("PostgreSQL (pgsql, PDO)"), pspell: tr("Spell checking"), redis: tr("Redis client"),
      snmp: tr("SNMP"), sqlite3: tr("SQLite (sqlite3, PDO)"), sybase: tr("SQL Server / Sybase (PDO dblib)"),
      tidy: tr("Clean up HTML (Tidy)"),
    };
    const data = phpExtensions && phpExtensions.php_version === version ? phpExtensions : null;
    return <div className="user-create-card php-ext-card" style={{ marginTop: 16 }}>
      <div className="php-ext-head">
        <h3>{tr("PHP extensions")} · {tr("PHP")} {version}</h3>
        <p className="hint">{tr("Server-wide: every website on PHP {0} gets the same extensions. Installing or removing one restarts OpenLiteSpeed.", version)}</p>
      </div>
      {!data ? <p className="hint">{tr("Loading…")}</p>
        : data.unavailable ? <p className="hint">{tr("PHP {0} is not installed on this server.", version)}</p>
        : <>
          <ul className="php-ext-list">
            {data.extensions.map(ext => <li key={ext.name}>
              <code>{ext.name}</code>
              <span className="php-ext-desc">{describe[ext.name] || ext.description}</span>
              <span className="php-ext-state">
                {ext.state === 'installed'
                  ? <span className={`badge ${ext.loaded ? 'ok' : 'warn'}`}>{ext.loaded ? tr("Enabled") : tr("Installed, not loaded")}</span>
                  : <span className="badge">{ext.state === 'available' ? tr("Not installed") : tr("Not in repository")}</span>}
                {ext.core && <small>{tr("Default")}</small>}
              </span>
              {ext.state === 'installed'
                ? (ext.core ? <span /> : <button className="mini secondary-light" disabled={!!loading} onClick={() => changePhpExtension(ext, 'remove')}>{tr("Remove")}</button>)
                : <button className="mini" disabled={!!loading || ext.state === 'missing'} onClick={() => changePhpExtension(ext, 'install')}>{tr("Install")}</button>}
            </li>)}
          </ul>
          <details className="php-modules">
            <summary>{tr("Modules PHP {0} loads ({1})", version, data.modules.length)}</summary>
            <div className="php-module-chips">{data.modules.map(m => <code key={m}>{m}</code>)}</div>
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
        <label><span>{tr("PHP version")}</span><select value={phpConfig.php_version} onChange={e => { const v = e.target.value; setPhpConfig(prev => ({ ...prev, php_version: v })); loadPhpConfig(v); loadPhpExtensions(v); }}>
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
          <div><strong>{tr("LSAPI workers:")}</strong> {phpTuning.recommendation.lsapi_children}</div>
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
              {mw.realtime_active && <span className="badge ok">{tr("Real-time on")}</span>}
            </p>
          </div>
          <button className="secondary" disabled={!!loading} onClick={loadMalwareScanStatus}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        <div className="info-box">
          <p className="hint">{mw.detail || tr("Checking status...")}</p>
          {!mwInstalled && <p className="hint" style={{marginTop:8}}>{tr("When enabled, ClamAV is installed on this server, and Linux Malware Detect is layered on top of it (its web-focused signatures catch the PHP shells ClamAV misses). Uploaded files are scanned in real-time.")}</p>}
          {mwInstalled && mwActive && <p className="hint" style={{marginTop:8}}>{tr("Uploaded files are scanned automatically. Scheduled scans default to incremental (files changed recently) once a full baseline scan has run, with a full scan at least weekly.")}</p>}
          <div className="actions" style={{marginTop:12}}>
            {!mwEnabled
              ? <button disabled={!!loading} onClick={() => toggleMalwareScan(true)}><Shield size={14}/> {tr("Enable Malware Scanner")}</button>
              : <button className="danger" disabled={!!loading} onClick={() => toggleMalwareScan(false)}>{tr("Disable Malware Scanner")}</button>
            }
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
              <p className="hint">{tr("Watches every file under")} <code>/home</code> {tr("and scans new/changed files within seconds — instead of only on schedule. Costs RAM per watched file; hits are surfaced, not auto-quarantined.")}</p>
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

      {isAdmin && [
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
    return <>
      <section className="section">
        <div className="section-title">
          <div><h2>{tr("Panel settings")}</h2><p className="hint">{tr("Branding and hostname.")}</p></div>
          <button className="secondary" disabled={!!loading} onClick={loadPanelSettings}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        <div className="panel-settings-grid panel-settings-compact">
          <label><span>{tr("Panel name")}</span><input value={panelSettingsForm.app_name} onChange={e => setPanelSettingsForm(prev => ({ ...prev, app_name: e.target.value }))} placeholder={tr("OPanel")} /></label>
          <label><span>{tr("Panel hostname")}</span><input value={panelSettingsForm.panel_hostname} onChange={e => setPanelSettingsForm(prev => ({ ...prev, panel_hostname: e.target.value }))} placeholder="panel.domain.com" /></label>
          <label className="check-line panel-ssl-status"><input type="checkbox" checked={!!panelSettingsForm.ssl_enabled} onChange={e => setPanelSettingsForm(prev => ({ ...prev, ssl_enabled: e.target.checked }))} /> {tr("Panel SSL")}</label>
          <button disabled={!!loading || !panelSettingsForm.app_name || !panelSettingsForm.panel_hostname} onClick={savePanelSettings}><SettingsIcon size={14}/> {tr("Save settings")}</button>
        </div>
      </section>
      <section className="section">
        <div className="section-title">
          <div>
            <h2>{tr("Server network")}</h2>
            <p className="hint">{tr("Addresses this server answers on. Detected live, so an IPv6 block added later shows up here.")}</p>
          </div>
          <button className="secondary" disabled={!!loading} onClick={loadNetworkStatus}><RefreshCw size={14}/> {tr("Refresh")}</button>
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
      </section>
      <section className="section">
        <div className="section-title">
          <div><h2>{tr("Brand assets")}</h2><p className="hint">{tr("Upload PNG, JPG, WEBP, or ICO files up to 1 MB.")}</p></div>
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
      </section>
      <section className="section">
        <div className="section-title">
          <div><h2>{tr("API Tokens")}</h2><p className="hint">{tr("Provisioning tokens for WHMCS or external billing systems.")}</p></div>
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
      </section>
    </>;
  }

  function renderUsers() {
    if (!isAdmin) return <section className="section"><h2>{tr("Users")}</h2><p className="hint">{tr("No permission.")}</p></section>;
    return <>
      <section className="section">
        <div className="section-title">
          <div><h2>{tr("Panel Users")}</h2><p className="hint">{tr("Manage panel accounts, hosting packages, and create new users.")}</p></div>
          <button className="secondary" disabled={!!loading} onClick={loadUsers}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        <div className="tab-bar">
          <button className={usersTab === 'list' ? 'tab active' : 'tab'} onClick={() => setUsersTab('list')}><Users size={14}/> {tr("List Users")}</button>
          <button className={usersTab === 'packages' ? 'tab active' : 'tab'} onClick={() => setUsersTab('packages')}><PackageOpen size={14}/> {tr("Packages")}</button>
          <button className={usersTab === 'add' ? 'tab active' : 'tab'} onClick={() => setUsersTab('add')}><Plus size={14}/> {tr("Add User")}</button>
        </div>
      </section>
      {usersTab === 'list' && renderUsersListTab()}
      {usersTab === 'packages' && renderUsersPackagesTab()}
      {usersTab === 'add' && renderUsersAddTab()}
    </>;
  }

  function renderUsersListTab() {
    return <section className="section">
      {users.length === 0 && <EmptyState icon={Users} message={tr("No users found.")} />}
      <div className="table">
        {users.map(user => <div className="row user-row" key={user.id}>
          <div className="user-main"><strong>{user.username}</strong><small>{user.email}</small></div>
          <span className="badge">{roleLabel(user.role)}</span>
          <span className={`badge ${user.is_active ? 'ok' : 'warn'}`}>{user.is_active ? tr("Active") : tr("Suspended")}</span>
          <span className={`user-metric${user.storage_used_bytes == null ? ' pending' : ''}`}><HardDrive size={13}/>{storageUsageText(user)}</span>
          <div className="row-actions">
            <button className="mini secondary-light" disabled={!!loading} onClick={() => startEditingUser(user)}><Pencil size={14}/> {tr("Edit")}</button>
            <button className="mini secondary-light" disabled={!!loading} onClick={() => quickLoginUser(user)}><LogIn size={14}/> {tr("Login as")}</button>
            {user.id !== currentUser?.id && <button className={`mini ${user.is_active ? 'danger' : 'secondary-light'}`} disabled={!!loading} onClick={() => toggleUserActive(user)}>{user.is_active ? <><Ban size={14}/> {tr("Suspend")}</> : <><CheckCircle size={14}/> {tr("Unsuspend")}</>}</button>}
            {user.totp_enabled && user.id !== currentUser?.id && <button className="mini secondary-light" disabled={!!loading} onClick={() => resetUserTwoFactor(user)}>{tr("Reset 2FA")}</button>}
            {user.id !== currentUser?.id && <button className="mini danger" disabled={!!loading} onClick={() => deletePanelUser(user)}><Trash2 size={14}/></button>}
          </div>
          {editingUser?.id === user.id && <div className="user-edit-panel">
            <div className="user-edit-heading">
              <div><strong>{tr("Edit")} {user.username}</strong><small>
                {user.id === currentUser?.id ? tr("Role is locked for the active admin session.") : tr("Role changes sign the user out of existing sessions.")}
                {editingUserForm.role === 'admin' ? tr(" Admin accounts bypass website and storage limits.") : ''}
              </small></div>
              <button className="user-edit-close secondary-light" onClick={cancelEditingUser} aria-label={tr("Close user editor")} title={tr("Close user editor")}><X size={16}/></button>
            </div>
            <div className="user-edit-grid">
              <label><span>{tr("Email")}</span><input type="email" value={editingUserForm.email} onChange={e => setEditingUserForm(prev => ({ ...prev, email: e.target.value }))} /></label>
              <label><span>{tr("Role")}</span><select value={editingUserForm.role} disabled={user.id === currentUser?.id} onChange={e => setEditingUserForm(prev => ({ ...prev, role: e.target.value }))}>
                <option value="end_user">{tr("End user")}</option><option value="admin">{tr("Admin")}</option>
              </select></label>
              <label><span>{tr("Package")}</span><select value={editingUserForm._planId || ''} onChange={e => {
                const planId = e.target.value;
                const plan = plans.find(p => String(p.id) === planId);
                if (plan) setEditingUserForm(prev => ({ ...prev, _planId: planId, website_limit: plan.website_limit, storage_limit_mb: plan.storage_limit_mb }));
                else setEditingUserForm(prev => ({ ...prev, _planId: '' }));
              }}>
                <option value="">{tr("Custom")}</option>
                {plans.filter(p => p.active).map(p => <option key={p.id} value={p.id}>{p.name} ({p.website_limit} {tr("sites,")} {p.storage_limit_mb > 0 ? tr("{0} MB", p.storage_limit_mb) : tr("unlimited")})</option>)}
              </select></label>
              <label><span>{tr("Site limit")}</span><input type="number" min="0" max="1000" value={editingUserForm.website_limit} onChange={e => setEditingUserForm(prev => ({ ...prev, website_limit: e.target.value, _planId: '' }))} /></label>
              <label><span>{tr("Disk limit (MB)")} <em>{tr("0 = unlimited")}</em></span><input type="number" min="0" max="1048576" value={editingUserForm.storage_limit_mb} onChange={e => setEditingUserForm(prev => ({ ...prev, storage_limit_mb: e.target.value, _planId: '' }))} /></label>
              <label><span>{tr("New password")} <small>{tr("(leave empty to keep)")}</small></span><input type="password" value={editingUserForm._password || ''} onChange={e => setEditingUserForm(prev => ({ ...prev, _password: e.target.value }))} placeholder={tr("Min 12 characters")} /></label>
            </div>
            <div className="user-edit-actions">
              <button className="secondary-light" onClick={cancelEditingUser}>{tr("Cancel")}</button>
              <button disabled={!!loading || !editingUserForm.email.trim()} onClick={updatePanelUser}><Save size={14}/> {tr("Save changes")}</button>
            </div>
          </div>}
        </div>)}
      </div>
      <div className="section" style={{marginTop:16}}>
        <h2>{tr("Assign domain to user")}</h2>
        <div className="assign-row">
          <select value={assignWebsiteId} onChange={e => setAssignWebsiteId(e.target.value)}>
            <option value="">{tr("Select domain")}</option>
            {websites.map(site => <option key={site.id} value={site.id}>{site.domain}</option>)}
          </select>
          <select value={assignUserId} onChange={e => setAssignUserId(e.target.value)}>
            <option value="">{tr("Select user")}</option>
            {users.map(user => <option key={user.id} value={user.id}>{user.username} ({roleLabel(user.role)})</option>)}
          </select>
          <button disabled={!assignWebsiteId || !assignUserId || !!loading} onClick={assignDomainToUser}>{tr("Assign")}</button>
        </div>
      </div>
    </section>;
  }

  function renderUsersPackagesTab() {
    return <>
      <section className="section">
        <div className="section-title">
          <div><h2>{tr("Hosting Packages")}</h2><p className="hint">{tr("Manage provisioning plans for WHMCS and billing systems.")}</p></div>
          <button className="secondary" disabled={!!loading} onClick={loadPlans}><RefreshCw size={14}/> {tr("Refresh")}</button>
        </div>
        <div className="token-create-form">
          <label><span>{tr("Name")}</span><input value={newPlan.name} onChange={e => { const name = e.target.value; setNewPlan(prev => ({ ...prev, name, slug: name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') })); }} placeholder={tr("Starter")} /></label>
          <label><span>{tr("Sites")}</span><input type="number" min="0" value={newPlan.website_limit} onChange={e => setNewPlan(prev => ({ ...prev, website_limit: parseInt(e.target.value) || 0 }))} /></label>
          <label><span>{tr("Disk (MB)")} <em>{tr("0 = unlimited")}</em></span><input type="number" min="0" value={newPlan.storage_limit_mb} onChange={e => setNewPlan(prev => ({ ...prev, storage_limit_mb: parseInt(e.target.value) || 0 }))} /></label>
          <button disabled={!!loading || !newPlan.name.trim()} onClick={createPlan}><Plus size={14}/> {tr("Add")}</button>
        </div>
        {plans.length === 0 && <p className="hint">{tr("No packages yet. Create one above.")}</p>}
        {plans.length > 0 && <div className="table">
          {plans.map(plan => <div className="row" key={plan.id}>
            <div className="token-info">
              <strong>{plan.name}</strong>
              <small>{plan.website_limit} {tr("site")}{plan.website_limit !== 1 ? 's' : ''} | {plan.storage_limit_mb > 0 ? (plan.storage_limit_mb >= 1024 ? tr("{0} GB", (plan.storage_limit_mb/1024).toFixed(plan.storage_limit_mb % 1024 ? 1 : 0)) : tr("{0} MB", plan.storage_limit_mb)) : tr("Unlimited disk")}</small>
            </div>
            <span className={plan.active ? 'badge ok' : 'badge'}>{plan.active ? tr("Active") : tr("Inactive")}</span>
            <div className="row-actions">
              <button className="mini secondary-light" onClick={() => startEditingPlan(plan)}><Pencil size={14}/> {tr("Edit")}</button>
              <button className="mini danger" onClick={() => deletePlanItem(plan)}><Trash2 size={14}/></button>
            </div>
            {editingPlan?.id === plan.id && <div className="user-edit-panel">
              <div className="user-edit-heading">
                <strong>{tr("Edit")} {plan.name}</strong>
                <button className="user-edit-close secondary-light" onClick={() => setEditingPlan(null)}><X size={16}/></button>
              </div>
              <div className="user-edit-grid">
                <label><span>{tr("Name")}</span><input value={editingPlanForm.name} onChange={e => setEditingPlanForm(prev => ({ ...prev, name: e.target.value }))} /></label>
                <label><span>{tr("Sites")}</span><input type="number" min="0" value={editingPlanForm.website_limit} onChange={e => setEditingPlanForm(prev => ({ ...prev, website_limit: parseInt(e.target.value) || 0 }))} /></label>
                <label><span>{tr("Disk (MB)")} <em>{tr("0 = unlimited")}</em></span><input type="number" min="0" value={editingPlanForm.storage_limit_mb} onChange={e => setEditingPlanForm(prev => ({ ...prev, storage_limit_mb: parseInt(e.target.value) || 0 }))} /></label>
                <label className="check-line"><input type="checkbox" checked={editingPlanForm.active} onChange={e => setEditingPlanForm(prev => ({ ...prev, active: e.target.checked }))} /> {tr("Active")}</label>
              </div>
              <div className="user-edit-actions">
                <button className="secondary-light" onClick={() => setEditingPlan(null)}>{tr("Cancel")}</button>
                <button disabled={!!loading} onClick={updatePlan}><Save size={14}/> {tr("Save")}</button>
              </div>
            </div>}
          </div>)}
        </div>}
      </section>
    </>;
  }

  function renderUsersAddTab() {
    return <>
      <section className="section">
        <div className="section-title">
          <div><h2>{tr("Add panel user")}</h2><p className="hint">{tr("Panel username is also the Linux user. Select a package to auto-fill limits.")}</p></div>
        </div>
        <div className="user-create-card">
          <label><span>{tr("Username")}</span><input value={newUser.username} onChange={e => setNewUser(prev => ({ ...prev, username: e.target.value.toLowerCase() }))} placeholder={tr("johndoe")} /></label>
          <label><span>{tr("Email")}</span><input value={newUser.email} onChange={e => setNewUser(prev => ({ ...prev, email: e.target.value }))} placeholder="user@domain.com" /></label>
          <label><span>{tr("Password")}</span><input value={newUser.password} onChange={e => setNewUser(prev => ({ ...prev, password: e.target.value }))} placeholder={tr("Min 12 characters")} type="password" /></label>
          <label><span>{tr("Role")}</span><select value={newUser.role} onChange={e => setNewUser(prev => ({ ...prev, role: e.target.value }))}>
            <option value="end_user">{tr("End user")}</option><option value="admin">{tr("Admin")}</option>
          </select></label>
          <label><span>{tr("Package")}</span><select value={newUser._planId || ''} onChange={e => {
            const planId = e.target.value;
            const plan = plans.find(p => String(p.id) === planId);
            if (plan) {
              setNewUser(prev => ({ ...prev, _planId: planId, website_limit: plan.website_limit, storage_limit_mb: plan.storage_limit_mb }));
            } else {
              setNewUser(prev => ({ ...prev, _planId: '' }));
            }
          }}>
            <option value="">{tr("Custom")}</option>
            {plans.filter(p => p.active).map(p => <option key={p.id} value={p.id}>{p.name} ({p.website_limit} {tr("sites,")} {p.storage_limit_mb > 0 ? tr("{0} MB", p.storage_limit_mb) : tr("unlimited")})</option>)}
          </select></label>
          <label><span>{tr("Site limit")}</span><input type="number" value={newUser.website_limit} onChange={e => setNewUser(prev => ({ ...prev, website_limit: e.target.value, _planId: '' }))} /></label>
          <label><span>{tr("Disk (MB)")} <em>{tr("0 = unlimited")}</em></span><input type="number" value={newUser.storage_limit_mb} onChange={e => setNewUser(prev => ({ ...prev, storage_limit_mb: e.target.value, _planId: '' }))} /></label>
          <button disabled={!!loading || !newUser.username || !newUser.password} onClick={createUser}><Plus size={14}/> {tr("Create user")}</button>
        </div>
      </section>
    </>;
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
    if (page === 'sftp') return renderSftp();
    if (page === 'services') return isAdmin ? renderServices() : renderDashboard();
    if (page === 'settings') return renderPanelSettings();
    if (page === 'users') return renderUsers();
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
          <button disabled={!!loading || !username || !password} onClick={login}>{loading ? tr("Logging in...") : tr("Login")}</button>
        </div>
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
            {section.items.map(([key, label, Icon]) => <button key={key} type="button" className={page === key ? 'active' : ''} onClick={() => navigateToPage(key)} aria-current={page === key ? 'page' : undefined}>
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
            <h1>{activeNavItem?.[1] || panelSettings.app_name || tr("opanel")}</h1>
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
                <button type="button" role="menuitem" className="user-menu-logout" onClick={() => { setUserMenuOpen(false); logout(); }}><LogOut size={15}/>{tr("Logout")}</button>
              </div>}
            </div>
          </div>
        </section>
        <div className="content-body">
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
