// The Shared blocklist addon on the Firewall page: whether this server is
// registered and approved on the collector, what it sent, and when it last
// read the list. Shown only while the addon is installed.
import React, { useEffect, useState } from 'react';
import { AlertCircle, ExternalLink, RefreshCw } from 'lucide-react';

const when = ts => (ts ? new Date(ts * 1000).toLocaleString() : '');

export function SharedBlocklistSection({ request, t }) {
  const [addon, setAddon] = useState(null);
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = async () => {
    const info = await request('/addons/sharedblocklist', { silent: true });
    setAddon(info || null);
    if (info?.installed) setState(await request('/addons/sharedblocklist/status', { silent: true }));
  };
  useEffect(() => { load(); }, []);
  if (!addon?.installed) return null;
  const check = async () => {
    setBusy(true);
    const answer = await request('/addons/sharedblocklist/check', { method: 'POST' });
    setBusy(false);
    if (answer) setState(answer);
  };
  const badge = !state ? null
    : !state.registered ? <span className="badge warn">{t('Registering')}</span>
      : state.status === 'approved' ? <span className="badge ok">{t('Approved')}</span>
        : <span className="badge warn">{t('Waiting for approval')}</span>;
  return <section className="section">
    <div className="section-title">
      <div><h2>{t('Shared blocklist')} {badge}</h2>
        <p className="hint">{t('Blocks the addresses other BNIX servers reported, and reports what this server blocked.')}</p></div>
      <div className="actions">
        <button className="secondary" disabled={busy} onClick={check}><RefreshCw size={14} className={busy ? 'spin' : ''}/> {t('Check now')}</button>
      </div>
    </div>
    {!state && <p className="hint">{t('Loading…')}</p>}
    {state && <div className="info-box">
      <div className="update-version-grid">
        <span>{t('Server ID')} <strong>{state.reporter_id ?? '—'}</strong></span>
        <span>{t('Sent in total')} <strong>{state.sent_total || 0}</strong></span>
        <span>{t('Last report')} <strong>{when(state.reported_at) || t('never')}</strong></span>
        <span>{t('List read')} <strong>{when(state.refreshed_at) || t('never')}</strong></span>
      </div>
      {state.registered && state.status !== 'approved' &&
        <p className="hint">{t('Reports from this server count once the operator approves it on the collector. The shared list is blocked already.')}</p>}
      {state.last_error && <p className="hint addon-error"><AlertCircle size={13}/> {state.last_error}</p>}
      {state.refresh_error && <p className="hint addon-error"><AlertCircle size={13}/> {state.refresh_error}</p>}
      <p className="hint"><a href={state.list_url} target="_blank" rel="noopener noreferrer">{state.list_url} <ExternalLink size={12}/></a></p>
    </div>}
  </section>;
}

export default SharedBlocklistSection;
