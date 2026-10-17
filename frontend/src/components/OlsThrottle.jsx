import { useEffect, useState } from 'react';
import { Save, Shield } from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import Switch from './Switch.jsx';
import { Field } from './Form.jsx';
import './OlsThrottle.css';

const msg = (text) => text;
// LiteSpeed's per-client throttle, as SNPanel's (components/LswsThrottle.jsx
// there): requests per second and connections per visitor's address, the
// grace and the ban, for every website the server serves. Off until saved
// on: behind a proxy or a CDN every visitor of that edge would share one
// limit. The loopback and the server's own addresses are always trusted.
const FIELDS = [
  ['static_rps', msg('Static requests per second')],
  ['dynamic_rps', msg('PHP requests per second')],
  ['conn_soft', msg('Connections (soft limit)')],
  ['conn_hard', msg('Connections (hard limit)')],
  ['grace_secs', msg('Grace period (seconds)')],
  ['ban_secs', msg('Ban period (seconds)')],
];

export default function OlsThrottle() {
  const t = useT();
  const { loading, request, setNotice } = usePanel();
  const [data, setData] = useState(null);
  const [form, setForm] = useState(null);

  async function load() {
    const answer = await request('/maintenance/ols-throttle', { silent: true });
    if (answer) { setData(answer); setForm({ ...answer.throttle }); }
  }
  useEffect(() => { load(); }, []);
  if (!data || !form) return null;

  const limits = data.limits || {};
  const saved = data.throttle;
  const dirty = !!form.enabled !== !!saved.enabled || FIELDS.some(([key]) => String(form[key]) !== String(saved[key]));
  async function save() {
    const body = { enabled: !!form.enabled };
    for (const [key] of FIELDS) body[key] = Number(form[key]);
    const answer = await request('/maintenance/ols-throttle', { method: 'PUT', body: JSON.stringify(body) }, t('Saving the per-visitor limits...'));
    if (answer) {
      setData(answer); setForm({ ...answer.throttle });
      setNotice(answer.throttle.enabled ? t('Each visitor\'s address is limited now.') : t('Visitors are no longer limited.'));
    }
  }
  const limiting = !!data.current?.limiting;
  const d = data.defaults || {};
  return <section className="section ols-throttle">
    <div className="section-title">
      <div>
        <h2>{t('Per-visitor limits (whole server)')}</h2>
        <p className="hint">{t('LiteSpeed limits the requests and connections of each visitor\'s address, on every website it serves.')}</p>
      </div>
      <span className={limiting ? 'badge ok' : 'badge'}>{limiting ? t('On now') : t('Off now')}</span>
    </div>
    <Switch checked={!!form.enabled} onChange={(on) => setForm((prev) => ({ ...prev, enabled: on }))}>{t('Limit each address')}</Switch>
    <div className="ols-throttle-grid">
      {FIELDS.map(([key, label]) => <Field key={key} label={t(label)}>
        <input type="number" min={limits[key]?.[0]} max={limits[key]?.[1]} value={form[key]} disabled={!form.enabled}
          onChange={(e) => setForm((prev) => ({ ...prev, [key]: e.target.value }))}/>
      </Field>)}
    </div>
    <div className="ols-throttle-actions">
      <button type="button" disabled={!!loading || !dirty} onClick={save}><Save size={14} aria-hidden="true"/> {t('Save')}</button>
      {dirty && <span className="hint" role="status">{t('Not saved yet: saving restarts LiteSpeed.')}</span>}
    </div>
    <p className="hint">{t('Over its requests per second, an address waits for the next second; over the soft connection limit for longer than the grace period, it is refused for the ban period. 0 requests per second is no limit.')}</p>
    <p className="hint"><Shield size={13} aria-hidden="true"/> {t('This server and the loopback are always trusted. Behind Cloudflare or another proxy, leave it off: every visitor of that edge would share one limit. Suggested: {static}/s static, {dynamic}/s PHP, {soft}/{hard} connections, {grace}s grace, {ban}s ban.', {
      static: d.static_rps, dynamic: d.dynamic_rps, soft: d.conn_soft, hard: d.conn_hard, grace: d.grace_secs, ban: d.ban_secs,
    })}</p>
  </section>;
}
