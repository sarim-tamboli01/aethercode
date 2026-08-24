from dataclasses import dataclass, field
from enum import Enum
import re

from llm_client import call_llm_json
from tools.file_tools import FileChange

SECURITY_SYSTEM_PROMPT = """You are the Security Review Agent inside \
AetherCode, an autonomous multi-agent software engineering system.

You are given a set of file changes and must review them for security \
issues a pattern scanner would miss — logic-level problems, not just \
suspicious strings.

Focus specifically on:
- Authentication/authorization logic done incorrectly or out of order
- Sensitive data (passwords, tokens, PII) logged, exposed in responses, \
or stored insecurely
- Missing input validation on user-controlled data
- Insecure defaults (e.g. debug mode enabled, permissive CORS, weak \
session config)

Rules:
- Only flag things actually present in the given diff — do not invent \
hypothetical issues unrelated to the code shown.
- Rate each finding's severity honestly: "critical", "warning", or "info".
- If you find nothing, say so plainly — do not manufacture findings to \
seem thorough.
- Do not include any reasoning, explanation, or text outside the JSON \
object in your final response.

Respond ONLY with a JSON object with exactly this shape:
{
  "findings": [
    {"severity": "critical|warning|info", "file_path": "...", "description": "..."}
  ],
  "overall_assessment": "short summary"
}"""


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


@dataclass
class SecurityFinding:
    severity: Severity
    file_path: str
    description: str
    source: str


@dataclass
class SecurityReport:
    findings: list[SecurityFinding] = field(default_factory=list)
    has_critical: bool = False
    summary: str = ""

    @property
    def is_clean(self) -> bool:
        return len(self.findings) == 0


SECRET_PATTERNS = [
    (
        r"""(?i)(api[_-]?key|secret[_-]?key|password|token)\s*=\s*["'][^"'\s]{8,}["']""",
        "Possible hardcoded secret or credential",
        Severity.CRITICAL,
    ),
    (
        r"""(?i)AKIA[0-9A-Z]{16}""",
        "String matches AWS access key ID pattern",
        Severity.CRITICAL,
    ),
]

DANGEROUS_PATTERNS = [
    (r"\beval\s*\(", "Use of eval() — can execute arbitrary code from untrusted input", Severity.WARNING),
    (r"\bexec\s*\(", "Use of exec() — can execute arbitrary code from untrusted input", Severity.WARNING),
    (r"""shell\s*=\s*True""", "subprocess call with shell=True — risk of shell injection", Severity.WARNING),
    (r"""(?i)debug\s*=\s*True""", "Debug mode appears enabled — should be off in production", Severity.INFO),
]


class SecurityAgent:
    def review(self, changes: list[FileChange]) -> SecurityReport:
        findings: list[SecurityFinding] = []
        findings.extend(self._pattern_scan(changes))

        try:
            llm_findings, summary = self._llm_review(changes)
            findings.extend(llm_findings)
        except Exception as e:
            findings.append(
                SecurityFinding(
                    severity=Severity.WARNING,
                    file_path="",
                    description=f"LLM security review could not complete: {e}. "
                                f"Only pattern-based checks were run.",
                    source="llm_review",
                )
            )
            summary = "LLM review unavailable — pattern scan only."

        has_critical = any(f.severity == Severity.CRITICAL for f in findings)

        return SecurityReport(findings=findings, has_critical=has_critical, summary=summary)

    def _pattern_scan(self, changes: list[FileChange]) -> list[SecurityFinding]:
        findings = []
        all_patterns = SECRET_PATTERNS + DANGEROUS_PATTERNS

        for change in changes:
            if not change.new_content:
                continue
            for pattern, description, severity in all_patterns:
                if re.search(pattern, change.new_content):
                    findings.append(
                        SecurityFinding(
                            severity=severity,
                            file_path=change.file_path,
                            description=description,
                            source="pattern_scan",
                        )
                    )
        return findings

    def _llm_review(self, changes: list[FileChange]) -> tuple[list[SecurityFinding], str]:
        changes_block = "\n\n".join(
            f"FILE: {c.file_path} (action: {c.action})\n```\n{c.new_content or ''}\n```"
            for c in changes
        )

        user_prompt = (
            f"FILE CHANGES TO REVIEW:\n{changes_block}\n\n"
            "Review these changes for security issues, following the "
            "rules in your system prompt."
        )

        response = self._call_llm(user_prompt)

        findings = [
            SecurityFinding(
                severity=Severity(f["severity"]),
                file_path=f["file_path"],
                description=f["description"],
                source="llm_review",
            )
            for f in response.get("findings", [])
        ]

        return findings, response.get("overall_assessment", "")

    def _call_llm(self, user_prompt: str) -> dict:
        return call_llm_json(
            SECURITY_SYSTEM_PROMPT,
            user_prompt,
            temperature=0.1,
            max_tokens=4096,
        )