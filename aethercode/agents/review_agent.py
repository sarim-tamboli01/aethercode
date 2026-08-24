from dataclasses import dataclass, field
from enum import Enum

from llm_client import call_llm_json
from tools.file_tools import FileChange

REVIEW_SYSTEM_PROMPT = """You are the Code Review Agent inside AetherCode, \
an autonomous multi-agent software engineering system.

You are given a set of file changes that have already passed tests and \
a security review. Your job is to review them the way a thoughtful \
senior engineer would in a pull request review — focused on quality, \
not on finding a reason to reject.

Focus specifically on:
- Naming clarity (functions, variables, files)
- Code that is unnecessarily complex for what it does
- Missing docstrings/comments where the "why" isn't obvious
- Inconsistency with typical conventions for the language shown
- Obvious edge cases the code doesn't handle (e.g. no None check where \
one is clearly needed)

Rules:
- Only comment on what's actually in the diff — do not invent \
hypothetical files or unrelated suggestions.
- Distinguish "must fix" issues (would cause bugs or real confusion) \
from "suggestion" issues (nice-to-have polish).
- If the code is genuinely fine, say so plainly — do not manufacture \
nitpicks to seem thorough.
- Do not include any reasoning, explanation, or text outside the JSON \
object in your final response.

Respond ONLY with a JSON object with exactly this shape:
{
  "comments": [
    {"severity": "must_fix|suggestion", "file_path": "...", "comment": "..."}
  ],
  "approved": true,
  "overall_assessment": "short summary"
}

Set "approved" to false only if there is at least one "must_fix" comment."""


class ReviewSeverity(str, Enum):
    MUST_FIX = "must_fix"
    SUGGESTION = "suggestion"


@dataclass
class ReviewComment:
    severity: ReviewSeverity
    file_path: str
    comment: str


@dataclass
class ReviewReport:
    comments: list[ReviewComment] = field(default_factory=list)
    approved: bool = True
    overall_assessment: str = ""

    @property
    def has_must_fix(self) -> bool:
        return any(c.severity == ReviewSeverity.MUST_FIX for c in self.comments)

    @property
    def suggestions(self) -> list[ReviewComment]:
        return [c for c in self.comments if c.severity == ReviewSeverity.SUGGESTION]


class ReviewAgent:
    """
    Runs an LLM-based code quality review over the final set of
    resolved file changes for a task.
    """

    def review(self, changes: list[FileChange]) -> ReviewReport:
        """
        Reviews a set of file changes for code quality.

        Args:
            changes: The final FileChanges after Implementation +
                     Debug Loop + Security review have all completed.

        Returns:
            ReviewReport with comments and an approve/reject verdict.
        """
        if not changes:
            return ReviewReport(
                comments=[],
                approved=True,
                overall_assessment="No file changes to review.",
            )

        try:
            response = self._call_llm(self._build_prompt(changes))
        except Exception as e:
            # Fail safe, not silent: an unreviewable diff should not
            # be treated as approved. Downstream (PR Agent / Human
            # Approval Gate) should see this as blocking, not passing.
            return ReviewReport(
                comments=[
                    ReviewComment(
                        severity=ReviewSeverity.MUST_FIX,
                        file_path="",
                        comment=f"Code review could not complete: {e}. "
                                f"Treat as unreviewed — do not proceed without "
                                f"a human look at this diff.",
                    )
                ],
                approved=False,
                overall_assessment="Automated review failed to run.",
            )

        comments = [
            ReviewComment(
                severity=ReviewSeverity(c["severity"]),
                file_path=c["file_path"],
                comment=c["comment"],
            )
            for c in response.get("comments", [])
        ]

        # Don't blindly trust the LLM's own "approved" flag — derive
        # it from the actual comments as a safety net, same principle
        # as the Planner's can_proceed cross-check in Phase 2.
        has_must_fix = any(c.severity == ReviewSeverity.MUST_FIX for c in comments)

        return ReviewReport(
            comments=comments,
            approved=not has_must_fix,
            overall_assessment=response.get("overall_assessment", ""),
        )

    def _build_prompt(self, changes: list[FileChange]) -> str:
        changes_block = "\n\n".join(
            f"FILE: {c.file_path} (action: {c.action})\n```\n{c.new_content or ''}\n```"
            for c in changes
        )

        return (
            f"FILE CHANGES TO REVIEW:\n{changes_block}\n\n"
            "Review these changes for code quality, following the "
            "rules in your system prompt."
        )

    def _call_llm(self, user_prompt: str) -> dict:
        return call_llm_json(
            REVIEW_SYSTEM_PROMPT,
            user_prompt,
            temperature=0.2,
            max_tokens=4096,
        )