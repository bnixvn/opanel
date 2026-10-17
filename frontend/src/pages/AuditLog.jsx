import { useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, ScrollText } from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import PageHeader from '../components/PageHeader.jsx';
import EmptyState from '../components/EmptyState.jsx';
import './AuditLog.css';

const PAGE = 50;

// Who did what in the panel, as SNPanel's activity log (pages/AuditLog.jsx
// there): the audit log, newest first, a page of 50 at a time, by account
// or all of them; the search filters the page shown. Administrators only
// (GET /users/audit/log).
export default function AuditLogPage() {
  const t = useT();
  const { loadUsers, loading, request, users } = usePanel();
  const [rows, setRows] = useState(null);
  const [userId, setUserId] = useState('');
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    const params = new URLSearchParams({ limit: String(PAGE + 1), offset: String(offset) });
    if (userId) params.set('user_id', userId);
    const data = await request(`/users/audit/log?${params}`, { silent: true });
    setRows(Array.isArray(data) ? data : []);
  }
  useEffect(() => { load(); }, [userId, offset]);
  useEffect(() => { if (users.length === 0) loadUsers(); }, []);
  async function refresh() {
    setRefreshing(true);
    try { await load(); } finally { setRefreshing(false); }
  }

  const nameOf = (id) => users.find((user) => user.id === id)?.username || (id ? `#${id}` : t('System'));
  const needle = query.trim().toLowerCase();
  const page = (rows || []).slice(0, PAGE);
  const shown = needle
    ? page.filter((row) => [nameOf(row.user_id), row.action, row.target, row.detail].join(' ').toLowerCase().includes(needle))
    : page;
  const hasMore = (rows || []).length > PAGE;
  const when = (value) => {
    if (!value) return '';
    const date = new Date(/Z|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  };

  return <>
    <PageHeader subtitle={t('What each account did in the panel, newest first.')} onRefresh={refresh} refreshing={refreshing} refreshDisabled={!!loading}/>
    <section className="section audit-log">
      <div className="audit-filters">
        <label><span>{t('Account')}</span>
          <select value={userId} onChange={(e) => { setOffset(0); setUserId(e.target.value); }}>
            <option value="">{t('All accounts')}</option>
            {users.map((user) => <option key={user.id} value={user.id}>{user.username}</option>)}
          </select>
        </label>
        <label><span>{t('Search this page')}</span>
          <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t('Action, target or detail')} spellCheck="false"/>
        </label>
      </div>
      {rows === null
        ? <p className="hint" aria-busy="true">{t('Loading…')}</p>
        : shown.length === 0
          ? <EmptyState icon={ScrollText} message={needle ? t('Nothing on this page matches.') : t('No activity recorded yet.')}/>
          : <div className="data-table-wrap">
            <table className="data-table audit-table">
              <thead><tr><th>{t('Time')}</th><th>{t('Account')}</th><th>{t('Action')}</th><th>{t('Target')}</th><th>{t('Detail')}</th></tr></thead>
              <tbody>
                {shown.map((row) => <tr key={row.id}>
                  <td className="audit-when">{when(row.created_at)}</td>
                  <td><strong>{nameOf(row.user_id)}</strong></td>
                  <td><code>{row.action}</code></td>
                  <td className="audit-target">{row.target || '—'}</td>
                  <td className="audit-detail">{row.detail || '—'}</td>
                </tr>)}
              </tbody>
            </table>
          </div>}
      <div className="audit-pager">
        <button type="button" className="secondary-light" disabled={offset === 0 || !!loading} onClick={() => setOffset(Math.max(0, offset - PAGE))}><ChevronLeft size={14} aria-hidden="true"/> {t('Newer')}</button>
        <span className="hint">{rows && page.length ? t('{from}-{to}', { from: offset + 1, to: offset + page.length }) : ''}</span>
        <button type="button" className="secondary-light" disabled={!hasMore || !!loading} onClick={() => setOffset(offset + PAGE)}>{t('Older')} <ChevronRight size={14} aria-hidden="true"/></button>
      </div>
    </section>
  </>;
}
