"""Real FastAPI middleware ordering and Uvicorn access-log target formatting."""
import logging

import pytest
from fastapi.testclient import TestClient
from uvicorn.protocols.utils import get_path_with_query_string

from app.main import app
from conftest import client
from test_discover_feed import feed, start

logger = logging.getLogger("feed_access_regression")


class AccessObserver:
    def __init__(self, application):
        self.app = application

    async def __call__(self, scope, receive, send):
        async def access_send(message):
            if message["type"] == "http.response.start":
                # Uvicorn uses this original scope when writing its access log.
                logger.info("%s %s %s %d", scope["client"], scope["method"],
                            get_path_with_query_string(scope), message["status"])
            await send(message)
        await self.app(scope, receive, access_send)


@pytest.mark.parametrize("mode,status", [("exact", 200), ("slash", 307), ("slash_many", 307), ("failure", 500), ("post", 200)])
def test_feed_query_redacted_before_redirect_and_outer_error(feed, monkeypatch, caplog, mode, status):
    user_id, service, cache, provider, _ = feed
    first = start(user_id, limit=1).json()
    token = first["next_cursor"] if mode == "post" else first["cursor"]
    caplog.set_level(logging.INFO, logger=logger.name)
    observed = TestClient(AccessObserver(app), headers=client.headers,
                          follow_redirects=False, raise_server_exceptions=False)
    path = f"/users/{user_id}/discover/feed"
    if mode == "failure":
        def fail(*args):
            raise RuntimeError("injected internal failure")
        monkeypatch.setattr(service, "replay", fail)
    if mode == "post":
        response = observed.post(path, json={"cursor": token})
        assert response.json()["items"][0]["external_id"] == "1"
    else:
        suffix = "/" if mode == "slash" else "///" if mode == "slash_many" else ""
        response = observed.get(path + suffix, params={"cursor": token})
    assert response.status_code == status
    if mode == "exact":
        assert response.json()["cursor"] == token  # Real query parsing still works.
    if mode in {"slash", "slash_many"}:
        assert token in response.headers["location"]  # Redirect semantics preserved.
    messages = [record.getMessage() for record in caplog.records if record.name == logger.name]
    assert len(messages) == 1
    assert path in messages[0] and str(status) in messages[0]
    assert ("POST" if mode == "post" else "GET") in messages[0] and "testclient" in messages[0]
    assert token not in messages[0] and "cursor=" not in messages[0]
    assert cache.fresh_reads == cache.cold_loads == provider.calls == [0]


def test_unrelated_access_query_is_unchanged(caplog):
    caplog.set_level(logging.INFO, logger=logger.name)
    observed = TestClient(AccessObserver(app))
    assert observed.get("/health?cursor=visible&query=keep").status_code == 200
    messages = [record.getMessage() for record in caplog.records if record.name == logger.name]
    assert len(messages) == 1 and "GET /health?cursor=visible&query=keep 200" in messages[0]
