import { useEffect, useRef } from "react";

const NODE_LABELS = {
  load_and_index: "Repo Indexer",
  understand: "Understanding Agent",
  planning: "Planner Agent",
  process_step: "Implementation / Test / Debug",
  security_review: "Security Agent",
  code_review: "Review Agent",
  open_pr: "PR Agent",
  cleanup: "Cleanup",
};

function extractCardContent(node, stateUpdate, fullState) {
  switch (node) {
    case "load_and_index":
      return {
        headline: fullState?.chunk_count
          ? `Indexed ${fullState.chunk_count} code chunks`
          : "Loading and indexing repository…",
        body: fullState?.halted ? fullState.halt_reason : null,
      };

    case "understand": {
      const u = stateUpdate?.understanding ?? fullState?.understanding;
      if (!u) return { headline: "Analyzing codebase context…", body: null };
      return {
        headline: u.summary || "Understanding complete",
        body: [
          u.relevant_files?.length
            ? `Relevant files: ${u.relevant_files.join(", ")}`
            : null,
          u.confidence_notes ? `Notes: ${u.confidence_notes}` : null,
        ]
          .filter(Boolean)
          .join("\n\n"),
      };
    }

    case "planning": {
      const plan = stateUpdate?.plan ?? fullState?.plan;
      if (!plan) return { headline: "Building implementation plan…", body: null };
      const steps = plan.steps
        ?.map((s) => `${s.step_number}. ${s.description}`)
        .join("\n");
      return {
        headline: plan.can_proceed ? `${plan.steps?.length ?? 0} steps planned` : "Planning blocked",
        body: [steps, plan.risks ? `Risks: ${plan.risks}` : null].filter(Boolean).join("\n\n"),
      };
    }

    case "process_step": {
      const idx = fullState?.current_step_index ?? 0;
      const plan = fullState?.plan;
      const step = plan?.steps?.[Math.max(0, idx - 1)] ?? plan?.steps?.[idx];
      const changes = fullState?.all_final_changes?.length ?? 0;
      if (fullState?.halted && stateUpdate?.halt_reason) {
        return { headline: "Step failed", body: stateUpdate.halt_reason };
      }
      return {
        headline: step
          ? `Step ${step.step_number} complete`
          : `Processing step ${idx + 1}`,
        body: [
          step?.description,
          step?.target_files?.length
            ? `Files: ${step.target_files.join(", ")}`
            : null,
          changes ? `${changes} file change(s) accumulated` : null,
        ]
          .filter(Boolean)
          .join("\n\n"),
      };
    }

    case "security_review": {
      const report = stateUpdate?.security_report ?? fullState?.security_report;
      if (!report) return { headline: "Running security scan…", body: null };
      const findings = report.findings
        ?.map((f) => `[${f.severity}] ${f.file_path}: ${f.description}`)
        .join("\n");
      return {
        headline: report.has_critical
          ? "Critical security issues found"
          : report.findings?.length
            ? `${report.findings.length} finding(s)`
            : "Security clean",
        body: report.summary || findings || null,
      };
    }

    case "code_review": {
      const report = stateUpdate?.review_report ?? fullState?.review_report;
      if (!report) return { headline: "Running code review…", body: null };
      const comments = report.comments
        ?.map((c) => `[${c.severity}] ${c.file_path}: ${c.comment}`)
        .join("\n");
      return {
        headline: report.approved ? "Review approved" : "Review: must-fix items",
        body: report.overall_assessment || comments || null,
      };
    }

    case "open_pr": {
      const pr = stateUpdate?.pr_result ?? fullState?.pr_result;
      if (!pr) return { headline: "Opening pull request…", body: null };
      return {
        headline: pr.success ? `PR #${pr.pr_number} opened` : "PR creation failed",
        body: pr.success ? pr.pr_url : pr.error,
        link: pr.success ? pr.pr_url : null,
      };
    }

    case "cleanup":
      return {
        headline: fullState?.halted ? "Pipeline halted" : "Pipeline complete",
        body: fullState?.halt_reason || null,
      };

    default:
      return { headline: node, body: JSON.stringify(stateUpdate, null, 2) };
  }
}

function TraceCard({ entry }) {
  const { headline, body, link } = extractCardContent(
    entry.node,
    entry.stateUpdate,
    entry.fullState,
  );
  const halted = entry.fullState?.halted && entry.node !== "cleanup";

  return (
    <article className={`trace-card${halted ? " halted" : ""}`}>
      <header className="trace-card-header">
        <span className="trace-agent">{NODE_LABELS[entry.node] ?? entry.node}</span>
        <time className="trace-time">
          {new Date(entry.timestamp).toLocaleTimeString()}
        </time>
      </header>
      <h3 className="trace-headline">{headline}</h3>
      {body && <pre className="trace-body">{body}</pre>}
      {link && (
        <a className="trace-link" href={link} target="_blank" rel="noreferrer">
          View pull request →
        </a>
      )}
    </article>
  );
}

export default function AgentTraceFeed({ trace, isRunning, error, result }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [trace.length]);

  return (
    <section className="trace-feed">
      <header className="panel-header">
        <h2>Agent Trace Feed</h2>
        {isRunning && <span className="live-badge">Live</span>}
      </header>

      <div className="trace-scroll">
        {trace.length === 0 && !isRunning && !error && (
          <p className="muted empty-state">
            Agent reasoning will appear here as the pipeline runs.
          </p>
        )}

        {trace.map((entry) => (
          <TraceCard key={entry.id} entry={entry} />
        ))}

        {isRunning && trace.length > 0 && (
          <div className="trace-card running-indicator">
            <span className="pulse" /> Waiting for next agent…
          </div>
        )}

        {error && (
          <article className="trace-card error">
            <h3 className="trace-headline">Pipeline error</h3>
            <pre className="trace-body">{error}</pre>
          </article>
        )}

        {result && !isRunning && (
          <article className={`trace-card final${result.halted ? " halted" : " success"}`}>
            <h3 className="trace-headline">
              {result.halted ? "Run halted" : "Run finished"}
            </h3>
            {result.halt_reason && (
              <pre className="trace-body">{result.halt_reason}</pre>
            )}
            {result.pr_url && (
              <a className="trace-link" href={result.pr_url} target="_blank" rel="noreferrer">
                View pull request →
              </a>
            )}
          </article>
        )}

        <div ref={bottomRef} />
      </div>
    </section>
  );
}
