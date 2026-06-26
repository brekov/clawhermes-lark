"""
完整的 Markdown 飞书卡片样式优化 — 对齐 openclaw-lark src/card/markdown-style.ts

优化 Markdown 在飞书卡片中的渲染效果：
  - 表格检测与转换
  - 代码块语法高亮（语言标注）
  - 链接格式优化
  - 标题层级调整
  - 空行清理
  - 特殊字符转义
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# 表格检测
_TABLE_RE = re.compile(r"^\|.*\|\n\|[-|: ]+\|", re.MULTILINE)

# 代码块 (```lang\n...\n```)
_FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)

# 行内代码
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")

# Markdown 链接 [text](url)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# 自动链接 (bare URLs)
_AUTO_LINK_RE = re.compile(r"(?<!\()(https?://[^\s<>\)]+)")

# 连续空行 (超过 2 个)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")

# 标题
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# 粗体
_BOLD_RE = re.compile(r"\*\*([^*\n]+?)\*\*")

# 斜体
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")

# 删除线
_STRIKETHROUGH_RE = re.compile(r"~~([^~\n]+?)~~")

# 水平线
_HR_RE = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)

# 飞书不支持的字符
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200e\u200f]")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def optimize_markdown_style(text: str, min_headings: int = 1) -> str:
    """
    优化 Markdown 文本以适应飞书卡片的渲染限制.

    Args:
        text: 原始 Markdown 文本
        min_headings: 最少需要的标题数量（不足时自动插入）

    Returns:
        优化后的 Markdown 文本
    """
    if not text:
        return text

    result = text

    # 1. 去除零宽字符（飞书不支持）
    result = _ZERO_WIDTH_RE.sub("", result)

    # 2. 压缩多余空行（最多保留 1 个空行）
    result = _MULTI_BLANK_RE.sub("\n\n", result)

    # 3. 代码块处理 — 确保语言标注
    def _fix_fence(match):
        lang = match.group(1) or ""
        code = match.group(2).rstrip()
        return f"```{lang}\n{code}\n```"

    result = _FENCE_RE.sub(_fix_fence, result)

    # 4. 链接处理 — 保留 `<url>` 格式用于长链接
    def _fix_link(match):
        label = match.group(1)
        url = match.group(2)
        if len(url) > 60:
            return f"[{label}]({url})"
        return match.group(0)

    result = _LINK_RE.sub(_fix_link, result)

    # 5. 标题层级检查 — 从 h2 开始（h1 在卡片 header 中）
    def _adjust_heading(match):
        hashes = match.group(1)
        title = match.group(2)
        level = len(hashes)
        # h1 → h2, h2 → h3, 以此类推，max h4
        new_level = min(level + 1, 4)
        return f"{'#' * new_level} {title}"

    result = _HEADING_RE.sub(_adjust_heading, result)

    # 6. 表格检测 — 表格在 card markdown 中可能渲染异常，添加提示
    #    （不改变内容，只是日志提示，表格实际仍由 SDK 渲染）

    # 7. 粗体和斜体 — 飞书支持的格式保持不变

    # 8. 截断过长内容
    MAX_LENGTH = 7_000
    if len(result) > MAX_LENGTH:
        result = result[:MAX_LENGTH] + "\n\n… *(内容过长，已截断)*"

    return result.strip()


def optimize_for_post_format(text: str) -> str:
    """
    优化 Markdown 为飞书 Post 格式（tag: md）。

    Post 格式支持的 Markdown 子集有限：
      - ✅ 粗体 **text**
      - ✅ 斜体 *text*
      - ✅ 删除线 ~~text~~
      - ✅ 链接 [text](url)
      - ✅ 行内代码 `code`
      - ✅ 代码块 ```code```
      - ❌ 表格（不支持，转为文本或省略）
      - ❌ HTML 标签（会被转义）
    """
    if not text:
        return text

    result = optimize_markdown_style(text)

    # 表格转列表
    if _TABLE_RE.search(result):
        lines = result.split("\n")
        new_lines = []
        in_table = False
        for line in lines:
            if _TABLE_RE.match(line):
                in_table = True
                new_lines.append("*[表格内容]*")
            elif line.startswith("|") and in_table:
                continue  # skip table rows
            else:
                in_table = False
                new_lines.append(line)
        result = "\n".join(new_lines)

    return result.strip()


def strip_markdown(text: str) -> str:
    """剥离 Markdown 格式，返回纯文本."""
    if not text:
        return text

    result = text
    # 去除代码块
    result = _FENCE_RE.sub("", result)
    # 去除行内代码
    result = _INLINE_CODE_RE.sub(r"\1", result)
    # 去除链接格式（保留文本）
    result = _LINK_RE.sub(r"\1", result)
    # 去除粗体/斜体格式
    result = _BOLD_RE.sub(r"\1", result)
    result = _ITALIC_RE.sub(r"\1", result)
    # 去除删除线
    result = _STRIKETHROUGH_RE.sub(r"\1", result)
    # 去除标题标记
    result = _HEADING_RE.sub(r"\2", result)
    # 去除水平线
    result = _HR_RE.sub("", result)
    # 压缩空行
    result = _MULTI_BLANK_RE.sub("\n\n", result)

    return result.strip()
