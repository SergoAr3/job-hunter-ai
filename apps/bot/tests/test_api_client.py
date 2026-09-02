import asyncio

import httpx
import pytest

from app.api_client import JobHunterApiClient, _json_object


def complete_profile_response() -> dict[str, object]:
    return {
        "user_id": 7,
        "target_roles": ["Engineer"],
        "skills": ["Python"],
        "experience": "middle",
        "location": ["Yerevan"],
        "workplace_preference": "remote",
        "salary_min": "2500.00",
        "salary_currency": "USD",
        "salary_period": "month",
        "languages": [{"language": "English", "level": "B2"}],
    }

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


def test_get_user_profile_returns_profile_object() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=complete_profile_response(), request=request)

        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://api", transport=httpx.MockTransport(handler)
        )
        try:
            result = await client.get_user_profile(7)
        finally:
            await client.close()

        assert result == complete_profile_response()
        assert requests[0].method == "GET"
        assert requests[0].url.path == "/users/7/profile"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "profile",
    [
        {"target_roles": ["Engineer"]},
        {**complete_profile_response(), "skills": "Python"},
    ],
)
def test_get_user_profile_rejects_partial_or_wrong_shape_response(
    profile: dict[str, object],
) -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=profile, request=request)

        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://api", transport=httpx.MockTransport(handler)
        )
        try:
            with pytest.raises(httpx.DecodingError):
                await client.get_user_profile(7)
        finally:
            await client.close()

    asyncio.run(scenario())


def test_get_user_profile_maps_only_not_found_to_none() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "Profile not found"}, request=request)

        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://api", transport=httpx.MockTransport(handler)
        )
        try:
            assert await client.get_user_profile(7) is None
        finally:
            await client.close()

    asyncio.run(scenario())


def test_get_user_profile_does_not_mask_server_error_as_missing() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"detail": "Unavailable"}, request=request)

        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://api", transport=httpx.MockTransport(handler)
        )
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await client.get_user_profile(7)
        finally:
            await client.close()

    asyncio.run(scenario())


def test_get_user_profile_does_not_mask_invalid_json_as_missing() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json", request=request)

        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://api", transport=httpx.MockTransport(handler)
        )
        try:
            with pytest.raises(httpx.DecodingError):
                await client.get_user_profile(7)
        finally:
            await client.close()

    asyncio.run(scenario())


def test_applications_client_uses_read_only_endpoints_and_validates_shape() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/applications"):
                return httpx.Response(200, json={"items": [{"app_id": 1, "job_id": 2, "created_at": "2026-01-01T00:00:00Z", "title": "Engineer", "company": None, "location": None, "workplace_type": "remote", "parsing_status": "success", "ai_enrichment_status": "success"}], "has_next": False}, request=request)
            return httpx.Response(200, json={"application": {"id": 1}, "job": {"id": 2}}, request=request)
        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))
        try:
            assert (await client.list_applications(7, limit=5, offset=0))["items"]
            assert (await client.get_application(7, 1))["job"] == {"id": 2}
        finally:
            await client.close()
    asyncio.run(scenario())


def test_create_profile_draft_from_cv_calls_multipart_endpoint() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"target_roles": ["Engineer"]}, request=request)

        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))
        try:
            result = await client.create_profile_draft_from_cv(
                7,
                filename="resume.pdf",
                content_type="application/pdf",
                content=b"%PDF-test",
            )
        finally:
            await client.close()

        assert result == {"target_roles": ["Engineer"]}
        assert requests[0].method == "POST"
        assert requests[0].url.path == "/users/7/profile/draft-from-cv"
        assert "multipart/form-data" in requests[0].headers["content-type"]
        assert b'resume.pdf' in requests[0].content
        assert b"%PDF-test" in requests[0].content

    asyncio.run(scenario())
