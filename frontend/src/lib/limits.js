// An account's use against its limits, as SNPanel's dashboard shows it
// (lib/limits.jsx there): text for each figure and the share of the limit
// for its bar.
//
// `resources` is { cpu: { percent }, memory: { bytes, oom_kills },
// processes: { current } }; `limits`, when given, { cpu_percent, memory_mb,
// process_limit } with null for none.
export function usageFigures(resources, formatBytes, limits) {
  if (!resources) return null;
  const cpu = { ...(resources.cpu || {}) };
  const memory = { ...(resources.memory || {}) };
  const processes = { ...(resources.processes || {}) };
  if (limits) {
    cpu.limit_percent = limits.cpu_percent ?? null;
    processes.limit = limits.process_limit ?? null;
    memory.limit_bytes = limits.memory_mb != null ? limits.memory_mb * 1024 * 1024 : null;
    memory.percent = memory.limit_bytes ? Math.round((Number(memory.bytes) || 0) * 1000 / memory.limit_bytes) / 10 : null;
  }
  const share = (used, limit) => (limit ? Math.min(100, (Number(used) / Number(limit)) * 100) : null);
  return {
    cpu: {
      text: cpu.limit_percent != null ? `${cpu.percent ?? 0}% / ${cpu.limit_percent}%` : `${cpu.percent ?? 0}%`,
      percent: share(cpu.percent, cpu.limit_percent),
    },
    memory: {
      text: memory.limit_bytes != null ? `${formatBytes(memory.bytes)} / ${formatBytes(memory.limit_bytes)}` : formatBytes(memory.bytes),
      percent: memory.percent ?? null,
      oomKills: Number(memory.oom_kills) || 0,
    },
    processes: {
      text: processes.limit != null ? `${processes.current ?? 0} / ${processes.limit}` : `${processes.current ?? 0}`,
      percent: share(processes.current, processes.limit),
    },
  };
}

// The tone a share of a limit gets: the same steps as the resource bars.
export function toneOf(percent) {
  if (percent == null) return '';
  if (percent >= 90) return 'bad';
  if (percent >= 75) return 'warn';
  return 'ok';
}
