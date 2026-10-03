from datetime import datetime, timezone

from sqlalchemy import select

from app.models import ApplicationStatus, User
from conftest import TestSessionLocal, client
from test_applications_list import _application, _user
from test_auth import bearer, login, plain, register


def test_summary_counts_all_statuses_beyond_first_page_and_isolates_users():
    owner, other = _user(901), _user(902)
    now = datetime.now(timezone.utc)
    for status in ApplicationStatus:
        for n in range(3):
            _application(owner, f"{status.value}-{n}", now, status=status.value)
    _application(other, "Other", now, status="offer")
    response = client.get(f"/users/{owner}/applications/summary")
    assert response.status_code == 200
    assert response.json() == {
        "total": 24, "status_counts": {status.value: 3 for status in ApplicationStatus},
    }
    assert len(client.get(f"/users/{owner}/applications").json()["items"]) == 5
    assert client.get(f"/users/{other}/applications/summary").json()["total"] == 1


def test_empty_summary_includes_zero_for_every_canonical_status():
    owner = _user(903)
    assert client.get(f"/users/{owner}/applications/summary").json() == {
        "total": 0, "status_counts": {status.value: 0 for status in ApplicationStatus},
    }


def test_summary_requires_session_and_forbids_other_owner():
    other = _user(904)
    register()
    token = login()["session_token"]
    with TestSessionLocal() as session:
        owner = session.scalar(select(User.id).where(User.email.is_not(None)))
    url = f"/users/{owner}/applications/summary"
    assert plain.get(url).status_code == 401
    assert plain.get(url, headers=bearer("x" * 43)).status_code == 401
    assert plain.get(url, headers=bearer(token)).json()["total"] == 0
    assert plain.get(f"/users/{other}/applications/summary", headers=bearer(token)).status_code == 404
    plain.post("/auth/logout", headers=bearer(token))
    assert plain.get(url, headers=bearer(token)).status_code == 401
