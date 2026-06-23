"""
兼容层 — 替代 Hermes 特有的 5 个依赖，使 feishu_hermes.py 可独立运行。

通过 sys.modules 注入在导入 feishu_hermes.py 前完成，
确保 `from gateway.config import ...` 等语句正确解析。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any


# ===========================================================================
# sys.modules 注入 — 必须在 feishu_hermes.py 导入前完成
# ===========================================================================

def _install_compat_modules() -> None:
    """将 Hermes 内部模块映射到本轮兼容实现"""
    class _GatewayConfigModule:
        """gateway.config 模块"""
        Platform = Platform
        PlatformConfig = PlatformConfig

    class _GatewayStatusModule:
        """gateway.status 模块"""
        acquire_scoped_lock = acquire_scoped_lock
        release_scoped_lock = release_scoped_lock

    class _GatewayBaseModule:
        """gateway.platforms.base 模块"""
        BasePlatformAdapter = BasePlatformAdapter
        MessageType = MessageType
        MessageEvent = MessageEvent
        SendResult = SendResult
        ProcessingOutcome = ProcessingOutcome
        SUPPORTED_DOCUMENT_TYPES = SUPPORTED_DOCUMENT_TYPES
        build_session_key = build_session_key
        strip_markdown = strip_markdown
        cache_image_from_bytes = cache_image_from_bytes
        cache_image_from_url = cache_image_from_url
        cache_document_from_bytes = cache_document_from_bytes
        cache_audio_from_bytes = cache_audio_from_bytes

    class _HermesConstantsModule:
        """hermes_constants 模块"""
        get_hermes_home = get_hermes_home

    class _UtilsModule:
        """utils 模块"""
        atomic_json_write = atomic_json_write
        env_float = env_float
        env_int = env_int

    sys.modules["gateway"] = type(sys)("gateway")
    sys.modules["gateway.config"] = _GatewayConfigModule()
    sys.modules["gateway.status"] = _GatewayStatusModule()
    sys.modules["gateway.platforms"] = type(sys)("gateway.platforms")
    sys.modules["gateway.platforms.base"] = _GatewayBaseModule()
    sys.modules["gateway.platforms.helpers"] = _GatewayBaseModule()  # strip_markdown
    sys.modules["hermes_constants"] = _HermesConstantsModule()
    sys.modules["utils"] = _UtilsModule()


# ===========================================================================
# 1. Platform / PlatformConfig 替代
# ===========================================================================

class Platform(str, Enum):
    FEISHU = "feishu"


class PlatformConfig:
    """模拟 Hermes PlatformConfig，映射到 ClawHermes config dict"""
    def __init__(self, platform: Platform, extra: dict[str, Any] | None = None):
        self.platform = platform
        self.extra = extra or {}


# ===========================================================================
# 2. get_hermes_home() 替代
# ===========================================================================

def get_hermes_home() -> Path:
    """返回 ClawHermes 数据目录"""
    return Path(os.environ.get("CH_DATA_DIR", Path.home() / ".clawhermes"))


# ===========================================================================
# 3. 工具函数替代
# ===========================================================================

def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def atomic_json_write(path: Path, data: Any) -> None:
    """原子写入 JSON 文件"""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, delete=False, suffix=".tmp"
    )
    try:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, str(path))
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


# ===========================================================================
# 4. BasePlatformAdapter shim
# ===========================================================================

class MessageType:
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    AUDIO = "audio"
    VIDEO = "video"


class MessageEvent:
    """简化的消息事件"""
    def __init__(self, **kwargs: Any):
        for k, v in kwargs.items():
            setattr(self, k, v)


class SendResult:
    """发送结果"""
    def __init__(self, message_id: str = "", success: bool = True):
        self.message_id = message_id
        self.success = success


class ProcessingOutcome:
    """处理结果"""
    def __init__(self, continue_processing: bool = True):
        self.continue_processing = continue_processing


SUPPORTED_DOCUMENT_TYPES = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
    "csv": "text/csv",
    "json": "application/json",
    "xml": "application/xml",
    "yaml": "application/x-yaml",
    "yml": "application/x-yaml",
}


class BasePlatformAdapter:
    """最小化 BasePlatformAdapter shim — 仅提供 FeishuAdapter 需要的接口"""
    supports_code_blocks: bool = True
    splits_long_messages: bool = False
    supports_async_delivery: bool = True
    MAX_MESSAGE_LENGTH: int = 8000

    def __init__(self, config: PlatformConfig, platform: Platform):
        self.config = config
        self.platform = platform
        self._running = False
        self._fatal_error_code: str | None = None
        self._fatal_error_message: str | None = None

    # stub methods FeishuAdapter may call
    def message_len_fn(self):
        return len

    def enforces_own_access_policy(self) -> bool:
        return False

    def has_fatal_error(self) -> bool:
        return self._fatal_error_code is not None

    def fatal_error_message(self) -> str | None:
        return self._fatal_error_message

    def fatal_error_code(self) -> str | None:
        return self._fatal_error_code

    def fatal_error_retryable(self) -> bool:
        return True

    def _mark_connected(self) -> None:
        self._running = True

    def _mark_disconnected(self) -> None:
        self._running = False

    def _set_fatal_error(self, code: str, message: str, *, retryable: bool = False) -> None:
        self._fatal_error_code = code
        self._fatal_error_message = message


# ===========================================================================
# 5. acquire_scoped_lock / release_scoped_lock shim
# ===========================================================================

_platform_locks: dict[str, asyncio.Lock] = {}

def acquire_scoped_lock(platform: str) -> asyncio.Lock:
    if platform not in _platform_locks:
        _platform_locks[platform] = asyncio.Lock()
    return _platform_locks[platform]

def release_scoped_lock(lock: asyncio.Lock) -> None:
    pass  # asyncio.Lock is released via async context manager


# ===========================================================================
# 6. Cache helpers (stubs)
# ===========================================================================

def cache_image_from_bytes(_data: bytes, _ext: str = ".jpg") -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=_ext, delete=False)
    tmp.write(_data)
    tmp.close()
    return tmp.name

def cache_image_from_url(_url: str, _ext: str = ".jpg") -> str:
    return ""

def cache_document_from_bytes(_data: bytes, _filename: str) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=Path(_filename).suffix, delete=False)
    tmp.write(_data)
    tmp.close()
    return tmp.name

def cache_audio_from_bytes(_data: bytes, _ext: str = ".ogg") -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=_ext, delete=False)
    tmp.write(_data)
    tmp.close()
    return tmp.name


# ===========================================================================
# 7. build_session_key / strip_markdown stubs
# ===========================================================================

def build_session_key(*_args: Any, **_kwargs: Any) -> str:
    import uuid
    return str(uuid.uuid4())

def strip_markdown(text: str) -> str:
    """简单的 markdown 剥离"""
    import re
    # Remove code blocks
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    # Remove inline code
    text = re.sub(r'`[^`]+`', '', text)
    # Remove bold/italic
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    return text.strip()
