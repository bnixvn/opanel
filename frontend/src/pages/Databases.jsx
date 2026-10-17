import { useEffect, useState } from 'react';
import { Check, Copy, Database, Download, ExternalLink, KeyRound, Plus, Search, Trash2, UserCog, X } from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import PageHeader from '../components/PageHeader.jsx';
import Drawer from '../components/Drawer.jsx';
import ActionMenu from '../components/ActionMenu.jsx';
import EmptyState from '../components/EmptyState.jsx';
import { Checkbox, Field, PasswordField } from '../components/Form.jsx';
import './Databases.css';

// The databases, as SNPanel's (pages/Databases.jsx there): one table row
// each - its name, user, website, owner and size - with phpMyAdmin as the
// one button and the rest (SQL download, password, owner, delete) in a
// labelled "…" menu. A new database and a change of owner are side sheets.
export default function DatabasesPage() {
  const t = useT();
  const {
    changeDbPassword, copiedField, createDatabase, createdDbInfo, currentUser, databaseSearch, databases, deleteDatabase,
    downloadDatabase, formatBytes, isAdmin, isReseller, loading, newDatabase, openPhpMyAdmin, refreshAll, request,
    setCopiedField, setCreatedDbInfo, setDatabaseSearch, setError, setNewDatabase, setNotice, setShowCreateDb, showCreateDb,
    users, websites,
  } = usePanel();
  const canSeeOwners = isAdmin || isReseller;
  // The row whose owner is being changed, and the choice so far.
  const [moving, setMoving] = useState(null);
  const [createTouched, setCreateTouched] = useState({});
  const [createTried, setCreateTried] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  // How big each database is: asked apart from the list, which every page
  // load fetches, as MariaDB takes a moment to count.
  const [sizes, setSizes] = useState({});
  async function loadSizes() {
    const data = await request('/databases/sizes', { silent: true });
    if (data) setSizes(data);
  }
  useEffect(() => { loadSizes(); }, [databases.length]);

  const creating = !!showCreateDb;
  function closeCreate() {
    setShowCreateDb(false);
    setCreateTried(false);
    setCreateTouched({});
  }

  const siteName = new Map(websites.map((site) => [site.id, site.domain]));
  const userName = new Map(users.map((user) => [user.id, user.username]));
  if (currentUser) userName.set(currentUser.id, currentUser.username);
  const query = databaseSearch.trim().toLowerCase();
  const shown = query
    ? databases.filter((db) => [db.db_name, db.db_user, siteName.get(db.website_id)].some((text) => String(text || '').toLowerCase().includes(query)))
    : databases;

  // How many databases the account has, and how many its package allows.
  const own = databases.filter((db) => db.owner_id === currentUser?.id).length;
  const limit = Number(currentUser?.database_limit) || 0;
  const subtitle = isAdmin ? t('{count} database(s)', { count: databases.length })
    : limit ? t('{used} of {limit} databases used', { used: own, limit })
      : t('{used} databases, no limit', { used: own });

  // The create form's problems, said under each field once it was left (or
  // Create was pressed) - the same rules createDatabase() holds it to.
  const validName = /^[a-zA-Z0-9_]+$/;
  const createProblems = {
    name: !newDatabase.db_name.trim() ? t('Type a name for the database.')
      : !validName.test(newDatabase.db_name.trim()) ? t('A database name is letters, digits and underscores only.') : '',
    user: newDatabase.db_user.trim() && !validName.test(newDatabase.db_user.trim()) ? t('A database user is letters, digits and underscores only.') : '',
    password: newDatabase.db_password && newDatabase.db_password.trim().length < 12 ? t('The password must be at least 12 characters.')
      : /[^\x20-\x7E]/.test(newDatabase.db_password || '') ? t('The password can only use plain ASCII characters.') : '',
  };
  const shownProblem = (key) => (createTouched[key] || createTried ? createProblems[key] : '');
  async function submitCreate(event) {
    event.preventDefault();
    if (Object.values(createProblems).some(Boolean)) {
      setCreateTried(true);
      document.querySelector('#db-create-form [aria-invalid="true"]')?.focus();
      return;
    }
    if (await createDatabase()) closeCreate();
  }

  async function refresh() {
    setRefreshing(true);
    try { await Promise.all([refreshAll(), loadSizes()]); } finally { setRefreshing(false); }
  }

  function copyToClipboard(text, field) {
    const doCopy = navigator.clipboard ? navigator.clipboard.writeText(text) : new Promise((resolve, reject) => {
      try { const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); resolve(); } catch (e) { reject(e); }
    });
    doCopy.then(() => { setCopiedField(field); setTimeout(() => setCopiedField(null), 2000); }).catch(() => setError(t('Copy failed.')));
  }
  function copyButton(text, field) {
    return <button type="button" className="mini secondary-light" title={copiedField === field ? t('Copied') : t('Copy')} aria-label={t('Copy')}
      onClick={() => copyToClipboard(text, field)}>{copiedField === field ? <Check size={12} style={{ color: 'var(--success)' }}/> : <Copy size={12}/>}</button>;
  }

  // An administrator moves a database no website uses to another account:
  // one a website uses moves with the website.
  async function saveOwner(event) {
    event?.preventDefault();
    const choice = users.find((user) => String(user.id) === String(moving.owner_id));
    if (!choice) { setError(t('Pick an account to move it to.')); return; }
    const data = await request(`/databases/${moving.id}/owner`, {
      method: 'POST', body: JSON.stringify({ owner_id: choice.id, ...(moving.website_id ? { website_id: Number(moving.website_id) } : {}) }),
    }, t('Moving database...'));
    if (data) {
      setMoving(null);
      setNotice(t('{name} now belongs to {owner}.', { name: moving.name, owner: choice.username }));
      await refreshAll();
    }
  }
  const moveChoices = (ownerId) => users.filter((user) => String(user.id) !== String(ownerId));
  // Whose a new database is: the caller's own, or an account it manages.
  const owners = currentUser && !users.some((user) => user.id === currentUser.id) ? [currentUser, ...users] : users;
  const createOwner = String(newDatabase.owner_id || currentUser?.id || '');
  const sitesOf = (ownerId) => websites.filter((site) => String(site.owner_id) === String(ownerId));

  function rowMenu(db) {
    return [
      { label: t('Download as SQL'), icon: Download, disabled: !!loading, onClick: () => downloadDatabase(db.id, db.db_name) },
      { label: t('Change password'), icon: KeyRound, disabled: !!loading, onClick: () => changeDbPassword(db.id) },
      { label: t('Change owner'), hint: db.website_id ? t('Moves with its website') : t('Another account'), icon: UserCog,
        hidden: !isAdmin, disabled: !!loading || !!db.website_id || moveChoices(db.owner_id).length === 0,
        onClick: () => setMoving({ id: db.id, name: db.db_name, owner_id: String(moveChoices(db.owner_id)[0]?.id || ''), from: db.owner_id }) },
      'separator',
      { label: t('Delete database'), icon: Trash2, danger: true, disabled: !!loading, onClick: () => deleteDatabase(db.id, db.db_name) },
    ];
  }
  const sizeOf = (db) => {
    const size = sizes[String(db.id)];
    return size ? <>{formatBytes(Number(size.size_bytes))}<small> · {t('{count} tables', { count: size.tables })}</small></> : '—';
  };

  return <>
    <PageHeader subtitle={subtitle} onRefresh={refresh} refreshing={refreshing} refreshDisabled={!!loading}
      actions={<button type="button" onClick={() => setShowCreateDb(true)}><Plus size={15} aria-hidden="true"/> {t('New database')}</button>}/>
    <section className="section dbt-section">
      {createdDbInfo && <div className="info-box db-created-box">
        <div className="db-created-head"><strong>{t('Database created')}</strong>
          <button type="button" className="mini secondary-light" onClick={() => setCreatedDbInfo(null)} aria-label={t('Close')}><X size={13}/></button></div>
        <div className="db-created-grid">
          <label>{t('Database')}</label><span>{createdDbInfo.db_name} {copyButton(createdDbInfo.db_name, 'db_name')}</span>
          <label>{t('User')}</label><span>{createdDbInfo.db_user} {copyButton(createdDbInfo.db_user, 'db_user')}</span>
          <label>{t('Password')}</label><span><code>{createdDbInfo.db_password}</code> {copyButton(createdDbInfo.db_password, 'db_password')}</span>
        </div>
      </div>}
      {databases.length > 0 && <div className="list-search">
        <Search size={16} aria-hidden="true"/>
        <input value={databaseSearch} onChange={(e) => setDatabaseSearch(e.target.value)}
          placeholder={t('Search by database, user or website')} aria-label={t('Search databases')}/>
        {databaseSearch && <button className="secondary-light icon-button" type="button" onClick={() => setDatabaseSearch('')}
          aria-label={t('Clear the search')} title={t('Clear the search')}><X size={15} aria-hidden="true"/></button>}
      </div>}
      {shown.length === 0 && (query
        ? <EmptyState icon={Search} message={t('No databases match this search.')}
          action={{ label: t('Clear the search'), icon: X, secondary: true, onClick: () => setDatabaseSearch('') }}/>
        : <EmptyState icon={Database} title={t('No databases yet.')} message={t('A database for a website or an application: MariaDB, with phpMyAdmin.')}
          action={{ label: t('New database'), icon: Plus, onClick: () => setShowCreateDb(true) }}/>)}
      {shown.length > 0 && <div className={`dbt${canSeeOwners ? ' with-owner' : ''}`} role="table" aria-label={t('Databases')}>
        <div className="dbt-row dbt-head" role="row">
          <span role="columnheader">{t('Database')}</span>
          <span role="columnheader">{t('User')}</span>
          <span role="columnheader">{t('Website')}</span>
          {canSeeOwners && <span role="columnheader">{t('Owner')}</span>}
          <span role="columnheader">{t('Size')}</span>
          <span role="columnheader"><span className="sr-only">{t('Actions')}</span></span>
        </div>
        {shown.map((db) => <div className="dbt-row" role="row" key={db.id}>
          <span role="cell" className="dbt-name"><Database size={15} aria-hidden="true"/><strong>{db.db_name}</strong></span>
          <span role="cell" data-label={t('User')}><code>{db.db_user}</code></span>
          <span role="cell" data-label={t('Website')}>{siteName.get(db.website_id) || <span className="dbt-muted">{t('No website')}</span>}</span>
          {canSeeOwners && <span role="cell" data-label={t('Owner')}>{userName.get(db.owner_id) || `#${db.owner_id}`}</span>}
          <span role="cell" data-label={t('Size')}>{sizeOf(db)}</span>
          <span role="cell" className="dbt-actions">
            <button type="button" className="secondary-light" disabled={!!loading} onClick={() => openPhpMyAdmin(db.id)}
              title={t('Open {name} in phpMyAdmin', { name: db.db_name })}><ExternalLink size={14} aria-hidden="true"/> phpMyAdmin</button>
            <ActionMenu label={t('More actions for {name}', { name: db.db_name })} items={rowMenu(db)}/>
          </span>
        </div>)}
      </div>}
      <p className="hint">{t('phpMyAdmin signs you in directly; the link works for 60 seconds.')}</p>
    </section>

    <Drawer open={creating} onClose={closeCreate} title={t('New database')} subtitle={t('A MariaDB database, with a user of its own.')}
      footer={<>
        {createTried && Object.values(createProblems).some(Boolean) && <span className="sn-drawer-foot-note" role="alert">{t('Some fields need attention.')}</span>}
        <button type="button" className="secondary-light" onClick={closeCreate}>{t('Cancel')}</button>
        <button type="submit" form="db-create-form" disabled={!!loading}><Plus size={15} aria-hidden="true"/> {t('Create database')}</button>
      </>}>
      <form id="db-create-form" noValidate onSubmit={submitCreate}>
        <Field label={t('Database name')} required error={shownProblem('name')} hint={t('Letters, digits and underscores.')}>
          <input value={newDatabase.db_name} onChange={(e) => setNewDatabase((prev) => ({ ...prev, db_name: e.target.value }))}
            onBlur={() => setCreateTouched((s) => ({ ...s, name: true }))} placeholder={t('e.g. {example}', { example: 'shop' })} autoComplete="off" spellCheck={false}/>
        </Field>
        <Field label={t('Database user')} optional error={shownProblem('user')} hint={t('The database name, if left empty.')}>
          <input value={newDatabase.db_user} onChange={(e) => setNewDatabase((prev) => ({ ...prev, db_user: e.target.value }))}
            onBlur={() => setCreateTouched((s) => ({ ...s, user: true }))} autoComplete="off" spellCheck={false}/>
        </Field>
        <Field label={t('Password')} optional error={shownProblem('password')} hint={t('At least 12 characters. Left empty, one is made for you.')}>
          <PasswordField value={newDatabase.db_password} onChange={(value) => setNewDatabase((prev) => ({ ...prev, db_password: value }))}
            onBlur={() => setCreateTouched((s) => ({ ...s, password: true }))} generate copy/>
        </Field>
        <div className="sn-drawer-grid">
          {canSeeOwners && <Field label={t('Owner')}>
            <select value={createOwner} onChange={(e) => setNewDatabase((prev) => ({ ...prev, owner_id: e.target.value, website_id: '' }))}>
              {owners.map((user) => <option key={user.id} value={user.id}>{user.username}</option>)}
            </select>
          </Field>}
          <Field label={t('Website')} hint={t('Its backups include the database.')}>
            <select value={newDatabase.website_id || ''} onChange={(e) => setNewDatabase((prev) => ({ ...prev, website_id: e.target.value }))}>
              <option value="">{t('No website')}</option>
              {sitesOf(createOwner).map((site) => <option key={site.id} value={site.id}>{site.domain}</option>)}
            </select>
          </Field>
        </div>
        {isAdmin && <Checkbox checked={!!newDatabase.force} onChange={(on) => setNewDatabase((prev) => ({ ...prev, force: on }))}
          label={t('Ignore the package\'s database limit')}/>}
      </form>
    </Drawer>

    <Drawer open={!!moving} onClose={() => setMoving(null)} title={moving ? t('Change the owner of {name}', { name: moving.name }) : ''}
      subtitle={t('The database, its MySQL user and its password do not change, so anything already connected keeps working.')}
      footer={<>
        <button type="button" className="secondary-light" onClick={() => setMoving(null)}>{t('Cancel')}</button>
        <button type="submit" form="db-move-form" disabled={!!loading || !moving?.owner_id}>{t('Move database')}</button>
      </>}>
      {moving && <form id="db-move-form" onSubmit={saveOwner}>
        <Field label={t('Currently')}><input value={userName.get(moving.from) || `#${moving.from}`} readOnly/></Field>
        <Field label={t('Move to')}>
          <select value={moving.owner_id} onChange={(e) => setMoving((prev) => ({ ...prev, owner_id: e.target.value, website_id: '' }))}>
            {moveChoices(moving.from).map((user) => <option key={user.id} value={user.id}>{user.username}</option>)}
          </select>
        </Field>
        <Field label={t('Website')} hint={t('Its backups include the database.')}>
          <select value={moving.website_id || ''} onChange={(e) => setMoving((prev) => ({ ...prev, website_id: e.target.value }))}>
            <option value="">{t('No website')}</option>
            {sitesOf(moving.owner_id).map((site) => <option key={site.id} value={site.id}>{site.domain}</option>)}
          </select>
        </Field>
      </form>}
    </Drawer>
  </>;
}
