import re
from dataclasses import dataclass

from tools.repo_loader import CodeChunk


@dataclass
class SearchResult:
    """A single search hit, with a relevance score for ranking/merging."""
    chunk: CodeChunk
    score: float
    match_type: str  # "keyword" | "structure"


def keyword_search(
    query: str,
    chunks: list[CodeChunk],
    top_k: int = 10,
) -> list[SearchResult]:
    """
    Simple case-insensitive keyword search across chunk content.
    Scores by how many query terms appear in a chunk, weighted by
    frequency. Good at catching exact matches vector search can miss
    (e.g. a specific function name, an error message, an import path).

    Args:
        query: Natural language or exact term to search for.
        chunks: All chunks to search across.
        top_k: Max number of results to return.

    Returns:
        Ranked list of SearchResult, highest score first.
    """
    terms = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 2]
    if not terms:
        return []

    results: list[SearchResult] = []

    for chunk in chunks:
        content_lower = chunk.content.lower()
        term_hits = sum(content_lower.count(term) for term in terms)

        if term_hits == 0:
            continue

        # Normalize score against chunk length so short, dense matches
        # rank higher than long chunks that happen to mention a term once.
        score = term_hits / max(len(content_lower.split()), 1)

        results.append(SearchResult(chunk=chunk, score=score, match_type="keyword"))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_k]


def structure_search(
    query: str,
    chunks: list[CodeChunk],
    top_k: int = 10,
) -> list[SearchResult]:
    """
    Finds chunks based on file path signals — folder names, file names,
    file extensions mentioned in the query. Helps answer questions like
    "where is the auth logic" or "show me the test files" by matching
    against structural location rather than code content.

    Args:
        query: Natural language query, may reference folders/filenames/types.
        chunks: All chunks to search across.
        top_k: Max number of results to return.

    Returns:
        Ranked list of SearchResult, highest score first.
    """
    query_terms = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 2]
    if not query_terms:
        return []

    results: list[SearchResult] = []

    for chunk in chunks:
        path_lower = chunk.file_path.lower()
        path_parts = re.split(r"[/\\._-]", path_lower)

        matches = sum(1 for term in query_terms if term in path_parts)

        if matches == 0:
            continue

        # Score favors matches on filename/folder over deep incidental matches.
        score = matches / max(len(query_terms), 1)

        results.append(SearchResult(chunk=chunk, score=score, match_type="structure"))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_k]


def get_chunks_by_path_prefix(
    path_prefix: str,
    chunks: list[CodeChunk],
) -> list[CodeChunk]:
    """
    Utility for directly fetching all chunks under a given folder,
    e.g. get_chunks_by_path_prefix("agents/", chunks) to inspect
    everything in the agents/ folder. Useful for targeted, non-fuzzy
    lookups the Understanding Agent may need.
    """
    prefix = path_prefix.lower().strip("/\\")
    return [
        chunk for chunk in chunks
        if chunk.file_path.lower().replace("\\", "/").startswith(prefix)
    ]