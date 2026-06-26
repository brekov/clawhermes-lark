"""
OAuth 2.0 Device Authorization Grant (RFC 8628) for Feishu/Lark.

对齐 larksuite/openclaw-lark src/core/device-flow.ts：
  1. request_device_authorization — 获取 device_code + user_code + verification_uri
  2. poll_device_token — 轮询 token 端点直到用户授权/拒绝/过期

这是飞书"扫码关联 Bot"的核心机制：
  - 调用 request_device_authorization 获取 user_code 和验证 URL
  - 将 verification_uri 生成二维码展示给用户
  - 用户使用飞书扫描二维码 → 输入 user_code → 完成授权
  - Bot 轮询获取 access_token + refresh_token

OAuth 端点：
  飞书: https://accounts.feishu.cn/oauth/v1/device_authorization
        https://open.feishu.cn/open-apis/authen/v2/oauth/token
  Lark:  https://accounts.larksuite.com/oauth/v1/device_authorization
        https://open.larksuite.com/open-apis/authen/v2/oauth/token
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("clawhermes.lark.device_flow")

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class DeviceAuthResponse:
    """设备授权响应 — 包含用户扫码所需的验证信息."""
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str = ""
    expires_in: int = 300  # seconds
    interval: int = 5      # recommended polling interval (seconds)


@dataclass
class DeviceFlowTokenData:
    """令牌响应数据."""
    access_token: str
    refresh_token: str = ""
    expires_in: int = 7200       # seconds
    refresh_expires_in: int = 0  # seconds
    scope: str = ""


@dataclass
class DeviceFlowResult:
    """Device Flow 结果 — 成功或失败."""
    ok: bool
    token: DeviceFlowTokenData | None = None
    error: str | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# Endpoint resolution
# ---------------------------------------------------------------------------


def resolve_oauth_endpoints(brand: str) -> dict[str, str]:
    """
    根据品牌解析 OAuth 端点 URL.

    Args:
        brand: "feishu" | "lark" | 自定义域名
    """
    if not brand or brand == "feishu":
        return {
            "device_authorization": "https://accounts.feishu.cn/oauth/v1/device_authorization",
            "token": "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
        }
    if brand == "lark":
        return {
            "device_authorization": "https://accounts.larksuite.com/oauth/v1/device_authorization",
            "token": "https://open.larksuite.com/open-apis/authen/v2/oauth/token",
        }

    # Custom domain — derive by convention: open.X → accounts.X
    base = brand.rstrip("/")
    accounts_base = base
    try:
        from urllib.parse import urlparse
        parsed = urlparse(base)
        if parsed.hostname and parsed.hostname.startswith("open."):
            accounts_host = parsed.hostname.replace("open.", "accounts.", 1)
            accounts_base = f"{parsed.scheme}://{accounts_host}"
    except Exception:
        pass

    return {
        "device_authorization": f"{accounts_base}/oauth/v1/device_authorization",
        "token": f"{base}/open-apis/authen/v2/oauth/token",
    }


# ---------------------------------------------------------------------------
# Step 1 — Device Authorization Request
# ---------------------------------------------------------------------------


async def request_device_authorization(
    app_id: str,
    app_secret: str,
    brand: str = "feishu",
    scopes: list[str] | None = None,
    timeout: int = 30,
) -> DeviceAuthResponse:
    """
    请求设备授权码.

    使用 Confidential Client 认证 (HTTP Basic auth: app_id:app_secret).
    自动追加 offline_access scope 以获取 refresh_token.

    Args:
        app_id: 应用 App ID
        app_secret: 应用 App Secret
        brand: 品牌 ("feishu" | "lark" | 自定义域名)
        scopes: 请求的权限 scope 列表
        timeout: HTTP 请求超时秒数

    Returns:
        DeviceAuthResponse 包含 device_code, user_code, verification_uri 等

    Raises:
        RuntimeError: API 返回错误或 HTTP 错误
    """
    endpoints = resolve_oauth_endpoints(brand)

    # 构建请求 scope — 追加 offline_access
    scope_list = list(scopes) if scopes else []
    if "offline_access" not in scope_list:
        scope_list.append("offline_access")

    # HTTP Basic auth
    credentials = f"{app_id}:{app_secret}"
    encoded = base64.b64encode(credentials.encode()).decode()

    body_data = {
        "scope": " ".join(scope_list),
        "client_id": app_id,
        "client_secret": app_secret,
    }
    body_bytes = urllib.parse.urlencode(body_data).encode()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encoded}",
    }

    loop = asyncio.get_event_loop()

    def _request():
        req = urllib.request.Request(
            endpoints["device_authorization"],
            data=body_bytes,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else str(e)
            raise RuntimeError(f"Device auth HTTP {e.code}: {body}")

    try:
        data = await loop.run_in_executor(None, _request)
    except Exception as e:
        logger.error("Device authorization request failed: %s", e)
        raise RuntimeError(f"设备授权请求失败: {e}") from e

    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid response: {data}")

    if "error" in data:
        desc = data.get("error_description", data["error"])
        raise RuntimeError(f"Device auth error: {desc}")

    return DeviceAuthResponse(
        device_code=str(data.get("device_code", "")),
        user_code=str(data.get("user_code", "")),
        verification_uri=str(data.get("verification_uri", "")),
        verification_uri_complete=str(data.get("verification_uri_complete", "")),
        expires_in=int(data.get("expires_in", 300)),
        interval=int(data.get("interval", 5)),
    )


# ---------------------------------------------------------------------------
# QR Code URL 生成
# ---------------------------------------------------------------------------


def build_qr_code_url(user_code: str, verification_uri: str) -> str:
    """
    构建可扫描的二维码 URL.

    使用 verification_uri_complete（含 user_code）直接生成二维码，
    用户扫描后无需手动输入 code。

    调用方可使用 qrcode 库生成图片::

        import qrcode
        img = qrcode.make(url)
        img.save("feishu_auth_qr.png")
    """
    # Feishu 支持 verification_uri_complete 直接跳转授权
    return verification_uri  # URL 即二维码内容


def build_auth_card_qr_text(device_resp: DeviceAuthResponse) -> str:
    """
    构建飞书卡片中展示的授权指引文本（含二维码 URL).
    """
    uri = device_resp.verification_uri_complete or (
        f"{device_resp.verification_uri}?user_code={device_resp.user_code}"
    )
    lines = [
        "📱 **授权机器人访问 Feishu**",
        "",
        f"请在浏览器打开以下链接完成授权：",
        f"[{uri}]({uri})",
        "",
        f"或手动输入授权码：**`{device_resp.user_code}`**",
        f"验证链接：{device_resp.verification_uri}",
        "",
        f"⏰ 授权码有效期：{device_resp.expires_in // 60} 分钟",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 2 — Poll Device Token
# ---------------------------------------------------------------------------


async def poll_device_token(
    app_id: str,
    app_secret: str,
    brand: str,
    device_code: str,
    interval: int = 5,
    expires_in: int = 300,
    max_poll_attempts: int = 200,
    cancel_event: asyncio.Event | None = None,
) -> DeviceFlowResult:
    """
    轮询 token 端点直到用户授权、拒绝或超时.

    处理以下状态:
      - authorization_pending: 继续轮询
      - slow_down: 增加轮询间隔 (+5s)
      - access_denied: 终止 — 用户拒绝
      - expired_token: 终止 — 授权码过期

    Args:
        app_id: 应用 App ID
        app_secret: 应用 App Secret
        brand: 品牌
        device_code: Step 1 返回的 device_code
        interval: 推荐轮询间隔 (秒)
        expires_in: 授权码有效期 (秒)
        max_poll_attempts: 安全上限
        cancel_event: 外部取消信号

    Returns:
        DeviceFlowResult
    """
    MAX_INTERVAL = 60
    endpoints = resolve_oauth_endpoints(brand)
    deadline = time.time() + expires_in
    current_interval = interval
    attempts = 0

    loop = asyncio.get_event_loop()

    while time.time() < deadline and attempts < max_poll_attempts:
        attempts += 1

        # 检查取消信号
        if cancel_event and cancel_event.is_set():
            return DeviceFlowResult(
                ok=False, error="expired_token", message="授权已取消"
            )

        # 等待轮询间隔
        await asyncio.sleep(current_interval)

        # 再次检查取消信号（sleep 后）
        if cancel_event and cancel_event.is_set():
            return DeviceFlowResult(
                ok=False, error="expired_token", message="授权已取消"
            )

        # 发起 token 请求
        body_data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": app_id,
            "client_secret": app_secret,
        }
        body_bytes = urllib.parse.urlencode(body_data).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        def _poll():
            req = urllib.request.Request(
                endpoints["token"],
                data=body_bytes,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                body = e.read().decode() if e.fp else "{}"
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    return {"error": "network", "error_description": str(e)}
            except Exception as e:
                return {"error": "network", "error_description": str(e)}

        data = await loop.run_in_executor(None, _poll)

        if not isinstance(data, dict):
            continue

        error = data.get("error")

        # 成功获取 token
        if not error and data.get("access_token"):
            logger.info("Device flow token obtained successfully")
            refresh_token = data.get("refresh_token", "")
            token_expires = data.get("expires_in", 7200)
            refresh_expires = data.get("refresh_token_expires_in", 0)
            if not refresh_token:
                logger.warning("No refresh_token in response")
                refresh_expires = token_expires

            return DeviceFlowResult(
                ok=True,
                token=DeviceFlowTokenData(
                    access_token=str(data["access_token"]),
                    refresh_token=str(refresh_token),
                    expires_in=int(token_expires),
                    refresh_expires_in=int(refresh_expires),
                    scope=str(data.get("scope", "")),
                ),
            )

        # 处理错误状态
        if error == "authorization_pending":
            logger.debug("authorization_pending, retrying...")
            continue

        if error == "slow_down":
            current_interval = min(current_interval + 5, MAX_INTERVAL)
            logger.info("slow_down, interval=%ds", current_interval)
            continue

        if error == "access_denied":
            logger.info("User denied authorization")
            return DeviceFlowResult(
                ok=False, error="access_denied", message="用户拒绝了授权"
            )

        if error in ("expired_token", "invalid_grant"):
            logger.info("Device code expired/invalid: %s", error)
            return DeviceFlowResult(
                ok=False, error="expired_token", message="授权码已过期，请重新发起"
            )

        # Unknown error — 视为终止
        desc = data.get("error_description", error or "Unknown error")
        logger.warning("Unexpected error: %s, desc=%s", error, desc)
        return DeviceFlowResult(
            ok=False, error="expired_token", message=str(desc)
        )

    if attempts >= max_poll_attempts:
        logger.warning("Max poll attempts (%d) reached", max_poll_attempts)

    return DeviceFlowResult(
        ok=False, error="expired_token", message="授权超时，请重新发起"
    )


# ---------------------------------------------------------------------------
# 便捷函数 — 完整 Device Flow
# ---------------------------------------------------------------------------


async def run_device_flow(
    app_id: str,
    app_secret: str,
    brand: str = "feishu",
    scopes: list[str] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> tuple[DeviceAuthResponse, DeviceFlowResult]:
    """
    执行完整的 Device Flow 流程.

    1. 请求设备授权 → 获取 user_code + verification_uri
    2. 轮询 token → 获取 access_token + refresh_token

    Returns:
        (auth_response, flow_result) — auth_response 包含展示给用户的信息,
        flow_result 包含最终结果
    """
    auth_resp = await request_device_authorization(
        app_id=app_id,
        app_secret=app_secret,
        brand=brand,
        scopes=scopes,
    )

    result = await poll_device_token(
        app_id=app_id,
        app_secret=app_secret,
        brand=brand,
        device_code=auth_resp.device_code,
        interval=auth_resp.interval,
        expires_in=auth_resp.expires_in,
        cancel_event=cancel_event,
    )

    return auth_resp, result
