"""
agents/pr_agent.py

The Pull Request Agent. Takes the final, approved set of file
changes (post Security Review, post Code Review) and opens a real
GitHub Pull Request: creates a branch, commits the changes, and
opens the PR with a clean, informative description.

This agent NEVER merges anything — it only opens the PR. Merging
requires the Human Approval Gate (enforced in graph.py / main.py,
Phase 4's final piece), consistent with the "never merges
automatically" requirement from the start.

Runs AFTER Review Agent approves. Feeds into the Human Approval Gate.

Used by: agents/orchestrator.py (via graph.py, in Phase 4)
"""

import base64
import re
from dataclasses import dataclass, field

from github import Github, GithubException

from agents.planner_agent import Plan
from agents.review_agent import ReviewReport
from agents.security_agent import SecurityReport
from tools.file_tools import FileChange
from config import settings


@dataclass
class PullRequestResult:
    success: bool
    pr_url: str = ""
    pr_number: int | None = None
    branch_name: str = ""
    error: str = ""


class PRAgent:
    """
    Creates a branch, commits file changes, and opens a GitHub PR.
    Uses the GITHUB_TOKEN + repo owner/name already configured in
    config.py.
    """

    def __init__(self):
        if not settings.GITHUB_TOKEN:
            raise RuntimeError("GITHUB_TOKEN is not set in .env — PR Agent cannot run.")

        self.client = Github(settings.GITHUB_TOKEN)
        self.repo_owner = settings.GITHUB_REPO_OWNER
        self.repo_name = settings.GITHUB_REPO_NAME

    def create_pull_request(
        self,
        plan: Plan,
        all_changes: list[FileChange],
        security_report: SecurityReport,
        review_report: ReviewReport,
        base_branch: str = "main",
    ) -> PullRequestResult:
        """
        Creates a branch, commits all changes, and opens a PR.

        Args:
            plan: The original Plan this PR implements (for the description).
            all_changes: All FileChanges across every resolved step.
            security_report: Final SecurityReport, included in the PR body.
            review_report: Final ReviewReport, included in the PR body.
            base_branch: Branch to open the PR against (default "main").

        Returns:
            PullRequestResult with the PR URL if successful.
        """
        try:
            repo = self.client.get_repo(f"{self.repo_owner}/{self.repo_name}")
        except GithubException as e:
            return PullRequestResult(
                success=False,
                error=f"Could not access repo {self.repo_owner}/{self.repo_name}: {e}",
            )

        branch_name = self._generate_branch_name(plan)

        try:
            base_ref = repo.get_branch(base_branch)
            repo.create_git_ref(
                ref=f"refs/heads/{branch_name}",
                sha=base_ref.commit.sha,
            )
        except GithubException as e:
            return PullRequestResult(
                success=False,
                error=f"Failed to create branch '{branch_name}': {e}",
            )

        try:
            self._commit_changes(repo, branch_name, all_changes)
        except GithubException as e:
            return PullRequestResult(
                success=False,
                branch_name=branch_name,
                error=f"Failed to commit changes: {e}",
            )

        pr_body = self._build_pr_description(plan, security_report, review_report)

        try:
            pr = repo.create_pull(
                title=self._generate_pr_title(plan),
                body=pr_body,
                head=branch_name,
                base=base_branch,
            )
        except GithubException as e:
            return PullRequestResult(
                success=False,
                branch_name=branch_name,
                error=f"Failed to open PR: {e}",
            )

        return PullRequestResult(
            success=True,
            pr_url=pr.html_url,
            pr_number=pr.number,
            branch_name=branch_name,
        )

    def _generate_branch_name(self, plan: Plan) -> str:
        import re
        import time

        slug = re.sub(r"[^a-z0-9]+", "-", plan.task.lower()).strip("-")[:40]
        timestamp = int(time.time())
        return f"aethercode/{slug}-{timestamp}"

    def _generate_pr_title(self, plan: Plan) -> str:
        title = plan.task.strip()
        if len(title) > 70:
            title = title[:67] + "..."
        return f"[AetherCode] {title}"

    def _commit_changes(
        self, repo, branch_name: str, changes: list[FileChange]
    ) -> None:
        for change in changes:
            if change.action == "delete":
                existing = repo.get_contents(change.file_path, ref=branch_name)
                repo.delete_file(
                    path=change.file_path,
                    message=f"Delete {change.file_path} (AetherCode)",
                    sha=existing.sha,
                    branch=branch_name,
                )
                continue

            try:
                existing = repo.get_contents(change.file_path, ref=branch_name)
                repo.update_file(
                    path=change.file_path,
                    message=f"Update {change.file_path} (AetherCode)",
                    content=change.new_content or "",
                    sha=existing.sha,
                    branch=branch_name,
                )
            except GithubException as e:
                if e.status == 404:
                    # File doesn't exist yet on this branch — create it.
                    repo.create_file(
                        path=change.file_path,
                        message=f"Create {change.file_path} (AetherCode)",
                        content=change.new_content or "",
                        branch=branch_name,
                    )
                else:
                    raise

    def _build_pr_description(
        self,
        plan: Plan,
        security_report: SecurityReport,
        review_report: ReviewReport,
    ) -> str:
        steps_block = "\n".join(
            f"- **Step {s.step_number}**: {s.description}" for s in plan.steps
        )

        security_block = (
            "No security findings."
            if security_report.is_clean
            else "\n".join(
                f"- `[{f.severity.value}]` {f.file_path}: {f.description}"
                for f in security_report.findings
            )
        )

        review_block = (
            "No review comments — code looks clean."
            if not review_report.comments
            else "\n".join(
                f"- `[{c.severity.value}]` {c.file_path}: {c.comment}"
                for c in review_report.comments
            )
        )

        return f"""## Task

{plan.task}

## Plan

{steps_block}

## Risks noted by Planner

{plan.risks or "None noted."}

## Security Review

{security_block}

## Code Review

{review_block}

---

🤖 This PR was generated autonomously by **AetherCode**. It has passed \
automated tests, security review, and code review. **It requires human \
approval before merging** — nothing in this pipeline merges automatically.
"""