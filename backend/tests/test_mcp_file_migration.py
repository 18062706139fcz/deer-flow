"""Tests for migrating local MCP-produced files into the sandbox outputs dir.

Regression coverage for GitHub issue #3597: Playwright MCP (and similar stdio
servers) write files to a path the sandbox/artifact API cannot resolve. The MCP
tool wrapper copies such files into the thread's ``/mnt/user-data/outputs``
directory and rewrites the returned URI to the virtual path.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from mcp.types import CallToolResult, ResourceLink, TextContent

from deerflow.config.paths import VIRTUAL_PATH_PREFIX, Paths
from deerflow.mcp import tools as mcp_tools


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(tmp_path)


def _patch_paths(paths: Paths):
    return patch("deerflow.mcp.tools.get_paths", return_value=paths)


class TestLocalPathFromUri:
    def test_file_uri(self):
        assert mcp_tools._local_path_from_uri("file:///tmp/shot.png") == Path("/tmp/shot.png")

    def test_bare_absolute_path(self):
        assert mcp_tools._local_path_from_uri("/var/data/out.pdf") == Path("/var/data/out.pdf")

    def test_remote_uri_is_ignored(self):
        assert mcp_tools._local_path_from_uri("https://example.com/a.png") is None
        assert mcp_tools._local_path_from_uri("data:image/png;base64,AAAA") is None

    def test_relative_path_is_ignored(self):
        assert mcp_tools._local_path_from_uri("relative/path.txt") is None

    def test_empty_is_ignored(self):
        assert mcp_tools._local_path_from_uri("") is None


class TestSafeOutputName:
    def test_strips_directory_components(self):
        assert mcp_tools._safe_output_name("/etc/passwd") == "passwd"

    def test_sanitizes_unsafe_chars(self):
        assert mcp_tools._safe_output_name("my file (1).png") == "my_file__1_.png"

    def test_leading_dots_removed(self):
        assert mcp_tools._safe_output_name("...secret") == "secret"

    def test_empty_falls_back(self):
        assert mcp_tools._safe_output_name("///") == "file"


class TestMigrateLocalFileToOutputs:
    def test_copies_local_file_and_returns_virtual_path(self, tmp_path: Path, paths: Paths):
        src = tmp_path / "screenshot.png"
        src.write_bytes(b"img-bytes")

        with _patch_paths(paths):
            result = mcp_tools._migrate_local_file_to_outputs(
                f"file://{src}", thread_id="t1", user_id="u1"
            )

        assert result == f"{VIRTUAL_PATH_PREFIX}/outputs/screenshot.png"
        dest = paths.sandbox_outputs_dir("t1", user_id="u1") / "screenshot.png"
        assert dest.read_bytes() == b"img-bytes"

    def test_file_already_in_outputs_is_not_recopied(self, paths: Paths):
        outputs = paths.sandbox_outputs_dir("t1", user_id="u1")
        outputs.mkdir(parents=True)
        existing = outputs / "report.pdf"
        existing.write_bytes(b"pdf")

        with _patch_paths(paths):
            result = mcp_tools._migrate_local_file_to_outputs(
                str(existing), thread_id="t1", user_id="u1"
            )

        assert result == f"{VIRTUAL_PATH_PREFIX}/outputs/report.pdf"
        # No duplicate copy created.
        assert list(outputs.iterdir()) == [existing]

    def test_missing_file_returns_none(self, tmp_path: Path, paths: Paths):
        with _patch_paths(paths):
            result = mcp_tools._migrate_local_file_to_outputs(
                f"file://{tmp_path / 'nope.png'}", thread_id="t1", user_id="u1"
            )
        assert result is None

    def test_directory_returns_none(self, tmp_path: Path, paths: Paths):
        with _patch_paths(paths):
            result = mcp_tools._migrate_local_file_to_outputs(
                str(tmp_path), thread_id="t1", user_id="u1"
            )
        assert result is None

    def test_remote_uri_returns_none(self, paths: Paths):
        with _patch_paths(paths):
            result = mcp_tools._migrate_local_file_to_outputs(
                "https://example.com/a.png", thread_id="t1", user_id="u1"
            )
        assert result is None

    def test_oversize_file_is_skipped(self, tmp_path: Path, paths: Paths):
        src = tmp_path / "big.bin"
        src.write_bytes(b"x")

        with _patch_paths(paths), patch.object(mcp_tools, "_MAX_MIGRATED_FILE_BYTES", 0):
            result = mcp_tools._migrate_local_file_to_outputs(
                str(src), thread_id="t1", user_id="u1"
            )
        assert result is None

    def test_name_collision_gets_unique_destination(self, tmp_path: Path, paths: Paths):
        outputs = paths.sandbox_outputs_dir("t1", user_id="u1")
        outputs.mkdir(parents=True)
        (outputs / "shot.png").write_bytes(b"existing")

        src = tmp_path / "shot.png"
        src.write_bytes(b"new")

        with _patch_paths(paths):
            result = mcp_tools._migrate_local_file_to_outputs(
                str(src), thread_id="t1", user_id="u1"
            )

        assert result == f"{VIRTUAL_PATH_PREFIX}/outputs/shot_1.png"
        assert (outputs / "shot_1.png").read_bytes() == b"new"


class TestConvertCallToolResultRewrites:
    def test_resource_link_image_rewritten(self, tmp_path: Path, paths: Paths):
        src = tmp_path / "page.png"
        src.write_bytes(b"png")
        result = CallToolResult(
            content=[ResourceLink(type="resource_link", name="page", uri=f"file://{src}", mimeType="image/png")],
            isError=False,
        )

        with _patch_paths(paths):
            content, _ = mcp_tools._convert_call_tool_result(result, thread_id="t1", user_id="u1")

        assert content[0]["type"] == "image"
        assert content[0]["url"] == f"{VIRTUAL_PATH_PREFIX}/outputs/page.png"

    def test_resource_link_file_rewritten(self, tmp_path: Path, paths: Paths):
        src = tmp_path / "doc.pdf"
        src.write_bytes(b"pdf")
        result = CallToolResult(
            content=[ResourceLink(type="resource_link", name="doc", uri=f"file://{src}", mimeType="application/pdf")],
            isError=False,
        )

        with _patch_paths(paths):
            content, _ = mcp_tools._convert_call_tool_result(result, thread_id="t1", user_id="u1")

        assert content[0]["type"] == "file"
        assert content[0]["url"] == f"{VIRTUAL_PATH_PREFIX}/outputs/doc.pdf"

    def test_remote_resource_link_untouched(self, paths: Paths):
        url = "https://example.com/remote.png"
        result = CallToolResult(
            content=[ResourceLink(type="resource_link", name="r", uri=url, mimeType="image/png")],
            isError=False,
        )

        with _patch_paths(paths):
            content, _ = mcp_tools._convert_call_tool_result(result, thread_id="t1", user_id="u1")

        assert content[0]["url"] == url

    def test_no_context_does_not_rewrite(self, tmp_path: Path, paths: Paths):
        src = tmp_path / "x.png"
        src.write_bytes(b"png")
        uri = f"file://{src}"
        result = CallToolResult(
            content=[ResourceLink(type="resource_link", name="x", uri=uri, mimeType="image/png")],
            isError=False,
        )

        with _patch_paths(paths):
            content, _ = mcp_tools._convert_call_tool_result(result)

        # Without thread_id/user_id the URI is passed through unchanged.
        assert content[0]["url"] == uri

    def test_text_content_passthrough(self, paths: Paths):
        result = CallToolResult(
            content=[TextContent(type="text", text="hello")],
            isError=False,
        )

        with _patch_paths(paths):
            content, _ = mcp_tools._convert_call_tool_result(result, thread_id="t1", user_id="u1")

        assert content[0]["type"] == "text"
        assert content[0]["text"] == "hello"
