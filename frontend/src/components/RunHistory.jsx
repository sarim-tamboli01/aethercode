import { useEffect, useState } from "react";

const STORAGE_KEY = "aethercode_run_history";
const MAX_ENTRIES = 20;

export function loadRunHistory() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function saveRunToHistory(entry) {
  const history = loadRunHistory();
  const next = [entry, ...history.filter((h) => h.id !== entry.id)].slice(0, MAX_ENTRIES);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  return next;
}

function formatTime(ts) {
  return new Date(ts).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusLabel(run) {
  if (run.status === "error") return "Error";
  if (run.halted) return "Halted";
  if (run.pr_url) return "PR opened";
  if (run.awaiting_human_approval) return "Awaiting review";
  return "Complete";
}

export default function RunHistory({ runs, onSelect, selectedId }) {
  const [localRuns, setLocalRuns] = useState(runs ?? loadRunHistory());

  useEffect(() => {
    if (runs) setLocalRuns(runs);
  }, [runs]);

  if (localRuns.length === 0) {
    return (
      <section className="run-history">
        <h2>Run History</h2>
        <p className="muted">No runs yet. Start a task above.</p>
      </section>
    );
  }

  return (
    <section className="run-history">
      <h2>Run History</h2>
      <ul className="history-list">
        {localRuns.map((run) => (
          <li key={run.id}>
            <button
              type="button"
              className={`history-item${selectedId === run.id ? " selected" : ""}`}
              onClick={() => onSelect?.(run)}
            >
              <span className="history-task">{run.task}</span>
              <span className="history-meta">
                {formatTime(run.startedAt)} · {statusLabel(run)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export { formatTime, statusLabel };
