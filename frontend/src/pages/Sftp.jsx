import { useState } from 'react';
import { FolderKey, KeyRound, Plus, Trash2 } from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import { confirmDialog } from '../components/Dialog.jsx';
import PageHeader from '../components/PageHeader.jsx';
import Drawer from '../components/Drawer.jsx';
import EmptyState from '../components/EmptyState.jsx';
import { Field, PasswordField } from '../components/Form.jsx';
import { OwnSftpPasswordDrawer } from '../components/SftpAccess.jsx';
import './Sftp.css';

// SFTP, as SNPanel's (pages/Sftp.jsx there): every login in one list - the
// account's own first, marked Main, then the ones it made, each shut into
// one folder. A password change folds open under its row; a new login is
// made in a side sheet. The main login signs in with the panel password or a
// password of its own, and an administrator may have turned it off.
export default function SftpPage() {
  const t = useT();
  const {
    createSftpAccount, currentUser, isAdmin, loadSftp, loading, openProfileModal, renderCopyBlock, request, setNotice, setSftpForm,
    setShowCreateSftp, sftpForm, sftpInfo, showCreateSftp, users, websites,
  } = usePanel();
  const [refreshing, setRefreshing] = useState(false);
  // The login whose password is being changed, and the new one so far.
  const [changing, setChanging] = useState(null);
  const [password, setPassword] = useState('');
  const [ownSheet, setOwnSheet] = useState(false);

  const info = sftpInfo || {};
  const host = info.host || window.location.hostname;
  const port = info.port || 22;
  const accounts = info.accounts || [];
  const endUsers = users.filter((user) => user.role !== 'admin');
  const ownerId = isAdmin ? Number(sftpForm.owner_id) || null : currentUser?.id;
  const owner = isAdmin ? endUsers.find((user) => user.id === ownerId) : currentUser;
  const ownerSites = websites.filter((site) => ownerId && site.owner_id === ownerId);
  const prefix = owner ? `${owner.username.toLowerCase()}_` : '';
  const nameOk = /^[a-z0-9]{1,16}$/.test(sftpForm.suffix.trim().toLowerCase());
  const canCreate = !!owner && nameOk && sftpForm.password.length >= 12;
  const full = !isAdmin && accounts.length >= (info.max_accounts || 10);

  async function refresh() {
    setRefreshing(true);
    try { await loadSftp(); } finally { setRefreshing(false); }
  }
  function startChange(account) {
    setChanging(changing === account.id ? null : account.id);
    setPassword('');
  }
  async function savePassword(event, account) {
    event.preventDefault();
    const data = await request(`/sftp/accounts/${account.id}/password`, { method: 'POST', body: JSON.stringify({ password }) }, t('Changing password...'));
    if (data) { setChanging(null); setPassword(''); setNotice(t('Password of {name} changed.', { name: account.username })); }
  }
  async function remove(account) {
    if (!(await confirmDialog({
      title: t('Delete SFTP account'), subject: account.username, danger: true, confirmLabel: t('Delete'),
      message: t('It can no longer sign in; the files in its folder stay.'),
    }))) return;
    const data = await request(`/sftp/accounts/${account.id}`, { method: 'DELETE' }, t('Deleting SFTP account...'));
    if (data) { setNotice(t('SFTP account {name} deleted.', { name: account.username })); await loadSftp(); }
  }
  async function submitCreate(event) {
    event.preventDefault();
    if (canCreate) await createSftpAccount();
  }

  return <>
    <PageHeader subtitle={t('Connect with FileZilla, WinSCP or any SFTP app - host {host}, port {port}. Each account sees only its own folder.', { host, port })}
      onRefresh={refresh} refreshing={refreshing} refreshDisabled={!!loading}
      actions={<button type="button" disabled={!!loading || full} onClick={() => setShowCreateSftp(true)}><Plus size={15} aria-hidden="true"/> {t('Add an SFTP account')}</button>}/>
    <section className="section sftp-page" aria-label={t('SFTP accounts')}>
      <div className="sftp-connect-grid">
        {renderCopyBlock(t('Host'), host)}
        {renderCopyBlock(t('Port'), String(port))}
        {info.primary && renderCopyBlock(t('Command line'), `sftp -P ${port} ${info.primary.username}@${host}`)}
      </div>

      {(info.primary || accounts.length > 0) && <ul className="sftp-list">
        {info.primary && <li>
          <FolderKey size={16} aria-hidden="true"/>
          <div className="sftp-row-text">
            <strong><code>{info.primary.username}</code> <span className="badge">{t('Main')}</span>
              {info.primary.enabled === false && <span className="badge bad">{t('Off')}</span>}</strong>
            <small>{info.primary.enabled === false
              ? t('Turned off by your administrator: this login is refused. Your websites keep running.')
              : info.primary.waiting_for_sign_in
                ? t('Back on your panel password: it opens at your next sign-in to the panel.')
                : t('The whole account · {password}. It opens at / - one folder per website (for example /{example}/public_html).', {
                  password: info.primary.password_mode === 'own' ? t('a password of its own') : t('the panel password'),
                  example: websites[0]?.domain || 'example.com' })}</small>
          </div>
          <div className="sftp-row-actions">
            <button type="button" className="secondary-light" disabled={!!loading || info.primary.enabled === false}
              onClick={() => setOwnSheet(true)}><KeyRound size={14} aria-hidden="true"/> {t('SFTP password')}</button>
          </div>
        </li>}
        {accounts.map((account) => <li key={account.id}>
          <FolderKey size={16} aria-hidden="true"/>
          <div className="sftp-row-text">
            <strong><code>{account.username}</code></strong>
            <small>{t('Folder: {folder}', { folder: account.directory })}{account.domain ? ` · ${account.domain}` : ''}{isAdmin && account.owner ? ` · ${t('Account')}: ${account.owner}` : ''}</small>
          </div>
          <div className="sftp-row-actions">
            <button type="button" className="secondary-light" disabled={!!loading} onClick={() => startChange(account)}
              aria-expanded={changing === account.id}><KeyRound size={14} aria-hidden="true"/> {t('Change password')}</button>
            <button type="button" className="danger icon-button" disabled={!!loading} onClick={() => remove(account)}
              aria-label={t('Delete {name}', { name: account.username })} title={t('Delete {name}', { name: account.username })}><Trash2 size={14} aria-hidden="true"/></button>
          </div>
          {changing === account.id && <form className="sftp-inline" aria-label={t('Change password')} onSubmit={(event) => savePassword(event, account)}>
            <Field label={t('New password')} hint={t('At least 12 characters. Copy it before saving: it is not shown again.')}>
              <PasswordField value={password} onChange={setPassword} generate copy autoFocus/>
            </Field>
            <div className="sftp-inline-actions">
              <button type="button" className="secondary-light" onClick={() => setChanging(null)}>{t('Cancel')}</button>
              <button type="submit" disabled={!!loading || password.length < 12}>{t('Save')}</button>
            </div>
          </form>}
        </li>)}
      </ul>}
      {sftpInfo && !info.primary && accounts.length === 0 && <EmptyState icon={FolderKey} title={t('No extra SFTP accounts yet.')}
        message={t('A separate login and password for one folder - for a developer or designer who should see one website and nothing else.')}
        action={{ label: t('Add an SFTP account'), icon: Plus, onClick: () => setShowCreateSftp(true) }}/>}
      {full && <p className="hint">{t('This account has {count} SFTP accounts, the most it may have.', { count: info.max_accounts || 10 })}</p>}
    </section>

    <OwnSftpPasswordDrawer open={ownSheet} primary={info.primary} onClose={() => setOwnSheet(false)} onSaved={loadSftp}/>

    <Drawer open={!!showCreateSftp} onClose={() => setShowCreateSftp(false)} title={t('New SFTP account')}
      subtitle={t('A login of its own, shut into one folder.')}
      footer={<>
        <button type="button" className="secondary-light" onClick={() => setShowCreateSftp(false)}>{t('Cancel')}</button>
        <button type="submit" form="sftp-add-form" disabled={!!loading || !canCreate}><Plus size={14} aria-hidden="true"/> {t('Create account')}</button>
      </>}>
      <form id="sftp-add-form" className="sftp-drawer-form" onSubmit={submitCreate}>
        {isAdmin && <Field label={t('Hosting account')} required>
          <select value={sftpForm.owner_id} onChange={(e) => setSftpForm((prev) => ({ ...prev, owner_id: e.target.value, website_id: '' }))}>
            <option value="">{t('-- Select account --')}</option>
            {endUsers.map((user) => <option key={user.id} value={user.id}>{user.username}</option>)}
          </select>
        </Field>}
        <Field label={t('Name')} required hint={t('Signs in as {name}', { name: `${prefix || '…_'}${sftpForm.suffix || '…'}` })}>
          <span className="sftp-name"><span aria-hidden="true">{prefix || '…_'}</span>
            <input value={sftpForm.suffix} maxLength={16} autoComplete="off" spellCheck={false} placeholder="dev"
              onChange={(e) => setSftpForm((prev) => ({ ...prev, suffix: e.target.value.toLowerCase().replace(/[^a-z0-9]/g, '') }))}/>
          </span>
        </Field>
        <div className="sn-drawer-grid">
          <Field label={t('Folder')}>
            <select value={sftpForm.website_id} disabled={!owner} onChange={(e) => setSftpForm((prev) => ({ ...prev, website_id: e.target.value }))}>
              <option value="">{t('Whole account')}</option>
              {ownerSites.map((site) => <option key={site.id} value={site.id}>{site.domain}</option>)}
            </select>
          </Field>
          <Field label={t('Subfolder')} optional>
            <input value={sftpForm.subpath} placeholder={sftpForm.website_id ? 'public_html' : ''} onChange={(e) => setSftpForm((prev) => ({ ...prev, subpath: e.target.value }))}/>
          </Field>
        </div>
        <Field label={t('Password')} required hint={t('At least 12 characters. Copy it now: it is not shown again.')}>
          <PasswordField value={sftpForm.password} onChange={(value) => setSftpForm((prev) => ({ ...prev, password: value }))} generate copy/>
        </Field>
        <p className="hint">{t('Files uploaded through this login belong to the hosting account, so the website can use them.')}</p>
      </form>
    </Drawer>
  </>;
}
