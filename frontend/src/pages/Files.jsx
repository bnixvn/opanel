import { useMemo, useState } from 'react';
import {
  Archive, ArchiveRestore, ArrowDown, ArrowUp, Clock, Copy, CornerLeftUp, Download, FilePen, FilePlus, FileText, FolderOpen, FolderPlus,
  Lock, MoveRight, PencilLine, Trash2, Upload, X,
} from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import { Field } from '../components/Form.jsx';
import PageHeader from '../components/PageHeader.jsx';
import ActionMenu from '../components/ActionMenu.jsx';
import './Files.css';

// Folders first, whichever way the list is turned - as the server lists them -
// then by the column chosen, and by name where that column ties. A folder has
// no size worth sorting by, so folders keep their names' order under Size.
function compareFiles(a, b, key, dir) {
  if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
  const x = String(a.name).toLowerCase();
  const y = String(b.name).toLowerCase();
  const byName = x < y ? -1 : x > y ? 1 : 0;
  const factor = dir === 'desc' ? -1 : 1;
  if (key === 'modified') return ((Number(a.modified) || 0) - (Number(b.modified) || 0)) * factor || byName;
  if (key === 'size') return (a.is_dir ? 0 : ((Number(a.size) || 0) - (Number(b.size) || 0)) * factor) || byName;
  return byName * factor;
}

// The way a column sorts first: names A to Z, the newest and the largest on top.
const FIRST_DIR = { name: 'asc', modified: 'desc', size: 'desc' };

// The file manager, as SNPanel's (pages/Files.jsx there): what can be done
// in the folder is the toolbar; what can be done with a selection is a bar
// under it, there only while something is ticked; each row's own actions
// are one "…" menu. The columns sort.
export default function FilesPage() {
  const t = useT();
  const {
    WebsiteSelect, archiveSelectedFiles, clearFileSelection, copySelectedFiles, currentSite, currentUser, deleteSelectedFiles,
    dismissFileJob, downloadFile, extractArchive, fileBreadcrumbs, fileJobs, fileListPath, files, formatBytes, formatFileTime, isAdmin,
    isExtractableArchive, isTextEditable, listFiles, loading, makeFile, makeFileDirectory, moveSelectedFiles, openChmod, openFileEditorTab,
    parentFilePath, renameFileItem, selectedFilePaths, selectedWebsiteId, storageUsageText, toggleAllFiles, toggleFileSelection,
    uploadProgress, uploadSiteFile,
  } = usePanel();
  const [sort, setSort] = useState({ key: 'name', dir: 'asc' });
  const shownFiles = useMemo(() => [...files].sort((a, b) => compareFiles(a, b, sort.key, sort.dir)), [files, sort]);
  const sortBy = (key) => setSort((prev) => (prev.key === key
    ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
    : { key, dir: FIRST_DIR[key] }));
  const hasTarget = !!selectedWebsiteId;

  // A column heading that sorts the list, with the way it is sorted. A
  // function rather than a component: one made anew each render would be a
  // new button each time, and keyboard focus would not survive a click.
  function sortHeading(column, label, className = '') {
    const active = sort.key === column;
    const Arrow = sort.dir === 'asc' ? ArrowUp : ArrowDown;
    return <button type="button" className={`file-sort ${className} ${active ? 'active' : ''}`} onClick={() => sortBy(column)}
      aria-label={active
        ? t('{column}: sorted {order}. Sort the other way', { column: label, order: sort.dir === 'asc' ? t('ascending') : t('descending') })
        : t('Sort by {column}', { column: label })}>
      {label}{active && <Arrow size={12} aria-hidden="true"/>}
    </button>;
  }

  const allSelected = files.length > 0 && selectedFilePaths.length === files.length;
  const selectedItems = files.filter((item) => selectedFilePaths.includes(item.path));
  const selectedArchive = selectedItems.length === 1 && isExtractableArchive(selectedItems[0]) ? selectedItems[0] : null;
  const visibleFileJobs = fileJobs.filter((job) => String(job.website_id) === String(selectedWebsiteId) && job.status !== 'done').slice(0, 4);
  const uploadPercent = uploadProgress?.total ? Math.round((uploadProgress.loaded / uploadProgress.total) * 100) : 0;

  return <>
    <PageHeader onRefresh={() => listFiles(fileListPath)} refreshDisabled={!hasTarget || !!loading}/>
    <section className="section">
      <div className="file-manager">
        <div className="file-panel">
          <div className="file-controls">
            <Field label={t('Website')}><WebsiteSelect/></Field>
            {currentSite && <div className="file-meta">
              <span>{t('Website: {domain}', { domain: <strong>{currentSite.domain}</strong> })}</span>
              <span>{t('Root: {path}', { path: <strong>{currentSite.root_path}{fileListPath ? `/${fileListPath}` : ''}</strong> })}</span>
              {currentUser && !isAdmin && <span>{t('Storage: {usage}', { usage: <strong>{storageUsageText(currentUser)}</strong> })}</span>}
            </div>}
            <div className="path-pill breadcrumb-line">
              <button className="crumb" disabled={!hasTarget || fileListPath === ''} onClick={() => listFiles('')}>{t('root')}</button>
              {fileBreadcrumbs(fileListPath).map((crumb) => <button className="crumb" key={crumb.path} onClick={() => listFiles(crumb.path)}>{crumb.label}</button>)}
            </div>
            <div className="file-toolbar fm-toolbar">
              <button className="secondary-light" disabled={!hasTarget || fileListPath === '' || !!loading} onClick={() => listFiles(parentFilePath(fileListPath))}><CornerLeftUp size={14} aria-hidden="true"/> {t('Up')}</button>
              <button className="secondary-light" disabled={!hasTarget || !!loading} onClick={makeFileDirectory}><FolderPlus size={14} aria-hidden="true"/> {t('New folder')}</button>
              <button className="secondary-light" disabled={!hasTarget || !!loading} onClick={makeFile}><FilePlus size={14} aria-hidden="true"/> {t('New file')}</button>
              <label className={`upload-button ${(!hasTarget || !!loading || !!uploadProgress) ? 'disabled' : ''}`}>
                <Upload size={14} aria-hidden="true"/> {t('Upload')}
                <input type="file" disabled={!hasTarget || !!loading || !!uploadProgress} onChange={(e) => { uploadSiteFile(e.target.files?.[0]); e.target.value = ''; }}/>
              </label>
            </div>
            {selectedFilePaths.length > 0 && <div className="fm-selection" role="region" aria-label={t('Selected items')}>
              <strong>{t('{count} selected', { count: selectedFilePaths.length })}</strong>
              <div className="fm-selection-actions">
                <button className="secondary-light" disabled={!!loading} onClick={() => copySelectedFiles()}><Copy size={14} aria-hidden="true"/> {t('Copy')}</button>
                <button className="secondary-light" disabled={!!loading} onClick={() => moveSelectedFiles()}><MoveRight size={14} aria-hidden="true"/> {t('Move')}</button>
                <ActionMenu text={t('Archive')} icon={Archive} label={t('Compress the selection')} disabled={!!loading} items={[
                  { label: t('Compress as {format}', { format: '.zip' }), icon: Archive, onClick: () => archiveSelectedFiles('zip') },
                  { label: t('Compress as {format}', { format: '.tar.gz' }), icon: Archive, onClick: () => archiveSelectedFiles('tar.gz') },
                ]}/>
                {selectedArchive && <button className="secondary-light" disabled={!!loading} onClick={() => extractArchive(selectedArchive)}><ArchiveRestore size={14} aria-hidden="true"/> {t('Extract')}</button>}
                {selectedItems.length === 1 && <button className="secondary-light" disabled={!!loading} onClick={() => openChmod(selectedItems[0])}><Lock size={14} aria-hidden="true"/> {t('Permissions')}</button>}
                <button className="danger" disabled={!!loading} onClick={() => deleteSelectedFiles()}><Trash2 size={14} aria-hidden="true"/> {t('Delete')}</button>
              </div>
              <button type="button" className="secondary-light fm-selection-clear" onClick={clearFileSelection}><X size={14} aria-hidden="true"/> {t('Clear selection')}</button>
            </div>}
            {uploadProgress && <div className="file-upload-progress" role="status">
              <Upload size={14} aria-hidden="true"/>
              <span>{t('Uploading {name}: {done} of {total} ({percent}%)', { name: uploadProgress.name, done: formatBytes(uploadProgress.loaded), total: formatBytes(uploadProgress.total), percent: uploadPercent })}</span>
              <progress max={100} value={uploadPercent}/>
            </div>}
            {visibleFileJobs.length > 0 && <div className="file-job-list">
              {visibleFileJobs.map((job) => <div className={`file-job ${job.status}`} key={job.job_id}>
                <Clock size={14} aria-hidden="true"/>
                <span><strong>{job.archive_path?.split('/').pop() || t('Archive')}</strong> {job.status === 'error' ? t('failed') : job.status}</span>
                {job.error && <small>{job.error}</small>}
                <button type="button" className="file-job-dismiss" onClick={() => dismissFileJob(job.job_id)} aria-label={t('Dismiss')}><X size={13} aria-hidden="true"/></button>
              </div>)}
            </div>}
          </div>
          <div className="file-list">
            <div className="file-list-header">
              <input type="checkbox" checked={allSelected} ref={(el) => { if (el) el.indeterminate = selectedFilePaths.length > 0 && !allSelected; }}
                onChange={toggleAllFiles} disabled={files.length === 0} aria-label={t('Select all')}/>
              <div className="file-col-name">
                {sortHeading('name', t('Name'))}
                <span className="file-count">{t('{count} item(s)', { count: files.length })}</span>
              </div>
              <span className="file-col-mode">{t('Permissions')}</span>
              {sortHeading('size', t('Size'), 'file-col-size')}
              {sortHeading('modified', t('Modified'), 'file-col-date')}
              <span className="file-col-actions" aria-hidden="true"></span>
            </div>
            {files.length === 0 && <div className="empty-box">{t('No files in this folder.')}</div>}
            {shownFiles.map((item) => <div className={`file-item ${selectedFilePaths.includes(item.path) ? 'selected' : ''}`} key={item.path}>
              <input type="checkbox" checked={selectedFilePaths.includes(item.path)} onChange={() => toggleFileSelection(item.path)} aria-label={t('Select {name}', { name: item.name })}/>
              <button className="file-name" onClick={() => item.is_dir ? listFiles(item.path) : (isTextEditable(item) ? openFileEditorTab(item.path) : downloadFile(item.path))}>
                {item.is_dir ? <FolderOpen size={16}/> : <FileText size={16}/>} <strong>{item.name}</strong>
              </button>
              <button className="file-mode" type="button" disabled={!!loading}
                title={t('Permissions {value} - click to change', { value: item.mode || '---' })}
                onClick={() => openChmod(item)}>{item.mode || '---'}</button>
              <span className="file-facts">
                <span className="file-size">{item.is_dir ? t('Folder') : formatBytes(item.size)}</span>
                <time className="file-date" dateTime={item.modified ? new Date(item.modified * 1000).toISOString() : undefined}
                  title={item.modified ? new Date(item.modified * 1000).toLocaleString() : undefined}>{formatFileTime(item.modified)}</time>
              </span>
              <div className="file-row-actions">
                <ActionMenu label={t('More actions for {name}', { name: item.name })} items={[
                  { label: t('Open'), icon: FolderOpen, hidden: !item.is_dir, onClick: () => listFiles(item.path) },
                  { label: t('Edit in a new tab'), icon: FilePen, hidden: item.is_dir || !isTextEditable(item), onClick: () => openFileEditorTab(item.path) },
                  { label: t('Download'), icon: Download, hidden: item.is_dir, disabled: !!loading, onClick: () => downloadFile(item.path) },
                  { label: t('Extract'), icon: ArchiveRestore, hidden: !isExtractableArchive(item), disabled: !!loading, onClick: () => extractArchive(item) },
                  'separator',
                  { label: t('Rename'), icon: PencilLine, disabled: !!loading, onClick: () => renameFileItem(item) },
                  { label: t('Copy'), icon: Copy, disabled: !!loading, onClick: () => copySelectedFiles([item.path]) },
                  { label: t('Move'), icon: MoveRight, disabled: !!loading, onClick: () => moveSelectedFiles([item.path]) },
                  { label: t('Permissions'), icon: Lock, disabled: !!loading, onClick: () => openChmod(item) },
                  'separator',
                  { label: t('Delete'), icon: Trash2, danger: true, disabled: !!loading, onClick: () => deleteSelectedFiles([item.path]) },
                ]}/>
              </div>
            </div>)}
          </div>
        </div>
      </div>
    </section>
  </>;
}
