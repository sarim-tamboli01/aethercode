from dataclasses import dataclass, field

from llm_client import call_llm_json
from agents.understanding_agent import CodebaseUnderstanding

PLANNER_SYSTEM_PROMPT = """You are the Planner Agent inside AetherCode, an \
autonomous multi-agent software engineering system.

You are given:
1. A natural language task describing a code change to make.
2. A grounded summary of the relevant codebase, produced by the \
Understanding Agent (based on real retrieved code, not guesses).

Your job is to break the task into a small ordered list of SAFE, \
SPECIFIC steps. Rules:
- Each step must be small enough to implement and test independently.
- Each step must reference specific files where possible (use only \
files mentioned in the codebase summary — never invent file names).
- Steps must be ordered so that earlier steps don't break on later ones.
- If the Understanding Agent's confidence notes mention gaps or \
uncertainty, your first step should be to resolve that gap, or you \
should flag that planning cannot safely proceed.
- Do not write actual code. Describe what needs to change, not how, \
line by line.
- Do not include any reasoning, explanation, or text outside the JSON \
object in your final response.

Respond ONLY with a JSON object with exactly this shape:
{
  "steps": [
    {"step_number": 1, "description": "...", "target_files": ["..."]}
  ],
  "risks": "short note on anything that could go wrong",
  "can_proceed": true
}

Set "can_proceed" to false if the codebase understanding is too thin \
or too uncertain to plan safely."""


@dataclass
class PlanStep:
    step_number: int
    description: str
    target_files: list[str] = field(default_factory=list)


@dataclass
class Plan:
    task: str
    steps: list[PlanStep] = field(default_factory=list)
    risks: str = ""
    can_proceed: bool = True


class PlannerAgent:
    def plan(self, understanding: CodebaseUnderstanding) -> Plan:
        if not understanding.retrieved_context:
            return Plan(
                task=understanding.task,
                can_proceed=False,
                risks=(
                    "Understanding Agent retrieved no context for this task. "
                    "Planning cannot proceed safely — re-check repo indexing "
                    "or rephrase the task."
                ),
            )

        user_prompt = self._build_prompt(understanding)
        llm_response = self._call_llm(user_prompt)

        steps = [
            PlanStep(
                step_number=s["step_number"],
                description=s["description"],
                target_files=s.get("target_files", []),
            )
            for s in llm_response.get("steps", [])
        ]

        return Plan(
            task=understanding.task,
            steps=steps,
            risks=llm_response.get("risks", ""),
            can_proceed=llm_response.get("can_proceed", True) and len(steps) > 0,
        )

    def _build_prompt(self, understanding: CodebaseUnderstanding) -> str:
        return (
            f"TASK:\n{understanding.task}\n\n"
            f"CODEBASE SUMMARY (from Understanding Agent):\n{understanding.summary}\n\n"
            f"RELEVANT FILES:\n{', '.join(understanding.relevant_files)}\n\n"
            f"UNDERSTANDING AGENT'S CONFIDENCE NOTES:\n{understanding.confidence_notes}\n\n"
            "Break this into a small ordered list of safe, specific steps "
            "following the rules in your system prompt."
        )

    def _call_llm(self, user_prompt: str) -> dict:
        return call_llm_json(
            PLANNER_SYSTEM_PROMPT,
            user_prompt,
            temperature=0.2,
            max_tokens=4096,
        )