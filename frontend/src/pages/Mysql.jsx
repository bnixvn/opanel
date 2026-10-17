import { useEffect, useState } from 'react';
import { Database, OctagonX, Save } from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import { confirmDialog } from '../components/Dialog.jsx';
import PageHeader from '../components/PageHeader.jsx';
import EmptyState from '../components/EmptyState.jsx';
import Switch from '../components/Switch.jsx';
import { Field } from '../components/Form.jsx';
import './Mysql.css';

const msg = (text) => text;
// Server > MySQL, as SNPanel's (pages/Mysql.jsx there): MariaDB's share of
// the server - CPU, IO and memory on its unit, and each database account's
// connection backstop - then MariaDB now: its use, the busiest database
// users and the statements running, which an administrator may stop. A
// customer's queries run in MariaDB, outside their account's own limits.
const FIELDS = [
  ['cpu_quota_percent', msg('CPU limit (% of one core, 0 = none)')],
  ['memory_max_mb', msg('Memory limit (MB, 0 = none)')],
  ['cpu_weight', msg('CPU weight (100 = equal share)')],
  ['io_weight', msg('Disk weight (100 = equal share)')],
  ['max_user_connections', msg('Connections per database account (0 = none)')],
];

export default function MysqlPage() {
  const t = useT();
  const { formatBytes, loading, request, setNotice } = usePanel();
  const [limits, setLimits] = useState(null);
  const [form, setForm] = useState(null);
  const [usage, setUsage] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  async function loadLimits() {
    const data = await request('/mysql/limits', { silent: true });
    if (data) { setLimits(data); setForm({ ...data.limits }); }
  }
  async function loadUsage() {
    const data = await request('/mysql/usage', { silent: true });
    if (data) setUsage(data);
  }
  async function refresh() {
    setRefreshing(true);
    try { await Promise.all([loadLimits(), loadUsage()]); } finally { setRefreshing(false); }
  }
  useEffect(() => { refresh(); }, []);

  const saved = limits?.limits;
  const dirty = !!(form && saved) && (FIELDS.some(([key]) => String(form[key]) !== String(saved[key])) || !!form.userstat !== !!saved.userstat);
  async function save() {
    const body = { userstat: !!form.userstat };
    for (const [key] of FIELDS) body[key] = Number(form[key]);
    const data = await request('/mysql/limits', { method: 'PUT', body: JSON.stringify(body) }, t('Saving MariaDB\'s limits...'));
    if (data) { setLimits(data); setForm({ ...data.limits }); setNotice(t('MariaDB\'s limits saved.')); loadUsage(); }
  }
  async function stop(item) {
    if (!(await confirmDialog({
      title: t('Stop this statement?'), subject: item.info || item.command, danger: true, confirmLabel: t('Stop'),
      message: t('The statement ends with an error for {user}; its connection stays.', { user: item.user }),
    }))) return;
    const data = await request(`/mysql/processes/${item.id}/kill`, { method: 'POST' }, t('Stopping the statement...'));
    if (data) { setNotice(t('Statement stopped.')); loadUsage(); }
  }

  const unit = usage?.unit;
  const server = usage?.server;
  const memoryLimit = unit?.memory_max_bytes;
  return <>
    <PageHeader subtitle={t('MariaDB runs every website\'s queries in its own process, outside the accounts\' limits: here it gets a budget of its own.')}
      onRefresh={refresh} refreshing={refreshing} refreshDisabled={!!loading}/>

    <section className="section mysql-now" aria-label={t('MariaDB now')}>
      <div className="mysql-cards">
        <div className="mysql-card"><span>{t('CPU')}</span><strong>{unit ? `${unit.cpu_percent}%` : '—'}</strong>
          <small>{unit?.cpu_quota_percent ? t('limit {value}%', { value: unit.cpu_quota_percent }) : t('no limit')}</small></div>
        <div className="mysql-card"><span>{t('Memory')}</span><strong>{unit ? formatBytes(unit.memory_bytes) : '—'}</strong>
          <small>{memoryLimit ? t('limit {value}', { value: formatBytes(memoryLimit) }) : t('no limit')}</small></div>
        <div className="mysql-card"><span>{t('Connections')}</span><strong>{server ? server.threads_connected : '—'}</strong>
          <small>{server ? t('of {count} allowed', { count: server.max_connections }) : ''}</small></div>
        <div className="mysql-card"><span>{t('Per database account')}</span><strong>{server ? (server.max_user_connections || t('no limit')) : '—'}</strong>
          <small>{t('connections at most')}</small></div>
      </div>
    </section>

    {form && limits && <section className="section mysql-limits">
      <PageHeader title={t('MariaDB\'s share of the server')} subtitle={t('Held by systemd on MariaDB\'s service, so a busy database cannot take the whole machine.')}/>
      <div className="mysql-grid">
        {FIELDS.map(([key, label]) => <Field key={key} label={t(label)}
          hint={key === 'memory_max_mb' ? t('At least {mb} MB: its buffer pool ({pool} MB) and room to work.', { mb: limits.memory_floor_mb, pool: limits.buffer_pool_mb ?? '?' }) : undefined}>
          <input type="number" min={limits.ranges?.[key]?.[0]} max={limits.ranges?.[key]?.[1]} value={form[key]}
            onChange={(e) => setForm((prev) => ({ ...prev, [key]: e.target.value }))}/>
        </Field>)}
      </div>
      <Switch checked={!!form.userstat} onChange={(on) => setForm((prev) => ({ ...prev, userstat: on }))}>
        {t('Keep per-user statistics (shows the busiest database users below)')}</Switch>
      <div className="mysql-actions">
        <button type="button" disabled={!!loading || !dirty} onClick={save}><Save size={14} aria-hidden="true"/> {t('Save')}</button>
        {dirty && <span className="hint" role="status">{t('Not saved yet: it applies at once, without restarting MariaDB.')}</span>}
      </div>
    </section>}

    <section className="section">
      <PageHeader title={t('Busiest database users')} subtitle={t('Over the last two seconds.')}/>
      {usage && !usage.users && <p className="hint">{t('Turn on per-user statistics above to see them.')}</p>}
      {usage?.users && usage.users.length === 0 && <p className="hint">{t('No database user has used MariaDB yet.')}</p>}
      {usage?.users?.length > 0 && <div className="table-wrap"><table className="data-table">
        <thead><tr><th>{t('Database user')}</th><th>{t('Account')}</th><th>{t('CPU')}</th><th>{t('Busy')}</th><th>{t('Connections')}</th><th>{t('Rows read')}</th></tr></thead>
        <tbody>{usage.users.map((row) => <tr key={row.user}>
          <td><code>{row.user}</code></td><td>{row.owner || <span className="dbt-muted">—</span>}</td>
          <td>{row.cpu_percent}%</td><td>{row.busy_percent}%</td><td>{row.connections}</td><td>{Number(row.rows_read).toLocaleString()}</td>
        </tr>)}</tbody>
      </table></div>}
    </section>

    <section className="section">
      <PageHeader title={t('Running statements')} subtitle={t('What MariaDB is doing now, longest first. Connections waiting idle are left out.')}/>
      {usage?.processes && usage.processes.length === 0 && <EmptyState compact icon={Database} message={t('Nothing is running right now.')}/>}
      {usage?.processes?.length > 0 && <div className="table-wrap"><table className="data-table mysql-processes">
        <thead><tr><th>{t('Database user')}</th><th>{t('Account')}</th><th>{t('Database')}</th><th>{t('Time')}</th><th>{t('Statement')}</th><th><span className="sr-only">{t('Actions')}</span></th></tr></thead>
        <tbody>{usage.processes.map((row) => <tr key={row.id}>
          <td><code>{row.user}</code></td><td>{row.owner || '—'}</td><td>{row.db || '—'}</td><td>{t('{count}s', { count: row.seconds })}</td>
          <td className="mysql-info"><code>{row.info || row.command}</code>{row.state && <small>{row.state}</small>}</td>
          <td><button type="button" className="mini danger" disabled={!!loading} onClick={() => stop(row)}
            aria-label={t('Stop statement {id}', { id: row.id })} title={t('Stop')}><OctagonX size={13} aria-hidden="true"/></button></td>
        </tr>)}</tbody>
      </table></div>}
    </section>
  </>;
}
