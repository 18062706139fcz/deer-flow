from app.gateway.routers.browser import _should_apply_browser_seed


def test_browser_stream_seed_applies_to_blank_page():
    assert _should_apply_browser_seed("about:blank", "https://github.com/bytedance/deer-flow")


def test_browser_stream_seed_applies_when_current_url_differs():
    assert _should_apply_browser_seed(
        "https://docs.byteplus.com/en/docs/InfoQuest/What_is_Info_Quest",
        "https://github.com/bytedance/deer-flow",
    )


def test_browser_stream_seed_ignores_hash_and_trailing_slash_for_same_page():
    assert not _should_apply_browser_seed(
        "https://github.com/bytedance/deer-flow/#readme",
        "https://github.com/bytedance/deer-flow/",
    )


def test_browser_frames_dirname_shared_between_tools_and_scanner():
    """The screenshots dir name must stay identical in the writer and the scanner.

    Both sides import the single ``BROWSER_FRAMES_DIRNAME`` constant; this locks
    that they resolve to the same value so the workspace-changes ignore cannot
    silently drift away from where the browser tools write frames.
    """
    from deerflow.community.browser_automation import tools as browser_tools
    from deerflow.constants import BROWSER_FRAMES_DIRNAME
    from deerflow.workspace_changes.scanner import EXCLUDED_DIR_NAMES

    assert browser_tools._BROWSER_FRAMES_DIRNAME == BROWSER_FRAMES_DIRNAME
    assert BROWSER_FRAMES_DIRNAME in EXCLUDED_DIR_NAMES


def test_validate_browser_url_rejects_private_and_non_http(monkeypatch):
    """WS seed / navigate events reuse the same SSRF policy as the agent tools.

    With no ``allow_private_addresses`` override the shared validator must reject
    loopback / metadata / non-http targets, so the live stream cannot be steered
    at internal infrastructure.
    """
    from deerflow.community.browser_automation import tools as browser_tools
    from deerflow.community.browser_automation import validate_browser_url

    # Isolate from any local config.yaml that may set allow_private_addresses.
    monkeypatch.setattr(browser_tools, "_get_tool_config", lambda _tool_name: {})

    assert validate_browser_url("http://169.254.169.254/latest/meta-data/") is not None
    assert validate_browser_url("http://127.0.0.1:8001/") is not None
    assert validate_browser_url("file:///etc/passwd") is not None
    assert validate_browser_url("ftp://example.com") is not None
    # A normal public URL passes (returns None = allowed).
    assert validate_browser_url("https://github.com/bytedance/deer-flow") is None
