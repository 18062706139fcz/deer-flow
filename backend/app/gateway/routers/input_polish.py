import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

import deerflow.utils.llm_text as llm_text
from app.gateway.authz import require_permission
from app.gateway.deps import get_config
from deerflow.config.app_config import AppConfig
from deerflow.models import create_chat_model
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.tracing import inject_langfuse_metadata

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["input-polish"])


class InputPolishRequest(BaseModel):
    text: str = Field(..., description="Draft text currently shown in the composer")
    locale: str | None = Field(default=None, description="Optional UI locale hint")
    thread_id: str | None = Field(default=None, description="Optional thread id for tracing only")


class InputPolishResponse(BaseModel):
    rewritten_text: str = Field(..., description="Polished draft text")
    changed: bool = Field(..., description="Whether the model changed the original draft")


_extract_response_text = llm_text.extract_response_text
_strip_markdown_code_fence = llm_text.strip_markdown_code_fence
_strip_think_blocks = llm_text.strip_think_blocks


def _clean_rewritten_text(text: str) -> str:
    candidate = _strip_think_blocks(text)
    candidate = _strip_markdown_code_fence(candidate)
    return candidate.strip()


def _build_system_instruction() -> str:
    return (
        "You are DeerFlow's pre-send prompt optimizer.\n"
        "Rewrite the user's rough draft into a clearer instruction for an AI agent before it is sent.\n"
        "Do not answer the task.\n"
        "Preserve the user's language, intent, entities, file paths, URLs, code blocks, and any leading slash command prefix exactly.\n"
        "Improve the draft by making the goal, scope, constraints, and desired output explicit when they are implied by the draft.\n"
        "For vague quality words such as 'better', 'good-looking', or 'polished', translate them into concrete but generic quality criteria.\n"
        "Do not invent facts, business context, tools, file names, dates, metrics, or user preferences that are not implied.\n"
        "Prefer one concise paragraph or a short bullet list. Keep it under 180 words unless the original draft is longer.\n"
        "Output only the rewritten draft, with no markdown wrapper, explanation, or alternatives."
    )


def _build_user_content(text: str, locale: str | None) -> str:
    locale_hint = locale.strip() if locale else "same language as the draft"
    return f"Locale hint: {locale_hint}\n\nRewrite this draft while preserving its intent:\n<draft>\n{text}\n</draft>"


@router.post(
    "/input-polish",
    response_model=InputPolishResponse,
    summary="Polish Composer Input",
    description="Rewrite a draft message before it is sent. This does not create a thread run or persist any message.",
)
@require_permission("runs", "create")
async def polish_input(
    body: InputPolishRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
) -> InputPolishResponse:
    del request  # Required by the auth decorator.

    if not config.input_polish.enabled:
        raise HTTPException(status_code=404, detail="Input polishing is disabled")

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Input text is required")

    max_chars = config.input_polish.max_chars
    if len(body.text) > max_chars:
        raise HTTPException(status_code=400, detail=f"Input text exceeds {max_chars} characters")

    model_name = config.input_polish.model_name
    try:
        model = create_chat_model(name=model_name, thinking_enabled=False, app_config=config)
        invoke_config: dict = {"run_name": "input_polish"}
        inject_langfuse_metadata(
            invoke_config,
            thread_id=body.thread_id,
            user_id=get_effective_user_id(),
            assistant_id="input_polish",
            model_name=model_name,
            environment=os.environ.get("DEER_FLOW_ENV") or os.environ.get("ENVIRONMENT"),
        )
        response = await model.ainvoke(
            [
                SystemMessage(content=_build_system_instruction()),
                HumanMessage(content=_build_user_content(body.text, body.locale)),
            ],
            config=invoke_config,
        )
        rewritten = _clean_rewritten_text(_extract_response_text(response.content))
    except Exception as exc:
        logger.exception("Failed to polish input: thread_id=%s err=%s", body.thread_id, exc)
        raise HTTPException(status_code=503, detail="Failed to polish input") from exc

    if not rewritten:
        raise HTTPException(status_code=503, detail="Failed to polish input")

    return InputPolishResponse(
        rewritten_text=rewritten,
        changed=rewritten.strip() != body.text.strip(),
    )
