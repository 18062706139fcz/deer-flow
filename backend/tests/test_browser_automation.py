"""Tests for the agentic browser automation tools and session manager.

The tool tests mock the browser session so they run without Playwright. A small
integration test at the end exercises a real headless Chromium session and is
skipped automatically when Playwright (or its browser binary) is unavailable.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deerflow.community.browser_automation import session as session_mod
from deerflow.community.browser_automation import tools
from deerflow.community.browser_automation.session import (
    _LIVE_FRAME_JPEG_QUALITY,
    BrowserSession,
    BrowserSessionManager,
    PageSnapshot,
    SnapshotElement,
)

PlaywrightTimeoutError = type(
    "TimeoutError",
    (TimeoutError,),
    {"__module__": "playwright.async_api"},
)


def _runtime(thread_id: str | None = "thread-1", outputs_path: str | None = None):
    state = {"thread_data": {"outputs_path": outputs_path}} if outputs_path is not None else {"thread_data": {}}
    return SimpleNamespace(context={"thread_id": thread_id}, state=state)


def _snapshot() -> PageSnapshot:
    return PageSnapshot(
        url="https://example.com/",
        title="Example",
        elements=[
            SnapshotElement(ref=1, tag="a", role="", type="", name="More info"),
            SnapshotElement(ref=2, tag="input", role="", type="text", name="Search"),
        ],
    )


class TestSnapshotRendering:
    def test_snapshot_lists_elements_by_ref(self):
        rendered = _snapshot().render()
        assert "URL: https://example.com/" in rendered
        assert "Title: Example" in rendered
        assert "[1] a: More info" in rendered
        assert "[2] input type=text: Search" in rendered

    def test_empty_snapshot_says_no_elements(self):
        snap = PageSnapshot(url="https://x.test/", title="X", elements=[])
        assert "No interactive elements detected." in snap.render()


@pytest.mark.asyncio
class TestBrowserTools:
    async def _patch_session(self, session):
        manager = MagicMock()
        manager.get_session.return_value = session
        return patch.object(tools, "get_browser_session_manager", return_value=manager), manager

    async def test_navigate_returns_snapshot(self):
        session = MagicMock()
        session.navigate = AsyncMock(return_value=_snapshot())
        ctx, manager = await self._patch_session(session)
        with ctx, patch.object(tools, "_get_tool_config", return_value={}):
            result = await tools.browser_navigate_tool.coroutine(
                runtime=_runtime(),
                url="https://example.com",
                tool_call_id="t1",
            )
        content = result.update["messages"][0].content
        assert "Navigated to https://example.com." in content
        assert "[1] a: More info" in content
        session.navigate.assert_awaited_once_with("https://example.com")
        manager.get_session.assert_called_once()

    async def test_navigate_emits_screenshot_artifact_and_browser_view(self, tmp_path):
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        session = MagicMock()
        session.navigate = AsyncMock(return_value=_snapshot())
        session.screenshot_bytes = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nshot")
        session.schedule_live_frames = MagicMock()
        ctx, _ = await self._patch_session(session)
        with ctx, patch.object(tools, "_get_tool_config", return_value={}):
            result = await tools.browser_navigate_tool.coroutine(
                runtime=_runtime(outputs_path=str(outputs)),
                url="https://example.com",
                tool_call_id="t1",
            )
        # Screenshot is captured, saved, exposed as an artifact + inline browser_view.
        session.screenshot_bytes.assert_awaited_once()
        session.schedule_live_frames.assert_called_once()
        artifact = result.update["artifacts"][0]
        assert artifact.startswith("/mnt/user-data/outputs/.browser-frames/browser-navigate-")
        assert artifact.endswith(".png")
        saved = list((outputs / ".browser-frames").glob("browser-navigate-*.png"))
        assert saved and saved[0].read_bytes() == b"\x89PNG\r\n\x1a\nshot"
        meta = result.update["messages"][0].additional_kwargs["browser_view"]
        assert meta["screenshot"] == artifact
        assert meta["url"] == "https://example.com/"
        assert meta["title"] == "Example"

    async def test_navigate_screenshot_failure_does_not_break_action(self, tmp_path):
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        session = MagicMock()
        session.navigate = AsyncMock(return_value=_snapshot())
        session.screenshot_bytes = AsyncMock(side_effect=RuntimeError("headless crashed"))
        ctx, _ = await self._patch_session(session)
        with ctx, patch.object(tools, "_get_tool_config", return_value={}):
            result = await tools.browser_navigate_tool.coroutine(
                runtime=_runtime(outputs_path=str(outputs)),
                url="https://example.com",
                tool_call_id="t1",
            )
        # Navigation result still returned; no artifact, no browser_view, no crash.
        assert "Navigated to https://example.com." in result.update["messages"][0].content
        assert "artifacts" not in result.update
        assert result.update["messages"][0].additional_kwargs == {}

    async def test_navigate_blocks_private_url(self):
        session = MagicMock()
        session.navigate = AsyncMock()
        ctx, _ = await self._patch_session(session)
        with ctx, patch.object(tools, "_get_tool_config", return_value={}):
            result = await tools.browser_navigate_tool.coroutine(
                runtime=_runtime(),
                url="http://169.254.169.254/latest/meta-data/",
                tool_call_id="t1",
            )
        assert "private, loopback, or metadata" in result.update["messages"][0].content
        session.navigate.assert_not_awaited()

    async def test_navigate_rejects_non_http_scheme(self):
        session = MagicMock()
        session.navigate = AsyncMock()
        ctx, _ = await self._patch_session(session)
        with ctx, patch.object(tools, "_get_tool_config", return_value={}):
            result = await tools.browser_navigate_tool.coroutine(
                runtime=_runtime(),
                url="file:///etc/passwd",
                tool_call_id="t1",
            )
        assert "Error" in result.update["messages"][0].content
        session.navigate.assert_not_awaited()

    async def test_click_returns_updated_snapshot(self):
        session = MagicMock()
        session.click = AsyncMock(return_value=_snapshot())
        ctx, _ = await self._patch_session(session)
        with ctx, patch.object(tools, "_get_tool_config", return_value={}):
            result = await tools.browser_click_tool.coroutine(runtime=_runtime(), ref=1, tool_call_id="t1")
        assert "Clicked element [1]." in result.update["messages"][0].content
        session.click.assert_awaited_once_with(1)

    async def test_click_error_is_recoverable_message(self):
        session = MagicMock()
        session.click = AsyncMock(side_effect=RuntimeError("no such element"))
        ctx, _ = await self._patch_session(session)
        with ctx, patch.object(tools, "_get_tool_config", return_value={}):
            result = await tools.browser_click_tool.coroutine(runtime=_runtime(), ref=9, tool_call_id="t1")
        assert result.update["messages"][0].content.startswith("Error: could not click element [9]")

    async def test_type_with_submit(self):
        session = MagicMock()
        session.type_text = AsyncMock(return_value=_snapshot())
        ctx, _ = await self._patch_session(session)
        with ctx, patch.object(tools, "_get_tool_config", return_value={}):
            result = await tools.browser_type_tool.coroutine(
                runtime=_runtime(),
                ref=2,
                text="hello",
                tool_call_id="t1",
                submit=True,
            )
        assert "Typed into element [2] and submitted." in result.update["messages"][0].content
        session.type_text.assert_awaited_once_with(2, "hello", submit=True)

    async def test_get_text_truncates_via_config(self):
        session = MagicMock()
        session.get_text = AsyncMock(return_value="body text")
        ctx, _ = await self._patch_session(session)
        with ctx, patch.object(tools, "_get_tool_config", return_value={"max_chars": 1234}):
            result = await tools.browser_get_text_tool.coroutine(runtime=_runtime(), tool_call_id="t1")
        assert result.update["messages"][0].content == "body text"
        session.get_text.assert_awaited_once_with(max_chars=1234)

    async def test_screenshot_writes_artifact(self, tmp_path):
        outputs = tmp_path / "outputs"
        outputs.mkdir()
        session = MagicMock()
        session.screenshot_bytes = AsyncMock(return_value=b"\x89PNG\r\n\x1a\npng-bytes")
        ctx, _ = await self._patch_session(session)
        with ctx, patch.object(tools, "_get_tool_config", return_value={}):
            result = await tools.browser_screenshot_tool.coroutine(
                runtime=_runtime(outputs_path=str(outputs)),
                tool_call_id="t1",
                filename="Login Page.png",
            )
        artifact = result.update["artifacts"][0]
        assert artifact == "/mnt/user-data/outputs/Login_Page.png"
        assert (outputs / "Login_Page.png").read_bytes() == b"\x89PNG\r\n\x1a\npng-bytes"

    async def test_screenshot_errors_without_outputs_path(self):
        session = MagicMock()
        session.screenshot_bytes = AsyncMock(return_value=b"x")
        ctx, _ = await self._patch_session(session)
        with ctx, patch.object(tools, "_get_tool_config", return_value={}):
            result = await tools.browser_screenshot_tool.coroutine(runtime=_runtime(), tool_call_id="t1")
        assert "outputs path is not available" in result.update["messages"][0].content
        session.screenshot_bytes.assert_not_awaited()

    async def test_close_reports_when_no_session(self):
        manager = MagicMock()
        manager.close_session = AsyncMock(return_value=False)
        with patch.object(tools, "get_browser_session_manager", return_value=manager):
            result = await tools.browser_close_tool.coroutine(runtime=_runtime(), tool_call_id="t1")
        assert "No active browser session" in result.update["messages"][0].content
        manager.close_session.assert_awaited_once_with("thread-1")


@pytest.mark.asyncio
async def test_wheel_input_scrolls_target_container_at_pointer_location():
    session = BrowserSession(
        MagicMock(),
        headless=True,
        timeout_ms=1000,
        viewport={"width": 1000, "height": 500},
    )
    page = MagicMock()
    page.mouse.move = AsyncMock()
    page.mouse.wheel = AsyncMock()
    page.evaluate = AsyncMock(return_value=True)
    session._ensure_page = AsyncMock(return_value=page)

    await session._dispatch_input(
        {"type": "wheel", "nx": 0.25, "ny": 0.75, "dx": 0, "dy": 240},
    )

    page.mouse.move.assert_awaited_once_with(250.0, 375.0)
    page.evaluate.assert_awaited_once()
    page.mouse.wheel.assert_not_awaited()


@pytest.mark.asyncio
async def test_wheel_input_scrolls_viewport_center_without_pointer_location():
    session = BrowserSession(
        MagicMock(),
        headless=True,
        timeout_ms=1000,
        viewport={"width": 1000, "height": 500},
    )
    page = MagicMock()
    page.mouse.move = AsyncMock()
    page.mouse.wheel = AsyncMock()
    page.evaluate = AsyncMock(return_value=True)
    session._ensure_page = AsyncMock(return_value=page)

    await session._dispatch_input({"type": "wheel", "dx": 0, "dy": 240})

    page.mouse.move.assert_not_awaited()
    page.evaluate.assert_awaited_once()
    _, payload = page.evaluate.await_args.args
    assert payload == {"x": 500.0, "y": 250.0, "dx": 0.0, "dy": 240.0}
    page.mouse.wheel.assert_not_awaited()


@pytest.mark.asyncio
async def test_wheel_input_falls_back_to_native_wheel_when_js_scroll_fails():
    session = BrowserSession(
        MagicMock(),
        headless=True,
        timeout_ms=1000,
        viewport={"width": 1000, "height": 500},
    )
    page = MagicMock()
    page.mouse.move = AsyncMock()
    page.mouse.wheel = AsyncMock()
    page.evaluate = AsyncMock(return_value=False)
    session._ensure_page = AsyncMock(return_value=page)

    await session._dispatch_input(
        {"type": "wheel", "nx": 0.25, "ny": 0.75, "dx": 0, "dy": 240},
    )

    page.mouse.move.assert_awaited_once_with(250.0, 375.0)
    page.evaluate.assert_awaited_once()
    page.mouse.wheel.assert_awaited_once_with(0.0, 240.0)


@pytest.mark.asyncio
async def test_live_frame_returns_base64_jpeg_screenshot():
    session = BrowserSession(
        MagicMock(),
        headless=True,
        timeout_ms=1000,
        viewport={"width": 1000, "height": 500},
    )
    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"\xff\xd8jpeg-bytes")
    session._ensure_page = AsyncMock(return_value=page)

    frame = await session._live_frame()

    assert frame == "/9hq cGVnLWJ5dGVz".replace(" ", "")
    page.screenshot.assert_awaited_once_with(type="jpeg", quality=_LIVE_FRAME_JPEG_QUALITY)


@pytest.mark.asyncio
async def test_click_fast_fails_on_stale_ref_without_blocking():
    """A ref missing after a re-render fails immediately with a re-snapshot hint.

    Previously ``page.click`` on a missing selector blocked for the 30s session
    default; here the locator reports count 0 so we raise straight away and never
    attempt the click, letting the model re-snapshot instead of stalling the loop.
    """
    session = BrowserSession(
        MagicMock(),
        headless=True,
        timeout_ms=30000,
        viewport={"width": 1000, "height": 500},
    )
    locator = MagicMock()
    locator.count = AsyncMock(return_value=0)
    page = MagicMock()
    page.locator = MagicMock(return_value=locator)
    session._ensure_page = AsyncMock(return_value=page)

    with pytest.raises(RuntimeError, match="no longer on the page"):
        await session._click(7)

    page.locator.assert_called_once_with('[data-df-ref="7"]')


@pytest.mark.asyncio
async def test_click_tolerates_spa_navigation_without_load_event():
    """A client-side click that never fires a load event still returns a snapshot.

    The post-click settle wait is best-effort: a Playwright timeout on
    ``wait_for_load_state`` must be swallowed so SPA navigations (which never emit
    a fresh load event) still yield the updated snapshot instead of raising.
    """
    session = BrowserSession(
        MagicMock(),
        headless=True,
        timeout_ms=30000,
        viewport={"width": 1000, "height": 500},
    )
    first = MagicMock()
    first.scroll_into_view_if_needed = AsyncMock()
    first.click = AsyncMock()
    locator = MagicMock()
    locator.count = AsyncMock(return_value=1)
    locator.first = first
    page = MagicMock()
    page.locator = MagicMock(return_value=locator)
    page.wait_for_load_state = AsyncMock(side_effect=PlaywrightTimeoutError("no load"))
    session._ensure_page = AsyncMock(return_value=page)
    session._snapshot_impl = AsyncMock(return_value=_snapshot())

    result = await session._click(1)

    assert result.url == "https://example.com/"
    first.click.assert_awaited_once()
    page.wait_for_load_state.assert_awaited_once()
    session._snapshot_impl.assert_awaited_once_with(page)


@pytest.mark.asyncio
async def test_tabs_report_active_page():
    session = BrowserSession(
        MagicMock(),
        headless=True,
        timeout_ms=1000,
        viewport={"width": 1000, "height": 500},
    )
    page_a = MagicMock()
    page_a.is_closed.return_value = False
    page_a.title = AsyncMock(return_value="A")
    page_a.url = "https://a.example/"
    page_b = MagicMock()
    page_b.is_closed.return_value = False
    page_b.title = AsyncMock(return_value="B")
    page_b.url = "https://b.example/"
    session._context = SimpleNamespace(pages=[page_a, page_b])
    session._page = page_b
    session._ensure_page = AsyncMock(return_value=page_b)

    tabs = await session._tabs()

    assert [(tab.index, tab.title, tab.url, tab.active) for tab in tabs] == [
        (0, "A", "https://a.example/", False),
        (1, "B", "https://b.example/", True),
    ]


@pytest.mark.asyncio
async def test_activate_tab_switches_active_page():
    session = BrowserSession(
        MagicMock(),
        headless=True,
        timeout_ms=1000,
        viewport={"width": 1000, "height": 500},
    )
    page_a = MagicMock()
    page_a.is_closed.return_value = False
    page_b = MagicMock()
    page_b.is_closed.return_value = False
    page_b.bring_to_front = AsyncMock()
    session._context = SimpleNamespace(pages=[page_a, page_b])
    session._page = page_a
    session._ensure_page = AsyncMock(return_value=page_a)

    await session._activate_tab(1)

    assert session._page is page_b
    page_b.bring_to_front.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_active_page_schedules_rebind_when_screencast_running():
    """Switching the active page while streaming must rebind the screencast.

    The frame source follows ``self._page``; the CDP repaint signal is bound to a
    specific page, so a page switch has to reschedule the bind or new-page
    repaints stop driving frames (the exact drift that froze Live on the old
    page). No rebind should be scheduled when the page is unchanged or idle.
    """
    session = BrowserSession(
        MagicMock(),
        headless=True,
        timeout_ms=1000,
        viewport={"width": 1000, "height": 500},
    )
    old_page = MagicMock()
    new_page = MagicMock()
    session._screencast_page = old_page
    rebind = AsyncMock()
    session._rebind_screencast_safe = rebind

    # Not streaming yet: no rebind.
    session._set_active_page(new_page)
    assert session._page is new_page
    rebind.assert_not_called()

    # Streaming and the page actually changed: schedule exactly one rebind.
    session._on_frame = lambda _data: None
    another_page = MagicMock()
    session._set_active_page(another_page)
    await asyncio.sleep(0)  # let the scheduled task run
    rebind.assert_awaited_once()

    # Same page again: no extra rebind.
    session._screencast_page = another_page
    rebind.reset_mock()
    session._set_active_page(another_page)
    await asyncio.sleep(0)
    rebind.assert_not_called()


@pytest.mark.asyncio
async def test_live_frame_screenshots_current_active_page_after_switch():
    """A live frame captures ``self._page``, not a stale handle.

    ``_live_frame`` and the screencast both screenshot the active page, so after
    the active page is reassigned (tab/popup handoff) the next frame reflects the
    new page — keeping Live aligned with the address bar and snapshot, which also
    track ``self._page``.
    """
    session = BrowserSession(
        MagicMock(),
        headless=True,
        timeout_ms=1000,
        viewport={"width": 1000, "height": 500},
    )
    new_page = MagicMock()
    new_page.screenshot = AsyncMock(return_value=b"\xff\xd8new-page")
    session._ensure_page = AsyncMock(return_value=new_page)

    frame = await session._live_frame()

    new_page.screenshot.assert_awaited_once_with(type="jpeg", quality=_LIVE_FRAME_JPEG_QUALITY)
    assert frame  # base64 payload of the new page


@pytest.mark.asyncio
async def test_stop_screencast_ignores_stale_connection_callback():
    """A closing old WebSocket must not clear the newer Live frame callback."""
    session = BrowserSession(
        MagicMock(),
        headless=True,
        timeout_ms=1000,
        viewport={"width": 1000, "height": 500},
    )

    def old_frame(_data: str) -> None:
        pass

    def new_frame(_data: str) -> None:
        pass

    session._on_frame = new_frame

    await session._stop_screencast(old_frame)

    assert session._on_frame is new_frame

    await session._stop_screencast(new_frame)

    assert session._on_frame is None


@pytest.mark.asyncio
class TestSessionManager:
    async def test_get_session_is_per_thread_and_cached(self):
        manager = BrowserSessionManager()
        fake_loop = MagicMock()
        with patch.object(manager, "_ensure_loop", return_value=fake_loop):
            a1 = manager.get_session("thread-a")
            a2 = manager.get_session("thread-a")
            b1 = manager.get_session("thread-b")
        assert a1 is a2
        assert a1 is not b1

    async def test_close_session_removes_and_closes(self):
        manager = BrowserSessionManager()
        fake_loop = MagicMock()
        with patch.object(manager, "_ensure_loop", return_value=fake_loop):
            session = manager.get_session("thread-a")
        session.close = AsyncMock()
        assert await manager.close_session("thread-a") is True
        session.close.assert_awaited_once()
        # Second close is a no-op because the session was dropped.
        assert await manager.close_session("thread-a") is False


def test_reset_manager_singleton():
    first = session_mod.get_browser_session_manager()
    session_mod.reset_browser_session_manager()
    second = session_mod.get_browser_session_manager()
    assert first is not second


@pytest.mark.asyncio
async def test_real_playwright_navigate_click_type():
    """End-to-end check against real headless Chromium (skipped if unavailable)."""
    pytest.importorskip("playwright.async_api")

    session_mod.reset_browser_session_manager()
    manager = session_mod.get_browser_session_manager()
    session = manager.get_session("it-thread", headless=True, timeout_ms=15000)

    html = "data:text/html,<h1>Hi</h1><input id='q' name='q' placeholder='Search'><button onclick=\"document.getElementById('out').innerText='clicked'\">Go</button><div id='out'></div>"
    try:
        snap = await session.navigate(html)
        assert any(el.tag == "input" for el in snap.elements)
        button = next(el for el in snap.elements if "Go" in el.name)
        after_click = await session.click(button.ref)
        assert after_click.url  # still a valid snapshot

        input_ref = next(el for el in snap.elements if el.tag == "input").ref
        typed = await session.type_text(input_ref, "hello", submit=False)
        assert typed.url

        text = await session.get_text()
        assert "clicked" in text

        shot = await session.screenshot_bytes()
        assert shot[:4] == b"\x89PNG"
    finally:
        await manager.close_session("it-thread")
        session_mod.reset_browser_session_manager()
