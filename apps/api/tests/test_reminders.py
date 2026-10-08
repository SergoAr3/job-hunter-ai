import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from app.models import Application, ApplicationReminder, User
from app.services import reminders as r
from app.services.telegram_notifications import Outcome
from app.workers.reminders import process_one
from conftest import TestSessionLocal, client
from test_match_api import create_user, create_application

NOW = datetime(2026, 10, 10, 7, tzinfo=timezone.utc)
AT = NOW + timedelta(minutes=2)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(r, "utc_now", lambda: NOW)


def setup():
    owner = create_user(90001)
    app_id, _ = create_application(owner)
    return owner, app_id, f"/users/{owner}/applications/{app_id}"


def schedule(url, **changes):
    return client.patch(url, json={"next_action": "Call HR", "next_action_remind_at": "2026-10-10T11:02:00+04:00",
        "next_action_timezone": "Asia/Yerevan", **changes})


def test_lifecycle_idempotency_text_state_legacy_done():
    owner, app_id, url = setup()
    client.patch(url, json={"note": "Keep"})
    client.put(url + "/next-action", json={"next_action": "Old", "next_action_due_on": "2026-10-10"})
    assert schedule(url).status_code == 200
    with TestSessionLocal() as session:
        gen = session.get(ApplicationReminder, app_id).generation
    value = schedule(url).json()["application"]
    assert value["next_action_due_on"] is None
    assert value["next_action_remind_at"] == "2026-10-10T07:02:00Z"
    assert not {"generation", "lease_token", "telegram_id", "attempt_count"} & value.keys()
    with TestSessionLocal() as session:
        assert session.get(ApplicationReminder, app_id).generation == gen
        claim = r.claim_due(session, AT)
        r.complete(session, claim, Outcome("sent"), AT)
    assert client.patch(url, json={"next_action": "New text"}).status_code == 200
    assert schedule(url, next_action="New text").json()["application"]["reminder_delivery_state"] == "sent"
    assert client.put(url + "/next-action", json={"next_action": "Legacy", "next_action_due_on": "2026-10-12"}).status_code == 409
    client.put(url + "/status", json={"status": "rejected"})
    assert client.get(url).json()["application"]["reminder_delivery_state"] == "sent"
    assert client.patch(url, json={"next_action_remind_at": None}).json()["application"]["next_action"] == "New text"
    schedule(url)
    value = client.patch(url, json={"next_action": None}).json()["application"]
    assert value["next_action_remind_at"] is value["next_action_due_on"] is value["next_action"] is None
    assert value["note"] == "Keep" and value["status"] == "rejected"


@pytest.mark.parametrize("changes,code", [
    ({"next_action": None}, "REMINDER_ACTION_REQUIRED"),
    ({"next_action_timezone": "Bad/Zone"}, "REMINDER_TIMEZONE_INVALID"),
    ({"next_action_remind_at": "2026-10-10T11:02:00"}, "APPLICATION_INVALID"),
    ({"next_action_remind_at": "2026-10-10T11:02:00+03:00"}, "REMINDER_OFFSET_MISMATCH"),
    ({"next_action_remind_at": "2026-10-10T10:00:00+04:00"}, "REMINDER_TOO_SOON"),
    ({"next_action_remind_at": "2026-10-10T11:00:30+04:00"}, "REMINDER_TOO_SOON"),
    ({"next_action_remind_at": "2027-03-14T02:30:00-05:00", "next_action_timezone": "America/New_York"}, "REMINDER_TIME_NONEXISTENT"),
    ({"next_action_remind_at": "2026-11-01T01:30:00-04:00", "next_action_timezone": "America/New_York"}, "REMINDER_TIME_AMBIGUOUS"),
    ({"generation": "secret-input"}, "APPLICATION_INVALID"),
])
def test_validation_atomic_bounded(changes, code):
    _, app_id, url = setup()
    before = client.get(url).json()
    response = schedule(url, **changes)
    assert response.status_code == 422 and response.json() == {"detail": {"code": code}}
    assert len(response.content) < 150 and "secret-input" not in response.text
    assert client.get(url).json() == before
    with TestSessionLocal() as session:
        assert session.get(ApplicationReminder, app_id) is None


def test_ownership_clear_recreate_stale_completion_and_cascade():
    owner, app_id, url = setup()
    other = create_user(90002)
    assert schedule(f"/users/{other}/applications/{app_id}").status_code == 404
    schedule(url)
    with TestSessionLocal() as session:
        claim = r.claim_due(session, AT)
    client.patch(url, json={"next_action_remind_at": None})
    schedule(url)
    with TestSessionLocal() as session:
        assert session.get(ApplicationReminder, app_id).generation != claim.generation
        assert r.preflight(session, claim, AT) is None
        assert r.complete(session, claim, Outcome("sent"), AT) is None
        session.execute(delete(Application).where(Application.id == app_id))
        session.commit()
        assert session.get(ApplicationReminder, app_id) is None


def test_worker_claim_preflight_success_no_normal_duplicates():
    _, app_id, url = setup()
    schedule(url)
    with TestSessionLocal() as session:
        assert r.claim_due(session, NOW) is None
        claim = r.claim_due(session, AT)
        assert not session.in_transaction()
        assert r.claim_due(session, AT) is None
        destination = r.preflight(session, claim, AT)
        assert not session.in_transaction() and destination.action == "Call HR"
        assert r.complete(session, claim, Outcome("sent"), AT)
        assert r.claim_due(session, AT + timedelta(minutes=1)) is None
        assert session.get(Application, app_id).next_action == "Call HR"


def test_safe_retries_budget_rate_limit_and_stale_token():
    _, app_id, url = setup()
    schedule(url)
    with TestSessionLocal() as session:
        first = r.claim_due(session, AT)
        r.complete(session, first, Outcome("retry", "RATE_LIMITED", 200), AT)
        assert r.claim_due(session, AT + timedelta(seconds=199)) is None
        second = r.claim_due(session, AT + timedelta(seconds=200))
        assert not r.complete(session, first, Outcome("sent"), AT + timedelta(seconds=201))
        r.complete(session, second, Outcome("retry", "CONNECT_FAILED"), AT + timedelta(seconds=200))
        third = r.claim_due(session, AT + timedelta(seconds=500))
        r.complete(session, third, Outcome("retry", "CONNECT_FAILED"), AT + timedelta(seconds=500))
        value = session.get(ApplicationReminder, app_id)
        assert value.delivery_state == "failed" and value.attempt_count == 3


@pytest.mark.parametrize("outcome", [Outcome("failed", "DELIVERY_UNCERTAIN"), Outcome("failed", "CHAT_UNAVAILABLE")])
def test_failure_never_requeued(outcome):
    _, app_id, url = setup()
    schedule(url)
    with TestSessionLocal() as session:
        claim = r.claim_due(session, AT)
        r.complete(session, claim, outcome, AT)
        assert r.claim_due(session, AT + timedelta(hours=1)) is None
        assert session.get(ApplicationReminder, app_id).last_error_code == outcome.code


def test_expired_lease_restart_uncertain_missing_telegram_and_24h():
    owner, app_id, url = setup()
    schedule(url)
    with TestSessionLocal() as session:
        claim = r.claim_due(session, AT)
    with TestSessionLocal() as session:
        assert r.recover_expired(session, AT + timedelta(seconds=120)) == 1
        assert not r.complete(session, claim, Outcome("sent"), AT + timedelta(seconds=121))
        assert r.claim_due(session, AT + timedelta(hours=1)) is None
    client.patch(url, json={"next_action_remind_at": "2026-10-10T11:03:00+04:00", "next_action_timezone": "Asia/Yerevan"})
    with TestSessionLocal() as session:
        user = session.get(User, owner)
        user.email = user.email_canonical = "no-telegram@example.com"
        user.password_hash = "unused"
        user.telegram_id = None
        session.commit()
        claim = r.claim_due(session, AT + timedelta(minutes=1))
        assert r.preflight(session, claim, AT + timedelta(minutes=1)) is None
        assert session.get(ApplicationReminder, app_id).last_error_code == "TELEGRAM_NOT_CONNECTED"
    client.patch(url, json={"next_action_remind_at": "2026-10-10T11:04:00+04:00", "next_action_timezone": "Asia/Yerevan"})
    with TestSessionLocal() as session:
        assert r.claim_due(session, AT + timedelta(days=2)) is None
        assert session.get(ApplicationReminder, app_id).last_error_code == "DELIVERY_EXPIRED"


def test_success_completion_db_retry_without_resend(monkeypatch):
    _, _, url = setup()
    schedule(url)
    original = r.complete
    complete = Mock(side_effect=[SQLAlchemyError("private payload"), None])
    def finish(session, claim, outcome, now):
        complete()
        return original(session, claim, outcome, now)
    monkeypatch.setattr(r, "complete", finish)
    async def no_sleep(*args):
        pass
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    class Sender:
        calls = 0
        async def send(self, *args):
            self.calls += 1
            return Outcome("sent")
    sender = Sender()
    assert asyncio.run(process_one(TestSessionLocal, sender, lambda: AT))
    assert sender.calls == 1 and complete.call_count == 2


@pytest.mark.parametrize("attempts,outcome,elapsed,state,code,event", [
    (2, Outcome("retry", "CONNECT_FAILED"), 0, "failed", "RETRY_EXHAUSTED", "reminder_failed"),
    (0, Outcome("sent"), 121, "failed", "DELIVERY_UNCERTAIN", "reminder_uncertain"),
    (0, Outcome("sent"), 0, "sent", None, "reminder_sent"),
    (0, Outcome("retry", "CONNECT_FAILED"), 0, "pending", "CONNECT_FAILED", "reminder_retry"),
    (0, Outcome("failed", "CHAT_UNAVAILABLE"), 0, "failed", "CHAT_UNAVAILABLE", "reminder_failed"),
])
def test_worker_logs_persisted_completion_with_safe_fields(
    caplog, attempts, outcome, elapsed, state, code, event,
):
    _, app_id, url = setup()
    schedule(url)
    with TestSessionLocal() as session:
        reminder = session.get(ApplicationReminder, app_id)
        reminder.attempt_count = attempts
        session.commit()
    ticks = iter([AT, AT, AT + timedelta(seconds=elapsed)])
    class Sender:
        calls = 0
        async def send(self, *args):
            self.calls += 1
            return outcome
    sender = Sender()
    caplog.set_level(logging.INFO, logger="reminders")
    assert asyncio.run(process_one(TestSessionLocal, sender, lambda: next(ticks)))
    assert sender.calls == 1
    with TestSessionLocal() as session:
        reminder = session.get(ApplicationReminder, app_id)
        assert reminder.delivery_state == state
        assert reminder.last_error_code == code
        assert reminder.attempt_count == attempts + 1
    events = [json.loads(record.message) for record in caplog.records if record.name == "reminders"]
    result = events[-1]
    assert result["event"] == event and result["error_code"] == code
    assert set(result) == {"event", "application_id", "generation", "error_code", "latency_ms"}
    assert set(events[0]) == {"event", "application_id", "generation"}
    assert "Call HR" not in caplog.text


def test_followups_local_midnight_sent_included_and_dst_day():
    _, app_id, url = setup()
    schedule(url)
    with TestSessionLocal() as session:
        from app.services.applications import list_application_follow_ups
        rows = list_application_follow_ups(session, int(url.split('/')[2]), limit=5, offset=0,
            timezone_name="Asia/Yerevan", bucket="today", now=NOW)
        assert rows[0][0].id == app_id
    response = client.get(url.rsplit('/', 1)[0] + '/follow-ups', params={"timezone": "Asia/Yerevan", "bucket": "today"})
    assert response.status_code == 200 and response.json()["items"][0]["next_action_due_on"] is None
    assert client.get(url.rsplit('/', 1)[0] + '/follow-ups', params={"timezone": "bad"}).status_code == 422


@pytest.mark.parametrize("now,boundary", [
    (datetime(2027, 3, 14, 5, 30, tzinfo=timezone.utc), datetime(2027, 3, 15, 4, tzinfo=timezone.utc)),
    (datetime(2026, 11, 1, 4, 30, tzinfo=timezone.utc), datetime(2026, 11, 2, 5, tzinfo=timezone.utc)),
])
def test_followups_23_and_25_hour_local_days(now, boundary):
    from app.services.applications import list_application_follow_ups
    owner = create_user(90001)
    apps = [create_application(owner, title=f"Day {index}")[0] for index in range(3)]
    with TestSessionLocal() as session:
        for app_id, instant in zip(apps, (now, boundary - timedelta(seconds=1), boundary)):
            app = session.get(Application, app_id)
            app.next_action = "Call"
            r.apply_schedule(session, app, {"next_action_remind_at": instant.astimezone(r.zone('America/New_York')), "next_action_timezone": 'America/New_York'}, now=now - timedelta(minutes=2))
        session.commit()
        overdue = list_application_follow_ups(session, owner, limit=5, offset=0, timezone_name='America/New_York', bucket='overdue', now=now)
        today = list_application_follow_ups(session, owner, limit=5, offset=0, timezone_name='America/New_York', bucket='today', now=now)
        upcoming = list_application_follow_ups(session, owner, limit=5, offset=0, timezone_name='America/New_York', bucket='upcoming', now=now)
        assert [row[0].id for row in overdue] == apps[:1]
        assert [row[0].id for row in today] == apps[1:2]
        assert [row[0].id for row in upcoming] == apps[2:]


def test_backend_presets_terminal_and_status_only_never_schedule():
    _, app_id, url = setup()
    for status, count in [('saved',1),('applied',2),('recruiter_response',2),('interview',2),('offer',2),('hired',0),('withdrawn',0),('rejected',0)]:
        client.put(url + '/status', json={'status':status})
        application = client.get(url).json()['application']
        assert len(application['next_action_suggestions']) == count
        assert application['next_action_remind_at'] is None
        assert all(set(suggestion) == {'id','label','action_text'} for suggestion in application['next_action_suggestions'])


@pytest.mark.parametrize('params', [{'timezone':'private-'+'x'*129},{'bucket':'private-rejected-input'},{'limit':0},{'offset':-1}])
def test_followups_query_validation_is_bounded_and_never_echoes_input(params):
    owner, _, _ = setup()
    response = client.get(f'/users/{owner}/applications/follow-ups',params=params)
    assert response.status_code == 422
    assert response.json() == {'detail':{'code':'FOLLOW_UPS_INVALID'}}
    assert 'private' not in response.text and len(response.content)<100
    assert response.headers['cache-control']=='no-store'
