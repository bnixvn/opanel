import { useEffect, useState } from 'react';
import { DatabaseZap, Eraser } from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import { confirmDialog } from '../components/Dialog.jsx';
import PageHeader from '../components/PageHeader.jsx';
import Switch from '../components/Switch.jsx';
import EmptyState from '../components/EmptyState.jsx';
import './Redis.css';

// Redis per account (operator, 2026-10-27, as DirectAdmin has it): the
// account's own Redis, on a unix socket in its home. The account turns it on
// and off here and empties it; how much memory it may use is its package's.
// An administrator has none of its own: the page lists the accounts that have
// Redis on, and a reseller sees its customers' under its own.
export default function RedisPage() {
  const t = useT();
  const { formatBytes, isAdmin, isReseller, loading, renderCopyBlock, request, setNotice, setRedisInfo } = usePanel();
  const [data, setData] = useState(null);
  const [accounts, setAccounts] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  // The switch's own state while a change is on its way.
  const [pending, setPending] = useState(null);

  function take(answer) {
    setData(answer);
    setRedisInfo?.(answer);
  }
  async function load() {
    const answer = await request('/redis', { silent: true });
    if (answer) take(answer);
    if (isAdmin || isReseller) {
      const list = await request('/redis/accounts', { silent: true });
      if (list) setAccounts(list.accounts || []);
    }
  }
  async function refresh() {
    setRefreshing(true);
    try { await load(); } finally { setRefreshing(false); }
  }
  useEffect(() => { load(); }, []);
  if (!data) return <PageHeader subtitle={t('Loading…')}/>;

  async function toggle(on) {
    setPending(on);
    try {
      const answer = await request(on ? '/redis/enable' : '/redis/disable', { method: 'POST' },
        on ? t('Turning Redis on...') : t('Turning Redis off...'));
      if (answer) {
        take(answer);
        setNotice(on ? t('Redis is on.') : t('Redis is off.'));
      }
    } finally { setPending(null); }
  }
  async function flush() {
    if (!(await confirmDialog({
      title: t('Flush Redis'), danger: true, confirmLabel: t('Flush'),
      message: t('Every key in this account\'s Redis is deleted. Your websites build their cache again as visitors come.'),
    }))) return;
    const answer = await request('/redis/flush', { method: 'POST' }, t('Flushing Redis...'));
    if (answer) { take(answer); setNotice(t('Redis flushed.')); }
  }

  const socket = data.socket || '';
  const header = <PageHeader subtitle={t('A Redis of this account\'s own, for its websites\' object cache. It listens only on a unix socket in the account\'s home, so no other account can reach it.')}
    onRefresh={refresh} refreshing={refreshing} refreshDisabled={!!loading}/>;

  if (!data.installed) {
    return <>{header}<section className="section"><EmptyState icon={DatabaseZap} title={t('The Redis per account addon is not installed.')}/></section></>;
  }

  const accountList = accounts && <section className="section" aria-label={t('Accounts with Redis on')}>
    <div className="section-title"><div><h2>{t('Accounts with Redis on')}</h2></div></div>
    {accounts.length === 0 && <EmptyState compact icon={DatabaseZap} message={t('No account has turned its Redis on yet.')}/>}
    {accounts.length > 0 && <div className="table-wrap"><table className="data-table">
      <thead><tr><th>{t('Account')}</th><th>{t('State')}</th><th>{t('Memory used')}</th><th>{t('RAM limit')}</th></tr></thead>
      <tbody>{accounts.map((row) => <tr key={row.id}>
        <td><code>{row.username}</code></td>
        <td>{!row.is_active
          ? <span className="badge warn">{t('Suspended')}</span>
          : row.running ? <span className="badge ok">{t('Running')}</span> : <span className="badge bad">{t('Not running')}</span>}</td>
        <td>{row.running ? formatBytes(row.used_bytes) : '—'}</td>
        <td>{row.limit_mb} MB</td>
      </tr>)}</tbody>
    </table></div>}
  </section>;

  // An administrator has no Redis of its own.
  if (data.problem === 'admin' || data.problem === 'no_user') {
    return <>
      {header}
      <section className="section"><p className="hint redis-detail">{t(data.detail)}</p></section>
      {accountList}
    </>;
  }

  if (!data.allowed) {
    return <>
      {header}
      <section className="section">
        <EmptyState icon={DatabaseZap} title={t('Redis is not part of this account\'s package.')}
          message={t('Your package gives Redis no memory (0 MB). Ask your provider if you need it.')}/>
      </section>
      {accountList}
    </>;
  }

  const on = pending ?? data.enabled;
  // Turning it off is always possible; on, only when nothing stands in the way.
  const blocked = !data.enabled && !!data.problem;
  const wpSnippet = `define('WP_REDIS_SCHEME', 'unix');\ndefine('WP_REDIS_PATH', '${socket}');`;
  const phpSnippet = `$redis = new Redis();\n$redis->connect('${socket}');`;
  const laravelSnippet = `REDIS_CLIENT=phpredis\nREDIS_HOST=${socket}\nREDIS_PORT=0\nREDIS_SCHEME=unix\nREDIS_PATH=${socket}`;
  // LiteSpeed Cache's Object tab, field by field, named as the plugin names
  // them: the socket is its Host, with Port 0 (operator, 2026-10-27: OPanel's
  // WordPress has LiteSpeed Cache, so this is the guide most sites need).
  const lscacheSettings = [
    ['Object Cache', 'ON'],
    ['Method', 'Redis'],
    ['Host', <code key="host">{socket}</code>],
    ['Port', '0'],
    ['Default Object Lifetime', '360'],
    ['Username / Password', t('leave empty')],
    ['Redis Database ID', t('0 - a different number for each website')],
    ['Persistent Connection', 'ON'],
    ['Cache WP-Admin', 'OFF'],
    ['Store Transients', 'ON'],
  ];

  return <>
    {header}
    <section className="section redis-page" aria-label="Redis">
      <div className="redis-status">
        <Switch checked={on} pending={pending !== null} disabled={!!loading || blocked} onChange={toggle}>
          {data.enabled ? t('Redis is on') : t('Redis is off')}</Switch>
        {data.enabled && (data.running
          ? <span className="badge ok">{t('Running')}</span>
          : <span className="badge bad">{t('Not running')}</span>)}
      </div>
      {data.problem && <p className="hint redis-detail">{t(data.detail)}</p>}
      <div className="redis-figures">
        <span>{t('RAM limit: {size}', { size: `${data.limit_mb} MB` })}</span>
        {data.enabled && data.running && <span>{t('Used: {size}', { size: formatBytes(data.used_bytes) })}</span>}
      </div>
      <p className="hint">{t('A cache: nothing is saved to disk, and restarting it empties it. When it is full, the keys unused the longest make room.')}</p>
      {renderCopyBlock(t('Socket path'), socket)}
      {data.enabled && <div>
        <button type="button" className="danger-light" disabled={!!loading || !data.running} onClick={flush}>
          <Eraser size={14} aria-hidden="true"/> {t('Flush')}</button>
      </div>}
    </section>

    <section className="section redis-page" aria-label={t('Use it')}>
      <div className="section-title"><div>
        <h2>{t('Use it')}</h2>
        <p className="hint">{t('Your websites connect to the socket; there is no host or port. It has 16 databases (0-15): give each website its own to keep their keys apart.')}</p>
      </div></div>
      <div className="redis-lscache">
        <h3>{t('WordPress with LiteSpeed Cache')}</h3>
        <p className="hint">{t('In wp-admin open LiteSpeed Cache, then Cache, then the Object tab. Set these and press Save Changes: the Status box at the top of the tab should then show Connection Test: Passed.')}</p>
        <dl className="redis-settings">
          {lscacheSettings.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
        </dl>
        {renderCopyBlock('Host', socket)}
      </div>
      {renderCopyBlock(t('WordPress: the Redis Object Cache plugin, in wp-config.php'), wpSnippet, { multiline: true })}
      {renderCopyBlock('PHP (phpredis)', phpSnippet, { multiline: true })}
      {renderCopyBlock('Laravel (.env)', laravelSnippet, { multiline: true })}
      <p className="hint">{t('phpredis reads REDIS_HOST with REDIS_PORT=0; Predis, or a config/database.php that reads them, REDIS_SCHEME and REDIS_PATH.')}</p>
    </section>
    {accountList}
  </>;
}
