"""
clawhermes-lark 测试
覆盖：LarkAdapter 生命周期 / WebSocket 事件 / 消息发送 / 用户信息
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from clawhermes.channel.adapter import ChannelMessage, ChannelType, ChannelUser
from clawhermes_lark import (
    LarkAdapter,
    LarkConfig,
    LarkEventType,
    create_lark_adapter,
)


class AsyncMock(MagicMock):
    async def __call__(self, *args, **kwargs):
        return super().__call__(*args, **kwargs)


# ---------------------------------------------------------------------------
# LarkAdapter
# ---------------------------------------------------------------------------

class TestLarkAdapter:
    @pytest.fixture
    def adapter(self):
        return LarkAdapter({
            "app_id": "test-app-id",
            "app_secret": "test-secret",
        })

    @pytest.mark.asyncio
    async def test_start_initializes_lark_client(self, adapter):
        with patch("clawhermes_lark.adapter.lark.Client") as mock_lark_cls:
            mock_client = MagicMock()
            mock_lark_cls.builder.return_value.app_id.return_value \
                .app_secret.return_value.domain.return_value \
                .log_level.return_value.build.return_value = mock_client

            await adapter.start()
            assert adapter._client is not None
            await adapter.stop()

    @pytest.mark.asyncio
    async def test_start_skip_without_credentials(self):
        adapter = LarkAdapter({})
        await adapter.start()
        assert adapter._client is None
        assert adapter.is_running is False

    @pytest.mark.asyncio
    async def test_stop_cancels_ws(self, adapter):
        with patch("clawhermes_lark.adapter.lark.Client"):
            await adapter.start()
            assert adapter.is_running
            await adapter.stop()
            assert adapter.is_running is False

    @pytest.mark.asyncio
    async def test_send_response(self, adapter):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_client.arequest = AsyncMock(return_value=mock_resp)
        adapter._client = mock_client

        msg = ChannelMessage(
            message_id="m1", channel_type=ChannelType.FEISHU,
            user=ChannelUser(user_id="ou_abc"), content="hi",
        )
        from clawhermes.channel.adapter import ChannelResponse
        resp = ChannelResponse(content="回复")
        await adapter.send_response(resp, msg)
        mock_client.arequest.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_response_no_client(self, adapter):
        msg = ChannelMessage("m1", ChannelType.FEISHU, ChannelUser("u"), "hi")
        from clawhermes.channel.adapter import ChannelResponse
        resp = ChannelResponse(content="x")
        await adapter.send_response(resp, msg)  # no-op

    @pytest.mark.asyncio
    async def test_get_user_info(self, adapter):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_user = MagicMock()
        mock_user.name = "张三"
        mock_user.avatar = None
        mock_user.email = "z@e.com"
        mock_resp.data.user = mock_user
        mock_client.arequest = AsyncMock(return_value=mock_resp)
        adapter._client = mock_client

        user = await adapter.get_user_info("ou_123")
        assert user is not None
        assert user.display_name == "张三"

    @pytest.mark.asyncio
    async def test_get_user_info_no_client(self, adapter):
        assert await adapter.get_user_info("x") is None

    @pytest.mark.asyncio
    async def test_handle_webhook_url_verification(self, adapter):
        result = await adapter.handle_webhook({
            "type": "url_verification", "challenge": "abc",
        })
        assert result["challenge"] == "abc"

    @pytest.mark.asyncio
    async def test_handle_webhook_other(self, adapter):
        assert await adapter.handle_webhook({"type": "x"}) == {}

    @pytest.mark.asyncio
    async def test_dispatch_error_handling(self, adapter, caplog):
        def _bad(_msg):
            raise RuntimeError("boom")
        adapter.on_message(_bad)
        adapter._dispatch_message(ChannelMessage(
            "e", ChannelType.FEISHU, ChannelUser("u"), "x",
        ))
        assert "Channel message handler error" in caplog.text


# ---------------------------------------------------------------------------
# Config & Factory
# ---------------------------------------------------------------------------

class TestLarkConfig:
    def test_defaults(self):
        cfg = LarkConfig(app_id="a", app_secret="s")
        assert cfg.domain == "feishu"
        assert cfg.auto_reconnect is True
        assert cfg.verification_token == ""

    def test_lark_domain(self):
        cfg = LarkConfig(app_id="a", app_secret="s", domain="lark")
        assert cfg.domain == "lark"


class TestCreateLarkAdapter:
    def test_factory(self):
        a = create_lark_adapter(app_id="f-id", app_secret="f-sec", domain="lark")
        assert a.channel_type == ChannelType.FEISHU
        assert a._lark_config.app_id == "f-id"
        assert a._lark_config.domain == "lark"


# ---------------------------------------------------------------------------
# Event Types
# ---------------------------------------------------------------------------

class TestLarkEventType:
    def test_values(self):
        assert LarkEventType.MESSAGE_RECEIVE == "im.message.receive_v1"
        assert LarkEventType.URL_VERIFICATION == "url_verification"
