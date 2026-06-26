"""
消息格式转换器 — 对齐 openclaw-lark src/messaging/converters/

飞书消息格式互转：
  - text → post (纯文本 → 富文本)
  - post → text (富文本 → 纯文本，内容提取)
  - text → interactive card (纯文本 → 卡片)
  - interactive → text (卡片 → 纯文本)

用于消息接收和发送时的格式适配.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("clawhermes.lark.converter")


# ---------------------------------------------------------------------------
# Text → Post
# ---------------------------------------------------------------------------


def text_to_post(text: str, title: str = "") -> str:
    """
    将纯文本转换为飞书 Post 格式 JSON.

    按段落拆分，每段落作为 post 的一个 content 块.
    """
    paragraphs = text.split("\n\n")
    content_list: list[list[dict]] = []

    for para in paragraphs:
        if para.strip():
            content_list.append([{"tag": "text", "text": para.strip()}])

    payload: dict[str, Any] = {
        "zh_cn": {
            "title": title,
            "content": content_list,
        }
    }

    return json.dumps(payload, ensure_ascii=False)


def text_to_post_md(text: str) -> str:
    """
    将 Markdown 文本转换为飞书 Post-MD 格式（tag: md).

    Post-MD 支持粗体、斜体、代码块、链接等 Markdown 子集.
    整个文本放在一个 md 元素中.
    """
    payload = {
        "zh_cn": {
            "content": [[{"tag": "md", "text": text}]],
        }
    }
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Post → Text
# ---------------------------------------------------------------------------


def post_to_text(post_json: str | dict) -> str:
    """从 Post 格式 JSON 中提取纯文本."""
    if isinstance(post_json, str):
        try:
            body = json.loads(post_json)
        except json.JSONDecodeError:
            return post_json
    else:
        body = post_json

    if not isinstance(body, dict):
        return str(body)

    texts: list[str] = []

    # 遍历所有 locale
    for locale_key in ("zh_cn", "en_us", "ja_jp"):
        locale_data = body.get(locale_key, {})
        if not isinstance(locale_data, dict):
            continue

        content = locale_data.get("content", [])
        for paragraph in content:
            if not isinstance(paragraph, list):
                continue
            para_texts = []
            for element in paragraph:
                if not isinstance(element, dict):
                    continue
                tag = element.get("tag", "")
                text = element.get("text", "")
                if tag == "text":
                    para_texts.append(text)
                elif tag == "a":
                    para_texts.append(text or element.get("href", ""))
                elif tag == "at":
                    para_texts.append(f"@{element.get('user_name', element.get('user_id', ''))}")
                elif tag == "img":
                    para_texts.append(f"[Image: {element.get('image_key', '')}]")
            if para_texts:
                texts.append("".join(para_texts))

        if texts:
            break  # 只取第一个有内容的 locale

    return "\n".join(texts)


# ---------------------------------------------------------------------------
# Text → Interactive Card
# ---------------------------------------------------------------------------


def text_to_card(text: str, title: str = "", header_template: str = "wathet") -> dict:
    """将纯文本转换为简单的交互式卡片."""
    elements: list[dict] = []

    # 尝试检测 Markdown 并智能分段
    if "\n\n" in text:
        paragraphs = text.split("\n\n")
        for para in paragraphs:
            if para.strip():
                elements.append({"tag": "markdown", "content": para.strip()})
    else:
        elements.append({"tag": "markdown", "content": text})

    card: dict[str, Any] = {
        "config": {"wide_screen_mode": True},
    }

    if title:
        card["header"] = {
            "title": {"tag": "plain_text", "content": title},
            "template": header_template,
        }

    card["elements"] = elements
    return card


# ---------------------------------------------------------------------------
# Interactive Card → Text
# ---------------------------------------------------------------------------


def card_to_text(card: dict) -> str:
    """从交互式卡片中提取纯文本内容."""
    elements = card.get("elements", [])
    if not elements and "body" in card:
        elements = card["body"].get("elements", [])

    texts: list[str] = []
    for elem in elements:
        tag = elem.get("tag", "")
        if tag == "markdown":
            texts.append(elem.get("content", ""))
        elif tag == "div":
            text_elem = elem.get("text", {})
            if isinstance(text_elem, dict):
                texts.append(text_elem.get("content", ""))
        elif tag == "plain_text":
            texts.append(elem.get("content", ""))
        elif tag == "note":
            for sub in elem.get("elements", []):
                if isinstance(sub, dict):
                    texts.append(sub.get("content", ""))

    return "\n".join(filter(None, texts))


# ---------------------------------------------------------------------------
# Multi-locale Post Builder
# ---------------------------------------------------------------------------


def build_multi_locale_post(
    texts: dict[str, str],
    tag: str = "md",
) -> str:
    """
    构建多语言 Post 格式消息.

    Args:
        texts: {"zh_cn": "你好", "en_us": "Hello", ...}
        tag: 元素标签 ("md" | "text")

    Returns:
        Post 格式 JSON 字符串
    """
    post_body: dict[str, Any] = {}
    for locale, text in texts.items():
        if text:
            post_body[locale] = {
                "content": [[{"tag": tag, "text": text}]],
            }

    return json.dumps(post_body, ensure_ascii=False)
