from dataclasses import dataclass, field

from llm_client import call_llm_json
from memory.multi_rag import MultiRAG, RetrievedContext
from tools.repo_loader import CodeChunk


@dataclass
class CodebaseUnderstanding:
    """Structured output of the Understanding Agent."""
    task: str
    relevant_files: list[str] = field(default_factory=list)
    summary: str = ""
    retrieved_context: list[RetrievedContext] = field(default_factory=list)
    confidence_notes: str = ""


UNDERSTANDING_SYSTEM_PROMPT = """You are the Codebase Understanding Agent inside \
AetherCode, an autonomous multi-agent software engineering system.

You are given:
1. A natural language task describing a code change to make.
2. Retrieved code context from the target repository (via Multi-RAG: \
vector + keyword + structure search combined).

Your job is ONLY to understand and summarize — you do not write or \
suggest code changes here; that happens later in the pipeline.

Produce:
- A short summary of how the relevant parts of the codebase currently work.
- A list of the specific files most relevant to the task.
- Honest notes on anything unclear or where the retrieved context seems \
insufficient (this matters — the Planner Agent depends on your honesty here).

Be precise and grounded only in the retrieved context provided. Do not \
invent file names, functions, or code that was not shown to you.

Do not include any reasoning, explanation, or text outside the JSON \
object in your final response.

Respond ONLY with a JSON object with exactly these keys:
{"summary": "...", "confidence_notes": "..."}"""


class UnderstandingAgent:
    def __init__(self, multi_rag: MultiRAG):
        self.multi_rag = multi_rag

    def understand(
        self,
        task: str,
        repo_url: str,
        all_chunks: list[CodeChunk],
        top_k: int = 8,
    ) -> CodebaseUnderstanding:
        retrieved = self.multi_rag.retrieve(
            query=task, repo_url=repo_url, all_chunks=all_chunks, top_k=top_k,
        )

        if not retrieved:
            return CodebaseUnderstanding(
                task=task,
                summary="No relevant context could be retrieved for this task.",
                confidence_notes=(
                    "Multi-RAG returned zero results. Either the repo was not "
                    "indexed correctly, or the task doesn't match anything in "
                    "this codebase. Do not proceed to planning until this is resolved."
                ),
            )

        context_block = self._format_context_for_prompt(retrieved)
        llm_response = self._call_llm(task, context_block)

        return CodebaseUnderstanding(
            task=task,
            relevant_files=self._extract_relevant_files(retrieved),
            summary=llm_response.get("summary", ""),
            retrieved_context=retrieved,
            confidence_notes=llm_response.get("confidence_notes", ""),
        )

    def _format_context_for_prompt(self, retrieved: list[RetrievedContext]) -> str:
        blocks = []
        for ctx in retrieved:
            blocks.append(
                f"File: {ctx.file_path} (lines {ctx.start_line}-{ctx.end_line}) "
                f"[found via: {', '.join(ctx.sources)}]\n"
                f"```\n{ctx.content}\n```"
            )
        return "\n\n".join(blocks)

    def _extract_relevant_files(self, retrieved: list[RetrievedContext]) -> list[str]:
        seen = []
        for ctx in retrieved:
            if ctx.file_path not in seen:
                seen.append(ctx.file_path)
        return seen

    def _call_llm(self, task: str, context_block: str) -> dict:
        user_prompt = (
            f"TASK:\n{task}\n\n"
            f"RETRIEVED CODEBASE CONTEXT:\n{context_block}\n\n"
            "Respond ONLY with a JSON object with exactly these keys:\n"
            '{"summary": "...", "confidence_notes": "..."}'
        )

        return call_llm_json(
            UNDERSTANDING_SYSTEM_PROMPT,
            user_prompt,
            temperature=0.2,
            max_tokens=4096,
        )