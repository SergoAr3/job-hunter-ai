import asyncio
import json

import httpx
import pytest

from app.api_client import JobHunterApiClient, _json_object


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_next_action_client_contract(method):
    async def scenario():
        requests = []
        payload = {"application": {"id": 18, "user_id": 4, "job_id": 7, "status": "saved", "note": None,
                    "next_action": "HR" if method == "PUT" else None,
                    "next_action_due_on": "2026-09-12" if method == "PUT" else None}, "job": {"id": 7}}
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=payload)
        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))
        try:
            result = (await client.set_application_next_action(4, 18, "HR", "2026-09-12")
                      if method == "PUT" else await client.delete_application_next_action(4, 18))
            assert result == payload
            assert requests[0].method == method
            assert requests[0].url.path == "/users/4/applications/18/next-action"
            if method == "PUT":
                assert json.loads(requests[0].content) == {"next_action": "HR", "next_action_due_on": "2026-09-12"}
            else:
                assert requests[0].content == b""
        finally:
            await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [{"id": 19}, {"user_id": 5}, {"job_id": 8},
    {"next_action": None}, {"next_action": 123}, {"next_action_due_on": None},
    {"next_action_due_on": "2026-02-29"}, {"next_action_due_on": "2026-09-12T00:00:00Z"}])
def test_next_action_client_rejects_invalid_detail(change):
    application = {"id": 18, "user_id": 4, "job_id": 7, "status": "saved", "note": None,
                   "next_action": "HR", "next_action_due_on": "2026-09-12", **change}
    response = httpx.Response(200, json={"application": application, "job": {"id": 7}},
                              request=httpx.Request("PUT", "http://api/next-action"))
    with pytest.raises(httpx.DecodingError):
        JobHunterApiClient._next_action_detail(response, 4, 18)


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_note_client_contract(method):
    async def scenario():
        requests = []
        payload = {"application": {"id": 18, "user_id": 4, "job_id": 7,
                    "status": "interview", "note": "text" if method == "PUT" else None},
                   "job": {"id": 7}}
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=payload)
        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))
        try:
            if method == "PUT":
                result = await client.put_application_note(4, 18, "text")
            else:
                result = await client.delete_application_note(4, 18)
            assert result == payload
            assert requests[0].method == method
            assert requests[0].url.path == "/users/4/applications/18/note"
            assert requests[0].content == (b'{"note":"text"}' if method == "PUT" else b"")
        finally:
            await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [{"id": 19}, {"user_id": 5}, {"job_id": 8}, {"note": 123}, {"status": "invalid"}])
def test_note_client_rejects_invalid_detail(change):
    application = {"id": 18, "user_id": 4, "job_id": 7, "status": "saved", "note": "text", **change}
    response = httpx.Response(200, json={"application": application, "job": {"id": 7}},
                              request=httpx.Request("PUT", "http://api/note"))
    with pytest.raises(httpx.DecodingError):
        JobHunterApiClient._note_detail(response, 4, 18)


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


def test_normalize_profile_skills_uses_stateless_api_endpoint() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"skills": ["Python", "PostgreSQL"]}, request=request)

        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))
        try:
            result = await client.normalize_profile_skills(["python", "postgres"])
        finally:
            await client.close()

        assert result == ["Python", "PostgreSQL"]
        assert requests[0].method == "POST"
        assert requests[0].url.path == "/profile/skills/normalize"
        assert requests[0].content == b'{"skills":["python","postgres"]}'

    asyncio.run(scenario())


def test_normalize_profile_languages_uses_stateless_api_endpoint() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={"languages": [{"language": "English", "level": "B1"}]},
                request=request,
            )

        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))
        try:
            result = await client.normalize_profile_languages([{"language": "English", "level": "b1"}])
        finally:
            await client.close()

        assert result == [{"language": "English", "level": "B1"}]
        assert requests[0].method == "POST"
        assert requests[0].url.path == "/profile/languages/normalize"
        assert requests[0].content == b'{"languages":[{"language":"English","level":"b1"}]}'

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


@pytest.mark.parametrize("outcome", ["success", "404", "422", "500", "network", "json", "shape", "wrong_owner", "wrong_status"])
def test_put_application_status_contract_and_errors(outcome):
    async def scenario():
        requests = []
        payload = {"application": {"id": 18, "user_id": 4, "job_id": 2, "status": "applied"}, "job": {"id": 2}}
        def handler(request):
            requests.append(request)
            if outcome == "network":
                raise httpx.ConnectError("test", request=request)
            if outcome.isdecimal():
                return httpx.Response(int(outcome), json={"detail": {"code": "APPLICATION_NOT_FOUND"}})
            if outcome == "json":
                return httpx.Response(200, content=b"invalid json")
            if outcome == "shape":
                return httpx.Response(200, json={"application": {}, "job": []})
            if outcome == "wrong_owner":
                payload["application"]["user_id"] = 999
            if outcome == "wrong_status":
                payload["application"]["status"] = "garbage"
            return httpx.Response(200, json=payload)
        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))
        try:
            if outcome == "success":
                assert await client.put_application_status(4, 18, "applied") == payload
            else:
                with pytest.raises(httpx.HTTPError):
                    await client.put_application_status(4, 18, "applied")
        finally:
            await client.close()
        assert len(requests) == 1
        assert requests[0].method == "PUT"
        assert requests[0].url.path == "/users/4/applications/18/status"
        assert requests[0].content == b'{"status":"applied"}'
    asyncio.run(scenario())


@pytest.mark.parametrize(
    "outcome", ["success", "404", "500", "network", "shape", "status", "naive_time"]
)
def test_get_application_status_history_contract_and_errors(outcome):
    async def scenario():
        requests = []
        payload = {
            "items": [{"status": "interview", "occurred_at": "2026-09-07T17:14:00+00:00"}]
        }

        def handler(request):
            requests.append(request)
            if outcome == "network":
                raise httpx.ConnectError("test", request=request)
            if outcome.isdecimal():
                return httpx.Response(int(outcome), json={"detail": "test"})
            if outcome == "shape":
                return httpx.Response(200, json={"items": {}})
            if outcome == "status":
                payload["items"][0]["status"] = "unknown"
            if outcome == "naive_time":
                payload["items"][0]["occurred_at"] = "2026-09-07T17:14:00"
            return httpx.Response(200, json=payload)

        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="http://api", transport=httpx.MockTransport(handler)
        )
        try:
            if outcome == "success":
                assert await client.get_application_status_history(4, 18) == payload
            else:
                with pytest.raises(httpx.HTTPError):
                    await client.get_application_status_history(4, 18)
        finally:
            await client.close()
        assert len(requests) == 1
        assert requests[0].method == "GET"
        assert requests[0].url.path == "/users/4/applications/18/status-history"

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [None, "saved", "applied", "interview", "rejected", "offer"])
@pytest.mark.parametrize("q", [None, "Python & SQL"])
@pytest.mark.parametrize("sort", ["newest", "oldest", "next_action"])
def test_list_applications_filter_query(status, q, sort):
    async def scenario():
        def handler(request):
            assert request.method == "GET"
            assert request.url.path == "/users/7/applications"
            expected = {"limit": "5", "offset": "10"}
            expected["sort"] = sort
            if status is not None:
                expected["status"] = status
            if q is not None:
                expected["q"] = q
            assert dict(request.url.params) == expected
            return httpx.Response(200, json={"items": [], "has_next": False})
        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))
        try:
            assert await client.list_applications(
                7, limit=5, offset=10, status=status, q=q, sort=sort
            ) == {"items": [], "has_next": False}
        finally:
            await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", [422, 500, "timeout"])
def test_list_applications_filter_does_not_mask_http_error(outcome):
    async def scenario():
        def handler(request):
            if outcome == "timeout":
                raise httpx.ReadTimeout("test", request=request)
            return httpx.Response(outcome, json={"detail": "test"})
        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(httpx.HTTPError):
                await client.list_applications(7, limit=5, offset=0, status="saved")
        finally:
            await client.close()
    asyncio.run(scenario())
