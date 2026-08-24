const STAGES = [
  { node: "load_and_index", label: "Index", short: "1" },
  { node: "understand", label: "Understanding", short: "2" },
  { node: "planning", label: "Planner", short: "3" },
  { node: "process_step", label: "Implement & Test", short: "4" },
  { node: "security_review", label: "Security", short: "5" },
  { node: "code_review", label: "Review", short: "6" },
  { node: "open_pr", label: "Pull Request", short: "7" },
  { node: "cleanup", label: "Complete", short: "8" },
];

const NODE_ORDER = STAGES.map((s) => s.node);

function stageStatus(stageNode, currentNode, trace, halted, isRunning) {
  const currentIdx = currentNode ? NODE_ORDER.indexOf(currentNode) : -1;
  const stageIdx = NODE_ORDER.indexOf(stageNode);

  const visited = trace.some((t) => t.node === stageNode);
  const isActive = currentNode === stageNode && isRunning;

  if (isActive) return "active";
  if (visited && stageIdx < currentIdx) return "complete";
  if (visited && stageNode === currentNode && !isRunning) return "complete";

  if (halted && visited && stageNode === currentNode) return "error";
  if (halted && !visited && stageIdx > currentIdx) return "skipped";

  if (visited) return "complete";
  if (stageIdx === currentIdx) return isRunning ? "active" : "complete";
  return "pending";
}

function SecurityGate({ report, halted, currentNode }) {
  if (!report) {
    return (
      <div className="gate gate-pending">
        <span className="gate-label">Security</span>
        <span className="gate-value">Pending</span>
      </div>
    );
  }

  const critical = report.has_critical;
  return (
    <div className={`gate${critical ? " gate-error" : " gate-ok"}`}>
      <span className="gate-label">Security</span>
      <span className="gate-value">
        {critical ? "Critical issues" : report.findings?.length ? "Warnings only" : "Clean"}
      </span>
      {halted && currentNode === "security_review" && (
        <span className="gate-note">Pipeline halted</span>
      )}
    </div>
  );
}

function ReviewGate({ report, halted, currentNode }) {
  if (!report) {
    return (
      <div className="gate gate-pending">
        <span className="gate-label">Review</span>
        <span className="gate-value">Pending</span>
      </div>
    );
  }

  return (
    <div className={`gate${report.approved ? " gate-ok" : " gate-error"}`}>
      <span className="gate-label">Review</span>
      <span className="gate-value">
        {report.approved ? "Approved" : "Must-fix items"}
      </span>
      {halted && currentNode === "code_review" && (
        <span className="gate-note">Pipeline halted</span>
      )}
    </div>
  );
}

export default function PipelineStepper({
  currentNode,
  fullState,
  trace,
  isRunning,
  result,
}) {
  const halted = fullState?.halted ?? result?.halted ?? false;
  const plan = fullState?.plan;
  const stepIndex = fullState?.current_step_index ?? result?.steps_completed ?? 0;

  return (
    <section className="pipeline-stepper">
      <header className="panel-header">
        <h2>Pipeline</h2>
      </header>

      <ol className="stepper">
        {STAGES.map((stage) => {
          const status = stageStatus(
            stage.node,
            currentNode,
            trace,
            halted,
            isRunning,
          );
          return (
            <li key={stage.node} className={`step step-${status}`}>
              <span className="step-marker">{stage.short}</span>
              <span className="step-label">{stage.label}</span>
            </li>
          );
        })}
      </ol>

      {plan?.steps?.length > 0 && (
        <div className="plan-steps">
          <h3>Plan Steps</h3>
          <ol>
            {plan.steps.map((step, i) => {
              const done = i < stepIndex;
              const active = i === stepIndex && isRunning;
              return (
                <li
                  key={step.step_number}
                  className={`plan-step${done ? " done" : ""}${active ? " active" : ""}`}
                >
                  <strong>Step {step.step_number}</strong>
                  <span>{step.description}</span>
                </li>
              );
            })}
          </ol>
        </div>
      )}

      <div className="safety-gates">
        <h3>Safety Gates</h3>
        <SecurityGate
          report={fullState?.security_report}
          halted={halted}
          currentNode={currentNode}
        />
        <ReviewGate
          report={fullState?.review_report}
          halted={halted}
          currentNode={currentNode}
        />
      </div>

      {result?.pr_url && (
        <div className="pr-result">
          <h3>Result</h3>
          <a href={result.pr_url} target="_blank" rel="noreferrer">
            Open PR →
          </a>
        </div>
      )}
    </section>
  );
}
