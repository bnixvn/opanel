import { useEffect, useState } from 'react';
import { RefreshCw, Save } from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import PageHeader from '../components/PageHeader.jsx';
import Switch from '../components/Switch.jsx';
import EmptyState from '../components/EmptyState.jsx';
import { Field } from '../components/Form.jsx';
import { AUTOSSL_STATES, when } from '../components/AutoSsl.jsx';
import '../components/AutoSsl.css';

// Security > AutoSSL, as SNPanel's: the server's switch and the addresses
// that count as this server's (NAT), the last pass, and every website with
// its state; a pass now. Each website's own details are on its SSL tab.
export default function AutoSslPage() {
  const t = useT();
  const { loading, navigateToPage, request, setNotice } = usePanel();
  const [data, setData] = useState(null);
  const [ips, setIps] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  async function load() {
    const answer = await request('/autossl', { silent: true });
    if (answer) { setData(answer); setIps((answer.extra_ips || []).join(' ')); }
  }
  async function refresh() {
    setRefreshing(true);
    try { await load(); } finally { setRefreshing(false); }
  }
  useEffect(() => { load(); }, []);
  if (!data) return <PageHeader subtitle={t('Loading…')}/>;

  async function save(patch) {
    const answer = await request('/autossl/settings', { method: 'PUT', body: JSON.stringify(patch) }, t('Saving AutoSSL...'));
    if (answer) { setNotice(t('AutoSSL saved.')); load(); }
  }
  async function checkAll() {
    const answer = await request('/autossl/check', { method: 'POST' }, t('Checking the websites...'));
    if (answer) { setNotice(t('Checked {checked}, issued {issued}, failed {failed}.', answer)); load(); }
  }
  const run = data.last_run;
  const ipsDirty = ips.trim() !== (data.extra_ips || []).join(' ');
  return <>
    <PageHeader subtitle={t('Free Let\'s Encrypt certificates for every website whose names point at this server, issued and kept by themselves.')}
      onRefresh={refresh} refreshing={refreshing} refreshDisabled={!!loading}
      actions={<button type="button" className="secondary-light" disabled={!!loading} onClick={checkAll}><RefreshCw size={14} aria-hidden="true"/> {t('Check now')}</button>}/>
    <section className="section autossl-settings">
      <Switch checked={!!data.enabled} disabled={!!loading} onChange={(on) => save({ enabled: on })}>
        {data.enabled ? t('AutoSSL is on') : t('AutoSSL is off')}</Switch>
      <p className="hint">{t('Every few minutes the websites without a certificate - or whose Let\'s Encrypt certificate misses a name or ends within 30 days - are checked on public DNS. A website is secured once its own name points only at this server; a failure waits longer each time, at most a day. Certificates chosen by hand, reused or wildcard ones are left alone.')}</p>
      <div className="autossl-ips">
        <Field label={t('More addresses of this server')} hint={t('Its own: {ips}. Add the public address in front of it when it sits behind NAT.', { ips: (data.server_ips || []).join(', ') || '—' })}>
          <input value={ips} placeholder="203.0.113.7" spellCheck={false} onChange={(e) => setIps(e.target.value)}/>
        </Field>
        {ipsDirty && <div><button type="button" disabled={!!loading} onClick={() => save({ extra_ips: ips })}><Save size={14} aria-hidden="true"/> {t('Save')}</button></div>}
      </div>
      {run && <div className="autossl-figures">
        <span>{t('Last pass: {date}', { date: when(run.at) })}</span>
        <span>{t('{count} checked', { count: run.checked })}</span>
        <span>{t('{count} issued', { count: run.issued })}</span>
        <span>{t('{count} failed', { count: run.failed })}</span>
      </div>}
    </section>
    <section className="section">
      {data.sites.length === 0 && <EmptyState compact message={t('No websites yet.')}/>}
      {data.sites.length > 0 && <div className="table-wrap"><table className="data-table">
        <thead><tr><th>{t('Website')}</th><th>{t('State')}</th><th>{t('Why')}</th><th>{t('Next check')}</th></tr></thead>
        <tbody>{data.sites.map((site) => {
          const [label, badge] = AUTOSSL_STATES[site.enabled ? site.status : 'skipped'] || [site.status, 'badge'];
          return <tr key={site.website_id}>
            <td><a href={`/website/${site.domain}/ssl`} onClick={(e) => { e.preventDefault(); navigateToPage('website-detail', { path: `/website/${site.domain}/ssl` }); }}>{site.domain}</a></td>
            <td><span className={badge}>{site.enabled ? t(label) : t('Off for this website')}</span></td>
            <td>{site.status === 'secured' && site.certificate ? t('until {date}', { date: when(site.certificate.expires_at) }) : (site.last_error || site.why)}</td>
            <td>{site.enabled ? when(site.next_check) || t('Next pass') : '—'}</td>
          </tr>;
        })}</tbody>
      </table></div>}
    </section>
  </>;
}
