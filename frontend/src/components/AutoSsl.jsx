import { useEffect, useState } from 'react';
import { RefreshCw, ShieldCheck } from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import Switch from './Switch.jsx';
import './AutoSsl.css';

const msg = (text) => text;
// AutoSSL's words for a website's state (services/autossl.py).
export const AUTOSSL_STATES = {
  secured: [msg('Secured'), 'badge ok'],
  waiting: [msg('Waiting for DNS'), 'badge warn'],
  failed: [msg('Failed'), 'badge bad'],
  pending: [msg('Not checked yet'), 'badge'],
  skipped: [msg('Own certificate'), 'badge'],
};

export function when(stamp) {
  const date = stamp ? new Date(stamp) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleString() : '';
}

// One website's AutoSSL, on its SSL tab: each name with what public DNS
// says, the certificate, when it is looked at next, an opt-out, a check now.
export function AutoSslSite({ site }) {
  const t = useT();
  const { loading, refreshAll, request, setNotice } = usePanel();
  const [state, setState] = useState(null);
  async function load() {
    const data = await request(`/websites/${site.id}/autossl`, { silent: true });
    if (data) setState(data);
  }
  useEffect(() => { load(); }, [site.id, site.ssl_mode]);
  if (!state) return null;
  async function toggle(on) {
    const data = await request(`/websites/${site.id}/autossl`, { method: 'PATCH', body: JSON.stringify({ enabled: on }) }, t('Saving...'));
    if (data) setState(data);
  }
  async function check() {
    const data = await request(`/websites/${site.id}/autossl/check`, { method: 'POST' }, t('Checking {domain}...', { domain: site.domain }));
    if (!data) return;
    setState(data);
    if (data.issued) { setNotice(t('{domain} is secured with Let\'s Encrypt.', { domain: site.domain })); refreshAll(); }
    else setNotice(data.status === 'secured' ? t('Nothing to do: {domain} is secured.', { domain: site.domain }) : t('Checked: {why}', { why: data.last_error || data.why }));
  }
  const [label, badge] = AUTOSSL_STATES[state.status] || [state.status, 'badge'];
  return <section className="section autossl-site">
    <div className="autossl-head">
      <div><h3><ShieldCheck size={16} aria-hidden="true"/> AutoSSL <span className={badge}>{t(label)}</span></h3>
        <p className="hint">{t('A free Let\'s Encrypt certificate, issued by itself once the website\'s names point at this server.')}</p></div>
      <div className="autossl-actions">
        <Switch checked={state.enabled} onChange={toggle} disabled={!!loading}>{state.enabled ? t('On for this website') : t('Off for this website')}</Switch>
        <button type="button" className="secondary-light" disabled={!!loading || !state.enabled} onClick={check}><RefreshCw size={14} aria-hidden="true"/> {t('Check now')}</button>
      </div>
    </div>
    {state.names.length > 0 && <table className="data-table autossl-names">
      <thead><tr><th>{t('Name')}</th><th>{t('Public DNS')}</th><th>{t('Result')}</th></tr></thead>
      <tbody>{state.names.map((item) => <tr key={item.name}>
        <td><code>{item.name}</code></td><td>{item.addresses.join(', ') || '—'}</td>
        <td>{item.ok ? <span className="badge ok">{t('Points here')}</span> : <span className="autossl-reason">{item.reason}</span>}</td>
      </tr>)}</tbody>
    </table>}
    <p className="hint">
      {state.certificate ? t('Certificate for {names}, until {date}.', { names: state.certificate.names.join(', '), date: when(state.certificate.expires_at) }) + ' ' : ''}
      {state.last_error && state.status !== 'secured' ? `${state.last_error} ` : ''}
      {state.next_check ? t('Next check: {date}.', { date: when(state.next_check) }) : ''}
    </p>
  </section>;
}
