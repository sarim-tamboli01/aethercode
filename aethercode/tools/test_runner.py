from dataclasses import dataclass, field

from llm_client import call_llm_json
from tools.docker_tools import DockerRunner, create_isolated_copy, cleanup_isolated_copy
from tools.file_tools import FileChange, write_file

TEST_GENERATION_SYSTEM_PROMPT = """You are a test-writing assistant inside \
AetherCode, an autonomous multi-agent software engineering system.

You are given a set of file changes (new or modified code) and must \
write pytest tests that verify the changed code behaves correctly.

Rules:
- Write tests using pytest conventions (test_ prefixed functions, \
plain assert statements).
- Import the code under test directly by module name (e.g. \
`from math_utils import add`), assuming it is importable from the \
repo root — do not use relative imports or sys.path tricks.
- Focus tests on the actual behavior of the changed code — not \
trivial checks like "function exists".
- If a change is not testable in isolation (e.g. a config file, a \
markdown doc), say so honestly instead of writing a fake test.
- Do not include markdown code fences in the test file content itself.
- Do not include any reasoning, explanation, or text outside the \
JSON object in your final response.

Respond ONLY with a JSON object with exactly this shape:
{
  "test_file_path": "tests/test_<something>.py",
  "test_content": "...",
  "testable": true,
  "notes": "short note on what is/isn't covered"
}"""


@dataclass
class TestExecutionResult:
    passed: bool
    testable: bool
    exit_code: int
    stdout: str
    stderr: str
    test_file_path: str = ""
    timed_out: bool = False
    error: str = ""
    notes: str = ""


class TestRunner:
    def __init__(self, docker_runner: DockerRunner | None = None):
        self.docker_runner = docker_runner or DockerRunner()

    def generate_and_run_tests(
        self,
        repo_path: str,
        changes: list[FileChange],
        timeout_seconds: int = 60,
    ) -> TestExecutionResult:
        try:
            llm_response = self._generate_tests(changes)
        except Exception as e:
            return TestExecutionResult(
                passed=False, testable=False, exit_code=-1,
                stdout="", stderr="", error=f"Test generation failed: {e}",
            )

        if not llm_response.get("testable", True):
            return TestExecutionResult(
                passed=True, testable=False, exit_code=0,
                stdout="", stderr="",
                notes=llm_response.get("notes", "Change is not independently testable."),
            )

        test_file_path = llm_response["test_file_path"]
        test_content = llm_response["test_content"]

        isolated_path = create_isolated_copy(repo_path)

        try:
            for change in changes:
                if change.new_content is not None:
                    write_file(isolated_path, change.file_path, change.new_content)

            write_file(isolated_path, test_file_path, test_content)

            # PYTHONPATH=/app ensures modules at the repo root (e.g.
            # math_utils.py) are importable from tests/ subfolders.
            # pytest itself is baked into the Docker image already —
            # no pip install here, since the container has no network.
            run_result = self.docker_runner.run_command(
                repo_path=isolated_path,
                command=f"PYTHONPATH=/app pytest {test_file_path} -v",
                requirements_file=None,
                timeout_seconds=timeout_seconds,
            )

            passed = run_result.exit_code == 0 and not run_result.timed_out

            return TestExecutionResult(
                passed=passed,
                testable=True,
                exit_code=run_result.exit_code,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
                test_file_path=test_file_path,
                timed_out=run_result.timed_out,
                error=run_result.error,
                notes=llm_response.get("notes", ""),
            )

        finally:
            cleanup_isolated_copy(isolated_path)

    def _generate_tests(self, changes: list[FileChange]) -> dict:
        changes_block = "\n\n".join(
            f"FILE: {c.file_path} (action: {c.action})\n```\n{c.new_content or ''}\n```"
            for c in changes
        )

        user_prompt = (
            f"FILE CHANGES TO TEST:\n{changes_block}\n\n"
            "Write pytest tests for these changes, following the rules "
            "in your system prompt."
        )

        return self._call_llm(user_prompt)

    def _call_llm(self, user_prompt: str) -> dict:
        return call_llm_json(
            TEST_GENERATION_SYSTEM_PROMPT,
            user_prompt,
            temperature=0.1,
            max_tokens=8192,
        )