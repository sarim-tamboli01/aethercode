import os
from dataclasses import dataclass
from pathlib import Path


class UnsafePathError(Exception):
    """Raised when a requested file path would escape the repo sandbox."""
    pass


@dataclass
class FileChange:
    """Record of a single file operation, used for PR diffs and logging."""
    file_path: str          # relative path, as given by the agent
    action: str              # "create" | "modify" | "delete"
    old_content: str | None = None
    new_content: str | None = None


def _resolve_safe_path(repo_root: str, relative_path: str) -> Path:
    """
    Resolves a relative path against repo_root and verifies the result
    stays inside repo_root. Raises UnsafePathError if it would escape
    (e.g. via '../' traversal or an absolute path override).
    """
    root = Path(repo_root).resolve()
    target = (root / relative_path).resolve()

    if not str(target).startswith(str(root)):
        raise UnsafePathError(
            f"Path '{relative_path}' resolves outside the repo sandbox "
            f"({root}). Refusing to touch it."
        )

    return target


def read_file(repo_root: str, relative_path: str) -> str:
    """
    Reads and returns the content of a file within the repo.

    Raises:
        UnsafePathError: if the path escapes the repo sandbox.
        FileNotFoundError: if the file doesn't exist.
    """
    path = _resolve_safe_path(repo_root, relative_path)

    if not path.is_file():
        raise FileNotFoundError(f"File not found: {relative_path}")

    return path.read_text(encoding="utf-8", errors="ignore")


def write_file(
    repo_root: str,
    relative_path: str,
    new_content: str,
) -> FileChange:
    """
    Writes content to a file, creating it (and parent folders) if it
    doesn't exist, or overwriting it if it does. Returns a FileChange
    record capturing before/after content for diffing and PR generation.

    Raises:
        UnsafePathError: if the path escapes the repo sandbox.
    """
    path = _resolve_safe_path(repo_root, relative_path)

    old_content = None
    action = "create"

    if path.is_file():
        old_content = path.read_text(encoding="utf-8", errors="ignore")
        action = "modify"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_content, encoding="utf-8")

    return FileChange(
        file_path=relative_path,
        action=action,
        old_content=old_content,
        new_content=new_content,
    )


def delete_file(repo_root: str, relative_path: str) -> FileChange:
    """
    Deletes a file within the repo. Returns a FileChange record so
    the deletion is tracked for the PR diff, same as create/modify.

    Raises:
        UnsafePathError: if the path escapes the repo sandbox.
        FileNotFoundError: if the file doesn't exist.
    """
    path = _resolve_safe_path(repo_root, relative_path)

    if not path.is_file():
        raise FileNotFoundError(f"Cannot delete — file not found: {relative_path}")

    old_content = path.read_text(encoding="utf-8", errors="ignore")
    path.unlink()

    return FileChange(
        file_path=relative_path,
        action="delete",
        old_content=old_content,
        new_content=None,
    )


def file_exists(repo_root: str, relative_path: str) -> bool:
    """Checks whether a file exists within the repo sandbox."""
    try:
        path = _resolve_safe_path(repo_root, relative_path)
    except UnsafePathError:
        return False
    return path.is_file()


def list_directory(repo_root: str, relative_dir: str = ".") -> list[str]:
    """
    Lists files and folders directly inside a given directory
    (non-recursive), relative to repo_root. Useful for the
    Implementation Agent to double-check structure before writing.

    Raises:
        UnsafePathError: if the path escapes the repo sandbox.
    """
    path = _resolve_safe_path(repo_root, relative_dir)

    if not path.is_dir():
        raise FileNotFoundError(f"Directory not found: {relative_dir}")

    return sorted(os.listdir(path))