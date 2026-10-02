import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest

from app.api_client import JobHunterApiClient
from app.start import API_UNAVAILABLE_MESSAGE, handle_start

TOKEN = "explicit-server-only-test-token-123456"


def test_client_credentials_are_scoped_to_user_routes(caplog):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"id": 41, "telegram_id": 123, "created": False})
    async def scenario():
        client = JobHunterApiClient("http://api", service_token=TOKEN)
        client._client._transport = httpx.MockTransport(handler)
        try:
            assert await client.create_or_get_user(SimpleNamespace(id=123, username=None, first_name="Actor", last_name=None, language_code=None)) == 41
            await client._client.get("/users/41/profile")
            await client._client.post("/profile/skills/normalize")
            await client._client.get("/health")
        finally:
            await client.close()
    with caplog.at_level(logging.DEBUG):
        asyncio.run(scenario())
    assert all(r.headers["X-Bot-Service-Token"] == TOKEN for r in seen[:2])
    assert all("X-Bot-Service-Token" not in r.headers for r in seen[2:])
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("status", [200, 401])
def test_start_uses_authenticated_client_and_preserves_error_ux(status, caplog):
    class Message:
        from_user = SimpleNamespace(id=123, username=None, first_name="Actor", last_name=None, language_code=None)
        answers = []
        async def answer(self, text, reply_markup=None):
            self.answers.append(text)
    def handler(request):
        assert request.url.path == "/users/telegram"
        assert request.headers["X-Bot-Service-Token"] == TOKEN
        return httpx.Response(status, json={"id": 41, "telegram_id": 123, "created": False} if status == 200 else {"detail": "AUTH_REQUIRED"})
    async def scenario():
        client = JobHunterApiClient("http://api", service_token=TOKEN)
        client._client._transport = httpx.MockTransport(handler)
        message = Message()
        try:
            await handle_start(message, client)
            assert message.answers == (["Привет, Actor! Я помогу с поиском работы."] if status == 200 else [API_UNAVAILABLE_MESSAGE])
        finally:
            await client.close()
    asyncio.run(scenario())
    assert TOKEN not in caplog.text


def test_client_missing_credential_fails_fast(monkeypatch):
    monkeypatch.delenv("BOT_API_SERVICE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="BOT_API_SERVICE_TOKEN"):
        JobHunterApiClient("http://api")


@pytest.mark.parametrize("token", ["", " " * 40, "short", "replace-with-a-secure-random-service-token", "bot-tests-server-only-service-token", TOKEN, "a" * 43])
def test_production_rejects_placeholder_credentials(monkeypatch, token):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="BOT_API_SERVICE_TOKEN"):
        JobHunterApiClient("http://api", service_token=token)


def test_production_accepts_random_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    client = JobHunterApiClient("http://api", service_token="Z4RpqK72C0vjA6GyD5tN3xQbU8mW1eLfH9sIoBVkzEY")
    asyncio.run(client.close())
