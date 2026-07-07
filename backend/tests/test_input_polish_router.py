import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.gateway.routers import input_polish


def _config(
    *,
    enabled: bool = True,
    max_chars: int = 4000,
    model_name: str | None = None,
):
    return SimpleNamespace(
        input_polish=SimpleNamespace(
            enabled=enabled,
            max_chars=max_chars,
            model_name=model_name,
        ),
    )


def test_clean_rewritten_text_removes_think_and_fence():
    text = "<think>reasoning</think>\n```text\nrewrite this\n```"
    assert input_polish._clean_rewritten_text(text) == "rewrite this"


def test_polish_input_uses_config_model_and_preserves_response(monkeypatch):
    request = input_polish.InputPolishRequest(
        text="/web-dev 做一个页面",
        locale="zh-CN",
        thread_id="thread-1",
    )
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock(return_value=MagicMock(content="/web-dev 请设计并实现一个视觉精致的页面。"))

    create_chat_model = MagicMock(return_value=fake_model)
    monkeypatch.setattr(input_polish, "create_chat_model", create_chat_model)
    config = _config(model_name="polish-model")

    result = asyncio.run(
        input_polish.polish_input.__wrapped__(
            request,
            request=None,
            config=config,
        ),
    )

    assert result.rewritten_text == "/web-dev 请设计并实现一个视觉精致的页面。"
    assert result.changed is True
    create_chat_model.assert_called_once_with(
        name="polish-model",
        thinking_enabled=False,
        app_config=config,
    )
    fake_model.ainvoke.assert_awaited_once()
    assert fake_model.ainvoke.await_args.kwargs["config"]["run_name"] == "input_polish"


def test_polish_input_uses_default_model_when_config_model_is_missing(monkeypatch):
    request = input_polish.InputPolishRequest(text="make this clearer")
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock(return_value=MagicMock(content="Make this clearer."))

    create_chat_model = MagicMock(return_value=fake_model)
    monkeypatch.setattr(input_polish, "create_chat_model", create_chat_model)

    result = asyncio.run(
        input_polish.polish_input.__wrapped__(
            request,
            request=None,
            config=_config(model_name=None),
        ),
    )

    assert result.rewritten_text == "Make this clearer."
    create_chat_model.assert_called_once()
    assert create_chat_model.call_args.kwargs["name"] is None


def test_polish_input_returns_404_when_disabled(monkeypatch):
    request = input_polish.InputPolishRequest(text="hello")
    fake_model = MagicMock()
    monkeypatch.setattr(input_polish, "create_chat_model", fake_model)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            input_polish.polish_input.__wrapped__(
                request,
                request=None,
                config=_config(enabled=False),
            ),
        )

    assert exc_info.value.status_code == 404
    fake_model.assert_not_called()


def test_polish_input_rejects_empty_or_too_long_input(monkeypatch):
    fake_model = MagicMock()
    monkeypatch.setattr(input_polish, "create_chat_model", fake_model)

    with pytest.raises(HTTPException) as empty_exc:
        asyncio.run(
            input_polish.polish_input.__wrapped__(
                input_polish.InputPolishRequest(text="  "),
                request=None,
                config=_config(),
            ),
        )
    assert empty_exc.value.status_code == 400

    with pytest.raises(HTTPException) as long_exc:
        asyncio.run(
            input_polish.polish_input.__wrapped__(
                input_polish.InputPolishRequest(text="hello"),
                request=None,
                config=_config(max_chars=4),
            ),
        )
    assert long_exc.value.status_code == 400
    fake_model.assert_not_called()


def test_polish_input_returns_503_on_model_error(monkeypatch):
    request = input_polish.InputPolishRequest(text="hello")
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(input_polish, "create_chat_model", MagicMock(return_value=fake_model))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            input_polish.polish_input.__wrapped__(
                request,
                request=None,
                config=_config(),
            ),
        )

    assert exc_info.value.status_code == 503
