import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from app.models import UserProfile, ProfileExperienceFact, WorkExperience, User
from app.database import get_session
from app.main import app
from conftest import client, TestSessionLocal
from test_work_experiences import setup
from test_auth import plain, register, login, bearer
from test_cv_import import account, upload, action


def test_patch_missing_null_blank_and_unrelated_profile_and_facts():
    user, url = setup()
    original = client.get(f'/users/{user}/profile').json()
    fact = client.post(f'/users/{user}/profile/experience-facts', json={'text': 'Built APIs'}).json()
    entry = client.post(url, json={'company': 'Acme', 'position': 'Engineer', 'start_year': 2020, 'start_month': 3, 'is_current': False, 'end_year': 2024}).json()
    path = f"{url}/{entry['id']}"
    updated = client.patch(path, json={'position': ' Senior   Engineer '}).json()
    assert updated['position'] == 'Senior Engineer'
    for field in ('company', 'start_year', 'start_month', 'end_year', 'is_current', 'created_at', 'id'):
        assert updated[field] == entry[field]
    updated = client.patch(path, json={'company': '  ', 'start_month': None, 'end_year': None}).json()
    assert updated['company'] is updated['start_month'] is updated['end_year'] is None
    assert client.patch(path, json={'position': None}).status_code == 422
    assert client.get(f'/users/{user}/profile').json() == original
    assert client.get(f'/users/{user}/profile/experience-facts').json()['items'] == [fact]
    # Two stale tabs changing different fields preserve each other's changes.
    assert client.patch(path, json={'company': 'Latest'}).status_code == 200
    assert client.patch(path, json={'engagement_kind': 'freelance'}).json()['company'] == 'Latest'
    assert client.delete(path).status_code == 204
    assert client.delete(path).status_code == 404
    assert client.get(f'/users/{user}/profile/experience-facts').json()['items'] == [fact]


def test_current_end_interaction_and_partial_dates():
    _, url = setup()
    entry = client.post(url, json={'position': 'Engineer', 'start_year': 2020, 'end_year': 2024, 'end_month': 3, 'is_current': False}).json()
    path = f"{url}/{entry['id']}"
    assert client.patch(path, json={'is_current': True}).status_code == 422
    assert client.patch(path, json={'is_current': True, 'end_year': None}).status_code == 422
    current = client.patch(path, json={'is_current': True, 'end_year': None, 'end_month': None}).json()
    assert current['is_current'] is True and current['end_year'] is current['end_month'] is None
    assert client.patch(path, json={'is_current': None}).json()['is_current'] is None
    assert client.patch(path, json={'start_year': None, 'start_month': 1}).status_code == 422
    assert client.patch(path, json={'is_current': False, 'end_year': 2019}).status_code == 422
    assert client.patch(path, json={}).status_code == 200


@pytest.mark.parametrize('payload', [
    {'position': 'x'*201}, {'engagement_kind': 'fulltime'}, {'engagement_kind': None},
    {'start_year': 9999}, {'start_month': 13}, {'is_current': 'true'}, {'start_year': True},
    {'company': 'Acme\x00'}, {'user_profile_id': 1},
])
def test_patch_rejects_invalid_without_mutation(payload):
    _, url = setup()
    entry = client.post(url, json={'company': 'Acme'}).json()
    assert client.patch(f"{url}/{entry['id']}", json=payload).status_code == 422
    assert client.get(url).json()['items'] == [entry]


@pytest.mark.parametrize('payload', [
    {'engagement_kind': 'invalid-review-value'},
    {'start_year': {'private': 'invalid-review-value'}},
    {'position': 'invalid-review-value' * 1000},
    {'unexpected': 'invalid-review-value'},
])
def test_patch_request_validation_is_bounded_and_does_not_echo_input(payload):
    _, url = setup()
    entry = client.post(url, json={'position': 'Engineer'}).json()
    response = client.patch(f"{url}/{entry['id']}", json=payload)
    assert response.status_code == 422
    assert response.json() == {'detail': {'code': 'WORK_EXPERIENCE_INVALID'}}
    assert len(response.content) < 100
    assert response.headers['Cache-Control'] == 'no-store'
    for forbidden in ('invalid-review-value', 'input', 'ctx', 'type', 'loc', 'msg'):
        assert forbidden not in response.text
    assert client.get(url).json()['items'] == [entry]


def test_bearer_ownership_and_anonymous_resource_operations():
    register(); raw = login()['session_token']
    with TestSessionLocal() as session:
        owner = session.scalar(select(User.id))
        other = User(telegram_id=990088); session.add(other); session.flush()
        other_id = other.id
        session.add_all([UserProfile(user_id=owner), UserProfile(user_id=other_id)]); session.commit()
    own_url = f'/users/{owner}/profile/work-experiences'
    other_url = f'/users/{other_id}/profile/work-experiences'
    foreign = client.post(other_url, json={'company': 'Foreign'}).json()
    entry = plain.post(own_url, headers=bearer(raw), json={'position': 'Owner'}).json()
    for method, path, body in [('GET', other_url, None), ('POST', other_url, {'company': 'No'}), ('PATCH', other_url+f"/{foreign['id']}", {'company': 'No'}), ('DELETE', other_url+f"/{foreign['id']}", None), ('PATCH', own_url+f"/{foreign['id']}", {'company': 'No'}), ('DELETE', own_url+f"/{foreign['id']}", None)]:
        assert plain.request(method, path, headers=bearer(raw), json=body).status_code == 404
    for method, path, body in [('GET', own_url, None), ('POST', own_url, {'company': 'No'}), ('PATCH', own_url+f"/{entry['id']}", {'company': 'No'}), ('DELETE', own_url+f"/{entry['id']}", None)]:
        assert plain.request(method, path, json=body).status_code == 401
    assert client.get(other_url).json()['items'] == [foreign]


@pytest.mark.parametrize('mutation', ['create', 'patch', 'delete'])
def test_manual_edit_cv_snapshot_stale_and_replacement(account, mutation):
    uid, raw, _ = account
    headers = bearer(raw)
    profile_url = f'/users/{uid}/profile'
    assert plain.put(profile_url, headers=headers, json={'target_roles': ['Engineer']}).status_code == 200
    url = profile_url+'/work-experiences'
    entry = plain.post(url, headers=headers, json={'company': 'Manual'}).json()
    preview = upload(account).json()
    path = f"{url}/{entry['id']}"
    if mutation == 'create': assert plain.post(url, headers=headers, json={'company': 'Additional'}).status_code == 201
    if mutation == 'patch': assert plain.patch(path, headers=headers, json={'position': 'Changed'}).status_code == 200
    if mutation == 'delete': assert plain.delete(path, headers=headers).status_code == 204
    stale = action(account, preview)
    assert stale.status_code == 409 and stale.json()['detail']['code'] == 'cv_import_stale'
    fresh = upload(account).json()
    assert fresh['work_experience_mode'] == 'replace'
    assert action(account, fresh).json() == {'ok': True}
    result = plain.get(url, headers=headers).json()['items']
    assert len(result) == 1 and result[0]['company'] == 'Acme'
    assert plain.get(profile_url, headers=headers).status_code == 200


@pytest.mark.parametrize('method', ['patch', 'delete'])
def test_database_failure_rolls_back_without_exposing_details(monkeypatch, method):
    _, url = setup()
    entry = client.post(url, json={'company': 'Acme'}).json()
    with TestSessionLocal() as session:
        previous = app.dependency_overrides[get_session]
        app.dependency_overrides[get_session] = lambda: session
        def fail(): raise OperationalError('private SQL', {'secret': 'hidden'}, RuntimeError('hidden'))
        monkeypatch.setattr(session, 'commit', fail)
        try:
            response = getattr(client, method)(f"{url}/{entry['id']}", **({'json': {'position': 'Changed'}} if method == 'patch' else {}))
            assert response.status_code == 503
            assert response.json()['detail']['code'] == 'WORK_EXPERIENCE_UNCONFIRMED'
            assert 'hidden' not in response.text and 'private' not in response.text
            assert session.is_active
            assert session.get(WorkExperience, entry['id']).position is None
        finally: app.dependency_overrides[get_session] = previous
