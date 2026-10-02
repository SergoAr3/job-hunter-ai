import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timezone
import httpx
import pytest
from aiogram import Bot
from aiogram.types import Message, CallbackQuery, Update, User, Chat
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
os.environ.setdefault("TELEGRAM_BOT_TOKEN","123456:dispatcher-test-token")
from app import main as main_module
from app.telegram_auth import KEY, INVALID
from app.api_client import JobHunterApiClient

TOKEN="T"*43
ACTOR=User(id=123,is_bot=False,first_name="Actor",username="actor",language_code="ru")

def text_update(text,number=1,chat=123):
    return Update(update_id=number,message=Message(message_id=number,date=datetime.now(timezone.utc),chat=Chat(id=chat,type="private"),from_user=ACTOR,text=text))

def callback_update(data,number=2,message_id=10):
    return Update(update_id=number,callback_query=CallbackQuery(id=str(number),from_user=ACTOR,chat_instance="private",data=data,message=Message(message_id=message_id,date=datetime.now(timezone.utc),chat=Chat(id=123,type="private"),from_user=User(id=999,is_bot=True,first_name="Bot"),text="Confirm")))

class Api:
    def __init__(self):self.inspections=[];self.decisions=[];self.users=[];self.error=None;self.info={"purpose":"login","code":"A1B2C3","status":"pending"}
    async def inspect_telegram_challenge(self,token):
        self.inspections.append(token)
        if self.error:raise self.error
        return self.info
    async def decide_telegram_challenge(self,token,user,approve):
        self.decisions.append((token,user.id,approve))
        if self.error:raise self.error
    async def create_or_get_user(self,user):self.users.append(user.id);return 4

@pytest.mark.parametrize("purpose,decision",[("login","a"),("login","c"),("link","a"),("link","c")])
def test_dispatcher_deep_link_approval_cancel_replay_no_premature_user(monkeypatch,purpose,decision):
    async def scenario():
        bot=Bot("123456:dispatcher-test-token");api=Api();api.info["purpose"]=purpose
        monkeypatch.setattr(main_module,"api_client",api)
        answers=[];removed=[];acks=[]
        async def answer(self,text,reply_markup=None,**kwargs):answers.append((text,reply_markup));return SimpleNamespace(message_id=10)
        async def edit(self,**kwargs):removed.append(kwargs)
        async def ack(self,text=None,**kwargs):acks.append(text)
        monkeypatch.setattr(Message,"answer",answer);monkeypatch.setattr(Bot,"edit_message_reply_markup",edit);monkeypatch.setattr(CallbackQuery,"answer",ack)
        state=FSMContext(storage=main_module.dp.storage,key=StorageKey(bot_id=bot.id,chat_id=123,user_id=123));await state.clear()
        try:
            await main_module.dp.feed_update(bot,text_update("/start auth_"+TOKEN))
            assert api.inspections==[TOKEN] and api.users==[]
            assert (await state.get_data())[KEY]["token"]==TOKEN
            assert "A1B2C3" in answers[-1][0]
            assert ("вход в Job Hunter AI" if purpose=="login" else "подключение Telegram") in answers[-1][0]
            markup=answers[-1][1]
            assert all(len(button.callback_data.encode())<=64 and TOKEN not in button.callback_data for row in markup.inline_keyboard for button in row)
            await main_module.dp.feed_update(bot,callback_update("tga:"+decision+":A1B2C3"))
            assert api.decisions==[(TOKEN,123,decision=="a")] and api.users==[]
            assert removed[-1]["reply_markup"] is None and KEY not in await state.get_data()
            if purpose == "login":
                assert answers[-1][0] == ("Вход подтверждён. Вернитесь на сайт." if decision == "a" else "Вход отменён.")
            await main_module.dp.feed_update(bot,callback_update("tga:"+decision+":A1B2C3",3))
            assert len(api.decisions)==1 and "неактуальна" in acks[-1]
            await main_module.dp.feed_update(bot,text_update("/start",4))
            assert api.users==[123] and answers[-1][0].startswith("Привет, Actor!")
        finally:await state.clear();await bot.session.close()
    asyncio.run(scenario())

@pytest.mark.parametrize("variant",["malformed","expired","wrong-chat","unavailable"])
def test_dispatcher_rejects_bad_auth_payload_without_create_user(monkeypatch,variant):
    async def scenario():
        bot=Bot("123456:dispatcher-test-token");api=Api();monkeypatch.setattr(main_module,"api_client",api)
        if variant=="expired":api.info["status"]="expired"
        if variant=="unavailable":api.error=httpx.ReadTimeout("unavailable")
        answers=[]
        async def answer(self,text,**kwargs):answers.append(text);return SimpleNamespace(message_id=10)
        monkeypatch.setattr(Message,"answer",answer)
        state=FSMContext(storage=main_module.dp.storage,key=StorageKey(bot_id=bot.id,chat_id=123,user_id=123));await state.clear()
        try:
            await main_module.dp.feed_update(bot,text_update("/start auth_"+("bad" if variant=="malformed" else TOKEN),chat=456 if variant=="wrong-chat" else 123))
            assert api.users==[] and api.decisions==[]
            assert answers and ("недоступен" in answers[-1] if variant=="unavailable" else answers[-1]==INVALID)
        finally:await state.clear();await bot.session.close()
    asyncio.run(scenario())


def test_menu_transition_invalidates_keyboard_even_when_telegram_cleanup_fails(monkeypatch,caplog):
    async def scenario():
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.methods import EditMessageReplyMarkup
        from app.menu import ADD_JOB_BUTTON
        bot=Bot("123456:dispatcher-test-token");api=Api();monkeypatch.setattr(main_module,"api_client",api)
        async def answer(self,text,**kwargs):return SimpleNamespace(message_id=10)
        async def edit(self,**kwargs):raise TelegramBadRequest(method=EditMessageReplyMarkup(chat_id=123,message_id=10),message="gone")
        monkeypatch.setattr(Message,"answer",answer);monkeypatch.setattr(Bot,"edit_message_reply_markup",edit);monkeypatch.setattr(CallbackQuery,"answer",AsyncMock())
        state=FSMContext(storage=main_module.dp.storage,key=StorageKey(bot_id=bot.id,chat_id=123,user_id=123));await state.clear()
        try:
            await main_module.dp.feed_update(bot,text_update("/start auth_"+TOKEN))
            await main_module.dp.feed_update(bot,text_update(ADD_JOB_BUTTON,2))
            assert KEY not in await state.get_data()
            await main_module.dp.feed_update(bot,callback_update("tga:a:A1B2C3",3))
            assert api.decisions==[]
        finally:await state.clear();await bot.session.close()
    asyncio.run(scenario());assert "Could not remove Telegram auth keyboard" in caplog.text


def test_client_credentials_only_trusted_bot_endpoints_and_real_actor(caplog):
    service="bot-tests-server-only-service-token";seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(200,json={"purpose":"link","code":"A1B2C3","status":"pending","extra_secret":"hidden"} if request.url.path.endswith("inspect") else {"ok":True})
    async def scenario():
        client=JobHunterApiClient("http://api",service_token=service);client._client._transport=httpx.MockTransport(handler)
        try:
            assert await client.inspect_telegram_challenge(TOKEN)=={"purpose":"link","code":"A1B2C3","status":"pending"}
            await client.decide_telegram_challenge(TOKEN,ACTOR,True)
            await client.decide_telegram_challenge(TOKEN,ACTOR,False)
            await client._client.post("/auth/telegram/complete",json={})
        finally:await client.close()
    asyncio.run(scenario())
    assert all(request.headers["X-Bot-Service-Token"]==service for request in seen[:3])
    assert "X-Bot-Service-Token" not in seen[3].headers
    import json
    assert json.loads(seen[1].content)["telegram"]["telegram_id"]==123
    assert service not in caplog.text and TOKEN not in caplog.text
