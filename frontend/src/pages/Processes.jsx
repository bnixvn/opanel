import { useMemo, useState } from 'react';
import { ArrowDown, ArrowUp } from 'lucide-react';
import { usePanel } from '../lib/panel-context.jsx';
import { useT } from '../lib/t.js';
import PageHeader from '../components/PageHeader.jsx';
import Switch from '../components/Switch.jsx';
import { Checkbox } from '../components/Form.jsx';
import './Processes.css';

const msg = (text) => text;

// top's memory columns: KiB, or a number with k, m, g, t, p after it.
function kib(text) {
  const m = /^([\d.]+)([kmgtp]?)$/i.exec(String(text || '').trim());
  if (!m) return 0;
  const power = { '': 0, k: 0, m: 1, g: 2, t: 3, p: 4 }[m[2].toLowerCase()];
  return Number(m[1]) * 1024 ** power;
}

// TIME+ ("12:34.56", "1234:05") in seconds.
function seconds(text) {
  const [min, sec] = String(text || '').split(':');
  return Number(min || 0) * 60 + Number(sec || 0);
}

// The rows of top's process list: PID USER PR NI VIRT RES SHR S %CPU %MEM
// TIME+ COMMAND, the command being everything after the eleventh column.
export function parseTop(output) {
  const lines = String(output || '').replace(/\s+$/, '').split('\n');
  const headAt = lines.findIndex((line) => /^\s*PID\s+USER\b/.test(line));
  if (headAt < 0) return { summary: lines, rows: [] };
  const rows = [];
  for (const line of lines.slice(headAt + 1)) {
    const m = /^\s*(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S)\s+([\d.,]+)\s+([\d.,]+)\s+(\S+)\s+(.*)$/.exec(line);
    if (!m) continue;
    const [, pid, user, , , virt, res, , state, cpu, mem, time, command] = m;
    rows.push({
      pid: Number(pid), user, virt, res, resKib: kib(res), state,
      cpu: Number(cpu.replace(',', '.')), mem: Number(mem.replace(',', '.')),
      time, timeSecs: seconds(time), command,
      // A kernel thread: no memory of its own, its name in brackets.
      kernel: kib(virt) === 0 && /^\[.*\]$/.test(command.trim()),
    });
  }
  return { summary: lines.slice(0, headAt).filter((line) => line.trim()), rows };
}

const COLUMNS = [
  ['pid', 'PID', (row) => row.pid],
  ['user', msg('Account'), (row) => row.user],
  ['cpu', '%CPU', (row) => row.cpu],
  ['mem', '%MEM', (row) => row.mem],
  ['res', msg('Memory'), (row) => row.resKib],
  ['time', msg('CPU time'), (row) => row.timeSecs],
  ['state', msg('State'), (row) => row.state],
  ['command', msg('Command'), (row) => row.command],
];
const NUMERIC = new Set(['pid', 'cpu', 'mem', 'res', 'time']);

// top's state letters.
const STATES = { R: msg('Running'), S: msg('Sleeping'), D: msg('Waiting for I/O'), I: msg('Idle'), T: msg('Stopped'), t: msg('Stopped'), Z: msg('Zombie') };

// The process monitor, as SNPanel's (pages/Processes.jsx there): what the
// server is running, read from `top -c` every 3 seconds while the page is
// open and not paused (App reads it) - as a table that sorts by any column
// and filters by account and by text, with top's own summary above it.
// Kernel threads are left out unless asked for; "As top shows it" gives
// the text as top printed it. Administrators only.
export default function ProcessesPage() {
  const t = useT();
  const { currentUser, formatBytes, processFilter, processPaused, processTop, setProcessFilter, setProcessPaused } = usePanel();
  const [account, setAccount] = useState('');
  const [kernel, setKernel] = useState(false);
  const [raw, setRaw] = useState(false);
  const [sort, setSort] = useState({ key: 'cpu', desc: true });

  const output = currentUser?.demo ? '' : processTop?.output || '';
  const { summary, rows } = useMemo(() => parseTop(output), [output]);
  const accounts = useMemo(() => {
    const counts = new Map();
    rows.forEach((row) => { if (kernel || !row.kernel) counts.set(row.user, (counts.get(row.user) || 0) + 1); });
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [rows, kernel]);
  const needle = processFilter.trim().toLowerCase();
  const shown = useMemo(() => {
    const value = COLUMNS.find(([key]) => key === sort.key)[2];
    const list = rows.filter((row) => (kernel || !row.kernel)
      && (!account || row.user === account)
      && (!needle || row.command.toLowerCase().includes(needle) || String(row.pid) === needle));
    return list.sort((a, b) => {
      const x = value(a);
      const y = value(b);
      const order = typeof x === 'number' ? x - y : String(x).localeCompare(String(y));
      return (sort.desc ? -order : order) || a.pid - b.pid;
    });
  }, [rows, kernel, account, needle, sort]);
  const time = processTop?.time ? new Date(processTop.time * 1000).toLocaleTimeString() : '';
  const fixed1 = (value) => value.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });

  // The raw text keeps the summary and the column heads, and the process
  // lines that contain what is typed.
  const lines = output.replace(/\s+$/, '').split('\n');
  const headAt = lines.findIndex((line) => /^\s*PID\s+USER\b/.test(line));
  const rawShown = headAt < 0 || !needle
    ? lines
    : [...lines.slice(0, headAt + 1), ...lines.slice(headAt + 1).filter((line) => line.toLowerCase().includes(needle))];

  function sortBy(key) {
    setSort((prev) => (prev.key === key ? { key, desc: !prev.desc } : { key, desc: NUMERIC.has(key) }));
  }

  return <>
    <PageHeader subtitle={t('What the server is running, read from top -c every 3 seconds. Click a column to sort by it.')}
      actions={<Switch checked={!processPaused} onChange={(on) => setProcessPaused(!on)}>{t('Live updates')}</Switch>}/>
    <section className="section process-page">
      <div className="process-toolbar">
        <label className="process-field"><span>{t('Account')}</span>
          <select value={account} onChange={(event) => setAccount(event.target.value)}>
            <option value="">{t('All accounts')}</option>
            {accounts.map(([user, count]) => <option key={user} value={user}>{user} ({count})</option>)}
          </select>
        </label>
        <label className="process-field process-field-grow"><span>{t('Filter processes')}</span>
          <input type="search" value={processFilter} onChange={(event) => setProcessFilter(event.target.value)} placeholder={t('A command or a PID')}/>
        </label>
        <Checkbox checked={kernel} onChange={setKernel} label={t('Kernel threads')}/>
        <Checkbox checked={raw} onChange={setRaw} label={t('As top shows it')}/>
        <span className="process-time" aria-live="off">{processPaused ? t('Paused') : time}</span>
      </div>

      {currentUser?.demo && <p className="hint">{t('This is a demo: the process list is not shown.')}</p>}
      {!currentUser?.demo && !output && <p className="hint">{t('Reading processes...')}</p>}

      {output && raw && <div className="process-terminal-wrap">
        <div className="process-terminal-head"><span>top -c</span><small>{processPaused ? t('Paused') : time}</small></div>
        <pre className="process-terminal" aria-live="off">{rawShown.join('\n')}</pre>
      </div>}

      {output && !raw && <>
        {summary.length > 0 && <pre className="process-summary" aria-label={t('Summary')}>{summary.join('\n')}</pre>}
        <p className="hint process-count">{t('{shown} of {total} processes', { shown: shown.length, total: rows.length })}</p>
        <div className="data-table-wrap process-table-wrap">
          <table className="data-table process-table">
            <thead><tr>
              {COLUMNS.map(([key, label]) => <th key={key} scope="col" className={`process-col-${key}`}
                aria-sort={sort.key === key ? (sort.desc ? 'descending' : 'ascending') : 'none'}>
                <button type="button" className="process-sort" onClick={() => sortBy(key)}>
                  {['PID', '%CPU', '%MEM'].includes(label) ? label : t(label)}
                  {sort.key === key && (sort.desc ? <ArrowDown size={12} aria-hidden="true"/> : <ArrowUp size={12} aria-hidden="true"/>)}
                </button>
              </th>)}
            </tr></thead>
            <tbody>
              {shown.map((row) => <tr key={row.pid}>
                <td className="process-col-pid">{row.pid}</td>
                <td className="process-col-user"><button type="button" className="process-user" onClick={() => setAccount(account === row.user ? '' : row.user)}
                  title={account === row.user ? t('Show every account') : t('Only {account}', { account: row.user })}>{row.user}</button></td>
                <td className="process-col-cpu">{fixed1(row.cpu)}</td>
                <td className="process-col-mem">{fixed1(row.mem)}</td>
                <td className="process-col-res" title={row.res}>{formatBytes(row.resKib * 1024)}</td>
                <td className="process-col-time">{row.time}</td>
                <td className="process-col-state" title={STATES[row.state] ? t(STATES[row.state]) : row.state}>{row.state}</td>
                <td className="process-col-command"><code title={row.command}>{row.command}</code></td>
              </tr>)}
            </tbody>
          </table>
          {shown.length === 0 && <p className="empty-note process-empty">{t('No process matches.')}</p>}
        </div>
      </>}
    </section>
  </>;
}
