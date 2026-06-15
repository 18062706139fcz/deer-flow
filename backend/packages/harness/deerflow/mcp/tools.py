"""Load MCP tools using langchain-mcp-adapters with stdio session pooling."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from langchain_core.tools import BaseTool, StructuredTool
from langgraph.config import get_config

from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.mcp.client import build_servers_config
from deerflow.mcp.oauth import build_oauth_tool_interceptor, get_initial_oauth_headers
from deerflow.mcp.session_pool import get_session_pool
from deerflow.reflection import resolve_variable
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.sync import make_sync_tool_wrapper
from deerflow.tools.types import Runtime

logger = logging.getLogger(__name__)

# Maximum size of an MCP-produced file we are willing to copy into a thread's
# sandbox outputs directory. Larger files are left untouched (path not rewritten).
_MAX_MIGRATED_FILE_BYTES = 100 * 1024 * 1024  # 100 MB

# Characters allowed in a migrated output filename; everything else is replaced.
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")

# Environment variable allowing operators to add extra trusted source roots
# (os.pathsep-separated) from which MCP-produced files may be migrated.
_EXTRA_SOURCE_ROOTS_ENV = "DEERFLOW_MCP_MIGRATION_SOURCE_ROOTS"


def _allowed_source_roots() -> list[Path]:
    """Return the directories MCP-produced files may legitimately be read from.

    Migration only copies files located under one of these roots. This is the
    security boundary that stops a malicious or buggy MCP server from having us
    copy arbitrary host files (e.g. ``/etc/passwd``, SSH keys) into a thread's
    outputs directory, from where the artifact API would happily serve them.

    Roots:
      * the OS temp directory — where stdio MCP servers such as Playwright write
        by default (e.g. ``--output-dir`` defaults under ``$TMPDIR``);
      * any per-thread sandbox roots (handled by the caller via ``get_paths``);
      * operator-configured roots via ``DEERFLOW_MCP_MIGRATION_SOURCE_ROOTS``.
    """
    roots: list[Path] = []
    try:
        roots.append(Path(tempfile.gettempdir()).resolve())
    except OSError:
        pass
    extra = os.environ.get(_EXTRA_SOURCE_ROOTS_ENV, "")
    for entry in extra.split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        try:
            roots.append(Path(entry).resolve())
        except OSError:
            logger.warning("Ignoring invalid MCP migration source root: %s", entry)
    return roots


def _is_within_any(path: Path, roots: list[Path]) -> bool:
    """Return True if *path* is equal to or nested under one of *roots*."""
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _local_path_from_uri(uri: str) -> Path | None:
    """Return an absolute local filesystem ``Path`` if *uri* points to a local
    file, otherwise ``None``.

    Accepts both bare absolute paths and ``file://`` URIs. Remote URIs
    (``http``/``https``/``data``/...) and relative paths return ``None`` so the
    caller leaves them untouched.
    """
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        raw = unquote(parsed.path)
    elif parsed.scheme == "":
        raw = uri
    else:
        return None
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        return None
    return path


def _safe_output_name(name: str) -> str:
    """Sanitize a filename for safe placement inside the outputs directory."""
    name = Path(name).name  # strip any directory component
    name = _UNSAFE_FILENAME_CHARS.sub("_", name)
    name = name.lstrip(".") or "file"
    return name


def _unique_destination(outputs_dir: Path, name: str) -> Path:
    """Return a non-colliding destination path inside *outputs_dir*."""
    dest = outputs_dir / name
    if not dest.exists():
        return dest
    stem = Path(name).stem
    suffix = Path(name).suffix
    counter = 1
    while True:
        candidate = outputs_dir / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _migrate_local_file_to_outputs(uri: str, *, thread_id: str, user_id: str) -> str | None:
    """Copy a local file produced by an MCP server into the thread's sandbox
    outputs directory and return its ``/mnt/user-data/outputs/...`` virtual path.

    Returns ``None`` (leaving the original URI untouched) when the URI is not a
    safe, local, regular file within the size limit. This is what makes
    Playwright-style MCP outputs readable through the sandbox/artifact API,
    which only resolves paths under ``/mnt/user-data``.
    """
    src = _local_path_from_uri(uri)
    if src is None:
        return None

    try:
        real = src.resolve()
    except OSError:
        return None
    if not real.is_file():
        return None

    paths = get_paths()
    outputs_dir = paths.sandbox_outputs_dir(thread_id, user_id=user_id)
    try:
        outputs_real = outputs_dir.resolve()
    except OSError:
        outputs_real = outputs_dir

    # Already inside the thread's outputs directory: just rewrite to the
    # virtual path, no copy needed.
    try:
        relative = real.relative_to(outputs_real)
        return f"{VIRTUAL_PATH_PREFIX}/outputs/{relative.as_posix()}"
    except ValueError:
        pass

    # Security boundary: only migrate files that live under a trusted source
    # root. Without this a malicious/buggy MCP server could return a path like
    # ``/etc/passwd`` and have us copy it into a location the artifact API
    # serves. The thread's own user-data tree is trusted (the agent's own
    # files); everything else must come from a configured source root.
    allowed_roots = _allowed_source_roots()
    try:
        allowed_roots.append(paths.sandbox_user_data_dir(thread_id, user_id=user_id).resolve())
    except OSError:
        pass
    if not _is_within_any(real, allowed_roots):
        logger.warning("Refusing to migrate MCP file outside allowed source roots: %s", real)
        return None

    try:
        size = real.stat().st_size
    except OSError:
        return None
    if size > _MAX_MIGRATED_FILE_BYTES:
        logger.warning("Skipping MCP file migration; file exceeds size limit: %s (%d bytes)", real, size)
        return None

    try:
        paths.ensure_thread_dirs(thread_id, user_id=user_id)
        dest = _unique_destination(outputs_dir, _safe_output_name(real.name))
        shutil.copy2(real, dest)
    except OSError:
        logger.warning("Failed to migrate MCP file into sandbox outputs: %s", real, exc_info=True)
        return None

    return f"{VIRTUAL_PATH_PREFIX}/outputs/{dest.name}"


def _extract_thread_id(runtime: Runtime | None) -> str:
    """Extract thread_id from the injected tool runtime or LangGraph config."""
    if runtime is not None:
        tid = runtime.context.get("thread_id") if runtime.context else None
        if tid is not None:
            return str(tid)
        config = runtime.config or {}
        tid = config.get("configurable", {}).get("thread_id")
        if tid is not None:
            return str(tid)

    try:
        tid = get_config().get("configurable", {}).get("thread_id")
        return str(tid) if tid is not None else "default"
    except RuntimeError:
        return "default"


def _convert_call_tool_result(
    call_tool_result: Any,
    *,
    thread_id: str | None = None,
    user_id: str | None = None,
) -> Any:
    """Convert an MCP CallToolResult to the LangChain ``content_and_artifact`` format.

    Implements the same conversion logic as the adapter without relying on
    the private ``langchain_mcp_adapters.tools._convert_call_tool_result`` symbol.

    When ``thread_id`` and ``user_id`` are provided, local files referenced by
    ``ResourceLink`` blocks (e.g. screenshots saved by Playwright MCP) are copied
    into the thread's sandbox outputs directory and their URIs are rewritten to
    ``/mnt/user-data/outputs/...`` so they can be resolved by the sandbox and
    artifact API. Remote URIs and inaccessible files are left untouched.
    """
    from langchain_core.messages import ToolMessage
    from langchain_core.messages.content import create_file_block, create_image_block, create_text_block
    from langchain_core.tools import ToolException
    from mcp.types import EmbeddedResource, ImageContent, ResourceLink, TextContent, TextResourceContents

    # Pass ToolMessage through directly (interceptor short-circuit).
    if isinstance(call_tool_result, ToolMessage):
        return call_tool_result, None

    # Pass LangGraph Command through directly when langgraph is installed.
    try:
        from langgraph.types import Command

        if isinstance(call_tool_result, Command):
            return call_tool_result, None
    except ImportError:
        # langgraph is optional; if unavailable, continue with standard MCP content conversion.
        pass

    def _resolve_link_url(uri: str) -> str:
        if thread_id is None or user_id is None:
            return uri
        rewritten = _migrate_local_file_to_outputs(uri, thread_id=thread_id, user_id=user_id)
        return rewritten if rewritten is not None else uri

    # Convert MCP content blocks to LangChain content blocks.
    lc_content = []
    for item in call_tool_result.content:
        if isinstance(item, TextContent):
            lc_content.append(create_text_block(text=item.text))
        elif isinstance(item, ImageContent):
            lc_content.append(create_image_block(base64=item.data, mime_type=item.mimeType))
        elif isinstance(item, ResourceLink):
            mime = item.mimeType or None
            url = _resolve_link_url(str(item.uri))
            if mime and mime.startswith("image/"):
                lc_content.append(create_image_block(url=url, mime_type=mime))
            else:
                lc_content.append(create_file_block(url=url, mime_type=mime))
        elif isinstance(item, EmbeddedResource):
            from mcp.types import BlobResourceContents

            res = item.resource
            if isinstance(res, TextResourceContents):
                lc_content.append(create_text_block(text=res.text))
            elif isinstance(res, BlobResourceContents):
                mime = res.mimeType or None
                if mime and mime.startswith("image/"):
                    lc_content.append(create_image_block(base64=res.blob, mime_type=mime))
                else:
                    lc_content.append(create_file_block(base64=res.blob, mime_type=mime))
            else:
                lc_content.append(create_text_block(text=str(res)))
        else:
            lc_content.append(create_text_block(text=str(item)))

    if call_tool_result.isError:
        error_parts = [item["text"] for item in lc_content if isinstance(item, dict) and item.get("type") == "text"]
        raise ToolException("\n".join(error_parts) if error_parts else str(lc_content))

    artifact = None
    if call_tool_result.structuredContent is not None:
        artifact = {"structured_content": call_tool_result.structuredContent}

    return lc_content, artifact


def _make_session_pool_tool(
    tool: BaseTool,
    server_name: str,
    connection: dict[str, Any],
    tool_interceptors: list[Any] | None = None,
) -> BaseTool:
    """Wrap an MCP tool so it reuses a persistent session from the pool.

    Replaces the per-call session creation with pool-managed sessions scoped
    by ``(server_name, user_id:thread_id)``.  This ensures stateful MCP servers
    (e.g. Playwright) keep their state across tool calls within the same thread
    while staying isolated per user.

    The configured ``tool_interceptors`` (OAuth, custom) are preserved and
    applied on every call before invoking the pooled session.
    """
    # Strip the server-name prefix to recover the original MCP tool name.
    original_name = tool.name
    prefix = f"{server_name}_"
    if original_name.startswith(prefix):
        original_name = original_name[len(prefix) :]

    pool = get_session_pool()

    async def call_with_persistent_session(
        runtime: Runtime | None = None,
        **arguments: Any,
    ) -> Any:
        thread_id = _extract_thread_id(runtime)
        user_id = resolve_runtime_user_id(runtime)
        # Scope the pooled session by user *and* thread. Filesystem isolation is
        # per-(user_id, thread_id), so a thread_id alone could otherwise let two
        # users with a colliding thread_id share one stateful MCP session.
        scope_key = f"{user_id}:{thread_id}"
        session = await pool.get_session(server_name, scope_key, connection)

        if tool_interceptors:
            from langchain_mcp_adapters.interceptors import MCPToolCallRequest

            async def base_handler(request: MCPToolCallRequest) -> Any:
                # Preserve interceptor-injected headers for stdio MCP calls by
                # forwarding them through MCP call meta.
                call_kwargs: dict[str, Any] = {}
                if request.headers:
                    if isinstance(request.headers, Mapping):
                        call_kwargs["meta"] = {"headers": dict(request.headers)}
                    else:
                        logger.warning("Ignoring MCP interceptor headers with unsupported type: %s", type(request.headers).__name__)
                return await session.call_tool(request.name, request.args, **call_kwargs)

            handler = base_handler
            for interceptor in reversed(tool_interceptors):
                outer = handler

                async def wrapped(req: Any, _i: Any = interceptor, _h: Any = outer) -> Any:
                    return await _i(req, _h)

                handler = wrapped

            request = MCPToolCallRequest(
                name=original_name,
                args=arguments,
                server_name=server_name,
                runtime=runtime,
            )
            call_tool_result = await handler(request)
        else:
            call_tool_result = await session.call_tool(original_name, arguments)

        return _convert_call_tool_result(call_tool_result, thread_id=thread_id, user_id=user_id)

    return StructuredTool(
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        coroutine=call_with_persistent_session,
        response_format="content_and_artifact",
        metadata=tool.metadata,
    )


async def get_mcp_tools() -> list[BaseTool]:
    """Get all tools from enabled MCP servers.

    Tools using stdio transport are wrapped with persistent-session logic so
    consecutive calls within the same thread reuse the same MCP session.
    HTTP/SSE tools are returned unwrapped to avoid cross-task TaskGroup
    cleanup errors.

    Returns:
        List of LangChain tools from all enabled MCP servers.
    """
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        logger.warning("langchain-mcp-adapters not installed. Install it to enable MCP tools: pip install langchain-mcp-adapters")
        return []

    # NOTE: We use ExtensionsConfig.from_file() instead of get_extensions_config()
    # to always read the latest configuration from disk. This ensures that changes
    # made through the Gateway API (which runs in a separate process) are immediately
    # reflected when initializing MCP tools.
    extensions_config = ExtensionsConfig.from_file()
    servers_config = build_servers_config(extensions_config)

    if not servers_config:
        logger.info("No enabled MCP servers configured")
        return []

    try:
        # Create the multi-server MCP client
        logger.info(f"Initializing MCP client with {len(servers_config)} server(s)")

        # Inject initial OAuth headers for server connections (tool discovery/session init)
        initial_oauth_headers = await get_initial_oauth_headers(extensions_config)
        for server_name, auth_header in initial_oauth_headers.items():
            if server_name not in servers_config:
                continue
            if servers_config[server_name].get("transport") in ("sse", "http"):
                existing_headers = dict(servers_config[server_name].get("headers", {}))
                existing_headers["Authorization"] = auth_header
                servers_config[server_name]["headers"] = existing_headers

        tool_interceptors: list[Any] = []
        oauth_interceptor = build_oauth_tool_interceptor(extensions_config)
        if oauth_interceptor is not None:
            tool_interceptors.append(oauth_interceptor)

        # Load custom interceptors declared in extensions_config.json
        # Format: "mcpInterceptors": ["pkg.module:builder_func", ...]
        raw_interceptor_paths = (extensions_config.model_extra or {}).get("mcpInterceptors")
        if isinstance(raw_interceptor_paths, str):
            raw_interceptor_paths = [raw_interceptor_paths]
        elif not isinstance(raw_interceptor_paths, list):
            if raw_interceptor_paths is not None:
                logger.warning(f"mcpInterceptors must be a list of strings, got {type(raw_interceptor_paths).__name__}; skipping")
            raw_interceptor_paths = []
        for interceptor_path in raw_interceptor_paths:
            try:
                builder = resolve_variable(interceptor_path)
                interceptor = builder()
                if callable(interceptor):
                    tool_interceptors.append(interceptor)
                    logger.info(f"Loaded MCP interceptor: {interceptor_path}")
                elif interceptor is not None:
                    logger.warning(f"Builder {interceptor_path} returned non-callable {type(interceptor).__name__}; skipping")
            except Exception as e:
                logger.warning(
                    f"Failed to load MCP interceptor {interceptor_path}: {e}",
                    exc_info=True,
                )

        client = MultiServerMCPClient(
            servers_config,
            tool_interceptors=tool_interceptors,
            tool_name_prefix=True,
        )

        # Get all tools from all servers (discovers tool definitions via
        # temporary sessions – the persistent-session wrapping is applied below).
        tools = await client.get_tools()
        logger.info(f"Successfully loaded {len(tools)} tool(s) from MCP servers")

        # Wrap each tool with persistent-session logic.
        # Only pool stdio sessions. HTTP/SSE transports use anyio TaskGroups
        # internally which cannot be closed from a different async task, so
        # pooling them causes RuntimeError on cleanup (see #3203).
        wrapped_tools: list[BaseTool] = []
        for tool in tools:
            tool_server: str | None = None
            for name in servers_config:
                if tool.name.startswith(f"{name}_"):
                    tool_server = name
                    break

            if tool_server is not None:
                transport = servers_config[tool_server].get("transport", "stdio")
                if transport == "stdio":
                    wrapped_tools.append(_make_session_pool_tool(tool, tool_server, servers_config[tool_server], tool_interceptors))
                else:
                    wrapped_tools.append(tool)
            else:
                wrapped_tools.append(tool)

        # Patch tools to support sync invocation, as deerflow client streams synchronously
        for tool in wrapped_tools:
            if getattr(tool, "func", None) is None and getattr(tool, "coroutine", None) is not None:
                tool.func = make_sync_tool_wrapper(tool.coroutine, tool.name)

        return wrapped_tools

    except Exception as e:
        logger.error(f"Failed to load MCP tools: {e}", exc_info=True)
        return []
