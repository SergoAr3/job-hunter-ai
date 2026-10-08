import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.services.reminders import Destination
from app.services.telegram_notifications import ConfigurationFailure, Outcome, TelegramSender

DESTINATION = Destination(123, "Call <HR> & ask", "Engineer", "Company", datetime(2026, 10, 10, 7, tzinfo=timezone.utc), "Asia/Yerevan")
TOKEN = "123456:" + "test_token_" * 4


@pytest.mark.parametrize("error,expected", [
    (httpx.ConnectError("private"), Outcome("retry", "CONNECT_FAILED")),
    (httpx.ConnectTimeout("private"), Outcome("retry", "CONNECT_FAILED")),
    (httpx.ReadTimeout("private"), Outcome("failed", "DELIVERY_UNCERTAIN")),
    (httpx.WriteError("private"), Outcome("failed", "DELIVERY_UNCERTAIN")),
    (httpx.RemoteProtocolError("private"), Outcome("failed", "DELIVERY_UNCERTAIN")),
])
def test_network_classification(error, expected):
    async def scenario():
        def handler(request):
            raise error
        sender = TelegramSender(TOKEN, "https://example.invalid", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            assert await sender.send(DESTINATION, 1) == expected
        finally:
            await sender.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("status,body,expected", [
    (200, {"ok": True, "result": {"message_id": 1}}, Outcome("sent")),
    (429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 200}}, Outcome("retry", "RATE_LIMITED", 200)),
    (403, {"ok": False, "error_code": 403}, Outcome("failed", "CHAT_UNAVAILABLE")),
    (400, {"ok": False, "error_code": 400}, Outcome("failed", "CHAT_UNAVAILABLE")),
    (401, {"ok": False}, Outcome("configuration", "CONFIGURATION_FAILED")),
    (500, {"ok": False, "error_code": 500}, Outcome("failed", "DELIVERY_UNCERTAIN")),
    (500, {"ok": False, "error_code": 429}, Outcome("failed", "DELIVERY_UNCERTAIN")),
    (500, {"ok": False, "error_code": 403}, Outcome("failed", "DELIVERY_UNCERTAIN")),
    (200, {"ok": True, "result": {}}, Outcome("failed", "DELIVERY_UNCERTAIN")),
    (200, [], Outcome("failed", "DELIVERY_UNCERTAIN")),
])
def test_provider_classification_and_private_plain_text_projection(status, body, expected):
    async def scenario():
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(status, json=body)
        sender = TelegramSender(TOKEN, "https://example.invalid", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            assert await sender.send(DESTINATION, 1) == expected
            import json
            payload = json.loads(requests[0].content)
            assert set(payload) == {"chat_id", "text", "link_preview_options"}
            assert "parse_mode" not in payload
            assert "11:00 (Asia/Yerevan)" in payload["text"]
            assert "https://example.invalid/applications/1" in payload["text"]
            assert payload["link_preview_options"] == {"is_disabled": True}
            assert len(payload["text"].encode("utf-16-le")) < 8192
        finally:
            await sender.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("token,origin,env", [("bad", "https://example.invalid", "production"), (TOKEN, "http://evil.invalid", "development"), (TOKEN, "https://user:password@example.invalid", "production"), (TOKEN, "https://example.invalid/path", "production")])
def test_configuration_rejected_before_network(token, origin, env):
    with pytest.raises(ConfigurationFailure):
        TelegramSender(token, origin, env)
