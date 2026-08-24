import { useCallback, useEffect, useRef, useState } from "react";
import TaskForm from "./components/TaskForm.jsx";
import AgentTraceFeed from "./components/AgentTraceFeed.jsx";
import PipelineStepper from "./components/PipelineStepper.jsx";
import RunHistory, { loadRunHistory, saveRunToHistory } from "./components/RunHistory.jsx";
import { useAetherCodeRun } from "./hooks/useAetherCodeRun.js";

export default function App() {
  const {
    status,
    trace,
    currentNode,
    fullState,
    result,
    error,
    isRunning,
    startRun,
    cancelRun,
  } = useAetherCodeRun();

  const [history, setHistory] = useState(loadRunHistory);
  const [selectedHistoryId, setSelectedHistoryId] = useState(null);
  const [backendOk, setBackendOk] = useState(null);

  useEffect(() => {
    fetch("/health")
      .then((res) => setBackendOk(res.ok))
      .catch(() => setBackendOk(false));
  }, []);

  const handleSubmit = useCallback(
    (payload) => {
      setSelectedHistoryId(null);
      startRun(payload);
    },
    [startRun],
  );

  const savedRunRef = useRef(false);

  useEffect(() => {
    if (status === "running") {
      savedRunRef.current = false;
    }
  }, [status]);

  useEffect(() => {
    if (status !== "done" && status !== "error") return;
    if (trace.length === 0 || savedRunRef.current) return;
    savedRunRef.current = true;

    const first = trace[0]?.fullState;
    const entry = {
      id: `run-${Date.now()}`,
      task: first?.task ?? "Unknown task",
      repo_url: first?.repo_url ?? "",
      startedAt: trace[0]?.timestamp ?? Date.now(),
      finishedAt: Date.now(),
      status,
      halted: result?.halted ?? status === "error",
      halt_reason: result?.halt_reason ?? error ?? "",
      awaiting_human_approval: result?.awaiting_human_approval ?? false,
      pr_url: result?.pr_url ?? null,
      traceCount: trace.length,
    };

    const next = saveRunToHistory(entry);
    setHistory(next);
    setSelectedHistoryId(entry.id);
  }, [status, result, error, trace]);

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>AetherCode</h1>
          <p className="tagline">Autonomous Multi-Agent Software Engineering</p>
        </div>
        <div className={`backend-status${backendOk ? " ok" : backendOk === false ? " err" : ""}`}>
          {backendOk === null && "Connecting…"}
          {backendOk === true && "Backend connected"}
          {backendOk === false && "Backend offline"}
        </div>
      </header>

      <main className="dashboard">
        <aside className="sidebar">
          <TaskForm
            onSubmit={handleSubmit}
            isRunning={isRunning}
            onCancel={cancelRun}
          />
          <RunHistory
            runs={history}
            selectedId={selectedHistoryId}
            onSelect={(run) => setSelectedHistoryId(run.id)}
          />
        </aside>

        <AgentTraceFeed
          trace={trace}
          isRunning={isRunning}
          error={error}
          result={result}
        />

        <PipelineStepper
          currentNode={currentNode}
          fullState={fullState}
          trace={trace}
          isRunning={isRunning}
          result={result}
        />
      </main>
    </div>
  );
}
