from dataclasses import dataclass

from sentence_transformers import SentenceTransformer
from supabase import Client

from tools.repo_loader import CodeChunk

# Small, fast, free local embedding model. 384 dimensions —
# good balance of quality vs. speed for code search on CPU.
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIMENSIONS = 384

TABLE_NAME = "code_chunks"

_model: SentenceTransformer | None = None


def get_embedding_model() -> SentenceTransformer:
    """
    Lazily loads the embedding model once and reuses it.
    Avoids reloading the model on every embed call (slow).
    """
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def embed_text(text: str) -> list[float]:
    """Converts a piece of text into a vector embedding."""
    model = get_embedding_model()
    vector = model.encode(text, normalize_embeddings=True)
    return vector.tolist()


def embed_texts_batch(texts: list[str]) -> list[list[float]]:
    """Batch version — much faster than embedding one at a time."""
    model = get_embedding_model()
    vectors = model.encode(texts, normalize_embeddings=True, batch_size=32)
    return vectors.tolist()


@dataclass
class VectorSearchResult:
    file_path: str
    content: str
    start_line: int
    end_line: int
    similarity: float


class VectorStore:
    """
    Wraps Supabase pgvector operations: inserting embedded chunks
    and querying by semantic similarity.

    Requires a `code_chunks` table in Supabase with a `match_code_chunks`
    RPC function (see the SQL setup below this class).
    """

    def __init__(self, supabase_client: Client):
        self.client = supabase_client

    def index_chunks(self, chunks: list[CodeChunk], repo_url: str) -> int:
        """
        Embeds and stores a list of code chunks in Supabase.

        Args:
            chunks: Chunks produced by tools/repo_loader.py.
            repo_url: The source repo, so chunks from different repos
                      don't collide during search.

        Returns:
            Number of chunks successfully indexed.
        """
        if not chunks:
            return 0

        # PostgreSQL text fields reject NUL characters.
        contents = [chunk.content.replace("\x00", "") for chunk in chunks]
        embeddings = embed_texts_batch(contents)

        rows = [
            {
                "repo_url": repo_url,
                "file_path": chunk.file_path,
                "content": content,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "language": chunk.language,
                "embedding": embedding,
            }
            for chunk, content, embedding in zip(chunks, contents, embeddings)
        ]

        # Insert in batches to avoid hitting request size limits.
        batch_size = 100
        inserted = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            self.client.table(TABLE_NAME).insert(batch).execute()
            inserted += len(batch)

        return inserted

    def similarity_search(
        self,
        query: str,
        repo_url: str,
        top_k: int = 10,
        match_threshold: float = 0.3,
    ) -> list[VectorSearchResult]:
        """
        Finds the top_k chunks most semantically similar to the query.

        Args:
            query: Natural language search query.
            repo_url: Restrict search to chunks from this repo.
            top_k: Max number of results.
            match_threshold: Minimum cosine similarity to include (0-1).

        Returns:
            Ranked list of VectorSearchResult, most similar first.
        """
        query_embedding = embed_text(query)

        response = self.client.rpc(
            "match_code_chunks",
            {
                "query_embedding": query_embedding,
                "match_repo_url": repo_url,
                "match_threshold": match_threshold,
                "match_count": top_k,
            },
        ).execute()

        return [
            VectorSearchResult(
                file_path=row["file_path"],
                content=row["content"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                similarity=row["similarity"],
            )
            for row in response.data
        ]

    def clear_repo_index(self, repo_url: str) -> None:
        """Removes all indexed chunks for a repo (e.g. before re-indexing)."""
        self.client.table(TABLE_NAME).delete().eq("repo_url", repo_url).execute()