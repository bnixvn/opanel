import { useEffect } from 'react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import PageHeader from '../components/PageHeader.jsx';
import EmptyState from '../components/EmptyState.jsx';
import RestoreHistory from '../components/RestoreHistory.jsx';

const msg = (text) => text;

// A migration addon's page (operator, 2026-10-18: DirectAdmin Import out of
// Backup & Restore, "giống Cpanel Import, sẽ có thêm phần kết nối SSH và
// import"): the panel signs in to the old server as root and brings the
// accounts picked over, or restores that panel's backups put here. The
// restore wizard, on this addon's kind of archive only, and its history.
const PANELS = {
  directadmin: {
    name: 'DirectAdmin', flag: 'directadminImport',
    subtitle: msg('Accounts from a DirectAdmin server: straight off it over SSH, or from its user backups.'),
  },
  cpanel: {
    name: 'cPanel', flag: 'cpanelImport',
    subtitle: msg('Accounts from a cPanel server: straight off it over SSH (pkgacct), or from its cpmove backups.'),
  },
};

export default function ImportPage({ panel }) {
  const t = useT();
  const { backupJobs, enterRestoreMode, isAdmin, loadBackupJobs, navCtx, navigateToPage, renderRestoreWizard, restoreMode } = usePanel();
  const meta = PANELS[panel];
  const on = !!navCtx?.[meta.flag];
  useEffect(() => { if (on) enterRestoreMode(panel); }, [panel, on]);
  useEffect(() => { if (on) loadBackupJobs?.(); }, [on]);

  const header = <PageHeader title={t('{name} Import', { name: meta.name })} subtitle={t(meta.subtitle)}/>;
  if (!isAdmin) return <section className="section">{header}<EmptyState message={t('Only an administrator can import accounts.')}/></section>;
  if (!on) {
    return <section className="section">
      {header}
      <EmptyState message={t('The {name} Import addon is off.', { name: meta.name })}
        action={<button type="button" onClick={() => navigateToPage('addons')}>{t('Open Addons')}</button>}/>
    </section>;
  }
  const running = (backupJobs || []).some((job) => job.kind === 'user_restore_batch' && ['queued', 'running'].includes(job.status));
  return <section className="section import-page">
    {header}
    {restoreMode === panel && renderRestoreWizard()}
    <RestoreHistory kind={panel} running={running}/>
  </section>;
}
