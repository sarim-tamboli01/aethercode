from dataclasses import dataclass, field

from agents.implementation_agent import ImplementationResult
from tools.test_runner import TestRunner, TestExecutionResult


@dataclass
class TestAgentReport:
    """
    Structured verdict for a single step's test run — this is the
    object that flows into the Debug & Repair Loop's decision logic.
    """
    step_number: int
    passed: bool
    testable: bool
    summary: str
    raw_result: TestExecutionResult | None = None
    failure_reason: str = ""  # concise, human/LLM-readable explanation


class TestAgent:
    """
    Runs tests for a step's implementation and produces a clear,
    structured report — pass/fail plus enough context for the Debug
    Agent to act on without re-parsing raw stdout itself.
    """

    def __init__(self, test_runner: TestRunner | None = None):
        self.test_runner = test_runner or TestRunner()

    def run_tests_for_step(
        self,
        repo_path: str,
        implementation_result: ImplementationResult,
        timeout_seconds: int = 60,
    ) -> TestAgentReport:
        """
        Runs tests against a single step's file changes and produces
        a structured pass/fail report.

        Args:
            repo_path: Path to the repo the changes apply to.
            implementation_result: Output of ImplementationAgent.implement_step().
            timeout_seconds: Max time to allow the test run.

        Returns:
            TestAgentReport summarizing the outcome.
        """
        if not implementation_result.success:
            return TestAgentReport(
                step_number=implementation_result.step_number,
                passed=False,
                testable=False,
                summary="Skipped — implementation failed before tests could run.",
                failure_reason=implementation_result.error,
            )

        if not implementation_result.changes:
            return TestAgentReport(
                step_number=implementation_result.step_number,
                passed=True,
                testable=False,
                summary="No file changes were produced for this step — nothing to test.",
            )

        result = self.test_runner.generate_and_run_tests(
            repo_path=repo_path,
            changes=implementation_result.changes,
            timeout_seconds=timeout_seconds,
        )

        return self._build_report(implementation_result.step_number, result)

    def _build_report(
        self, step_number: int, result: TestExecutionResult
    ) -> TestAgentReport:
        if not result.testable:
            return TestAgentReport(
                step_number=step_number,
                passed=True,
                testable=False,
                summary=result.notes or "Change was not independently testable.",
                raw_result=result,
            )

        if result.error:
            return TestAgentReport(
                step_number=step_number,
                passed=False,
                testable=True,
                summary="Test execution encountered an infrastructure error.",
                failure_reason=result.error,
                raw_result=result,
            )

        if result.timed_out:
            return TestAgentReport(
                step_number=step_number,
                passed=False,
                testable=True,
                summary="Tests timed out — possible infinite loop or hang in the implementation.",
                failure_reason=f"Execution exceeded the timeout limit.\nPartial stdout:\n{result.stdout}",
                raw_result=result,
            )

        if result.passed:
            return TestAgentReport(
                step_number=step_number,
                passed=True,
                testable=True,
                summary="All generated tests passed.",
                raw_result=result,
            )

        return TestAgentReport(
            step_number=step_number,
            passed=False,
            testable=True,
            summary="Tests ran but failed.",
            failure_reason=self._extract_failure_reason(result),
            raw_result=result,
        )

    def _extract_failure_reason(self, result: TestExecutionResult) -> str:
        """
        Builds a concise failure summary for the Debug Agent — full
        stdout/stderr is preserved on raw_result, but this trims it to
        the most useful bit (pytest's own failure summary section) so
        the Debug Agent's prompt isn't bloated with passing-test noise.
        """
        combined = result.stdout + "\n" + result.stderr

        if "FAILED" in result.stdout:
            failure_lines = [
                line for line in result.stdout.splitlines()
                if "FAILED" in line or "AssertionError" in line or "Error" in line
            ]
            if failure_lines:
                return "\n".join(failure_lines)

        # Fallback: last ~30 lines of combined output, which usually
        # contains the actual traceback even if the pattern match above misses it.
        lines = combined.strip().splitlines()
        return "\n".join(lines[-30:]) if lines else "Tests failed with no captured output."