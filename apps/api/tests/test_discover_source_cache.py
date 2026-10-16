"""API/service freshness and provider regressions with injected source transport."""

import json
import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.services.discover as discover_service
from app.services.source_search_cache import SourceSearchCache
from app.services.trudvsem import TrudvsemClient
from conftest import client
from test_discover_api import StubClient, create_user, put_profile, vacancy
from test_trudvsem_client import response, vacancy_payload


PARAMS = {"market_country": "RU", "query": "Backend", "limit": 10, "offset": 0}


class DisabledAI:
    configured = False


def search(user_id, **params):
    result = client.get(f"/users/{user_id}/discover/jobs", params={**PARAMS, **params})
    assert result.status_code == 200
    return result.json()


def test_cached_source_keeps_save_status_and_other_users_fresh(monkeypatch):
    owner, other = create_user(9101), create_user(9102)
    source = StubClient()
    monkeypatch.setattr(main_module, "trudvsem_client", source)
    monkeypatch.setattr(main_module, "ai_enrichment_service", DisabledAI())
    first = search(owner)["items"][0]
    assert first["application_id"] is None
    body = {key: first[key] for key in ("source", "source_scope", "external_id")}
    saved = client.post(f"/users/{owner}/discover/jobs/save", json=body)
    assert saved.status_code == 200
    app_id = saved.json()["application"]["id"]
    after_save = search(owner)["items"][0]
    assert after_save["application_id"] == app_id and after_save["application_status"] == "saved"
    changed = client.put(f"/users/{owner}/applications/{app_id}/status", json={"status": "interview"})
    assert changed.status_code == 200
    after_change = search(owner)["items"][0]
    assert after_change["application_id"] == app_id
    assert after_change["application_status"] == "interview"
    others_item = search(other)["items"][0]
    assert others_item["application_id"] is None
    assert others_item["application_status"] is None
    assert others_item["already_saved_for_user"] is False
    repeated_save = client.post(f"/users/{owner}/discover/jobs/save", json=body)
    assert repeated_save.status_code == 200
    assert repeated_save.json()["application"]["status"] == "interview"
    assert len(source.search_calls) == 1
    assert source.detail_calls == [("company-1", "vacancy-1")] * 2


def test_cache_hits_read_profile_and_recalculate_v21_matching_for_each_user(monkeypatch):
    owner, other = create_user(9103), create_user(9104)
    source, previews = StubClient(), []
    monkeypatch.setattr(main_module, "trudvsem_client", source)
    calculate = discover_service.calculate_match_preview

    def record(profile, job, *, evidence):
        previews.append(tuple(profile.target_roles))
        return calculate(profile, job, evidence=evidence)

    monkeypatch.setattr(discover_service, "calculate_match_preview", record)
    assert search(owner)["items"][0]["preview_match"]["available"] is False
    put_profile(owner)
    first = search(owner)["items"][0]["preview_match"]
    assert first["components"]["role"]["score"] == 100
    put_profile(owner, target_roles=["Accountant"])
    changed = search(owner)["items"][0]["preview_match"]
    assert changed["components"]["role"]["score"] == 0
    assert changed["algorithm_version"] == first["algorithm_version"] == "job-match-v2.1"
    assert search(other)["items"][0]["preview_match"]["available"] is False
    assert previews == [("Backend Developer",), ("Accountant",)]
    assert len(source.search_calls) == 1


def test_remote_only_reuses_source_page_and_filters_again(monkeypatch):
    source = StubClient([vacancy("onsite"), vacancy("remote", workplace_type="remote")], total=12)
    monkeypatch.setattr(main_module, "trudvsem_client", source)
    user_id = create_user(9105)
    all_jobs = search(user_id)
    remote = search(user_id, remote_only=True)
    again = search(user_id, remote_only=False)
    assert [item["external_id"] for item in all_jobs["items"]] == ["onsite", "remote"]
    assert [item["external_id"] for item in remote["items"]] == ["remote"]
    assert remote["locally_filtered"] and remote["returned_count"] == 1
    assert remote["source_total"] == all_jobs["source_total"] == 12
    assert remote["next_offset"] == all_jobs["next_offset"] == 10
    assert again == all_jobs
    assert len(source.search_calls) == 1


def test_auth_and_user_lookup_are_not_bypassed_on_cache_hit(monkeypatch):
    source = StubClient()
    monkeypatch.setattr(main_module, "trudvsem_client", source)
    user_id = create_user(9106)
    search(user_id)
    missing = client.get("/users/999999/discover/jobs", params=PARAMS)
    assert missing.status_code == 404
    assert missing.json() == {"detail": {"code": "USER_NOT_FOUND"}}
    anonymous = TestClient(main_module.app).get(f"/users/{user_id}/discover/jobs", params=PARAMS)
    assert anonymous.status_code == 401
    assert len(source.search_calls) == 1


def test_follower_wait_timeout_keeps_504_mapping_and_owner_can_complete(monkeypatch, concurrent_database):
    entered, release = Event(), Event()

    class BlockingClient(StubClient):
        def search(self, query, *, limit, offset, region_code=None):
            result = super().search(query, limit=limit, offset=offset, region_code=region_code)
            entered.set()
            assert release.wait(5)
            return result

    source = BlockingClient()
    monkeypatch.setattr(main_module, "trudvsem_client", source)
    monkeypatch.setattr(main_module, "discover_source_cache", SourceSearchCache(follower_wait_seconds=0.001))
    user_id = create_user(9109)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            owner = pool.submit(search, user_id)
            assert entered.wait(5)
            follower = client.get(f"/users/{user_id}/discover/jobs", params=PARAMS)
            assert follower.status_code == 504
            assert follower.json() == {"detail": {"code": "source_timeout"}}
            assert main_module.discover_source_cache.info().inflight == 1
            assert len(source.search_calls) == 1
        finally:
            release.set()
        result = owner.result(5)
    assert search(user_id) == result
    assert main_module.discover_source_cache.info().inflight == 0
    assert len(source.search_calls) == 1


def test_real_provider_pagination_expiry_empty_pages_and_invalid_offsets(monkeypatch):
    calls, now = [], [0.0]

    def transport(url, timeout):
        params = parse_qs(urlsplit(url).query)
        page = int(params["offset"][0])
        calls.append((page, timeout))
        batch = [vacancy_payload(id=f"id-{i}") for i in range(page * 10, min(page * 10 + 10, 12))]
        return 200, json.dumps({
            "status": "200", "meta": {"total": 12},
            "results": {"vacancies": batch} if batch else {},
        }).encode()

    monkeypatch.setattr(main_module, "trudvsem_client", TrudvsemClient(transport=transport))
    monkeypatch.setattr(main_module, "discover_source_cache", SourceSearchCache(clock=lambda: now[0]))
    user_id = create_user(9107)
    for offset, count, next_offset in [(0, 10, 10), (10, 2, None), (20, 0, None)]:
        page = search(user_id, offset=offset)
        assert page["returned_count"] == count and page["next_offset"] == next_offset
        assert page["source_total"] == 12
        assert search(user_id, offset=offset) == page
    assert calls == [(0, 25.0), (1, 25.0), (2, 25.0)]
    invalid = client.get(f"/users/{user_id}/discover/jobs", params={**PARAMS, "offset": 1})
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": {"code": "INVALID_DISCOVER_QUERY"}}
    assert len(calls) == 3
    now[0] = 120
    search(user_id)
    assert calls == [(0, 25.0), (1, 25.0), (2, 25.0), (0, 25.0)]


@pytest.mark.parametrize(("failure", "status", "code"), [
    (socket.timeout(), 504, "source_timeout"),
    (HTTPError("https://source.test", 503, "failed", {}, None), 503, "source_unavailable"),
    ((429, b"{}"), 429, "source_rate_limited"),
    ((200, b"invalid-json"), 502, "source_bad_response"),
    ((200, b'{"status":"200","meta":{"total":12},"results":{}}'), 502, "source_bad_response"),
])
def test_source_errors_do_not_poison_same_page_cache(monkeypatch, failure, status, code):
    calls = []

    def transport(_url, _timeout):
        calls.append(1)
        if len(calls) == 1:
            if isinstance(failure, Exception):
                raise failure
            return failure
        return 200, response(vacancy_payload(), total=25)

    monkeypatch.setattr(main_module, "trudvsem_client", TrudvsemClient(transport=transport))
    user_id = create_user(9108)
    failed = client.get(f"/users/{user_id}/discover/jobs", params=PARAMS)
    assert failed.status_code == status and failed.json() == {"detail": {"code": code}}
    assert main_module.discover_source_cache.info().entries == 0
    assert search(user_id) == search(user_id)
    assert len(calls) == 2
