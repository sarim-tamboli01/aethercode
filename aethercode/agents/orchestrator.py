from dataclasses import dataclass, field

from agents.understanding_agent import UnderstandingAgent, CodebaseUnderstanding
from agents.planner_agent import PlannerAgent, Plan
from agents.implementation_agent import ImplementationAgent, ImplementationResult
from agents.test_agent import TestAgent, TestAgentReport
from agents.debug_agent import DebugAgent, DebugLoopResult
from agents.security_agent import SecurityAgent, SecurityReport
from agents.review_agent import ReviewAgent, ReviewReport
from agents.pr_agent import PRAgent, PullRequestResult
from memory.multi_rag import MultiRAG
from tools.repo_loader import load_and_chunk_repo, cleanup_repo, CodeChunk
from tools.file_tools import FileChange


@dataclass
class StepOutcome:
    step_number: int
    resolved: bool
    final_changes: list[FileChange] = field(default_factory=list)
    debug_result: DebugLoopResult | None = None
    error: str = ""


@dataclass
class OrchestratorResult:
    task: str
    repo_url: str
    understanding: CodebaseUnderstanding | None = None
    plan: Plan | None = None
    step_outcomes: list[StepOutcome] = field(default_factory=list)
    security_report: SecurityReport | None = None
    review_report: ReviewReport | None = None
    pr_result: PullRequestResult | None = None
    awaiting_human_approval: bool = False
    halted: bool = False
    halt_reason: str = ""


class Orchestrator:
    

    def __init__(self, multi_rag: MultiRAG):
        self.multi_rag = multi_rag
        self.understanding_agent = UnderstandingAgent(multi_rag)
        self.planner_agent = PlannerAgent()
        self.implementation_agent = ImplementationAgent()
        self.test_agent = TestAgent()
        self.debug_agent = DebugAgent(
            implementation_agent=self.implementation_agent,
            test_agent=self.test_agent,
        )
        self.security_agent = SecurityAgent()
        self.review_agent = ReviewAgent()
        # PR Agent needs GITHUB_TOKEN — constructed lazily so the
        # orchestrator can still run (and halt cleanly) without it
        # configured, e.g. during local testing of earlier stages.
        self._pr_agent: PRAgent | None = None

    def run(self, task: str, repo_url: str, apply_changes: bool = True) -> OrchestratorResult:
        """
        Runs the full pipeline for a task against a repo.

        Args:
            task: Natural language description of the change to make.
            repo_url: GitHub URL of the repo to work on.
            apply_changes: Whether Implementation/Debug agents write
                            to the actual cloned repo on disk (True)
                            or stay in dry-run mode (False).

        Returns:
            OrchestratorResult capturing every stage's output, halting
            early and honestly if any safety gate fails.
        """
        result = OrchestratorResult(task=task, repo_url=repo_url)

        loaded = load_and_chunk_repo(repo_url)
        repo_root = loaded.local_path

        try:
            indexed = self.multi_rag.index_repo(loaded.chunks, repo_url)
            if indexed == 0:
                return self._halt(result, "Repo indexing produced zero chunks — nothing to work with.")

            result.understanding = self.understanding_agent.understand(
                task=task, repo_url=repo_url, all_chunks=loaded.chunks,
            )
            if not result.understanding.retrieved_context:
                return self._halt(result, "Understanding Agent found no relevant context for this task.")

            result.plan = self.planner_agent.plan(result.understanding)
            if not result.plan.can_proceed:
                return self._halt(result, f"Planner refused to proceed: {result.plan.risks}")

            all_final_changes: list[FileChange] = []

            for step in result.plan.steps:
                outcome = self._run_step(step, result.understanding, repo_root, apply_changes)
                result.step_outcomes.append(outcome)

                if not outcome.resolved:
                    return self._halt(
                        result,
                        f"Step {step.step_number} could not be resolved: {outcome.error}",
                    )

                all_final_changes.extend(outcome.final_changes)

            result.security_report = self.security_agent.review(all_final_changes)
            if result.security_report.has_critical:
                return self._halt(
                    result,
                    "Security Agent found critical issues — halting before PR creation.",
                )

            result.review_report = self.review_agent.review(all_final_changes)
            if not result.review_report.approved:
                return self._halt(
                    result,
                    "Review Agent did not approve the changes — halting before PR creation.",
                )

            pr_agent = self._get_pr_agent()
            if pr_agent is None:
                return self._halt(result, "GITHUB_TOKEN not configured — cannot open a PR.")

            result.pr_result = pr_agent.create_pull_request(
                plan=result.plan,
                all_changes=all_final_changes,
                security_report=result.security_report,
                review_report=result.review_report,
            )

            if not result.pr_result.success:
                return self._halt(result, f"PR creation failed: {result.pr_result.error}")

            # Success path stops here — a PR is open, nothing merges
            # automatically. This is the Human Approval Gate boundary.
            result.awaiting_human_approval = True
            return result

        finally:
            cleanup_repo(repo_root)

    def _run_step(
        self,
        step,
        understanding: CodebaseUnderstanding,
        repo_root: str,
        apply_changes: bool,
    ) -> StepOutcome:
        implementation = self.implementation_agent.implement_step(
            step=step, understanding=understanding, repo_root=repo_root,
            apply_changes=apply_changes,
        )

        if not implementation.success:
            return StepOutcome(
                step_number=step.step_number, resolved=False, error=implementation.error,
            )

        test_report = self.test_agent.run_tests_for_step(
            repo_path=repo_root, implementation_result=implementation,
        )

        if test_report.passed:
            return StepOutcome(
                step_number=step.step_number,
                resolved=True,
                final_changes=implementation.changes,
            )

        debug_result = self.debug_agent.repair(
            step=step, understanding=understanding, repo_root=repo_root,
            failing_report=test_report, apply_changes=apply_changes,
        )

        if debug_result.resolved and debug_result.final_implementation:
            return StepOutcome(
                step_number=step.step_number,
                resolved=True,
                final_changes=debug_result.final_implementation.changes,
                debug_result=debug_result,
            )

        return StepOutcome(
            step_number=step.step_number,
            resolved=False,
            debug_result=debug_result,
            error=debug_result.gave_up_reason,
        )

    def _get_pr_agent(self) -> PRAgent | None:
        if self._pr_agent is None:
            try:
                self._pr_agent = PRAgent()
            except RuntimeError:
                return None
        return self._pr_agent

    def _halt(self, result: OrchestratorResult, reason: str) -> OrchestratorResult:
        result.halted = True
        result.halt_reason = reason
        return result