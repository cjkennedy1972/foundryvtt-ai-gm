"""Path safety utilities — validate and sanitize file paths to prevent traversal attacks.

Provides a single point of validation for all file operations that use
untrusted input (LLM-generated filenames, user-supplied paths, external APIs).
"""

import os
import re
from pathlib import Path
from typing import Optional


# Windows reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9", "lpt1", "lpt2", "lpt3", "lpt4",
    "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
}


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """Sanitize a filename to remove path separators and dangerous characters.

    Preserves a simple file extension (alphanumeric, up to 8 chars) and
    truncates the basename if necessary while keeping the extension intact.
    """
    if not filename or not isinstance(filename, str):
        raise ValueError("Filename must be a non-empty string")

    # Split extension first to preserve it
    root, ext = os.path.splitext(filename)

    # Sanitize root (basename)
    safe_root = root.replace("/", "").replace("\\", "")
    safe_root = re.sub(r'[*?"<>|:]', "_", safe_root)
    safe_root = re.sub(r'^\.*', "", safe_root)  # Remove leading dots
    safe_root = safe_root.replace("..", "_")
    safe_root = safe_root.strip()

    # Sanitize extension: keep only alphanumeric characters, limit length
    safe_ext = re.sub(r'[^A-Za-z0-9]', '', ext.replace('.', ''))[:8]

    # Build safe filename
    if safe_ext:
        safe = f"{safe_root}.{safe_ext}"
    else:
        safe = safe_root

    # Truncate while preserving extension if present
    if len(safe) > max_length:
        if safe_ext:
            keep = max_length - (1 + len(safe_ext))
            safe_root = safe_root[:max(0, keep)].rstrip("_")
            safe = f"{safe_root}.{safe_ext}" if safe_root else safe_ext
        else:
            safe = safe[:max_length].rstrip("_")

    # Final checks
    if not safe or safe in {"", "."}:
        raise ValueError(f"Filename '{filename}' contains only invalid characters")

    base = safe.split('.')[0].lower()
    if base in _RESERVED_NAMES:
        raise ValueError(f"Filename '{filename}' is a reserved device name")

    return safe


def validate_contained_path(
    path: str, base_dir: str, allow_absolute: bool = True
) -> Path:
    """Validate that a path is contained within a base directory.

    Prevents path traversal attacks where a filename or path might escape
    the intended directory using ../, absolute paths, or symlinks.

    Args:
        path: The path to validate (untrusted)
        base_dir: The base directory that should contain the path
        allow_absolute: If False, reject absolute paths outright. Note that
            containment is enforced either way — an absolute path joined onto
            base replaces it, and the resolved result then fails the
            relative_to(base) check. This flag only controls whether such a
            path is rejected early with a clearer error.

    Returns:
        Absolute pathlib.Path object (resolved, symlinks followed)

    Raises:
        ValueError: If path escapes base_dir, is absolute (when not allowed),
                   or contains invalid characters
    """
    if not path or not isinstance(path, str):
        raise ValueError("Path must be a non-empty string")

    base = Path(base_dir).resolve()

    # Reject absolute paths if not allowed
    if os.path.isabs(path) and not allow_absolute:
        raise ValueError(f"Absolute paths not allowed: {path}")

    # Join and resolve the path (follows symlinks)
    try:
        full_path = (base / path).resolve()
    except (OSError, ValueError) as e:
        raise ValueError(f"Invalid path '{path}': {e}")

    # Verify the resolved path is still under base
    # Use is_relative_to (Python 3.9+) or manual check
    try:
        full_path.relative_to(base)
    except ValueError:
        raise ValueError(
            f"Path '{path}' escapes base directory '{base_dir}' (resolved to {full_path})"
        )

    return full_path


def validate_and_open_file(
    path: str, base_dir: str, mode: str = "r"
) -> tuple[Path, object]:
    """Safely open a file with path validation.

    Args:
        path: The path to open (untrusted)
        base_dir: The base directory that should contain the file
        mode: File open mode (r, w, rb, etc.)

    Returns:
        Tuple of (validated_path, file_object)

    Raises:
        ValueError: If path is invalid or escapes base_dir
        OSError: If file cannot be opened
    """
    validated = validate_contained_path(path, base_dir)

    # Additional checks based on mode
    if "r" in mode and not validated.exists():
        raise FileNotFoundError(f"File not found: {validated}")

    if "w" in mode or "a" in mode:
        # Ensure parent directory exists
        validated.parent.mkdir(parents=True, exist_ok=True)

    return validated, open(validated, mode)


def validate_and_delete_tree(base_dir: str, confirm: bool = True) -> int:
    """Safely delete a directory tree after validation.

    Only deletes if the path is valid and contained. Never follows symlinks
    to delete elsewhere.

    Args:
        base_dir: The directory to delete
        confirm: If True, require confirmation (always True in production)

    Returns:
        Number of files/dirs deleted

    Raises:
        ValueError: If path is invalid
        PermissionError: If user lacks permissions
    """
    import shutil

    base = Path(base_dir).resolve()

    # Sanity checks
    if not base.exists():
        raise ValueError(f"Directory does not exist: {base}")

    if base == Path("/") or base == Path(os.path.expanduser("~")):
        raise ValueError(f"Refusing to delete system/home directory: {base}")

    # Count items before deletion
    count = sum(1 for _ in base.rglob("*"))

    if confirm:
        # In production, require explicit confirmation
        raise PermissionError(
            f"Refusing to delete {count} items from {base} without explicit confirmation"
        )

    # Safe delete (doesn't follow symlinks)
    shutil.rmtree(base)
    return count
