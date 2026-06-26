"""
AskUserQuestion 工具 — AI 主动向用户提问并等待回答.

对齐 openclaw-lark src/tools/ask-user-question.ts.
流程（非阻塞）：
  1. AI 调用 AskUserQuestion 工具，传入问题和选项
  2. 发送 form 交互式飞书卡片（含多选/单选/文本输入）
  3. 工具立即返回 { status: 'pending' }
  4. 用户填写表单并点击提交
  5. 解析答案，通过回调注入 synthetic message
  6. AI 在新一轮对话中收到用户答案
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import OrderedDict
from typing import Any, Callable

logger = logging.getLogger("clawhermes.lark.tools.ask_user")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTION_SUBMIT = "ask_user_submit"
PENDING_QUESTION_TTL_MS = 5 * 60 * 1000  # 5 minutes
INPUT_FIELD_NAME = "answer"
SELECT_FIELD_NAME = "selection"

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class QuestionItem:
    """单个问题定义."""
    __slots__ = ("question", "header", "options", "multi_select", "select_style")

    def __init__(
        self,
        question: str,
        header: str = "",
        options: list[dict] | None = None,
        multi_select: bool = False,
        select_style: str = "dropdown",
    ):
        self.question = question
        self.header = header or question[:50]
        self.options = options or []
        self.multi_select = multi_select
        self.select_style = select_style


class QuestionContext:
    """等待用户回答的问题上下文."""
    __slots__ = (
        "question_id", "chat_id", "account_id", "sender_open_id",
        "card_message_id", "questions", "thread_id", "chat_type",
        "submitted", "created_at", "on_answer",
    )

    def __init__(
        self,
        question_id: str,
        chat_id: str,
        account_id: str = "default",
        sender_open_id: str = "",
        card_message_id: str = "",
        questions: list[QuestionItem] | None = None,
        thread_id: str | None = None,
        chat_type: str = "p2p",
        on_answer: Callable | None = None,
    ):
        self.question_id = question_id
        self.chat_id = chat_id
        self.account_id = account_id
        self.sender_open_id = sender_open_id
        self.card_message_id = card_message_id
        self.questions = questions or []
        self.thread_id = thread_id
        self.chat_type = chat_type
        self.submitted = False
        self.created_at = asyncio.get_event_loop().time()
        self.on_answer = on_answer

    @property
    def is_expired(self) -> bool:
        loop = asyncio.get_event_loop()
        now = loop.time()
        return (now - self.created_at) * 1000 > PENDING_QUESTION_TTL_MS


# ---------------------------------------------------------------------------
# Pending Question Registry
# ---------------------------------------------------------------------------

_pending_questions: dict[str, QuestionContext] = {}
_lock: asyncio.Lock | None = None  # lazy init to avoid requiring event loop at import time



def _get_lock() -> asyncio.Lock:
    """Get or lazily create the async lock."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def _register_question(ctx: QuestionContext) -> None:
    async with _get_lock():
        _pending_questions[ctx.question_id] = ctx


async def _unregister_question(question_id: str) -> QuestionContext | None:
    async with _get_lock():
        return _pending_questions.pop(question_id, None)


async def _get_question(question_id: str) -> QuestionContext | None:
    async with _get_lock():
        ctx = _pending_questions.get(question_id)
        if ctx and ctx.is_expired:
            _pending_questions.pop(question_id, None)
            return None
        return ctx


# ---------------------------------------------------------------------------
# Card Builder
# ---------------------------------------------------------------------------


def _build_question_card(
    question_id: str,
    questions: list[QuestionItem],
    title: str = "请回答以下问题",
) -> dict[str, Any]:
    """构建提问 form 交互卡片."""
    elements: list[dict] = []

    for i, q in enumerate(questions):
        # Question header
        elements.append({
            "tag": "markdown",
            "content": f"**{i + 1}. {q.question}**",
        })

        if q.options:
            # Multiple choice — dropdown or checkbox
            option_list = [
                {"text": {"tag": "plain_text", "content": opt.get("label", "")},
                 "value": opt.get("value", opt.get("label", ""))}
                for opt in q.options
            ]

            elements.append({
                "tag": "select_static",
                "placeholder": {"tag": "plain_text", "content": "请选择…"},
                "options": option_list,
                "value": {SELECT_FIELD_NAME: q.header},
            })
        else:
            # Free text input
            elements.append({
                "tag": "input",
                "placeholder": {"tag": "plain_text", "content": "输入你的回答…"},
                "value": {INPUT_FIELD_NAME: q.header},
            })

        if i < len(questions) - 1:
            elements.append({"tag": "hr"})

    # Submit button
    elements.append({
        "tag": "action",
        "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "✓ 提交"},
            "type": "primary",
            "value": {"action": ACTION_SUBMIT, "question_id": question_id},
        }],
    })

    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue",
        },
        "elements": elements,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def ask_user_question(
    adapter: Any,
    chat_id: str,
    questions: list[QuestionItem],
    sender_open_id: str = "",
    account_id: str = "default",
    thread_id: str | None = None,
    title: str = "请回答以下问题",
    on_answer: Callable | None = None,
) -> dict[str, Any]:
    """
    向用户发送提问卡片，等待回答.

    Args:
        adapter: LarkAdapter 实例（用于发送卡片）
        chat_id: 目标 chat_id
        questions: 问题列表
        sender_open_id: 提问对象 open_id（用于权限校验）
        account_id: 账户 ID
        thread_id: 话题 ID
        title: 卡片标题
        on_answer: 用户回答后的回调函数 callback(ctx: QuestionContext, answers: dict)

    Returns:
        {"status": "pending", "question_id": "..."}
        或 {"status": "error", "message": "..."}
    """
    question_id = str(uuid.uuid4())[:12]

    # Build card
    card = _build_question_card(question_id, questions, title)

    # Send card
    try:
        card_content = json.dumps(card, ensure_ascii=False)
        msg_id = await adapter._send_card_message(
            chat_id=chat_id,
            card=card,
        )

        if not msg_id:
            return {"status": "error", "message": "Failed to send question card"}

    except Exception as e:
        logger.exception("Failed to send question card")
        return {"status": "error", "message": str(e)}

    # Register pending question
    ctx = QuestionContext(
        question_id=question_id,
        chat_id=chat_id,
        account_id=account_id,
        sender_open_id=sender_open_id,
        card_message_id=msg_id,
        questions=questions,
        thread_id=thread_id,
        on_answer=on_answer,
    )
    await _register_question(ctx)

    logger.info("Question sent: id=%s chat=%s", question_id, chat_id[:20])

    return {"status": "pending", "question_id": question_id}


async def handle_ask_user_action(
    data: Any,
    account_id: str = "default",
) -> dict[str, Any] | None:
    """
    处理用户提交的提问卡片回调.

    应由 interactive dispatcher 在 card.action.trigger 时调用.

    Returns:
        None 表示不是 AskUserQuestion 的提交（交给其他 handler）
        或 {"status": "answered", "answers": {...}}
    """
    try:
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            return None

        action_obj = data.get("action", {})
        action_value = action_obj.get("value", {})
        if not isinstance(action_value, dict):
            return None

        action = action_value.get("action", "")
        if action != ACTION_SUBMIT:
            return None

        question_id = action_value.get("question_id", "")
        if not question_id:
            return None

    except Exception:
        return None

    # Find the pending question
    ctx = await _get_question(question_id)
    if not ctx or ctx.submitted:
        return None

    ctx.submitted = True
    await _unregister_question(question_id)

    # Extract answers
    form_values = data.get("form_value", {})
    answers: dict[str, str] = {}

    for q in ctx.questions:
        key = q.header
        val = form_values.get(key, "")
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val)
        answers[q.question] = str(val)

    # Call user callback
    if ctx.on_answer:
        try:
            result = ctx.on_answer(ctx, answers)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("on_answer callback error")

    # Return toast
    return {
        "status": "answered",
        "answers": answers,
        "toast": {"type": "success", "content": "回答已提交 ✓"},
    }


def get_pending_question_count() -> int:
    """获取当前等待回答的问题数."""
    return len(_pending_questions)
