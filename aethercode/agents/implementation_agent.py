from dataclasses import dataclass, field

from llm_client import call_llm_json
from agents.planner_agent import PlanStep
from agents.understanding_agent import CodebaseUnderstanding
from tools.file_tools import (
    FileChange,
    read_file,
    write_file,
    file_exists,
    UnsafePathError,
)

IMPLEMENTATION_SYSTEM_PROMPT = """You are the Implementation Agent inside \
AetherCode, an autonomous multi-agent software engineering system.

You are given ONE specific step from a larger plan, plus the current \
content of the file(s) it targets (if they already exist). Your job is \
to produce the exact new content for each target file after this step \
is applied.

Rules:
- Only modify files listed as target files for this step. Never touch \
files outside that list.
- If a target file already exists, preserve everything not related to \
this step; do not rewrite unrelated code.
- If a target file does not exist yet, write it from scratch based on \
the step description and codebase context.
- Write complete, working file content — not diffs, not snippets, not \
placeholders like "// rest of code here".
- Do not include markdown code fences in the file content itself.
- Do not include any reasoning, explanation, or text outside the JSON \
object in your final response.

Respond ONLY with a JSON object with exactly this shape:
{
  "files": [
    {"file_path": "...", "action": "create_or_modify", "content": "..."}
  ],
  "notes": "short note on what you did or any uncertainty"
}"""


@dataclass
class ImplementationResult:
    step_number: int
    changes: list[FileChange] = field(default_factory=list)
    applied: bool = False
    notes: str = ""
    success: bool = True
    error: str = ""


class ImplementationAgent:
    def implement_step(
        self,
        step: PlanStep,
        understanding: CodebaseUnderstanding,
        repo_root: str,
        apply_changes: bool = True,
    ) -> ImplementationResult:
        try:
            current_state = self._gather_current_file_state(step, repo_root)
        except UnsafePathError as e:
            return ImplementationResult(
                step_number=step.step_number, success=False, error=str(e),
            )

        user_prompt = self._build_prompt(step, understanding, current_state)

        try:
            llm_response = self._call_llm(user_prompt)
        except Exception as e:
            return ImplementationResult(
                step_number=step.step_number,
                success=False,
                error=f"LLM call failed: {e}",
            )

        changes: list[FileChange] = []
        try:
            for file_entry in llm_response.get("files", []):
                file_path = file_entry["file_path"]
                new_content = file_entry["content"]

                if apply_changes:
                    change = write_file(repo_root, file_path, new_content)
                else:
                    old_content = current_state.get(file_path)
                    change = FileChange(
                        file_path=file_path,
                        action="modify" if old_content is not None else "create",
                        old_content=old_content,
                        new_content=new_content,
                    )
                changes.append(change)
        except UnsafePathError as e:
            return ImplementationResult(
                step_number=step.step_number, success=False, error=str(e),
            )

        return ImplementationResult(
            step_number=step.step_number,
            changes=changes,
            applied=apply_changes,
            notes=llm_response.get("notes", ""),
            success=True,
        )

    def _gather_current_file_state(
        self, step: PlanStep, repo_root: str
    ) -> dict[str, str | None]:
        state: dict[str, str | None] = {}
        for file_path in step.target_files:
            if file_exists(repo_root, file_path):
                state[file_path] = read_file(repo_root, file_path)
            else:
                state[file_path] = None
        return state

    def _build_prompt(
        self,
        step: PlanStep,
        understanding: CodebaseUnderstanding,
        current_state: dict[str, str | None],
    ) -> str:
        files_block = "\n\n".join(
            f"FILE: {path}\n"
            + (
                f"CURRENT CONTENT:\n```\n{content}\n```"
                if content is not None
                else "CURRENT CONTENT: (file does not exist yet — create it)"
            )
            for path, content in current_state.items()
        )

        return (
            f"STEP TO IMPLEMENT:\n{step.description}\n\n"
            f"TARGET FILES FOR THIS STEP:\n{', '.join(step.target_files)}\n\n"
            f"OVERALL TASK CONTEXT:\n{understanding.summary}\n\n"
            f"CURRENT FILE STATE:\n{files_block}\n\n"
            "Produce the complete new content for each target file, "
            "following the rules in your system prompt."
        )

    def _call_llm(self, user_prompt: str) -> dict:
        return call_llm_json(
            IMPLEMENTATION_SYSTEM_PROMPT,
            user_prompt,
            temperature=0.1,
            max_tokens=8192,
        )