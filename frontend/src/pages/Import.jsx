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
  fastpanel: {
    name: 'FastPanel', flag: 'fastpanelImport',
    subtitle: msg('Accounts from a FastPanel server, straight off it over SSH: FastPanel keeps files and databases in separate backups, so the panel packs each account itself.'),
  },
  // Core, no addon flag: an OPanel/BPanel archive restores natively here. Its
  // restore history is the panel's own native kind.
  panel: {
    name: 'OPanel/BPanel', historyKind: 'opanel',
    subtitle: msg('Accounts from another OPanel or BPanel server, straight off it over SSH as root: its own panel backs up each account you pick and this one restores it directly - an OPanel account into BPanel and the other way round.'),
  },
};

export default function ImportPage({ panel }) {
  const t = useT();
  const { backupJobs, enterRestoreMode, isAdmin, loadBackupJobs, navCtx, navigateToPage, renderRestoreWizard, restoreMode } = usePanel();
  const meta = PANELS[panel];
  // A core page (no addon flag) is always on; an addon page follows its flag.
  const on = meta.flag ? !!navCtx?.[meta.flag] : true;
  useEffect(() => { if (on) enterRestoreMode(panel); }, [panel, on]);
  useEffect(() => { if (on) loadBackupJobs?.(); }, [on]);

  const header = <PageHeader title={t('{name} Import', { name: meta.name })} subtitle={t(meta.subtitle)}/>;
  if (!isAdmin) return <section className="section">{header}<EmptyState message={t('Only an administrator can import accounts.')}/></section>;
  if (!navCtx?.addonsLoaded) return <section className="section">{header}<p className="hint" aria-busy="true">{t('Checking…')}</p></section>;
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
    <RestoreHistory kind={meta.historyKind || panel} running={running}/>
  </section>;
}
