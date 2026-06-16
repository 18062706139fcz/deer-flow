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

    def test_file_uri_with_url_encoded_spaces(self):
        assert mcp_tools._local_path_from_uri("file:///tmp/my%20shot.png") == Path("/tmp/my shot.png")

    def test_remote_uri_is_ignored(self):
        assert mcp_tools._local_path_from_uri("https://example.com/a.png") is None
        assert mcp_tools._local_path_from_uri("data:image/png;base64,AAAA") is None

    def test_relative_path_is_ignored(self):
        assert mcp_tools._local_path_from_uri("relative/path.txt") is None

    def test_relative_path_uses_base_dir_when_provided(self, tmp_path: Path):
        assert mcp_tools._local_path_from_uri("./shot.png", base_dir=tmp_path) == tmp_path / "shot.png"

    def test_file_uri_with_relative_path_is_ignored(self):
        assert mcp_tools._local_path_from_uri("file:relative.txt") is None

    def test_file_uri_with_empty_path_is_ignored(self):
        assert mcp_tools._local_path_from_uri("file://") is None

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


class TestAllowedSourceRoots:
    def test_includes_temp_dir(self):
        import tempfile

        roots = mcp_tools._allowed_source_roots()
        assert Path(tempfile.gettempdir()).resolve() in roots

    def test_extra_roots_from_env(self, tmp_path: Path):
        extra = tmp_path / "trusted"
        extra.mkdir()
        with patch.dict("os.environ", {mcp_tools._EXTRA_SOURCE_ROOTS_ENV: str(extra)}):
            roots = mcp_tools._allowed_source_roots()
        assert extra.resolve() in roots

    def test_blank_env_entries_ignored(self):
        import os

        value = f"  {os.pathsep} {os.pathsep}  "
        with patch.dict("os.environ", {mcp_tools._EXTRA_SOURCE_ROOTS_ENV: value}):
            roots = mcp_tools._allowed_source_roots()
        # Only the temp dir should be present; no empty entries added.
        assert all(str(r) for r in roots)

    def test_invalid_extra_root_is_skipped(self, tmp_path: Path):
        extra = tmp_path / "trusted"
        extra.mkdir()
        with patch.dict("os.environ", {mcp_tools._EXTRA_SOURCE_ROOTS_ENV: str(extra)}):
            with patch("pathlib.Path.resolve", side_effect=OSError("boom")):
                roots = mcp_tools._allowed_source_roots()
        # resolve() raised for every entry; the function degrades gracefully.
        assert roots == []


class TestIsWithinAny:
    def test_nested_path_matches(self, tmp_path: Path):
        assert mcp_tools._is_within_any(tmp_path / "a" / "b", [tmp_path])

    def test_unrelated_path_does_not_match(self, tmp_path: Path):
        assert not mcp_tools._is_within_any(Path("/etc/passwd"), [tmp_path])

    def test_no_roots_never_matches(self, tmp_path: Path):
        assert not mcp_tools._is_within_any(tmp_path, [])


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
        src.write_bytes(b"x" * 4)

        with _patch_paths(paths), patch.object(mcp_tools, "_MAX_MIGRATED_FILE_BYTES", 1):
            result = mcp_tools._migrate_local_file_to_outputs(
                str(src), thread_id="t1", user_id="u1"
            )
        assert result is None
        # No partial file is left behind in the outputs directory.
        outputs = paths.sandbox_outputs_dir("t1", user_id="u1")
        assert not outputs.exists() or list(outputs.iterdir()) == []

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

    def test_double_name_collision_increments_counter(self, tmp_path: Path, paths: Paths):
        outputs = paths.sandbox_outputs_dir("t1", user_id="u1")
        outputs.mkdir(parents=True)
        (outputs / "shot.png").write_bytes(b"a")
        (outputs / "shot_1.png").write_bytes(b"b")

        src = tmp_path / "shot.png"
        src.write_bytes(b"new")

        with _patch_paths(paths):
            result = mcp_tools._migrate_local_file_to_outputs(
                str(src), thread_id="t1", user_id="u1"
            )

        assert result == f"{VIRTUAL_PATH_PREFIX}/outputs/shot_2.png"
        assert (outputs / "shot_2.png").read_bytes() == b"new"

    def test_file_outside_allowed_roots_is_refused(self, tmp_path: Path, paths: Paths):
        # A real file that exists but lives outside every trusted source root
        # (simulating a malicious MCP server returning e.g. /etc/passwd).
        src = tmp_path / "secret.txt"
        src.write_bytes(b"top-secret")

        with _patch_paths(paths), patch.object(mcp_tools, "_allowed_source_roots", return_value=[]):
            result = mcp_tools._migrate_local_file_to_outputs(
                str(src), thread_id="t1", user_id="u1"
            )

        assert result is None
        # Nothing leaked into the outputs directory.
        outputs = paths.sandbox_outputs_dir("t1", user_id="u1")
        assert not outputs.exists() or list(outputs.iterdir()) == []

    def test_file_under_thread_user_data_is_allowed(self, paths: Paths):
        # Files the agent itself produced (under the thread's user-data tree)
        # are trusted even when no external source root matches.
        workspace = paths.sandbox_work_dir("t1", user_id="u1")
        workspace.mkdir(parents=True)
        src = workspace / "made-by-agent.txt"
        src.write_bytes(b"agent-output")

        with _patch_paths(paths), patch.object(mcp_tools, "_allowed_source_roots", return_value=[]):
            result = mcp_tools._migrate_local_file_to_outputs(
                str(src), thread_id="t1", user_id="u1"
            )

        assert result == f"{VIRTUAL_PATH_PREFIX}/outputs/made-by-agent.txt"

    def test_relative_source_under_base_dir_is_migrated(self, paths: Paths):
        workspace = paths.sandbox_work_dir("t1", user_id="u1")
        src = workspace / ".playwright-mcp" / "page.png"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"png")

        with _patch_paths(paths), patch.object(mcp_tools, "_allowed_source_roots", return_value=[]):
            result = mcp_tools._migrate_local_file_to_outputs(
                ".playwright-mcp/page.png",
                thread_id="t1",
                user_id="u1",
                source_base_dir=workspace,
            )

        assert result == f"{VIRTUAL_PATH_PREFIX}/outputs/page.png"

    def test_copy_failure_returns_none(self, tmp_path: Path, paths: Paths):
        src = tmp_path / "shot.png"
        src.write_bytes(b"img")

        with _patch_paths(paths), patch.object(mcp_tools.os, "write", side_effect=OSError("disk full")):
            result = mcp_tools._migrate_local_file_to_outputs(
                str(src), thread_id="t1", user_id="u1"
            )

        assert result is None

    def test_unresolvable_source_returns_none(self, paths: Paths):
        with _patch_paths(paths), patch("pathlib.Path.resolve", side_effect=OSError("boom")):
            result = mcp_tools._migrate_local_file_to_outputs(
                "/some/abs/path.png", thread_id="t1", user_id="u1"
            )

        assert result is None

    def test_symlink_escape_is_refused(self, tmp_path: Path, paths: Paths):
        # A symlink living under a trusted root that points at a file outside
        # every allowed root must be refused: .resolve() follows the link, so
        # the boundary check sees the real (out-of-bounds) target.
        secret_root = tmp_path / "outside"
        secret_root.mkdir()
        secret = secret_root / "passwd"
        secret.write_bytes(b"root:x:0:0")

        trusted_root = tmp_path / "trusted"
        trusted_root.mkdir()
        link = trusted_root / "innocent.txt"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this platform")

        with _patch_paths(paths), patch.object(mcp_tools, "_allowed_source_roots", return_value=[trusted_root.resolve()]):
            result = mcp_tools._migrate_local_file_to_outputs(
                str(link), thread_id="t1", user_id="u1"
            )

        assert result is None
        outputs = paths.sandbox_outputs_dir("t1", user_id="u1")
        assert not outputs.exists() or list(outputs.iterdir()) == []

    def test_temp_dir_file_is_migrated(self, paths: Paths):
        # Explicitly assert that a file under the OS temp dir (a default allowed
        # root) is migrated, rather than relying on tmp_path implicitly.
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            fh.write(b"png")
            src = Path(fh.name)
        try:
            with _patch_paths(paths):
                result = mcp_tools._migrate_local_file_to_outputs(
                    str(src), thread_id="t1", user_id="u1"
                )
            assert result == f"{VIRTUAL_PATH_PREFIX}/outputs/{src.name}"
        finally:
            src.unlink(missing_ok=True)

    def test_migrated_file_is_world_readable(self, tmp_path: Path, paths: Paths):
        # Source has restrictive perms; the migrated copy must still be readable
        # by a differently-UID sandbox container (0o644), not inherit 0o600.
        src = tmp_path / "private.png"
        src.write_bytes(b"img")
        src.chmod(0o600)

        with _patch_paths(paths):
            mcp_tools._migrate_local_file_to_outputs(str(src), thread_id="t1", user_id="u1")

        dest = paths.sandbox_outputs_dir("t1", user_id="u1") / "private.png"
        assert dest.stat().st_mode & 0o777 == mcp_tools._MIGRATED_FILE_MODE

    def test_outputs_dir_resolve_failure_is_tolerated(self, tmp_path: Path, paths: Paths):
        # When the outputs dir cannot be resolved, migration still proceeds via
        # the unresolved fallback (covers the except OSError branch).
        src = tmp_path / "shot.png"
        src.write_bytes(b"img")

        real_resolve = Path.resolve

        def flaky_resolve(self, *args, **kwargs):
            if self == paths.sandbox_outputs_dir("t1", user_id="u1"):
                raise OSError("boom")
            return real_resolve(self, *args, **kwargs)

        with _patch_paths(paths), patch("pathlib.Path.resolve", flaky_resolve):
            result = mcp_tools._migrate_local_file_to_outputs(
                str(src), thread_id="t1", user_id="u1"
            )

        assert result == f"{VIRTUAL_PATH_PREFIX}/outputs/shot.png"

    def test_user_data_resolve_failure_is_tolerated(self, paths: Paths):
        # When the user-data root cannot be resolved it is simply skipped as a
        # trusted root (covers the except OSError branch); a temp-dir source is
        # still migrated because the temp dir remains an allowed root.
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            fh.write(b"png")
            src = Path(fh.name)

        real_resolve = Path.resolve

        def flaky_resolve(self, *args, **kwargs):
            if self == paths.sandbox_user_data_dir("t1", user_id="u1"):
                raise OSError("boom")
            return real_resolve(self, *args, **kwargs)

        try:
            with _patch_paths(paths), patch("pathlib.Path.resolve", flaky_resolve):
                result = mcp_tools._migrate_local_file_to_outputs(
                    str(src), thread_id="t1", user_id="u1"
                )
            assert result == f"{VIRTUAL_PATH_PREFIX}/outputs/{src.name}"
        finally:
            src.unlink(missing_ok=True)


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

    def test_text_content_relative_playwright_path_rewritten(self, paths: Paths):
        workspace = paths.sandbox_work_dir("t1", user_id="u1")
        src = workspace / ".playwright-mcp" / "page.png"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"png")
        result = CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text="### Result\n- [Screenshot](.playwright-mcp/page.png)\npath: '.playwright-mcp/page.png'",
                )
            ],
            isError=False,
        )

        with _patch_paths(paths), patch.object(mcp_tools, "_allowed_source_roots", return_value=[]):
            content, _ = mcp_tools._convert_call_tool_result(
                result,
                thread_id="t1",
                user_id="u1",
                source_base_dir=workspace,
            )

        assert content[0]["text"].count(f"{VIRTUAL_PATH_PREFIX}/outputs/page.png") == 2
        assert list(paths.sandbox_outputs_dir("t1", user_id="u1").glob("*.png")) == [
            paths.sandbox_outputs_dir("t1", user_id="u1") / "page.png"
        ]

    def test_text_content_explicit_playwright_filename_rewritten(self, paths: Paths):
        workspace = paths.sandbox_work_dir("t1", user_id="u1")
        src = workspace / "homepage.png"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"png")
        result = CallToolResult(
            content=[TextContent(type="text", text="- [Screenshot](./homepage.png)\npath: './homepage.png'")],
            isError=False,
        )

        with _patch_paths(paths), patch.object(mcp_tools, "_allowed_source_roots", return_value=[]):
            content, _ = mcp_tools._convert_call_tool_result(
                result,
                thread_id="t1",
                user_id="u1",
                source_base_dir=workspace,
            )

        assert content[0]["text"].count(f"{VIRTUAL_PATH_PREFIX}/outputs/homepage.png") == 2
        assert (paths.sandbox_outputs_dir("t1", user_id="u1") / "homepage.png").read_bytes() == b"png"

    def test_image_content_passthrough(self, paths: Paths):
        from mcp.types import ImageContent

        result = CallToolResult(
            content=[ImageContent(type="image", data="QUJD", mimeType="image/png")],
            isError=False,
        )

        with _patch_paths(paths):
            content, _ = mcp_tools._convert_call_tool_result(result, thread_id="t1", user_id="u1")

        assert content[0]["type"] == "image"

    def test_embedded_text_resource(self, paths: Paths):
        from mcp.types import EmbeddedResource, TextResourceContents

        res = TextResourceContents(uri="mem://note.txt", text="note", mimeType="text/plain")
        result = CallToolResult(
            content=[EmbeddedResource(type="resource", resource=res)],
            isError=False,
        )

        with _patch_paths(paths):
            content, _ = mcp_tools._convert_call_tool_result(result, thread_id="t1", user_id="u1")

        assert content[0]["type"] == "text"
        assert content[0]["text"] == "note"

    def test_embedded_blob_image_resource(self, paths: Paths):
        from mcp.types import BlobResourceContents, EmbeddedResource

        res = BlobResourceContents(uri="mem://img.png", blob="QUJD", mimeType="image/png")
        result = CallToolResult(
            content=[EmbeddedResource(type="resource", resource=res)],
            isError=False,
        )

        with _patch_paths(paths):
            content, _ = mcp_tools._convert_call_tool_result(result, thread_id="t1", user_id="u1")

        assert content[0]["type"] == "image"

    def test_embedded_blob_file_resource(self, paths: Paths):
        from mcp.types import BlobResourceContents, EmbeddedResource

        res = BlobResourceContents(uri="mem://doc.pdf", blob="QUJD", mimeType="application/pdf")
        result = CallToolResult(
            content=[EmbeddedResource(type="resource", resource=res)],
            isError=False,
        )

        with _patch_paths(paths):
            content, _ = mcp_tools._convert_call_tool_result(result, thread_id="t1", user_id="u1")

        assert content[0]["type"] == "file"

    def test_unknown_content_item_stringified(self, paths: Paths):
        # An item that is none of the known MCP content types falls through to
        # the str() text block branch.
        class _Weird:
            def __str__(self) -> str:
                return "weird-item"

        result = CallToolResult(content=[TextContent(type="text", text="x")], isError=False)
        result.content = [_Weird()]  # bypass pydantic validation on the union

        with _patch_paths(paths):
            content, _ = mcp_tools._convert_call_tool_result(result, thread_id="t1", user_id="u1")

        assert content[0]["type"] == "text"
        assert content[0]["text"] == "weird-item"

    def test_error_result_raises_tool_exception(self, paths: Paths):
        from langchain_core.tools import ToolException

        result = CallToolResult(
            content=[TextContent(type="text", text="boom")],
            isError=True,
        )

        with _patch_paths(paths), pytest.raises(ToolException, match="boom"):
            mcp_tools._convert_call_tool_result(result, thread_id="t1", user_id="u1")

    def test_structured_content_becomes_artifact(self, paths: Paths):
        result = CallToolResult(
            content=[TextContent(type="text", text="ok")],
            structuredContent={"k": "v"},
            isError=False,
        )

        with _patch_paths(paths):
            _, artifact = mcp_tools._convert_call_tool_result(result, thread_id="t1", user_id="u1")

        assert artifact == {"structured_content": {"k": "v"}}
