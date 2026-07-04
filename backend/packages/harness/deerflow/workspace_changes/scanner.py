from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path

from .types import (
    DiffUnavailableReason,
    FileSnapshot,
    WorkspaceChangeLimits,
    WorkspaceRoot,
    WorkspaceSnapshot,
)

EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".next",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}

BINARY_EXTENSIONS = {
    ".7z",
    ".avif",
    ".bmp",
    ".class",
    ".db",
    ".dll",
    ".dmg",
    ".doc",
    ".docx",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".o",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".tar",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
}

SENSITIVE_PATH_PATTERNS = (
    ".env",
    ".env.*",
    "*.key",
    "*.pem",
    "*credential*",
    "*secret*",
    "*token*",
)

SAMPLE_BYTES = 4096


def is_sensitive_workspace_path(path: str) -> bool:
    normalized = path.lower()
    parts = [part.lower() for part in Path(path).parts]
    basename = parts[-1] if parts else normalized
    for pattern in SENSITIVE_PATH_PATTERNS:
        if fnmatch.fnmatch(basename, pattern) or fnmatch.fnmatch(normalized, pattern):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def scan_workspace_roots(
    roots: list[WorkspaceRoot],
    *,
    limits: WorkspaceChangeLimits | None = None,
) -> WorkspaceSnapshot:
    resolved_limits = limits or WorkspaceChangeLimits()
    files: dict[str, FileSnapshot] = {}
    scanned = 0
    truncated = False

    for root in roots:
        if not root.host_path.exists():
            continue

        for dirpath, dirnames, filenames in os.walk(root.host_path, followlinks=False):
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if dirname not in EXCLUDED_DIR_NAMES and not (Path(dirpath) / dirname).is_symlink()
            ]
            for filename in sorted(filenames):
                if scanned >= resolved_limits.max_scanned_files:
                    truncated = True
                    return WorkspaceSnapshot(files=files, truncated=truncated)

                host_file = Path(dirpath) / filename
                if host_file.is_symlink() or not host_file.is_file():
                    continue

                snapshot = _snapshot_file(root, host_file, limits=resolved_limits)
                if snapshot is not None:
                    files[snapshot.path] = snapshot
                    scanned += 1

    return WorkspaceSnapshot(files=files, truncated=truncated)


def _snapshot_file(
    root: WorkspaceRoot,
    host_file: Path,
    *,
    limits: WorkspaceChangeLimits,
) -> FileSnapshot | None:
    try:
        stat = host_file.stat()
        size = stat.st_size
        mtime_ns = stat.st_mtime_ns
        relative = host_file.relative_to(root.host_path).as_posix()
        virtual_path = f"{root.virtual_prefix}/{relative}"
        sensitive = is_sensitive_workspace_path(virtual_path)
    except OSError:
        return None

    if sensitive:
        return FileSnapshot(
            path=virtual_path,
            root=root.name,
            size=size,
            mtime_ns=mtime_ns,
            sha256=None,
            binary=False,
            sensitive=True,
            text=None,
            content_unavailable_reason="sensitive",
        )

    try:
        sample = host_file.read_bytes()[:SAMPLE_BYTES] if size <= SAMPLE_BYTES else _read_sample(host_file)
    except OSError:
        return None

    binary = host_file.suffix.lower() in BINARY_EXTENSIONS or _looks_binary(sample)
    sha256 = _sha256_file(host_file) if size <= limits.max_file_bytes_for_diff else None
    text: str | None = None
    reason: DiffUnavailableReason | None = None

    if binary:
        reason = "binary"
    elif size > limits.max_file_bytes_for_diff:
        reason = "large"
    else:
        try:
            text = host_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            binary = True
            reason = "binary"
        except OSError:
            return None

    return FileSnapshot(
        path=virtual_path,
        root=root.name,
        size=size,
        mtime_ns=mtime_ns,
        sha256=sha256,
        binary=binary,
        sensitive=sensitive,
        text=text,
        content_unavailable_reason=reason,
    )


def _read_sample(path: Path) -> bytes:
    with path.open("rb") as file:
        return file.read(SAMPLE_BYTES)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _looks_binary(sample: bytes) -> bool:
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False
