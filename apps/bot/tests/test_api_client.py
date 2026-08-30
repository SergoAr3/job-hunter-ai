import asyncio

import httpx
import pytest

from app.api_client import JobHunterApiClient, _json_object


def test_json_object_returns_valid_api_object() -> None:
    request = httpx.Request("GET", "http://api/example")
    response = httpx.Response(200, json={"id": 7}, request=request)

    assert _json_object(response) == {"id": 7}


@pytest.mark.parametrize("content", [b"not json", b"[]"])
def test_json_object_maps_malformed_or_non_object_response_to_http_error(content: bytes) -> None:
    request = httpx.Request("GET", "http://api/example")
    response = httpx.Response(200, content=content, request=request)

    with pytest.raises(httpx.DecodingError):
        _json_object(response)


def test_put_user_profile_calls_full_replace_endpoint() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"user_id": 7}, request=request)

        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))
        try:
            result = await client.put_user_profile(7, {"target_roles": ["Engineer"]})
        finally:
            await client.close()

        assert result == {"user_id": 7}
        assert len(requests) == 1
        assert requests[0].method == "PUT"
        assert requests[0].url.path == "/users/7/profile"
        assert requests[0].content == b'{"target_roles":["Engineer"]}'
    asyncio.run(scenario())
