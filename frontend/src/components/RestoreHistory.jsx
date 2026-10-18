import { useEffect, useState } from 'react';
import { RotateCcw } from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import { confirmDialog } from './Dialog.jsx';

const msg = (text) => text;
// The last restores, each archive with how it went (services/restore_jobs.py):
// done with what came back, failed with why, or interrupted by a restart -
// and those run again, from this server or a saved destination. Each page
// with the restore wizard shows its own kind (Backup & Restore the panel's,
// each migration addon its own).
const ITEM_STATES = { done: msg('Restored'), failed: msg('Failed'), interrupted: msg('Interrupted'), running: msg('Running'), pending: msg('Waiting') };
const JOB_BADGES = { done: 'badge ok', error: 'badge bad', interrupted: 'badge bad' };

export default function RestoreHistory({ running, kind }) {
  const t = useT();
  const { loading, loadBackupJobs, request, setNotice } = usePanel();
  const [jobs, setJobs] = useState(null);
  const [open, setOpen] = useState(null);
  async function load() {
    const data = await request(`/maintenance/restore/jobs${kind ? `?kind=${kind}` : ''}`, { silent: true });
    if (data) setJobs(data.jobs || []);
  }
  useEffect(() => { load(); }, [running, kind]);
  async function retry(job) {
    if (!(await confirmDialog({ title: t('Run the unfinished archives again?'), subject: job.source,
      message: t('{count} archive(s) that failed or were interrupted are restored again.', { count: job.failed }), confirmLabel: t('Run again') }))) return;
    const data = await request(`/maintenance/restore/jobs/${job.id}/retry`, { method: 'POST' }, t('Starting the restore...'));
    if (data) { setNotice(t('Restore started. Watch Backup logs for progress.')); loadBackupJobs(); load(); }
  }
  if (!jobs || jobs.length === 0) return null;
  const when = (stamp) => { const date = stamp ? new Date(stamp) : null; return date && !Number.isNaN(date.getTime()) ? date.toLocaleString() : ''; };
  return <div className="backup-tab-panel bk-restore-history">
    <h3>{t('Restore history')}</h3>
    <div className="backup-list">
      {jobs.map((job) => <div className="backup-item bk-restore-job" key={job.id}>
        <span>
          <strong>{when(job.created_at)}</strong> · {job.source}
          {' '}<span className={JOB_BADGES[job.status] || 'badge'}>{job.status === 'done' ? t('Done') : job.status === 'error' ? t('Failed') : job.status === 'interrupted' ? t('Interrupted') : t('Running')}</span>
          <small>{t('{done} restored, {failed} not', { done: job.done, failed: job.failed })}{job.retry_of ? ` · ${t('a retry of #{id}', { id: job.retry_of })}` : ''}</small>
          {open === job.id && <ul className="bk-restore-items">
            {job.items.map((item, index) => <li key={`${item.ref}-${index}`} className={`is-${item.status}`}>
              <code>{item.name}</code> <span className="badge">{t(ITEM_STATES[item.status] || item.status)}</span>
              {(item.note || item.error) && <small>{item.error || item.note}</small>}
            </li>)}
          </ul>}
        </span>
        <div className="actions bk-row-actions">
          <button type="button" className="secondary-light" aria-expanded={open === job.id} onClick={() => setOpen(open === job.id ? null : job.id)}>
            {open === job.id ? t('Hide details') : t('Details')}</button>
          {job.can_retry && <button type="button" disabled={!!loading || running} onClick={() => retry(job)}><RotateCcw size={14} aria-hidden="true"/> {t('Run again')}</button>}
        </div>
      </div>)}
    </div>
    <p className="hint">{t('A restore from a server typed into the wizard is run again from the wizard: its login is never stored.')}</p>
  </div>;
}
