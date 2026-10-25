import { Fragment, useState } from 'react';
import { ArrowRightLeft, Ban, ChevronDown, FolderKey, FolderX, LogIn, Package, Pencil, Play, Plus, Save, Search, ShieldCheck, ShieldOff, Trash2, Users, RefreshCw } from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import PageHeader from '../components/PageHeader.jsx';
import Tabs from '../components/Tabs.jsx';
import Drawer from '../components/Drawer.jsx';
import ActionMenu from '../components/ActionMenu.jsx';
import EmptyState from '../components/EmptyState.jsx';
import { Checkbox, Field } from '../components/Form.jsx';
import { SftpAccessDrawer } from '../components/SftpAccess.jsx';
import './Users.css';

// Users & packages, as SNPanel's (pages/Users.jsx there): a filter by role
// (a reseller's, by state) and a search over one table - who each account
// is, its role, its package, its disk, its state - with Edit and the other
// actions behind "…"; a reseller's customers fold under it. An account and a
// package are edited in side sheets, and added in them; moving a domain to
// another account is a sheet too. The forms in the sheets are OPanel's own
// (App's renderUserFields and renderPlanFields), so the reseller's share,
// the terminal and the resource limits are there as they were.
export default function UsersPage() {
  const t = useT();
  const {
    assignDomainToUser, assignUserId, assignWebsiteId, cancelEditingUser, canManageUsers, createPlan, createUser, currentUser,
    deletePanelUser, deletePlanItem, editingPlan, editingPlanForm, editingUser, editingUserForm, isAdmin, isReseller, limitsOn,
    loadPlans, loadResellerPool, loadUsers, loading, newPlan, newUser, planChips, plans, quickLoginUser, renderPlanFields,
    renderResellerPool, renderUserDisk, renderUserFields, renderUserResources, resetUserTwoFactor, roleLabel, setAssignUserId,
    setAssignWebsiteId, setEditingPlan, setEditingPlanForm, setEditingUserForm, setNewPlan, setNewUser, setUsersTab, startEditingPlan,
    startEditingUser, toggleUserActive, updatePanelUser, updatePlan, userSearch, setUserSearch, users, usersTab, websites,
  } = usePanel();
  const [filter, setFilter] = useState('all');
  const [expanded, setExpanded] = useState([]);
  const [moveOpen, setMoveOpen] = useState(false);
  // Moving copies the files and rewrites the site's users, which takes a
  // while; the sheet says so instead of sitting still (operator, 2026-10-24).
  const [moving, setMoving] = useState(false);
  const moveDomain = async () => {
    setMoving(true);
    try { if (await assignDomainToUser()) setMoveOpen(false); } finally { setMoving(false); }
  };
  const [packageSheet, setPackageSheet] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  // The account whose main SFTP login is being set.
  const [sftpUser, setSftpUser] = useState(null);

  if (!canManageUsers) return <section className="section"><p className="hint">{t('No permission.')}</p></section>;
  // "Add user" is a side sheet over the list: the 'add' tab (the Dashboard's
  // tile) opens it there.
  const tab = usersTab === 'packages' ? 'packages' : 'list';

  async function refresh() {
    setRefreshing(true);
    try {
      if (tab === 'packages') await loadPlans();
      else { await loadUsers(); if (isReseller) await loadResellerPool(); }
    } finally { setRefreshing(false); }
  }

  // The package an account's limits are: the one with its websites and disk.
  const planOf = (user) => plans.find((plan) => plan.website_limit === user.website_limit && plan.storage_limit_mb === user.storage_limit_mb);
  const resellerName = (id) => users.find((user) => user.id === id)?.username || `#${id}`;
  const roleBadge = (role) => <span className={`badge role-${role}`}>{roleLabel(role)}</span>;

  function openMove(user) {
    setAssignWebsiteId('');
    setAssignUserId(user ? String(user.id) : '');
    setMoveOpen(true);
  }

  function userRow(user, children = [], child = false) {
    const self = user.id === currentUser?.id;
    const open = expanded.includes(user.id);
    const toggle = () => setExpanded((prev) => (prev.includes(user.id) ? prev.filter((id) => id !== user.id) : [...prev, user.id]));
    const plan = user.role !== 'admin' ? planOf(user) : null;
    return <Fragment key={user.id}>
      <div className={`ust-row${child ? ' is-child' : ''}${user.is_active ? '' : ' is-suspended'}${editingUser?.id === user.id ? ' is-editing' : ''}`} role="row">
        <span role="cell" className="ust-account">
          <span className="users-avatar" aria-hidden="true">{(user.username || '?').charAt(0).toUpperCase()}</span>
          <span className="ust-name"><strong>{user.username}</strong>{user.email && <small>{user.email}</small>}</span>
        </span>
        <div className="ust-meta">
          {isAdmin && <span role="cell" className="ust-role" data-label={t('Role')}>
            {roleBadge(user.role)}
            {user.reseller_id && !child && <small>{t('via {name}', { name: resellerName(user.reseller_id) })}</small>}
            {children.length > 0 && <button type="button" className="ust-expand" aria-expanded={open} onClick={toggle}
              aria-label={open ? t('Hide the customers of {name}', { name: user.username }) : t('Show the customers of {name}', { name: user.username })}>
              <ChevronDown size={14} aria-hidden="true"/>{t('{count} customer(s)', { count: children.length })}</button>}
          </span>}
          <span role="cell" className="ust-package" data-label={t('Package')}>
            {user.role === 'admin' ? <span className="ust-muted" title={t('Administrators are not bound by website or storage limits.')}>—</span>
              : plan ? plan.name : <span className="ust-muted">{t('Own limits')}</span>}
          </span>
          <span role="cell" className="ust-use" data-label={t('Disk')}>{renderUserDisk(user)}</span>
          {limitsOn && <span role="cell" className="ust-use" data-label={t('CPU / RAM')} title={t('Resource use now')}>{renderUserResources(user)}</span>}
          <span role="cell" className="ust-status" data-label={t('Status')}>
            <span className={`ust-state ${user.is_active ? 'is-on' : 'is-off'}`}><i aria-hidden="true"></i>{user.is_active ? t('Active') : t('Suspended')}</span>
            {user.totp_enabled && <span className="ust-flag" role="img" aria-label={t('Two-step verification is on')} title={t('Two-step verification is on')}><ShieldCheck size={14} aria-hidden="true"/></span>}
            {user.role !== 'admin' && user.sftp_enabled === false && <span className="ust-flag is-off" role="img" aria-label={t('SFTP is off')} title={t('SFTP is off')}><FolderX size={14} aria-hidden="true"/></span>}
          </span>
        </div>
        <span role="cell" className="ust-actions">
          <button type="button" className="secondary-light" disabled={!!loading} onClick={() => startEditingUser(user)}
            aria-label={t('Edit {name}', { name: user.username })}><Pencil size={14} aria-hidden="true"/> {t('Edit')}</button>
          <ActionMenu label={t('More actions for {name}', { name: user.username })} items={[
            { label: t('Log in as'), icon: LogIn, hidden: self, disabled: !!loading, onClick: () => quickLoginUser(user) },
            { label: t('Move a domain to {name}', { name: user.username }), icon: ArrowRightLeft, hidden: !isAdmin || websites.length === 0, disabled: !!loading, onClick: () => openMove(user) },
            { label: t('SFTP access'), icon: FolderKey, hidden: self || user.role === 'admin', disabled: !!loading, onClick: () => setSftpUser(user) },
            { label: t('Reset 2FA'), icon: ShieldOff, hidden: self || !user.totp_enabled, disabled: !!loading, onClick: () => resetUserTwoFactor(user) },
            user.is_active
              ? { label: t('Suspend'), icon: Ban, hidden: self, disabled: !!loading, onClick: () => toggleUserActive(user) }
              : { label: t('Unsuspend'), icon: Play, hidden: self, disabled: !!loading, onClick: () => toggleUserActive(user) },
            'separator',
            { label: t('Delete {name}', { name: user.username }), icon: Trash2, danger: true, hidden: self, disabled: !!loading, onClick: () => deletePanelUser(user) },
          ]}/>
        </span>
      </div>
      {open && children.map((kid) => userRow(kid, [], true))}
    </Fragment>;
  }

  function renderUserList() {
    const needle = userSearch.trim().toLowerCase();
    const matches = (user) => !needle || `${user.username || ''}\n${user.email || ''}`.toLowerCase().includes(needle);
    const count = (test) => users.filter(test).length;
    const filters = isAdmin
      ? [['all', t('All'), () => true], ['admin', t('Admins'), (u) => u.role === 'admin'],
        ['reseller', t('Resellers'), (u) => u.role === 'reseller'], ['end_user', t('Customers'), (u) => u.role === 'end_user']]
      : [['all', t('All'), () => true], ['active', t('Active'), (u) => u.is_active], ['suspended', t('Suspended'), (u) => !u.is_active]];
    const chosen = filters.find(([id]) => id === filter) || filters[0];
    const shown = users.filter(chosen[2]).filter(matches);
    // An administrator's resellers carry their customers, folded, in All and
    // in Resellers; a search, or the Customers filter, lists everyone flat.
    const nest = isAdmin && !needle && (chosen[0] === 'all' || chosen[0] === 'reseller');
    const resellerIds = new Set(users.filter((u) => u.role === 'reseller').map((u) => u.id));
    const customersOf = (user) => (nest && user.role === 'reseller' ? users.filter((u) => u.reseller_id === user.id && u.role === 'end_user') : []);
    const top = nest ? shown.filter((u) => !(u.role === 'end_user' && u.reseller_id && resellerIds.has(u.reseller_id))) : shown;
    return <section className="section">
      <div className="users-toolbar ust-toolbar">
        <Tabs className={`ust-filter${filters.length === 4 ? ' is-four' : ''}`} label={isAdmin ? t('Show accounts by role') : t('Show customers by state')} value={chosen[0]} onChange={setFilter}
          tabs={filters.map(([id, label, test]) => ({ id, label, badge: count(test) }))}/>
        <label className="users-search"><Search size={15} aria-hidden="true"/>
          <input type="search" value={userSearch} onChange={(e) => setUserSearch(e.target.value)}
            aria-label={t('Search by username or email')} placeholder={t('Search by username or email')}/>
        </label>
      </div>
      {users.length === 0 && <EmptyState icon={Users} message={isReseller ? t('No customers yet.') : t('No users found.')}/>}
      {users.length > 0 && shown.length === 0 && <EmptyState icon={Search} message={needle ? t('No users match the search.') : t('No accounts here.')}/>}
      {shown.length > 0 && <div className={`ust${isAdmin ? ' with-role' : ''}${limitsOn ? ' with-resources' : ''}`} role="table" aria-label={isReseller ? t('Customers') : t('Panel users')}>
        <div className="ust-row ust-head" role="row">
          <span role="columnheader">{t('Account')}</span>
          <div className="ust-meta">
            {isAdmin && <span role="columnheader">{t('Role')}</span>}
            <span role="columnheader">{t('Package')}</span>
            <span role="columnheader">{t('Disk')}</span>
            {limitsOn && <span role="columnheader">{t('CPU / RAM')}</span>}
            <span role="columnheader">{t('Status')}</span>
          </div>
          <span role="columnheader"><span className="sr-only">{t('Actions')}</span></span>
        </div>
        {top.map((user) => userRow(user, customersOf(user)))}
      </div>}
    </section>;
  }

  function renderPackages() {
    return <section className="section">
      {plans.length === 0 && <EmptyState icon={Package} message={t('No packages yet.')}
        action={{ label: t('New package'), icon: Plus, onClick: () => setPackageSheet(true) }}/>}
      {plans.length > 0 && <div className="ust ust-plans" role="table" aria-label={t('Packages')}>
        <div className="ust-row ust-head" role="row"><span role="columnheader">{t('Package')}</span><span role="columnheader">{t('Status')}</span><span role="columnheader"><span className="sr-only">{t('Actions')}</span></span></div>
        {plans.map((plan) => <div className={`ust-row${editingPlan?.id === plan.id ? ' is-editing' : ''}`} role="row" key={plan.id}>
          <span role="cell" className="ust-account">
            <span className="users-avatar" aria-hidden="true"><Package size={16}/></span>
            <span><strong>{plan.name}</strong>{planChips(plan)}</span>
          </span>
          <span role="cell" data-label={t('Status')}><span className={plan.active ? 'badge ok' : 'badge'}>{plan.active ? t('Active') : t('Inactive')}</span></span>
          <span role="cell" className="ust-actions">
            <button type="button" className="secondary-light" disabled={!!loading} onClick={() => startEditingPlan(plan)}
              aria-label={t('Edit {name}', { name: plan.name })}><Pencil size={14} aria-hidden="true"/> {t('Edit')}</button>
            <ActionMenu label={t('More actions for {name}', { name: plan.name })} items={[
              { label: t('Delete {name}', { name: plan.name }), icon: Trash2, danger: true, disabled: !!loading, onClick: () => deletePlanItem(plan) },
            ]}/>
          </span>
        </div>)}
      </div>}
    </section>;
  }

  const site = websites.find((item) => String(item.id) === String(assignWebsiteId));
  const sameOwner = site && String(site.owner_id) === String(assignUserId);
  const ownerName = (item) => users.find((u) => String(u.id) === String(item.owner_id))?.username;
  const closeAdd = () => setUsersTab('list');

  return <>
    <PageHeader className="ust-header"
      subtitle={isReseller ? t('Your customers\' accounts, your packages, and new customers.')
        : tab === 'packages' ? t('Manage provisioning plans for WHMCS and billing systems.') : t('Manage panel accounts, hosting packages, and create new users.')}
      onRefresh={refresh} refreshing={refreshing} refreshDisabled={!!loading}
      actions={tab === 'packages'
        ? <button type="button" onClick={() => setPackageSheet(true)}><Plus size={15} aria-hidden="true"/> {t('New package')}</button>
        : <>
          {isAdmin && websites.length > 0 && <button type="button" className="secondary-light" onClick={() => openMove(null)}><ArrowRightLeft size={15} aria-hidden="true"/> {t('Move a domain')}</button>}
          <button type="button" onClick={() => setUsersTab('add')}><Plus size={15} aria-hidden="true"/> {isReseller ? t('Add customer') : t('Add user')}</button>
        </>}/>
    {renderResellerPool()}
    <Tabs label={t('Panel user sections')} value={tab} onChange={setUsersTab} tabs={[
      { id: 'list', icon: Users, label: isReseller ? t('Customers') : t('List users') },
      { id: 'packages', icon: Package, label: t('Packages') },
    ]}/>
    {tab === 'list' && renderUserList()}
    {tab === 'packages' && renderPackages()}

    <Drawer open={usersTab === 'add'} onClose={closeAdd} wide className="ust-drawer"
      title={isReseller ? t('Add customer') : t('Add user')}
      subtitle={t('Panel username is also the Linux user. Select a package to auto-fill limits.')}
      footer={<>
        <button type="button" className="secondary-light" onClick={closeAdd}>{t('Cancel')}</button>
        <button type="button" disabled={!!loading || !newUser.username || !newUser.password}
          onClick={async () => { if (await createUser()) closeAdd(); }}><Plus size={14} aria-hidden="true"/> {isReseller ? t('Create customer') : t('Create user')}</button>
      </>}>
      {renderUserFields(newUser, setNewUser, { creating: true })}
    </Drawer>

    <Drawer open={!!editingUser} onClose={cancelEditingUser} wide className="ust-drawer"
      title={editingUser ? t('Edit {name}', { name: editingUser.username }) : ''}
      subtitle={editingUser && isAdmin ? `${editingUser.id === currentUser?.id ? t('Role is locked for the active admin session.') : t('Role changes sign the user out of existing sessions.')}${editingUserForm.role === 'admin' ? ` ${t('Administrators are not bound by website or storage limits.')}` : ''}` : null}
      footer={<>
        <button type="button" className="secondary-light" onClick={cancelEditingUser}>{t('Cancel')}</button>
        <button type="button" disabled={!!loading || !editingUserForm.email?.trim()} onClick={updatePanelUser}><Save size={14} aria-hidden="true"/> {t('Save changes')}</button>
      </>}>
      {editingUser && renderUserFields(editingUserForm, setEditingUserForm, { user: editingUser })}
    </Drawer>

    <Drawer open={packageSheet} onClose={() => setPackageSheet(false)} wide className="ust-drawer" title={t('New package')}
      subtitle={isReseller ? t('Your own packages: only you see them, to fill in your customers\' limits.') : null}
      footer={<>
        <button type="button" className="secondary-light" onClick={() => setPackageSheet(false)}>{t('Cancel')}</button>
        <button type="button" disabled={!!loading || !newPlan.name.trim()} onClick={async () => { if (await createPlan()) setPackageSheet(false); }}><Plus size={14} aria-hidden="true"/> {t('Add package')}</button>
      </>}>
      {renderPlanFields(newPlan, setNewPlan, { creating: true })}
    </Drawer>

    <Drawer open={!!editingPlan} onClose={() => setEditingPlan(null)} wide className="ust-drawer"
      title={editingPlan ? t('Edit {name}', { name: editingPlan.name }) : ''}
      footer={<>
        <button type="button" className="secondary-light" onClick={() => setEditingPlan(null)}>{t('Cancel')}</button>
        <button type="button" disabled={!!loading} onClick={updatePlan}><Save size={14} aria-hidden="true"/> {t('Save')}</button>
      </>}>
      {editingPlan && <>
        {renderPlanFields(editingPlanForm, setEditingPlanForm)}
        <Checkbox checked={!!editingPlanForm.active} onChange={(on) => setEditingPlanForm((prev) => ({ ...prev, active: on }))} label={t('Active')}/>
      </>}
    </Drawer>

    {isAdmin && <Drawer open={moveOpen} onClose={() => { if (!moving) setMoveOpen(false); }} title={t('Move a domain')}
      subtitle={t('Give a website to another account.')}
      footer={<>
        <button type="button" className="secondary-light" disabled={moving} onClick={() => setMoveOpen(false)}>{t('Cancel')}</button>
        <button type="button" disabled={!assignWebsiteId || !assignUserId || sameOwner || !!loading || moving} onClick={moveDomain}>
          {moving ? <RefreshCw size={14} className="spin" aria-hidden="true"/> : <ArrowRightLeft size={14} aria-hidden="true"/>} {moving ? t('Moving...') : t('Move domain')}</button>
      </>}>
      <div className="form-sections">
        <Field label={t('Domain')} required>
          <select value={assignWebsiteId} onChange={(e) => setAssignWebsiteId(e.target.value)}>
            <option value="">{t('Select domain')}</option>
            {websites.map((item) => <option key={item.id} value={item.id}>{ownerName(item) ? `${item.domain} · ${ownerName(item)}` : item.domain}</option>)}
          </select>
        </Field>
        <Field label={t('New owner')} required error={sameOwner ? t('The domain already belongs to this account.') : ''}>
          <select value={assignUserId} onChange={(e) => setAssignUserId(e.target.value)}>
            <option value="">{t('Select user')}</option>
            {users.map((user) => <option key={user.id} value={user.id}>{user.username} ({roleLabel(user.role)})</option>)}
          </select>
        </Field>
        {moving && <div className="info-box"><span className="job-progress"><span className="progress-bar indeterminate"><span className="progress-bar-fill" /></span></span>
          <p className="hint">{t('Moving the website: its files, database and settings go to the new account. This can take a minute for a large site; keep this sheet open.')}</p></div>}
      </div>
    </Drawer>}
    <SftpAccessDrawer user={sftpUser} onClose={() => { setSftpUser(null); loadUsers(); }}/>
  </>;
}
