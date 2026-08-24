import { useCallback, useRef, useState } from "react";
import { streamTask } from "../api/sse.js";

const INITIAL = {
  status: "idle",
  trace: [],
  currentNode: null,
  fullState: null,
  result: null,
  error: null,
};

export function useAetherCodeRun() {
  const [status, setStatus] = useState(INITIAL.status);
  const [trace, setTrace] = useState(INITIAL.trace);
  const [currentNode, setCurrentNode] = useState(INITIAL.currentNode);
  const [fullState, setFullState] = useState(INITIAL.fullState);
  const [result, setResult] = useState(INITIAL.result);
  const [error, setError] = useState(INITIAL.error);

  const abortRef = useRef(null);
  const runMetaRef = useRef(null);

  const reset = useCallback(() => {
    setStatus("idle");
    setTrace([]);
    setCurrentNode(null);
    setFullState(null);
    setResult(null);
    setError(null);
  }, []);

  const startRun = useCallback(
    ({ task, repo_url, apply_changes = true }) => {
      abortRef.current?.();

      setStatus("running");
      setTrace([]);
      setCurrentNode(null);
      setFullState(null);
      setResult(null);
      setError(null);

      runMetaRef.current = { task, repo_url, startedAt: Date.now() };

      abortRef.current = streamTask(
        { task, repo_url, apply_changes },
        {
          onStep: (event) => {
            const entry = {
              id: `${event.node}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
              node: event.node,
              timestamp: Date.now(),
              stateUpdate: event.state_update,
              fullState: event.full_state,
            };
            setTrace((prev) => [...prev, entry]);
            setCurrentNode(event.node);
            setFullState(event.full_state);
          },
          onDone: (event) => {
            setStatus("done");
            setResult(event);
            setCurrentNode("cleanup");
            abortRef.current = null;
          },
          onError: (event) => {
            setStatus("error");
            setError(event.detail ?? "Unknown error");
            abortRef.current = null;
          },
        },
      );
    },
    [],
  );

  const cancelRun = useCallback(() => {
    abortRef.current?.();
    abortRef.current = null;
    setStatus("idle");
  }, []);

  return {
    status,
    trace,
    currentNode,
    fullState,
    result,
    error,
    isRunning: status === "running",
    startRun,
    cancelRun,
    reset,
    runMeta: runMetaRef.current,
  };
}
