from __future__ import annotations

import difflib

from .types import (
    DiffUnavailableReason,
    FileSnapshot,
    WorkspaceChangeLimits,
    WorkspaceChangeResult,
    WorkspaceChangeStatus,
    WorkspaceChangeSummary,
    WorkspaceFileChange,
    WorkspaceSnapshot,
)


def compare_snapshots(
    before: WorkspaceSnapshot,
    after: WorkspaceSnapshot,
    *,
    limits: WorkspaceChangeLimits | None = None,
) -> WorkspaceChangeResult:
    resolved_limits = limits or WorkspaceChangeLimits()
    all_paths = sorted(set(before.files) | set(after.files))
    changes: list[WorkspaceFileChange] = []
    created = modified = deleted = additions = deletions = 0
    total_diff_bytes = 0
    truncated = before.truncated or after.truncated

    for path in all_paths:
        before_file = before.files.get(path)
        after_file = after.files.get(path)
        if before_file and after_file and _same_file(before_file, after_file):
            continue

        status = _status(before_file, after_file)
        if status == "created":
            created += 1
        elif status == "modified":
            modified += 1
        else:
            deleted += 1

        diff, line_additions, line_deletions, diff_truncated, reason = _build_diff(
            path,
            before_file,
            after_file,
            remaining_bytes=max(0, resolved_limits.max_total_diff_bytes - total_diff_bytes),
        )
        if diff:
            total_diff_bytes += len(diff.encode("utf-8"))
        if diff_truncated or reason in {"large", "truncated"}:
            truncated = True
        additions += line_additions
        deletions += line_deletions

        if len(changes) < resolved_limits.max_files:
            sample = after_file or before_file
            assert sample is not None
            changes.append(
                WorkspaceFileChange(
                    path=path,
                    root=sample.root,
                    status=status,
                    binary=bool((after_file or before_file).binary if (after_file or before_file) else False),
                    sensitive=bool((after_file or before_file).sensitive if (after_file or before_file) else False),
                    size_before=before_file.size if before_file else None,
                    size_after=after_file.size if after_file else None,
                    sha256_before=before_file.sha256 if before_file else None,
                    sha256_after=after_file.sha256 if after_file else None,
                    diff=diff,
                    diff_truncated=diff_truncated,
                    diff_unavailable_reason=reason,
                    additions=line_additions,
                    deletions=line_deletions,
                )
            )
        else:
            truncated = True

    return WorkspaceChangeResult(
        summary=WorkspaceChangeSummary(
            created=created,
            modified=modified,
            deleted=deleted,
            additions=additions,
            deletions=deletions,
            truncated=truncated,
        ),
        files=changes,
        limits=resolved_limits,
    )


def _status(
    before_file: FileSnapshot | None,
    after_file: FileSnapshot | None,
) -> WorkspaceChangeStatus:
    if before_file is None:
        return "created"
    if after_file is None:
        return "deleted"
    return "modified"


def _same_file(before_file: FileSnapshot, after_file: FileSnapshot) -> bool:
    if before_file.sha256 is not None and after_file.sha256 is not None:
        return before_file.sha256 == after_file.sha256
    return before_file.size == after_file.size and before_file.mtime_ns == after_file.mtime_ns


def _build_diff(
    path: str,
    before_file: FileSnapshot | None,
    after_file: FileSnapshot | None,
    *,
    remaining_bytes: int,
) -> tuple[str, int, int, bool, DiffUnavailableReason | None]:
    reason = _diff_unavailable_reason(before_file, after_file)
    if reason is not None:
        return "", 0, 0, False, reason

    before_text = before_file.text if before_file and before_file.text is not None else ""
    after_text = after_file.text if after_file and after_file.text is not None else ""
    lines = list(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=f"a{path}",
            tofile=f"b{path}",
            lineterm="",
        )
    )
    diff = "\n".join(lines)
    additions, deletions = _count_diff_lines(lines)
    if len(diff.encode("utf-8")) > remaining_bytes:
        return "", additions, deletions, True, "truncated"
    return diff, additions, deletions, False, None


def _diff_unavailable_reason(
    before_file: FileSnapshot | None,
    after_file: FileSnapshot | None,
) -> DiffUnavailableReason | None:
    files = [file for file in (before_file, after_file) if file is not None]
    for preferred in ("sensitive", "binary", "large"):
        if any(file.content_unavailable_reason == preferred for file in files):
            return preferred  # type: ignore[return-value]
    return None


def _count_diff_lines(lines: list[str]) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in lines:
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions
