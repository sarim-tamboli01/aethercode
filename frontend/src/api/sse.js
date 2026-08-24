const API_BASE = import.meta.env.VITE_API_BASE ?? "";

/**
 * Parse SSE lines from a chunk of text. Returns complete events
 * and any leftover partial line for the next read.
 */
function parseSSEBuffer(buffer) {
  const events = [];
  const parts = buffer.split("\n\n");
  const remainder = parts.pop() ?? "";

  for (const part of parts) {
    const dataLine = part.split("\n").find((line) => line.startsWith("data: "));
    if (dataLine) {
      try {
        events.push(JSON.parse(dataLine.slice(6)));
      } catch {
        // skip malformed events
      }
    }
  }

  return { events, remainder };
}

/**
 * Start a streaming pipeline run.
 *
 * @param {{ task: string, repo_url: string, apply_changes?: boolean }} body
 * @param {{ onStep?: Function, onDone?: Function, onError?: Function }} callbacks
 * @returns {() => void} abort function
 */
export function streamTask(body, { onStep, onDone, onError } = {}) {
  const controller = new AbortController();

  (async () => {
    try {
      const response = await fetch(`${API_BASE}/run-task/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          apply_changes: true,
          ...body,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const detail = await response.text();
        onError?.({ event: "error", detail: detail || `HTTP ${response.status}` });
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const { events, remainder } = parseSSEBuffer(buffer);
        buffer = remainder;

        for (const event of events) {
          if (event.event === "step") onStep?.(event);
          else if (event.event === "done") onDone?.(event);
          else if (event.event === "error") onError?.(event);
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        onError?.({ event: "error", detail: err.message });
      }
    }
  })();

  return () => controller.abort();
}
