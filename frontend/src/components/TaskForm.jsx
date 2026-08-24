import { useState } from "react";

export default function TaskForm({ onSubmit, isRunning, onCancel }) {
  const [task, setTask] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [applyChanges, setApplyChanges] = useState(true);

  function handleSubmit(e) {
    e.preventDefault();
    if (!task.trim() || !repoUrl.trim() || isRunning) return;
    onSubmit({ task: task.trim(), repo_url: repoUrl.trim(), apply_changes: applyChanges });
  }

  return (
    <form className="task-form" onSubmit={handleSubmit}>
      <h2>New Task</h2>

      <label className="field">
        <span>Task description</span>
        <textarea
          value={task}
          onChange={(e) => setTask(e.target.value)}
          placeholder="e.g. Add a /health endpoint that returns JSON status"
          rows={4}
          disabled={isRunning}
          required
        />
      </label>

      <label className="field">
        <span>Repository URL</span>
        <input
          type="url"
          value={repoUrl}
          onChange={(e) => setRepoUrl(e.target.value)}
          placeholder="https://github.com/owner/repo"
          disabled={isRunning}
          required
        />
      </label>

      <label className="checkbox-field">
        <input
          type="checkbox"
          checked={applyChanges}
          onChange={(e) => setApplyChanges(e.target.checked)}
          disabled={isRunning}
        />
        <span>Apply changes to repo (required for PR)</span>
      </label>

      <div className="form-actions">
        <button type="submit" className="btn primary" disabled={isRunning}>
          {isRunning ? "Running…" : "Run Pipeline"}
        </button>
        {isRunning && (
          <button type="button" className="btn secondary" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
