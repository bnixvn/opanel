import { useEffect, useState } from 'react';
import { FolderKey, KeyRound } from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import Drawer from './Drawer.jsx';
import Switch from './Switch.jsx';
import { Field, PasswordField } from './Form.jsx';

// An account's main SFTP login, as SNPanel's (components/SftpAccess.jsx
// there): on or off, and the panel password or one of its own. Two sheets -
// an administrator's or reseller's for an account it manages, and the
// account's own, confirmed with its panel password. A password made up by
// the server is shown once, in the sheet, before it closes.

function ShownOnce({ password }) {
  const t = useT();
  if (!password) return null;
  return <div className="info-box sftp-shown-once">
    <strong>{t('The new SFTP password')}</strong>
    <PasswordField value={password} onChange={() => {}} revealed copy aria-label={t('The new SFTP password')}/>
    <p className="hint">{t('Copy it now: it is not shown again.')}</p>
  </div>;
}

export function SftpAccessDrawer({ user, onClose }) {
  const t = useT();
  const { loading, request, setNotice } = usePanel();
  const [state, setState] = useState(null);
  const [form, setForm] = useState({ enabled: true, password_mode: 'panel', password: '' });
  const [shown, setShown] = useState('');

  useEffect(() => {
    setState(null);
    setShown('');
    if (!user) return;
    request(`/users/${user.id}/sftp`, {}, t('Loading SFTP access...')).then((data) => {
      if (!data) return;
      setState(data);
      setForm({ enabled: data.enabled, password_mode: data.password_mode, password: '' });
    });
  }, [user?.id]);

  async function save(event) {
    event.preventDefault();
    const switching = form.password_mode !== state.password_mode;
    const body = { enabled: form.enabled };
    if (form.password_mode === 'own' && (switching || form.password)) {
      Object.assign(body, { password_mode: 'own', ...(form.password ? { password: form.password } : { generate: true }) });
    } else if (form.password_mode === 'panel' && switching) {
      body.password_mode = 'panel';
    }
    const data = await request(`/users/${user.id}/sftp`, { method: 'PUT', body: JSON.stringify(body) }, t('Saving SFTP access...'));
    if (!data) return;
    setState(data);
    setForm({ enabled: data.enabled, password_mode: data.password_mode, password: '' });
    setNotice(t('SFTP access of {name} saved.', { name: user.username }));
    if (data.password) setShown(data.password);
    else onClose();
  }

  const ownNew = form.password_mode === 'own' && state && state.password_mode !== 'own';
  return <Drawer open={!!user} onClose={onClose} title={user ? t('SFTP access of {name}', { name: user.username }) : ''}
    subtitle={t('The account\'s main login: the whole account, every website in it.')}
    footer={shown ? <button type="button" onClick={onClose}>{t('Done')}</button> : <>
      <button type="button" className="secondary-light" onClick={onClose}>{t('Cancel')}</button>
      <button type="submit" form="sftp-access-form" disabled={!!loading || !state || (form.password && form.password.length < 12)}>
        <FolderKey size={14} aria-hidden="true"/> {t('Save')}</button>
    </>}>
    {shown ? <ShownOnce password={shown}/> : state && <form id="sftp-access-form" className="sftp-access-form" onSubmit={save}>
      <Field label={t('Login')}><input value={state.login} readOnly/></Field>
      <Switch checked={form.enabled} onChange={(on) => setForm((prev) => ({ ...prev, enabled: on }))}>
        <span>{form.enabled ? t('SFTP is on') : t('SFTP is off: the login is refused; its websites and cron keep running')}</span>
      </Switch>
      <Field label={t('Password')}>
        <select value={form.password_mode} onChange={(e) => setForm((prev) => ({ ...prev, password_mode: e.target.value, password: '' }))}>
          <option value="panel">{t('The panel password')}</option>
          <option value="own">{t('A password of its own')}</option>
        </select>
      </Field>
      {form.password_mode === 'own' && <Field label={ownNew ? t('Its SFTP password') : t('New SFTP password')} optional
        hint={ownNew ? t('Left empty, one is made up and shown once.') : t('Left empty, it keeps the one it has.')}>
        <PasswordField value={form.password} onChange={(value) => setForm((prev) => ({ ...prev, password: value }))} generate copy/>
      </Field>}
      {form.password_mode === 'panel' && state.password_mode === 'own' && <p className="hint">
        {t('The panel keeps only a hash of the panel password, so the login stays shut until the account next signs in to the panel.')}</p>}
      {state.waiting_for_sign_in && <p className="hint">{t('Waiting for the account\'s next sign-in to the panel; shut until then.')}</p>}
    </form>}
  </Drawer>;
}

export function OwnSftpPasswordDrawer({ open, primary, onClose, onSaved }) {
  const t = useT();
  const { currentUser, loading, openProfileModal, request, setNotice } = usePanel();
  const [form, setForm] = useState({ password_mode: 'own', password: '', current_password: '', code: '' });
  const [shown, setShown] = useState('');

  useEffect(() => {
    if (!open) return;
    setShown('');
    setForm({ password_mode: 'own', password: '', current_password: '', code: '' });
  }, [open]);

  async function save(event) {
    event.preventDefault();
    const body = {
      current_password: form.current_password, password_mode: form.password_mode,
      ...(form.code ? { code: form.code } : {}),
      ...(form.password_mode === 'own' ? (form.password ? { password: form.password } : { generate: true }) : {}),
    };
    const data = await request('/users/me/sftp-password', { method: 'POST', body: JSON.stringify(body) }, t('Saving SFTP password...'));
    if (!data) return;
    setNotice(form.password_mode === 'own' ? t('SFTP password changed.') : t('SFTP signs in with your panel password again.'));
    onSaved?.();
    if (data.password) setShown(data.password);
    else onClose();
  }

  const totp = !!currentUser?.totp_enabled;
  return <Drawer open={open} onClose={onClose} title={t('SFTP password')}
    subtitle={t('The main login signs in with your panel password, or with a password of its own.')}
    footer={shown ? <button type="button" onClick={onClose}>{t('Done')}</button> : <>
      <button type="button" className="secondary-light" onClick={onClose}>{t('Cancel')}</button>
      <button type="submit" form="own-sftp-form" disabled={!!loading || !form.current_password || (totp && !form.code)
        || (form.password && form.password.length < 12)}><KeyRound size={14} aria-hidden="true"/> {t('Save')}</button>
    </>}>
    {shown ? <ShownOnce password={shown}/> : <form id="own-sftp-form" className="sftp-access-form" onSubmit={save}>
      <Field label={t('SFTP signs in with')}>
        <select value={form.password_mode} onChange={(e) => setForm((prev) => ({ ...prev, password_mode: e.target.value, password: '' }))}>
          <option value="own">{t('A password of its own')}</option>
          <option value="panel">{t('My panel password')}</option>
        </select>
      </Field>
      {form.password_mode === 'own' && <Field label={t('New SFTP password')} optional hint={t('Left empty, one is made up and shown once.')}>
        <PasswordField value={form.password} onChange={(value) => setForm((prev) => ({ ...prev, password: value }))} generate copy/>
      </Field>}
      {form.password_mode === 'panel' && <p className="hint">{t('Your panel password itself is changed on your profile.')}{' '}
        <button type="button" className="mini secondary-light" onClick={() => { onClose(); openProfileModal(); }}>{t('Open my profile')}</button></p>}
      <Field label={t('Your panel password')} required hint={t('To confirm it is you.')}>
        <PasswordField value={form.current_password} onChange={(value) => setForm((prev) => ({ ...prev, current_password: value }))}
          autoComplete="current-password"/>
      </Field>
      {totp && <Field label={t('Two-step code')} required>
        <input value={form.code} inputMode="numeric" autoComplete="one-time-code" maxLength={12}
          onChange={(e) => setForm((prev) => ({ ...prev, code: e.target.value.trim() }))}/>
      </Field>}
    </form>}
  </Drawer>;
}
