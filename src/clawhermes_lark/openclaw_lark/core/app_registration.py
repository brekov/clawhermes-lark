"""
飞书应用自动注册 — 对齐 openclaw-lark src/setup-surface.ts 的 runNewAppFlow

两种创建方式：
  方式一：扫码创建 — OAuth App Registration Flow
    自动在飞书开放平台创建"个人 Agent"类型应用，
    通过终端二维码扫码 → 飞书 App 授权 → 自动下发 appId + appSecret

  方式二：手动配置 — 用户在 open.feishu.cn 手动创建应用，
    填入 App ID / App Secret，然后自动获取 owner open_id 用于安全策略

App Registration API (与 Device Flow 不同！):
  POST https://accounts.feishu.cn/oauth/v1/app/registration
  支持三种 action:
    - "init"    → 验证环境，检查是否支持 client_secret 认证
    - "begin"   → 生成 device_code + QR URL，指定 auth_method 和 archetype
    - "poll"    → 轮询授权结果，返回 client_id + client_secret
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("clawhermes.lark.app_registration")

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

REGISTRATION_ENDPOINTS = {
    "feishu": "https://accounts.feishu.cn/oauth/v1/app/registration",
    "lark": "https://accounts.larksuite.com/oauth/v1/app/registration",
}


def _get_endpoint(brand: str) -> str:
    """获取 App Registration API 端点."""
    return REGISTRATION_ENDPOINTS.get(brand, REGISTRATION_ENDPOINTS["feishu"])


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class AppRegistrationInitResult:
    """init 阶段的返回."""
    ok: bool
    supports_client_secret: bool = False
    error: str = ""


@dataclass
class AppRegistrationBeginResult:
    """begin 阶段的返回 — 包含展示给用户的二维码信息."""
    ok: bool
    device_code: str = ""
    user_code: str = ""
    verification_uri_complete: str = ""
    interval: int = 5
    expire_in: int = 300
    error: str = ""


@dataclass
class AppRegistrationPollResult:
    """poll 阶段的返回 — 包含创建好的应用凭证."""
    ok: bool
    client_id: str = ""       # aka app_id
    client_secret: str = ""   # aka app_secret
    open_id: str = ""         # app owner 的 open_id
    error: str = ""


# ---------------------------------------------------------------------------
# 方式一：扫码创建 (App Registration Flow)
# ---------------------------------------------------------------------------


def _post_registration(brand: str, body: dict[str, Any], timeout: int = 30) -> dict:
    """向 App Registration API 发送 POST 请求."""
    endpoint = _get_endpoint(brand)
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}

    req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else str(e)
        try:
            return json.loads(body_text)
        except json.JSONDecodeError:
            return {"error": "http_error", "error_description": body_text, "code": e.code}


async def _post_registration_async(brand: str, body: dict, timeout: int = 30) -> dict:
    """异步版本."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _post_registration(brand, body, timeout))


# ---- Step 1: Init ----


async def app_registration_init(brand: str = "feishu") -> AppRegistrationInitResult:
    """
    Step 1 — 验证环境是否支持 client_secret 认证.

    返回 supports_client_secret 表示是否可以继续扫码创建流程.
    """
    try:
        resp = await _post_registration_async(brand, {"action": "init"})

        if resp.get("error"):
            return AppRegistrationInitResult(
                ok=False,
                error=resp.get("error_description", resp["error"]),
            )

        # 检查是否支持 client_secret
        supports = resp.get("supports_client_secret", False)

        return AppRegistrationInitResult(
            ok=True,
            supports_client_secret=supports,
        )

    except Exception as e:
        logger.error("App registration init failed: %s", e)
        return AppRegistrationInitResult(ok=False, error=str(e))


# ---- Step 2: Begin — 生成设备码 + QR URL ----


async def app_registration_begin(
    brand: str = "feishu",
    archetype: str = "PersonalAgent",
) -> AppRegistrationBeginResult:
    """
    Step 2 — 生成 device_code 和二维码 URL.

    飞书会自动创建"个人 Agent"类型的企业自建应用.
    返回的 verification_uri_complete 可直接生成二维码供用户扫描.
    """
    body = {
        "action": "begin",
        "archetype": archetype,
        "auth_method": "client_secret",
        "request_user_info": "open_id",
    }

    try:
        resp = await _post_registration_async(brand, body)

        if resp.get("error"):
            return AppRegistrationBeginResult(
                ok=False,
                error=resp.get("error_description", resp["error"]),
            )

        return AppRegistrationBeginResult(
            ok=True,
            device_code=resp.get("device_code", ""),
            user_code=resp.get("user_code", ""),
            verification_uri_complete=resp.get("verification_uri_complete", ""),
            interval=int(resp.get("interval", 5)),
            expire_in=int(resp.get("expire_in", 300)),
        )

    except Exception as e:
        logger.error("App registration begin failed: %s", e)
        return AppRegistrationBeginResult(ok=False, error=str(e))


# ---- Step 3: 生成终端 QR 码文本 ----


def render_qr_terminal(url: str) -> str:
    """
    在终端渲染 QR 码（ASCII 版本).

    如果安装了 qrcode 库则使用它，否则返回纯文本 URL.
    """
    try:
        import qrcode
        import io

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)

        # 使用终端字符渲染
        matrix = qr.modules
        lines = []
        for row in range(0, len(matrix), 2):
            line = ""
            for col in range(len(matrix)):
                upper = matrix[row][col]
                lower = matrix[row + 1][col] if row + 1 < len(matrix) else False

                if upper and lower:
                    line += "█"
                elif upper:
                    line += "▀"
                elif lower:
                    line += "▄"
                else:
                    line += " "
            lines.append(line)

        return "\n".join(lines)

    except ImportError:
        # 没有 qrcode 库 — 返回 URL
        return f"[QR Code URL]\n{url}"


def build_qr_guide_text(begin_result: AppRegistrationBeginResult) -> str:
    """
    构建终端显示的扫码引导文本.
    """
    qr = render_qr_terminal(begin_result.verification_uri_complete)

    return f"""
{qr}

📱 请使用飞书 App 扫描上方二维码

授权码: {begin_result.user_code}
有效期: {begin_result.expire_in // 60} 分钟

扫描后飞书将自动创建企业自建应用并完成授权.
"""


# ---- Step 4: Poll — 轮询授权结果 ----


async def app_registration_poll(
    brand: str,
    device_code: str,
    interval: int = 5,
    expire_in: int = 300,
    cancel_event: asyncio.Event | None = None,
) -> AppRegistrationPollResult:
    """
    Step 4 — 轮询授权结果，获取创建好的应用凭证.

    用户扫码后在飞书 App 点授权 → 飞书后端自动创建企业自建应用
    → 返回 client_id (appId) + client_secret (appSecret) + open_id
    """
    deadline = time.time() + expire_in

    while time.time() < deadline:
        if cancel_event and cancel_event.is_set():
            return AppRegistrationPollResult(
                ok=False, error="用户取消了操作"
            )

        await asyncio.sleep(interval)

        try:
            resp = await _post_registration_async(brand, {
                "action": "poll",
                "device_code": device_code,
            })

            error = resp.get("error")

            # 成功 — 拿到凭证
            if not error and resp.get("client_id"):
                logger.info("App registration completed: app_id=%s", resp["client_id"][:12] + "***")
                return AppRegistrationPollResult(
                    ok=True,
                    client_id=resp.get("client_id", ""),
                    client_secret=resp.get("client_secret", ""),
                    open_id=resp.get("open_id", resp.get("user_info", {}).get("open_id", "")),
                )

            if error == "authorization_pending":
                logger.debug("App registration: waiting for user scan...")
                continue

            if error == "slow_down":
                interval = min(interval + 5, 60)
                logger.debug("App registration: slow_down, interval=%ds", interval)
                continue

            if error == "access_denied":
                return AppRegistrationPollResult(
                    ok=False, error="用户拒绝了授权"
                )

            if error in ("expired_token", "invalid_grant"):
                return AppRegistrationPollResult(
                    ok=False, error="授权码已过期，请重新发起"
                )

            # Unknown error
            desc = resp.get("error_description", error or "Unknown error")
            return AppRegistrationPollResult(ok=False, error=str(desc))

        except Exception as e:
            logger.warning("App registration poll error: %s", e)
            continue

    return AppRegistrationPollResult(
        ok=False, error="授权超时，请重新发起"
    )


# ---------------------------------------------------------------------------
# 完整流程封装
# ---------------------------------------------------------------------------


async def run_qr_code_app_creation(
    brand: str = "feishu",
    cancel_event: asyncio.Event | None = None,
) -> AppRegistrationPollResult:
    """
    执行完整的扫码创建应用流程.

    1. init → 检查环境
    2. begin → 生成 QR 码（调用方负责展示）
    3. poll → 等待扫码授权 → 返回 appId + appSecret

    Returns:
        AppRegistrationPollResult with client_id + client_secret + open_id
    """
    # Step 1: Init
    init_result = await app_registration_init(brand)
    if not init_result.ok:
        return AppRegistrationPollResult(ok=False, error=f"环境检查失败: {init_result.error}")

    if not init_result.supports_client_secret:
        return AppRegistrationPollResult(
            ok=False,
            error="当前环境不支持自动创建应用，请使用手动配置方式",
        )

    # Step 2: Begin
    begin_result = await app_registration_begin(brand)
    if not begin_result.ok:
        return AppRegistrationPollResult(ok=False, error=f"创建失败: {begin_result.error}")

    # 展示 QR 码给调用方
    logger.info(
        "App registration QR ready: code=%s uri=%s",
        begin_result.user_code, begin_result.verification_uri_complete[:60],
    )
    # 调用方应在此时展示 QR 码：print(build_qr_guide_text(begin_result))

    # Step 3: Poll
    poll_result = await app_registration_poll(
        brand=brand,
        device_code=begin_result.device_code,
        interval=begin_result.interval,
        expire_in=begin_result.expire_in,
        cancel_event=cancel_event,
    )

    return poll_result


# ---------------------------------------------------------------------------
# 方式二：手动配置 — 获取 owner open_id
# ---------------------------------------------------------------------------


async def get_app_owner_open_id(
    client,
    app_id: str = "",
) -> str:
    """
    获取应用 owner 的 open_id（用于安全策略自动配置).

    调用 bot/v3/info API 获取应用的基本信息，解析 owner.
    """
    try:
        from lark_oapi.api.verification.v1 import GetBotInfoRequest

        req = GetBotInfoRequest()
        resp = client.verification.v1.bot_info.get(req)

        if not resp.success() or not resp.data:
            return ""

        # bot/v3/info 返回的 data 中没有直接的 owner open_id
        # 需要通过其他方式获取，这里返回空字符串表示待实现
        # 实际生产环境可通过 getAppOwnerFallback 获取
        return ""

    except Exception:
        logger.debug("get_app_owner_open_id failed", exc_info=True)
        return ""


async def apply_auto_security_policy(
    adapter: Any,
    owner_open_id: str,
) -> None:
    """
    自动安全策略：仅允许 app owner 触发 bot.

    拿到 openId 后自动设置:
      dm_policy = "allowlist"
      allow_from = [owner_open_id]
    """
    if not owner_open_id:
        return

    if hasattr(adapter, '_lark_config'):
        cfg = adapter._lark_config
        cfg.group_policy = "allowlist"
        if owner_open_id not in cfg.allowed_group_users:
            cfg.allowed_group_users.append(owner_open_id)
        if owner_open_id not in cfg.admins:
            cfg.admins.append(owner_open_id)
        logger.info(
            "Auto security policy: owner=%s added to allowlist+admins",
            owner_open_id[:12] + "***",
        )
