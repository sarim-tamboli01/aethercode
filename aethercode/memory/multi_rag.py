from dataclasses import dataclass

from tools.repo_loader import CodeChunk
from tools.search_tools import (
    SearchResult,
    keyword_search,
    structure_search,
)
from memory.vector_store import VectorStore, VectorSearchResult

# Weights used when merging scores from different search methods.
# Vector search gets the highest weight since it's the most reliable
# general-purpose signal; keyword and structure act as correctives.
VECTOR_WEIGHT = 0.5
KEYWORD_WEIGHT = 0.3
STRUCTURE_WEIGHT = 0.2


@dataclass
class RetrievedContext:
    """A single merged, deduplicated result ready to hand to an agent."""
    file_path: str
    content: str
    start_line: int
    end_line: int
    combined_score: float
    sources: list[str]  # e.g. ["vector", "keyword"]


class MultiRAG:
    """
    Orchestrates all three search methods and merges their output
    into one ranked, deduplicated context list.
    """

    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store

    def retrieve(
        self,
        query: str,
        repo_url: str,
        all_chunks: list[CodeChunk],
        top_k: int = 8,
    ) -> list[RetrievedContext]:
        """
        Runs vector + keyword + structure search and merges results.

        Args:
            query: Natural language question or task description.
            repo_url: Repo to restrict vector search to.
            all_chunks: Full in-memory chunk list, for keyword/structure search.
            top_k: Max number of merged results to return.

        Returns:
            Ranked, deduplicated list of RetrievedContext.
        """
        vector_results = self.vector_store.similarity_search(
            query=query, repo_url=repo_url, top_k=top_k * 2
        )
        keyword_results = keyword_search(query=query, chunks=all_chunks, top_k=top_k * 2)
        structure_results = structure_search(query=query, chunks=all_chunks, top_k=top_k * 2)

        merged = self._merge_results(vector_results, keyword_results, structure_results)
        return merged[:top_k]

    def _merge_results(
        self,
        vector_results: list[VectorSearchResult],
        keyword_results: list[SearchResult],
        structure_results: list[SearchResult],
    ) -> list[RetrievedContext]:
        """
        Merges results from all three sources, keyed by (file_path, start_line)
        so the same chunk found by multiple methods gets combined instead
        of duplicated — and gets a score boost for being found multiple ways.
        """
        merged: dict[tuple[str, int], RetrievedContext] = {}

        def _upsert(
            file_path: str,
            content: str,
            start_line: int,
            end_line: int,
            weighted_score: float,
            source: str,
        ) -> None:
            key = (file_path, start_line)
            if key in merged:
                existing = merged[key]
                existing.combined_score += weighted_score
                if source not in existing.sources:
                    existing.sources.append(source)
            else:
                merged[key] = RetrievedContext(
                    file_path=file_path,
                    content=content,
                    start_line=start_line,
                    end_line=end_line,
                    combined_score=weighted_score,
                    sources=[source],
                )

        for r in vector_results:
            _upsert(
                r.file_path, r.content, r.start_line, r.end_line,
                r.similarity * VECTOR_WEIGHT, "vector",
            )

        for r in keyword_results:
            _upsert(
                r.chunk.file_path, r.chunk.content, r.chunk.start_line, r.chunk.end_line,
                r.score * KEYWORD_WEIGHT, "keyword",
            )

        for r in structure_results:
            _upsert(
                r.chunk.file_path, r.chunk.content, r.chunk.start_line, r.chunk.end_line,
                r.score * STRUCTURE_WEIGHT, "structure",
            )

        results = list(merged.values())
        results.sort(key=lambda r: r.combined_score, reverse=True)
        return results

    def index_repo(self, chunks: list[CodeChunk], repo_url: str) -> int:
        """
        Convenience pass-through to index a freshly loaded repo
        into the vector store before any retrieval happens.
        """
        self.vector_store.clear_repo_index(repo_url)
        return self.vector_store.index_chunks(chunks, repo_url)