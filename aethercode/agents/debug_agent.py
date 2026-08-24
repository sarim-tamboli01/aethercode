from dataclasses import dataclass, field

from llm_client import call_llm_json
from agents.implementation_agent import ImplementationAgent, ImplementationResult
from agents.planner_agent import PlanStep
from agents.test_agent import TestAgent, TestAgentReport
from agents.understanding_agent import CodebaseUnderstanding
from config import settings

DEBUG_SYSTEM_PROMPT = """You are the Debug Agent inside AetherCode, an \
autonomous multi-agent software engineering system.

You are given a plan step, the current content of its target file(s), \
and the failure output from running tests against the last \
implementation attempt. Your job is to diagnose the failure and \
produce corrected file content that fixes it.

Rules:
- Base your fix ONLY on the actual failure output provided — do not \
guess at unrelated problems.
- Preserve everything in the file that isn't related to the failure; \
make the smallest change that fixes the root cause.
- Do not include markdown code fences in the file content itself.
- Do not include any reasoning, explanation, or text outside the \
JSON object in your final response.

Respond ONLY with a JSON object with exactly this shape:
{
  "diagnosis": "short explanation of what caused the failure",
  "files": [
    {"file_path": "...", "content": "..."}
  ]
}"""


@dataclass
class DebugAttempt:
    attempt_number: int
    diagnosis: str
    test_report: TestAgentReport


@dataclass
class DebugLoopResult:
    step_number: int
    resolved: bool
    attempts: list[DebugAttempt] = field(default_factory=list)
    final_implementation: ImplementationResult | None = None
    final_test_report: TestAgentReport | None = None
    gave_up_reason: str = ""


class DebugAgent:
    def __init__(
        self,
        implementation_agent: ImplementationAgent,
        test_agent: TestAgent,
        max_attempts: int | None = None,
    ):
        self.implementation_agent = implementation_agent
        self.test_agent = test_agent
        self.max_attempts = max_attempts or settings.MAX_DEBUG_ATTEMPTS

    def repair(
        self,
        step: PlanStep,
        understanding: CodebaseUnderstanding,
        repo_root: str,
        failing_report: TestAgentReport,
        apply_changes: bool = False,
    ) -> DebugLoopResult:
        attempts: list[DebugAttempt] = []
        current_report = failing_report
        current_implementation: ImplementationResult | None = None

        for attempt_number in range(1, self.max_attempts + 1):
            try:
                fix = self._diagnose_and_fix(step, repo_root, current_report)
            except Exception as e:
                return DebugLoopResult(
                    step_number=step.step_number,
                    resolved=False,
                    attempts=attempts,
                    gave_up_reason=f"Debug fix generation failed on attempt {attempt_number}: {e}",
                )

            current_implementation = self._apply_fix(step, repo_root, fix, apply_changes)

            if not current_implementation.success:
                attempts.append(
                    DebugAttempt(
                        attempt_number=attempt_number,
                        diagnosis=fix.get("diagnosis", ""),
                        test_report=current_report,
                    )
                )
                continue

            current_report = self.test_agent.run_tests_for_step(
                repo_path=repo_root,
                implementation_result=current_implementation,
            )

            attempts.append(
                DebugAttempt(
                    attempt_number=attempt_number,
                    diagnosis=fix.get("diagnosis", ""),
                    test_report=current_report,
                )
            )

            if current_report.passed:
                return DebugLoopResult(
                    step_number=step.step_number,
                    resolved=True,
                    attempts=attempts,
                    final_implementation=current_implementation,
                    final_test_report=current_report,
                )

        return DebugLoopResult(
            step_number=step.step_number,
            resolved=False,
            attempts=attempts,
            final_implementation=current_implementation,
            final_test_report=current_report,
            gave_up_reason=(
                f"Reached max debug attempts ({self.max_attempts}) without "
                f"resolving the failure. Last diagnosis: "
                f"{attempts[-1].diagnosis if attempts else 'none'}"
            ),
        )

    def _diagnose_and_fix(
        self, step: PlanStep, repo_root: str, failing_report: TestAgentReport,
    ) -> dict:
        from tools.file_tools import read_file, file_exists

        current_state = {}
        for file_path in step.target_files:
            if file_exists(repo_root, file_path):
                current_state[file_path] = read_file(repo_root, file_path)

        files_block = "\n\n".join(
            f"FILE: {path}\n```\n{content}\n```"
            for path, content in current_state.items()
        )

        user_prompt = (
            f"STEP:\n{step.description}\n\n"
            f"TARGET FILES:\n{', '.join(step.target_files)}\n\n"
            f"CURRENT FILE CONTENT:\n{files_block}\n\n"
            f"TEST FAILURE OUTPUT:\n{failing_report.summary}\n"
            f"{failing_report.failure_reason}\n\n"
            "Diagnose the failure and produce corrected file content, "
            "following the rules in your system prompt."
        )

        return self._call_llm(user_prompt)

    def _apply_fix(
        self, step: PlanStep, repo_root: str, fix: dict, apply_changes: bool,
    ) -> ImplementationResult:
        from tools.file_tools import write_file, read_file, file_exists, FileChange, UnsafePathError

        changes = []
        try:
            for file_entry in fix.get("files", []):
                file_path = file_entry["file_path"]
                new_content = file_entry["content"]

                if apply_changes:
                    change = write_file(repo_root, file_path, new_content)
                else:
                    old_content = (
                        read_file(repo_root, file_path)
                        if file_exists(repo_root, file_path)
                        else None
                    )
                    change = FileChange(
                        file_path=file_path,
                        action="modify" if old_content is not None else "create",
                        old_content=old_content,
                        new_content=new_content,
                    )
                changes.append(change)
        except UnsafePathError as e:
            return ImplementationResult(
                step_number=step.step_number, success=False, error=str(e)
            )

        return ImplementationResult(
            step_number=step.step_number,
            changes=changes,
            applied=apply_changes,
            notes=fix.get("diagnosis", ""),
            success=True,
        )

    def _call_llm(self, user_prompt: str) -> dict:
        return call_llm_json(
            DEBUG_SYSTEM_PROMPT,
            user_prompt,
            temperature=0.1,
            max_tokens=8192,
        )