import { useState } from 'react';
import { Archive, Clock, Download, FileText, FolderOpen, Globe, History, Network, Play, Plus, RotateCcw, Trash2, Upload, Users, Zap } from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import { confirmDialog } from '../components/Dialog.jsx';
import PageHeader from '../components/PageHeader.jsx';
import Tabs from '../components/Tabs.jsx';
import Drawer from '../components/Drawer.jsx';
import ActionMenu from '../components/ActionMenu.jsx';
import EmptyState from '../components/EmptyState.jsx';
import { Checkbox, Field, PasswordField } from '../components/Form.jsx';
import './Backups.css';

const msg = (text) => text;
const JOB_TITLES = { site_backup: msg('Website backup'), user_backup: msg('Full user backup'), sftp_backup: msg('SFTP backup') };
const JOB_STATES = { queued: msg('Queued'), running: msg('Running'), done: msg('Done'), error: msg('Failed') };

// A running job's bar: a share when the job counts one, moving when not.
function JobProgress({ job }) {
  if (job.status !== 'running' && job.status !== 'queued') return null;
  return <span className="job-progress">
    <span className={`progress-bar${job.progress_percent == null ? ' indeterminate' : ''}`}>
      <span className="progress-bar-fill" style={job.progress_percent == null ? undefined : { width: `${job.progress_percent}%` }}/>
    </span>
    {job.progress_percent != null && <small className="job-progress-pct">{Math.round(job.progress_percent)}%</small>}
  </span>;
}

// Backup & Restore, as SNPanel's (pages/Backups.jsx there): one page, its
// sections as tabs - Backup (a website, or a whole account), Restore,
// Schedules, Destinations, and the log of backup tasks - each with its one
// Refresh in the page's head. A schedule and a destination are added in side
// sheets; a backup says what goes in it and where before it starts.
export default function BackupsPage() {
  const t = useT();
  const {
    WebsiteSelect, backupJobs, backupSchedules, backupTab, backups, createBackup, createBackupSchedule, createSftpTarget,
    createUserBackup, cronScheduleLabel, databases, deleteBackup, deleteBackupSchedule, deleteSftpTarget, deleteTargetObject,
    deleteUserBackup, downloadBackup, downloadUserBackup, formatBytes, isAdmin, loadBackupJobs, loadSftpTargets, loadTargetObjects,
    loading, newBackupSchedule, newSftpTarget, refreshBackupArea, refreshScheduledBackupArea, refreshUserBackupArea,
    renderRestoreWizard, renderSchedulePicker, restoreBackup, restoreUserBackup, runBackupScheduleNow, selectedBackupUserId,
    selectedSftpTargetId, selectedWebsiteId, setBackupTab, setNewBackupSchedule, setNewSftpTarget, setSelectedBackupUserId,
    setSelectedSftpTargetId, sftpTargets, targetObjects, testBackupTarget, uploadBackup, userBackups, users, websites,
  } = usePanel();
  const busy = !!loading;
  const [scheduleSheet, setScheduleSheet] = useState(false);
  const [targetSheet, setTargetSheet] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const tabs = [
    { id: 'website', label: t('Backup'), icon: Archive },
    { id: 'restore', label: t('Restore'), icon: History, hidden: !isAdmin },
    { id: 'schedule', label: t('Schedules'), icon: Clock, hidden: !isAdmin },
    { id: 'destination', label: t('Destinations'), icon: Network, hidden: !isAdmin },
    { id: 'logs', label: t('Backup logs'), icon: FileText },
  ];
  // The Backup tab's two halves were two tabs: 'user' is its account half.
  const scope = isAdmin && backupTab === 'user' ? 'user' : 'website';
  const wanted = backupTab === 'user' ? 'website' : backupTab;
  const active = tabs.some((tab) => tab.id === wanted && !tab.hidden) ? wanted : 'website';

  const userNameById = (id) => users.find((user) => String(user.id) === String(id))?.username || t('User #{id}', { id });
  const scheduleUserLabel = (item) => {
    if (item.all_users) return t('All users');
    const ids = (item.user_ids && item.user_ids.length > 0) ? item.user_ids : (item.user_id ? [item.user_id] : []);
    return ids.length ? ids.map(userNameById).join(', ') : t('No users');
  };
  const targetName = (id) => (id ? sftpTargets.find((target) => target.id === id)?.name || t('Destination #{id}', { id }) : t('Local only'));
  const jobTitle = (job) => t(JOB_TITLES[job.kind] || 'Backup task');
  const jobState = (job) => (JOB_STATES[job.status] ? t(JOB_STATES[job.status]) : job.status);
  const jobDetail = (job) => job.error || job.remote_file || job.backup_file || job.message || job.status;
  const jobTime = (job) => {
    const stamp = job.finished_at || job.started_at || job.created_at || '';
    const date = stamp ? new Date(stamp) : null;
    return date && !Number.isNaN(date.getTime()) ? date.toLocaleString() : stamp;
  };
  const liveJobs = backupJobs.filter((job) => job.status === 'queued' || job.status === 'running');

  // Before a backup starts: what goes in it and where it goes.
  async function confirmSiteBackup() {
    const site = websites.find((item) => String(item.id) === String(selectedWebsiteId));
    if (!site) return;
    const dbs = databases.filter((db) => String(db.website_id) === String(site.id));
    const ok = await confirmDialog({
      title: t('Back up {domain}?', { domain: site.domain }),
      body: <><dl className="bk-summary">
        <div><dt>{t('What goes in')}</dt><dd>
          {t('The files in {path}', { path: <code>{site.root_path}</code> })}
          <br/>{dbs.length ? t('An SQL export of {count} database(s): {names}', { count: dbs.length, names: dbs.map((db) => db.db_name).join(', ') }) : t('No database is attached to this website.')}
        </dd></div>
        <div><dt>{t('Where it goes')}</dt><dd>{t('This server, in the website\'s backup list: download or restore it from there.')}</dd></div>
      </dl><p className="hint">{t('It runs on the server: you can leave this page.')}</p></>,
      confirmLabel: t('Create backup'),
    });
    if (ok) createBackup();
  }
  async function confirmUserBackup() {
    const user = users.find((item) => String(item.id) === String(selectedBackupUserId));
    if (!user) return;
    const sites = websites.filter((site) => String(site.owner_id) === String(user.id));
    const dbs = databases.filter((db) => String(db.owner_id) === String(user.id));
    const ok = await confirmDialog({
      title: t('Back up the account {name}?', { name: user.username }),
      body: <><dl className="bk-summary">
        <div><dt>{t('What goes in')}</dt><dd>{t('The panel account, {sites} website(s) and {dbs} database(s): their files, database dumps and what a restore needs.', { sites: sites.length, dbs: dbs.length })}</dd></div>
        <div><dt>{t('Where it goes')}</dt><dd>{selectedSftpTargetId
          ? t('This server, and a copy to {name}.', { name: targetName(Number(selectedSftpTargetId)) })
          : t('This server only.')}</dd></div>
      </dl><p className="hint">{t('It runs on the server: you can leave this page.')}</p></>,
      confirmLabel: t('Create backup'),
    });
    if (ok) createUserBackup();
  }

  const refreshers = {
    website: scope === 'user' ? refreshUserBackupArea : refreshBackupArea,
    restore: null,
    schedule: refreshScheduledBackupArea,
    destination: loadSftpTargets,
    logs: () => loadBackupJobs(true),
  };
  async function refresh() {
    const run = refreshers[active];
    if (!run) return;
    setRefreshing(true);
    try { await run(); } finally { setRefreshing(false); }
  }
  const subtitles = {
    website: scope === 'user'
      ? t('Includes the panel user, all owned websites, source files, database dumps, and restore metadata.')
      : t('Backups include website source files and a database SQL export.'),
    restore: t('Put accounts back from their backups: pick where the backups are, tick the accounts, press Restore.'),
    schedule: t('Full user backups on a timetable, kept here or sent to a destination.'),
    destination: t('Where off-server backup copies are sent. SFTP, or any S3-compatible object storage.'),
    logs: t('Recent queued, running, completed, and failed backup tasks.'),
  };

  const setSchedule = (key) => (value) => setNewBackupSchedule((prev) => ({ ...prev, [key]: value }));
  const scheduleReady = newBackupSchedule.all_users || (newBackupSchedule.user_ids || []).length > 0;
  async function submitSchedule(event) {
    event.preventDefault();
    if (await createBackupSchedule()) setScheduleSheet(false);
  }
  const setTarget = (key) => (value) => setNewSftpTarget((prev) => ({ ...prev, [key]: value }));
  const targetReady = !!newSftpTarget.name && (newSftpTarget.kind === 's3'
    ? (newSftpTarget.s3_bucket && newSftpTarget.s3_access_key && newSftpTarget.s3_secret_key)
    : (newSftpTarget.host && newSftpTarget.username && (newSftpTarget.password || newSftpTarget.private_key)));
  async function submitTarget(event) {
    event.preventDefault();
    if (targetReady && await createSftpTarget()) setTargetSheet(false);
  }

  function backupRows(files, { onDownload, onRestore, restoreLabel, onDelete }) {
    return <div className="backup-list">
      {files.map((file) => <div className="backup-item" key={file}>
        <span>{file.split('/').pop()}</span>
        <div className="actions bk-row-actions">
          <button className="secondary-light" disabled={busy} onClick={() => onDownload(file)}><Download size={14} aria-hidden="true"/> {t('Download')}</button>
          <button className="secondary-light" disabled={busy} onClick={() => onRestore(file)}><RotateCcw size={14} aria-hidden="true"/> {restoreLabel}</button>
          <ActionMenu label={t('More actions for {name}', { name: file.split('/').pop() })} items={[
            { label: t('Delete'), icon: Trash2, danger: true, disabled: busy, onClick: () => onDelete(file) },
          ]}/>
        </div>
      </div>)}
    </div>;
  }

  return <>
    <PageHeader subtitle={subtitles[active]} onRefresh={refreshers[active] ? refresh : undefined} refreshing={refreshing} refreshDisabled={busy}
      actions={active === 'schedule' ? <button type="button" onClick={() => setScheduleSheet(true)}><Plus size={15} aria-hidden="true"/> {t('Add schedule')}</button>
        : active === 'destination' ? <button type="button" onClick={() => setTargetSheet(true)}><Plus size={15} aria-hidden="true"/> {t('Add destination')}</button>
          : null}/>
    <section className="section backups-page">
      <Tabs label={t('Backup sections')} value={active} onChange={setBackupTab} tabs={tabs}/>
      {active !== 'logs' && liveJobs.length > 0 && <div className="backup-job-list">
        {liveJobs.map((job) => <div className={`backup-job ${job.status}`} key={job.job_id}>
          <Clock size={14} aria-hidden="true"/>
          <span><strong>{jobTitle(job)}</strong><small>{jobDetail(job)}</small><JobProgress job={job}/></span>
          <span className="badge">{jobState(job)}</span>
        </div>)}
      </div>}

      {active === 'website' && <div className="backup-tab-panel">
        {isAdmin && <div className="segmented-control bk-scope" role="group" aria-label={t('What to back up')}>
          {[['website', t('A website'), Globe], ['user', t('A whole account'), Users]].map(([id, label, Icon]) => <button key={id} type="button"
            aria-pressed={scope === id} className={scope === id ? 'active' : ''} onClick={() => setBackupTab(id)}>
            <Icon size={14} aria-hidden="true"/> {label}</button>)}
        </div>}
        {scope === 'website' && <>
          <div className="bk-form bk-site-form">
            <Field label={t('Website')}><WebsiteSelect/></Field>
            <div className="bk-actions">
              <button disabled={!selectedWebsiteId || busy} onClick={confirmSiteBackup}><Plus size={14} aria-hidden="true"/> {t('Create backup')}</button>
              <label className="upload-button secondary-light">
                <Upload size={14} aria-hidden="true"/> {t('Upload backup')}
                <input type="file" accept=".tar.gz,application/gzip" onChange={(e) => { uploadBackup(e.target.files?.[0]); e.target.value = ''; }}/>
              </label>
            </div>
          </div>
          {backups.length === 0 && selectedWebsiteId && <EmptyState compact icon={Archive} message={t('No backups found for this website.')}/>}
          {backupRows(backups, { onDownload: downloadBackup, onRestore: restoreBackup, restoreLabel: t('Restore'), onDelete: deleteBackup })}
        </>}
        {isAdmin && scope === 'user' && <>
          <div className="bk-form bk-user-form">
            <Field label={t('User')}>
              <select value={selectedBackupUserId} onChange={(e) => setSelectedBackupUserId(e.target.value)}>
                <option value="">{t('Select user')}</option>
                {users.map((user) => <option key={user.id} value={user.id}>{user.username}</option>)}
              </select>
            </Field>
            <Field label={t('Destination')} hint={t('A copy goes there; the backup stays on this server too.')}>
              <select value={selectedSftpTargetId} onChange={(e) => setSelectedSftpTargetId(e.target.value)}>
                <option value="">{t('Local only')}</option>
                {sftpTargets.map((target) => <option key={target.id} value={target.id}>{target.name}</option>)}
              </select>
            </Field>
            <div className="bk-actions">
              <button disabled={!selectedBackupUserId || busy} onClick={confirmUserBackup}><Archive size={14} aria-hidden="true"/> {t('Create backup')}</button>
            </div>
          </div>
          {selectedBackupUserId && userBackups.length === 0 && <EmptyState compact icon={Archive} message={t('No user backups found.')}/>}
          {backupRows(userBackups, { onDownload: downloadUserBackup, onRestore: restoreUserBackup, restoreLabel: t('Restore user'), onDelete: deleteUserBackup })}
        </>}
      </div>}

      {isAdmin && active === 'restore' && renderRestoreWizard()}

      {isAdmin && active === 'schedule' && <div className="backup-tab-panel">
        {backupSchedules.length === 0 && <EmptyState compact icon={Clock} message={t('No backup schedules yet.')}
          action={{ label: t('Add schedule'), icon: Plus, onClick: () => setScheduleSheet(true) }}/>}
        {backupSchedules.length > 0 && <div className="backup-list">
          {backupSchedules.map((item) => {
            // A run started from this row reports on this row.
            const live = liveJobs.find((job) => job.schedule_id === item.id);
            return <div className="backup-item bk-schedule-row" key={item.id}>
              <span>
                {scheduleUserLabel(item)} · {cronScheduleLabel(item.schedule)} · {targetName(item.target_id)}
                {live
                  ? <><small>{live.message || t('Running')}</small><JobProgress job={live}/></>
                  : <small>
                    {item.last_status === 'ok' && <span className="badge ok">{t('OK')}</span>}
                    {item.last_status === 'error' && <span className="badge bad">{t('Failed')}</span>}
                    {' '}{item.last_message || t('Not run yet')}
                  </small>}
              </span>
              <div className="actions bk-row-actions">
                <button className="secondary-light" disabled={busy || !!live} onClick={() => runBackupScheduleNow(item, scheduleUserLabel(item))}><Play size={14} aria-hidden="true"/> {t('Run now')}</button>
                <ActionMenu label={t('More actions for {name}', { name: scheduleUserLabel(item) })} items={[
                  { label: t('Delete schedule'), icon: Trash2, danger: true, disabled: busy, onClick: () => deleteBackupSchedule(item.id) },
                ]}/>
              </div>
            </div>;
          })}
        </div>}
        <p className="hint">{t('A daily schedule rotates through seven files named for the day — {example} and so on — so you keep a week and the eighth day overwrites the first. With a destination, that week is kept there: each archive is removed from this server once it has uploaded, and stays here only if the upload fails.', { example: <code>username-monday.tar.gz</code> })}</p>
      </div>}

      {isAdmin && active === 'destination' && <div className="backup-tab-panel">
        {sftpTargets.length === 0 && <EmptyState compact icon={Network} message={t('No backup destinations found.')}
          action={{ label: t('Add destination'), icon: Plus, onClick: () => setTargetSheet(true) }}/>}
        {sftpTargets.length > 0 && <div className="backup-list">
          {sftpTargets.map((target) => <div className="backup-item" key={target.id}>
            <span>
              <strong>{target.name}</strong> <span className="badge">{target.kind === 's3' ? 'S3' : 'SFTP'}</span>
              <small>{target.kind === 's3'
                ? `${target.s3_bucket}/${String(target.remote_path || '').replace(/^\/+/, '')}${target.s3_endpoint ? ` @ ${target.s3_endpoint}` : ''}`
                : `${target.username}@${target.host}:${target.remote_path}`}</small>
            </span>
            <div className="actions bk-row-actions">
              {target.kind === 's3' && <button className="secondary-light" disabled={busy} onClick={() => loadTargetObjects(target.id)}>
                <FolderOpen size={14} aria-hidden="true"/> {targetObjects.id === target.id ? t('Hide files') : t('Files')}</button>}
              <ActionMenu label={t('More actions for {name}', { name: target.name })} items={[
                { label: t('Test'), icon: Zap, hidden: target.kind !== 's3', disabled: busy, onClick: () => testBackupTarget(target.id) },
                'separator',
                { label: t('Delete destination'), icon: Trash2, danger: true, disabled: busy, onClick: () => deleteSftpTarget(target.id) },
              ]}/>
            </div>
          </div>)}
        </div>}
        {targetObjects.id && <div className="backup-list target-objects">
          {targetObjects.items.length === 0
            ? <p className="hint">{t('Nothing in {bucket} under this prefix yet.', { bucket: targetObjects.bucket })}</p>
            : targetObjects.items.map((item) => <div className="backup-item" key={item.key}>
              <span>{item.name} <small>{formatBytes(item.size)} · {item.modified.slice(0, 19).replace('T', ' ')}</small></span>
              <ActionMenu label={t('More actions for {name}', { name: item.name })} items={[
                { label: t('Delete from the bucket'), icon: Trash2, danger: true, disabled: busy, onClick: () => deleteTargetObject(targetObjects.id, item.key) },
              ]}/>
            </div>)}
        </div>}
      </div>}

      {active === 'logs' && <div className="backup-tab-panel backup-logs-panel">
        {backupJobs.length === 0 && <EmptyState compact icon={FileText} message={t('No backup logs found.')}/>}
        {backupJobs.length > 0 && <div className="backup-job-list">
          {backupJobs.map((job) => <div className={`backup-job ${job.status}`} key={job.job_id}>
            <Clock size={14} aria-hidden="true"/>
            <span>
              <strong>{jobTitle(job)}</strong>
              <small>{jobDetail(job)}</small>
              <JobProgress job={job}/>
              <small className="backup-job-time">{jobTime(job)}</small>
            </span>
            <span className={job.status === 'done' ? 'badge ok' : job.status === 'error' ? 'badge bad' : 'badge'}>{jobState(job)}</span>
          </div>)}
        </div>}
      </div>}
    </section>

    <Drawer open={scheduleSheet} onClose={() => setScheduleSheet(false)} title={t('Add schedule')} subtitle={t('Full user backups')}
      footer={<>
        <button type="button" className="secondary-light" onClick={() => setScheduleSheet(false)}>{t('Cancel')}</button>
        <button type="submit" form="bk-schedule-form" disabled={!scheduleReady || busy}><Clock size={14} aria-hidden="true"/> {t('Add schedule')}</button>
      </>}>
      <form id="bk-schedule-form" onSubmit={submitSchedule}>
        <div className="bk-users" role="group" aria-label={t('Users')}>
          <span className="sn-field-label"><span className="sn-field-label-text">{t('Users')}</span></span>
          <Checkbox checked={!!newBackupSchedule.all_users} onChange={setSchedule('all_users')} label={t('All users')}/>
          {!newBackupSchedule.all_users && <div className="bk-user-list">
            {users.map((user) => {
              const picked = (newBackupSchedule.user_ids || []).includes(String(user.id));
              return <Checkbox key={user.id} checked={picked} label={user.username}
                onChange={(on) => setSchedule('user_ids')(on
                  ? [...(newBackupSchedule.user_ids || []), String(user.id)]
                  : (newBackupSchedule.user_ids || []).filter((id) => id !== String(user.id)))}/>;
            })}
          </div>}
        </div>
        <Field label={t('Schedule')}>{renderSchedulePicker('backup', newBackupSchedule.schedule, setSchedule('schedule'), 'backup-schedule-input')}</Field>
        <Field label={t('Destination')}>
          <select value={newBackupSchedule.target_id || ''} onChange={(e) => setSchedule('target_id')(e.target.value)}>
            <option value="">{t('Local only')}</option>
            {sftpTargets.map((target) => <option key={target.id} value={target.id}>{target.name}</option>)}
          </select>
        </Field>
      </form>
    </Drawer>

    <Drawer open={targetSheet} onClose={() => setTargetSheet(false)} title={t('Add destination')}
      subtitle={t('Where off-server backup copies are sent. SFTP, or any S3-compatible object storage.')}
      footer={<>
        <button type="button" className="secondary-light" onClick={() => setTargetSheet(false)}>{t('Cancel')}</button>
        <button type="submit" form="bk-target-form" disabled={!targetReady || busy}><Plus size={14} aria-hidden="true"/> {t('Save destination')}</button>
      </>}>
      <form id="bk-target-form" onSubmit={submitTarget}>
        <div className="sn-drawer-grid">
          <Field label={t('Destination name')} required><input value={newSftpTarget.name} onChange={(e) => setTarget('name')(e.target.value)}/></Field>
          <Field label={t('Kind')}>
            <select value={newSftpTarget.kind} onChange={(e) => setTarget('kind')(e.target.value)}>
              <option value="sftp">SFTP</option>
              <option value="s3">{t('S3 compatible')}</option>
            </select>
          </Field>
        </div>
        {newSftpTarget.kind === 'sftp' ? <>
          <div className="sn-drawer-grid">
            <Field label={t('Host')} required><input value={newSftpTarget.host} onChange={(e) => setTarget('host')(e.target.value)} autoCapitalize="none" spellCheck={false}/></Field>
            <Field label={t('Port')}><input value={newSftpTarget.port} onChange={(e) => setTarget('port')(e.target.value)} placeholder="22" inputMode="numeric"/></Field>
            <Field label={t('Username')} required><input value={newSftpTarget.username} onChange={(e) => setTarget('username')(e.target.value)} autoComplete="off"/></Field>
            <Field label={t('Password')} hint={t('Or a private key below.')}><PasswordField value={newSftpTarget.password} onChange={setTarget('password')} autoComplete="off"/></Field>
          </div>
          <Field label={t('Folder on the server')}><input value={newSftpTarget.remote_path} onChange={(e) => setTarget('remote_path')(e.target.value)} placeholder="/backups/opanel"/></Field>
          <Field label={t('Private key')} optional><textarea value={newSftpTarget.private_key} onChange={(e) => setTarget('private_key')(e.target.value)} rows={4} spellCheck={false}/></Field>
        </> : <>
          <div className="sn-drawer-grid">
            <Field label={t('Bucket')} required><input value={newSftpTarget.s3_bucket} onChange={(e) => setTarget('s3_bucket')(e.target.value)}/></Field>
            <Field label={t('Region')}><input value={newSftpTarget.s3_region} onChange={(e) => setTarget('s3_region')(e.target.value)} placeholder="us-east-1"/></Field>
          </div>
          <Field label={t('Endpoint')} hint={t('Leave empty for AWS.')}><input value={newSftpTarget.s3_endpoint} onChange={(e) => setTarget('s3_endpoint')(e.target.value)} autoCapitalize="none" spellCheck={false}/></Field>
          <div className="sn-drawer-grid">
            <Field label={t('Access key')} required><input value={newSftpTarget.s3_access_key} onChange={(e) => setTarget('s3_access_key')(e.target.value)} autoComplete="off"/></Field>
            <Field label={t('Secret key')} required><PasswordField value={newSftpTarget.s3_secret_key} onChange={setTarget('s3_secret_key')} autoComplete="off"/></Field>
          </div>
          <Field label={t('Key prefix')}><input value={newSftpTarget.remote_path} onChange={(e) => setTarget('remote_path')(e.target.value)} placeholder="opanel/backups"/></Field>
          <Checkbox checked={!!newSftpTarget.s3_use_path_style} onChange={setTarget('s3_use_path_style')} label={t('Path-style addressing (MinIO, Ceph)')}/>
        </>}
      </form>
    </Drawer>
  </>;
}
