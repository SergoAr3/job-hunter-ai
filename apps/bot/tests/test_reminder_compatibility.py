import asyncio
import httpx
import pytest

from app.api_client import JobHunterApiClient
from app.applications import APPLICATIONS_DETAIL_VIEW, APPLICATIONS_VIEW, _application_detail_content, _follow_up_queue_content
from test_application_next_actions import run


@pytest.mark.parametrize("zone,valid", [("Asia/Yerevan", True), ("Bad/Zone", False)])
def test_nullable_date_exact_queue_contract(zone, valid):
    async def scenario():
        item = {"application_id": 18, "title": None, "company": None, "status": "applied", "next_action": "Call", "next_action_due_on": None,
                "next_action_remind_at": "2026-10-10T07:02:00Z", "next_action_timezone": zone, "reminder_delivery_state": "sent", "due_state": "overdue"}
        client = JobHunterApiClient("http://api")
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(lambda req: httpx.Response(200,json={"items":[item],"has_next":False})))
        try:
            if valid:
                assert (await client.list_application_follow_ups(4,limit=5,offset=0))["items"][0]["next_action_due_on"] is None
                text, _ = _follow_up_queue_content([item],offset=0,has_next=False,token="test")
                assert "11:02" in text and "Asia/Yerevan" in text
                detail, _ = _application_detail_content({"application":item,"job":{"title":"Test"}},18,0)
                assert "Call" in detail and "11:02" in detail
            else:
                with pytest.raises(httpx.DecodingError):
                    await client.list_application_follow_ups(4,limit=5,offset=0)
        finally:
            await client.close()
    asyncio.run(scenario())


def test_dispatcher_exact_action_stays_detail_and_does_not_start_legacy_editor(monkeypatch):
    async def scenario(ui, api, state):
        original = api.get_application
        async def get(user_id, app_id):
            detail = await original(user_id, app_id)
            detail["application"].update(next_action_remind_at="2026-10-10T07:02:00Z",next_action_timezone="Asia/Yerevan")
            return detail
        api.get_application = get
        await ui.click("📅 Следующее действие")
        assert await state.get_state() is None
        assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_DETAIL_VIEW
        assert api.action_puts == [] and api.action_deletes == []
        assert "Точное напоминание настроено в Web" in ui.text
    run(monkeypatch, scenario, ("Call", None))
