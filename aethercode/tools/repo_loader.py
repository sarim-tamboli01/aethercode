"""
tools/repo_loader.py

Responsible for pulling a target GitHub repository onto disk and
breaking its files into indexable chunks. This is the first step
in Multi-RAG: before we can search a codebase, we need to load it
and split it into pieces small enough to embed meaningfully.

Used by: agents/understanding_agent.py (via memory/multi_rag.py)
"""

import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import git  # GitPython
from git.exc import GitCommandError

# File types worth indexing. Skipping binaries, media, lockfiles, etc.
INDEXABLE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php",
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml",
    ".html", ".css", ".sql",
}

# Folders never worth indexing — noise, dependencies, build artifacts.
IGNORED_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__",
    "dist", "build", ".next", "target", ".idea", ".vscode",
    "coverage", ".pytest_cache", "vendor",
}

MAX_FILE_SIZE_BYTES = 500_000  # skip anything larger (avoid generated/huge files)
CHUNK_SIZE_LINES = 80           # lines per chunk
CHUNK_OVERLAP_LINES = 10        # overlap between consecutive chunks


@dataclass
class CodeChunk:
    """A single indexable piece of a file."""
    file_path: str          # path relative to repo root
    content: str
    start_line: int
    end_line: int
    language: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class LoadedRepo:
    """Result of loading and chunking a repository."""
    repo_url: str
    local_path: str
    chunks: list[CodeChunk] = field(default_factory=list)
    file_count: int = 0


def _remove_readonly(func, path, _exc_info):
    """Handles Windows read-only file deletion errors during cleanup."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def clone_repo(repo_url: str, branch: str | None = None) -> str:
    """
    Clones a public GitHub repo into a temporary directory.

    Args:
        repo_url: HTTPS URL of the GitHub repo.
        branch: Optional specific branch to clone. Defaults to the repo's default branch.

    Returns:
        Local filesystem path to the cloned repo.
    """
    local_path = tempfile.mkdtemp(prefix="aethercode_repo_")

    try:
        if branch:
            git.Repo.clone_from(repo_url, local_path, depth=1, branch=branch)
        else:
            git.Repo.clone_from(repo_url, local_path, depth=1)
    except GitCommandError as e:
        shutil.rmtree(local_path, onerror=_remove_readonly)
        raise RuntimeError(f"Failed to clone repo '{repo_url}': {e}") from e

    return local_path


def cleanup_repo(local_path: str) -> None:
    """Removes a cloned repo from disk once indexing is complete."""
    if os.path.exists(local_path):
        shutil.rmtree(local_path, onerror=_remove_readonly)


def _should_index_file(file_path: Path) -> bool:
    if file_path.suffix.lower() not in INDEXABLE_EXTENSIONS:
        return False
    if any(part in IGNORED_DIRS for part in file_path.parts):
        return False
    try:
        if file_path.stat().st_size > MAX_FILE_SIZE_BYTES:
            return False
    except OSError:
        return False
    return True


def _chunk_file_content(content: str, file_path: str) -> list[CodeChunk]:
    """
    Splits a single file's content into overlapping line-based chunks.
    Line-based chunking is simple, predictable, and language-agnostic —
    good enough for Phase 2. Can be upgraded to AST-aware chunking later.
    """
    lines = content.splitlines()
    if not lines:
        return []

    chunks: list[CodeChunk] = []
    step = CHUNK_SIZE_LINES - CHUNK_OVERLAP_LINES
    language = Path(file_path).suffix.lstrip(".")

    for start in range(0, len(lines), step):
        end = min(start + CHUNK_SIZE_LINES, len(lines))
        chunk_text = "\n".join(lines[start:end]).replace("\x00", "").strip()

        if chunk_text:
            chunks.append(
                CodeChunk(
                    file_path=file_path,
                    content=chunk_text,
                    start_line=start + 1,
                    end_line=end,
                    language=language,
                )
            )

        if end == len(lines):
            break

    return chunks


def load_and_chunk_repo(repo_url: str, branch: str | None = None) -> LoadedRepo:
    """
    Main entry point: clones a repo and chunks all indexable files.

    Args:
        repo_url: HTTPS URL of the GitHub repo to load.
        branch: Optional branch name.

    Returns:
        LoadedRepo containing all chunks ready for embedding.
    """
    local_path = clone_repo(repo_url, branch)
    root = Path(local_path)

    all_chunks: list[CodeChunk] = []
    file_count = 0

    for file_path in root.rglob("*"):
        if not file_path.is_file() or not _should_index_file(file_path):
            continue

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue

        relative_path = str(file_path.relative_to(root))
        file_chunks = _chunk_file_content(content, relative_path)

        if file_chunks:
            all_chunks.extend(file_chunks)
            file_count += 1

    return LoadedRepo(
        repo_url=repo_url,
        local_path=local_path,
        chunks=all_chunks,
        file_count=file_count,
    )