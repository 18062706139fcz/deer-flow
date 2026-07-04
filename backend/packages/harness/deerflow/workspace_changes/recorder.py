from __future__ import annotations

import asyncio
import logging
from typing import Any

from deerflow.config import get_paths

from .diff import compare_snapshots
from .scanner import scan_workspace_roots
from .types import (
    WORKSPACE_CHANGES_EVENT_TYPE,
    WORKSPACE_CHANGES_METADATA_KEY,
    WorkspaceChangeLimits,
    WorkspaceRoot,
    WorkspaceSnapshot,
)

logger = logging.getLogger(__name__)


def build_thread_workspace_roots(thread_id: str, *, user_id: str | None = None) -> list[WorkspaceRoot]:
    paths = get_paths()
    return [
        WorkspaceRoot(
            name="workspace",
            host_path=paths.sandbox_work_dir(thread_id, user_id=user_id),
            virtual_prefix="/mnt/user-data/workspace",
        ),
        WorkspaceRoot(
            name="outputs",
            host_path=paths.sandbox_outputs_dir(thread_id, user_id=user_id),
            virtual_prefix="/mnt/user-data/outputs",
        ),
    ]


async def capture_workspace_snapshot(
    thread_id: str,
    *,
    user_id: str | None = None,
    limits: WorkspaceChangeLimits | None = None,
) -> WorkspaceSnapshot:
    roots = build_thread_workspace_roots(thread_id, user_id=user_id)
    return await asyncio.to_thread(scan_workspace_roots, roots, limits=limits)


async def record_workspace_changes(
    event_store: Any,
    thread_id: str,
    run_id: str,
    before: WorkspaceSnapshot,
    *,
    user_id: str | None = None,
    limits: WorkspaceChangeLimits | None = None,
) -> dict | None:
    roots = build_thread_workspace_roots(thread_id, user_id=user_id)
    after = await asyncio.to_thread(scan_workspace_roots, roots, limits=limits)
    result = compare_snapshots(before, after, limits=limits)
    if not result.has_changes():
        return None

    payload = result.to_dict()
    summary = result.summary
    changed_file_count = summary.created + summary.modified + summary.deleted
    content = (
        f"{changed_file_count} file{'s' if changed_file_count != 1 else ''} changed "
        f"+{summary.additions} -{summary.deletions}"
    )
    return await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type=WORKSPACE_CHANGES_EVENT_TYPE,
        category="workspace",
        content=content,
        metadata={WORKSPACE_CHANGES_METADATA_KEY: payload},
    )
