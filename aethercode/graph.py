from typing import TypedDict, Optional, cast

from langgraph.graph import StateGraph, END

from agents.understanding_agent import UnderstandingAgent, CodebaseUnderstanding
from agents.planner_agent import PlannerAgent, Plan
from agents.implementation_agent import ImplementationAgent
from agents.test_agent import TestAgent
from agents.debug_agent import DebugAgent
from agents.security_agent import SecurityAgent, SecurityReport
from agents.review_agent import ReviewAgent, ReviewReport
from agents.pr_agent import PRAgent, PullRequestResult
from memory.multi_rag import MultiRAG
from memory.vector_store import VectorStore
from tools.repo_loader import load_and_chunk_repo, cleanup_repo
from tools.file_tools import FileChange
from config import settings
from supabase import create_client


class GraphState(TypedDict):
    """
    Shared state that flows through every node. This is LangGraph's
    equivalent of the OrchestratorResult dataclass — a plain dict
    instead of a dataclass because LangGraph nodes read and return
    partial state updates, which dicts handle more naturally.

    NOTE: the field name "plan" here is the STATE KEY. The graph
    NODE that produces it is named "planning" (not "plan") because
    LangGraph does not allow a node name to collide with a state
    key — they must be distinct identifiers.
    """
    task: str
    repo_url: str
    apply_changes: bool

    repo_root: Optional[str]
    all_chunks: Optional[list]

    understanding: Optional[CodebaseUnderstanding]
    plan: Optional[Plan]

    current_step_index: int
    all_final_changes: list[FileChange]

    security_report: Optional[SecurityReport]
    review_report: Optional[ReviewReport]
    pr_result: Optional[PullRequestResult]

    awaiting_human_approval: bool
    halted: bool
    halt_reason: str


def _build_agents():
    """
    Constructs one shared instance of every agent, reused across all
    node functions via closures. Mirrors Orchestrator.__init__.
    """
    supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
    vector_store = VectorStore(supabase_client)
    multi_rag = MultiRAG(vector_store)

    implementation_agent = ImplementationAgent()
    test_agent = TestAgent()

    return {
        "multi_rag": multi_rag,
        "understanding_agent": UnderstandingAgent(multi_rag),
        "planner_agent": PlannerAgent(),
        "implementation_agent": implementation_agent,
        "test_agent": test_agent,
        "debug_agent": DebugAgent(implementation_agent, test_agent),
        "security_agent": SecurityAgent(),
        "review_agent": ReviewAgent(),
    }


def build_graph():
    """
    Constructs the AetherCode LangGraph StateGraph. Call .invoke() on
    the returned compiled graph with an initial GraphState to run the
    full pipeline.
    """
    agents = _build_agents()
    graph = StateGraph(GraphState)

    # --- Node: load + index repo ---
    def load_and_index_node(state: GraphState) -> dict:
        loaded = load_and_chunk_repo(state["repo_url"])
        indexed = agents["multi_rag"].index_repo(loaded.chunks, state["repo_url"])

        if indexed == 0:
            return {
                "repo_root": loaded.local_path,
                "halted": True,
                "halt_reason": "Repo indexing produced zero chunks — nothing to work with.",
            }

        return {"repo_root": loaded.local_path, "all_chunks": loaded.chunks}

    # --- Node: Understanding Agent ---
    def understanding_node(state: GraphState) -> dict:
        understanding = agents["understanding_agent"].understand(
            task=state["task"],
            repo_url=state["repo_url"],
            all_chunks=state["all_chunks"],
        )

        if not understanding.retrieved_context:
            return {
                "understanding": understanding,
                "halted": True,
                "halt_reason": "Understanding Agent found no relevant context for this task.",
            }

        return {"understanding": understanding}

    # --- Node: Planner Agent (node name "planning", state key "plan") ---
    def planning_node(state: GraphState) -> dict:
        plan = agents["planner_agent"].plan(state["understanding"])

        if not plan.can_proceed:
            return {
                "plan": plan,
                "halted": True,
                "halt_reason": f"Planner refused to proceed: {plan.risks}",
            }

        return {"plan": plan, "current_step_index": 0}

    # --- Node: process one plan step (Implementation -> Test -> Debug) ---
    def process_step_node(state: GraphState) -> dict:
        plan = state["plan"]
        if plan is None:
            return {
                "halted": True,
                "halt_reason": "Cannot process a step without a plan.",
            }
        step = plan.steps[state["current_step_index"]]

        implementation = agents["implementation_agent"].implement_step(
            step=step,
            understanding=state["understanding"],
            repo_root=state["repo_root"],
            apply_changes=state["apply_changes"],
        )

        if not implementation.success:
            return {
                "halted": True,
                "halt_reason": f"Step {step.step_number} implementation failed: {implementation.error}",
            }

        test_report = agents["test_agent"].run_tests_for_step(
            repo_path=state["repo_root"], implementation_result=implementation,
        )

        if test_report.passed:
            return {
                "all_final_changes": state["all_final_changes"] + implementation.changes,
                "current_step_index": state["current_step_index"] + 1,
            }

        debug_result = agents["debug_agent"].repair(
            step=step,
            understanding=state["understanding"],
            repo_root=state["repo_root"],
            failing_report=test_report,
            apply_changes=state["apply_changes"],
        )

        if debug_result.resolved and debug_result.final_implementation:
            return {
                "all_final_changes": state["all_final_changes"] + debug_result.final_implementation.changes,
                "current_step_index": state["current_step_index"] + 1,
            }

        return {
            "halted": True,
            "halt_reason": f"Step {step.step_number} could not be resolved: {debug_result.gave_up_reason}",
        }

    # --- Node: Security Agent ---
    def security_node(state: GraphState) -> dict:
        report = agents["security_agent"].review(state["all_final_changes"])

        if report.has_critical:
            return {
                "security_report": report,
                "halted": True,
                "halt_reason": "Security Agent found critical issues — halting before PR creation.",
            }

        return {"security_report": report}

    # --- Node: Review Agent ---
    def review_node(state: GraphState) -> dict:
        report = agents["review_agent"].review(state["all_final_changes"])

        if not report.approved:
            return {
                "review_report": report,
                "halted": True,
                "halt_reason": "Review Agent did not approve the changes — halting before PR creation.",
            }

        return {"review_report": report}

    # --- Node: PR Agent ---
    def pr_node(state: GraphState) -> dict:
        plan = state["plan"]
        security_report = state["security_report"]
        review_report = state["review_report"]
        if plan is None or security_report is None or review_report is None:
            return {
                "halted": True,
                "halt_reason": "Cannot open a PR without a plan and completed reviews.",
            }

        try:
            pr_agent = PRAgent()
        except RuntimeError as e:
            return {"halted": True, "halt_reason": str(e)}

        pr_result = pr_agent.create_pull_request(
            plan=plan,
            all_changes=state["all_final_changes"],
            security_report=security_report,
            review_report=review_report,
        )

        if not pr_result.success:
            return {
                "pr_result": pr_result,
                "halted": True,
                "halt_reason": f"PR creation failed: {pr_result.error}",
            }

        # Human Approval Gate boundary — the graph ends here on
        # success. No node exists that merges a PR.
        return {"pr_result": pr_result, "awaiting_human_approval": True}

    # --- Node: cleanup (always runs last, success or halt) ---
    def cleanup_node(state: GraphState) -> dict:
        repo_root = state.get("repo_root")
        if repo_root is not None:
            cleanup_repo(repo_root)
        return {"repo_root": None}

    graph.add_node("load_and_index", load_and_index_node)
    graph.add_node("understand", understanding_node)
    graph.add_node("planning", planning_node)
    graph.add_node("process_step", process_step_node)
    graph.add_node("security_review", security_node)
    graph.add_node("code_review", review_node)
    graph.add_node("open_pr", pr_node)
    graph.add_node("cleanup", cleanup_node)

    graph.set_entry_point("load_and_index")

    # Every stage checks "halted" first — if any previous node set
    # it, skip straight to cleanup instead of continuing. This is
    # the same halt logic as Orchestrator, just expressed as routing.
    def route_after(state: GraphState, next_node: str) -> str:
        return "cleanup" if state.get("halted") else next_node

    graph.add_conditional_edges("load_and_index", lambda s: route_after(s, "understand"))
    graph.add_conditional_edges("understand", lambda s: route_after(s, "planning"))
    graph.add_conditional_edges("planning", lambda s: route_after(s, "process_step"))

    def route_after_step(state: GraphState) -> str:
        if state.get("halted"):
            return "cleanup"
        plan = state["plan"]
        if plan is None:
            return "cleanup"
        if state["current_step_index"] >= len(plan.steps):
            return "security_review"
        return "process_step"  # more steps remain — loop back

    graph.add_conditional_edges("process_step", route_after_step)
    graph.add_conditional_edges("security_review", lambda s: route_after(s, "code_review"))
    graph.add_conditional_edges("code_review", lambda s: route_after(s, "open_pr"))
    graph.add_edge("open_pr", "cleanup")
    graph.add_edge("cleanup", END)

    return graph.compile()


def _make_initial_state(task: str, repo_url: str, apply_changes: bool = True) -> GraphState:
    """Builds the starting GraphState for a new pipeline run."""
    return {
        "task": task,
        "repo_url": repo_url,
        "apply_changes": apply_changes,
        "repo_root": None,
        "all_chunks": None,
        "understanding": None,
        "plan": None,
        "current_step_index": 0,
        "all_final_changes": [],
        "security_report": None,
        "review_report": None,
        "pr_result": None,
        "awaiting_human_approval": False,
        "halted": False,
        "halt_reason": "",
    }


def stream_aethercode(task: str, repo_url: str, apply_changes: bool = True):
    """
    Generator that yields (node_name, state_update, full_state) after
    each LangGraph node completes. Used by the SSE endpoint in main.py
    to stream live pipeline progress to the frontend.
    """
    compiled_graph = build_graph()
    full_state: GraphState = _make_initial_state(task, repo_url, apply_changes)

    for chunk in compiled_graph.stream(full_state, stream_mode="updates"):
        for node_name, state_update in chunk.items():
            full_state = cast(GraphState, {**full_state, **state_update})
            yield node_name, state_update, dict(full_state)


def run_aethercode(task: str, repo_url: str, apply_changes: bool = True) -> GraphState:
    """
    Convenience entry point — builds and runs the graph for a single
    task/repo pair, returning the final state.
    """
    compiled_graph = build_graph()
    return cast(
        GraphState,
        compiled_graph.invoke(_make_initial_state(task, repo_url, apply_changes)),
    )