import asyncio
import os
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageReplyMarkup
from aiogram.types import CallbackQuery, Chat, Message, Update, User

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:applications-test-token")

import app.main as main_module
from app.applications import (
    APPLICATIONS_APPLICATION_ID,
    APPLICATIONS_DETAIL_VIEW,
    APPLICATIONS_FILTER_STATUS,
    APPLICATIONS_HISTORY_VIEW,
    APPLICATIONS_MESSAGE_ID,
    APPLICATIONS_OFFSET,
    APPLICATIONS_STATUS_TOKEN,
    APPLICATIONS_VIEW,
    APPLICATION_NOT_FOUND_MESSAGE,
)
from test_applications import (
    DispatcherApi,
    _applications_callback_update,
    _dispatcher_state,
)


def _menu_update(text: str, update_id: int) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(),
            chat=Chat(id=456, type="private"),
            from_user=User(id=123, is_bot=False, first_name="Anna"),
            text=text,
        ),
    )


def test_dispatcher_history_lifecycle_order_localization_utc_and_stale_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:applications-test-token")
        api = DispatcherApi()
        api.detail["application"] = {"id": 18, "status": "interview", "note": None}
        api.history = {"items": [
            {"status": "interview", "occurred_at": "2026-09-07T21:14:00+04:00"},
            {"status": "applied", "occurred_at": "2026-09-05T14:40:00Z"},
            {"status": "saved", "occurred_at": "2026-09-02T08:05:00+00:00"},
        ]}
        edits: list[tuple[str, object | None]] = []

        async def edit(message: Message, text: str, **kwargs: object) -> None:
            edits.append((text, kwargs.get("reply_markup")))

        async def ack(callback: CallbackQuery, **kwargs: object) -> bool:
            return True

        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "edit_text", edit)
        monkeypatch.setattr(CallbackQuery, "answer", ack)
        state = await _dispatcher_state(bot, {
            APPLICATIONS_MESSAGE_ID: 10,
            APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW,
            APPLICATIONS_APPLICATION_ID: 18,
            APPLICATIONS_OFFSET: 5,
            APPLICATIONS_FILTER_STATUS: "interview",
        })

        await main_module.dp.feed_update(
            bot,
            _applications_callback_update(
                "applications:history:18:5", update_id=1, message_id=10
            ),
        )

        data = await state.get_data()
        assert data[APPLICATIONS_MESSAGE_ID] == 10
        assert data[APPLICATIONS_VIEW] == APPLICATIONS_HISTORY_VIEW
        assert data[APPLICATIONS_APPLICATION_ID] == 18
        assert data[APPLICATIONS_OFFSET] == 5
        assert data[APPLICATIONS_FILTER_STATUS] == "interview"
        assert edits[-1][0].splitlines() == [
            "🕘 История статусов",
            "",
            "07.09.2026 17:14 UTC — Собеседование",
            "05.09.2026 14:40 UTC — Откликнулся",
            "02.09.2026 08:05 UTC — Сохранена",
        ]
        back = edits[-1][1].inline_keyboard[0][0]
        assert (back.text, back.callback_data) == (
            "⬅️ К вакансии", "applications:detail:18:5"
        )

        await main_module.dp.feed_update(
            bot,
            _applications_callback_update(
                "applications:history:18:5", update_id=2, message_id=10
            ),
        )
        await main_module.dp.feed_update(
            bot,
            _applications_callback_update(
                "applications:history:18:5", update_id=3, message_id=999
            ),
        )
        assert api.history_calls == [(4, 18)]

        await main_module.dp.feed_update(
            bot,
            _applications_callback_update(back.callback_data, update_id=4, message_id=10),
        )
        assert api.detail_calls == [(4, 18)]
        assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_DETAIL_VIEW
        await main_module.dp.feed_update(
            bot,
            _applications_callback_update(back.callback_data, update_id=5, message_id=10),
        )
        assert api.detail_calls == [(4, 18)]
        await state.clear()
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_empty_legacy_history(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        bot = Bot("123456:applications-test-token")
        api = DispatcherApi()
        edits: list[tuple[str, object | None]] = []

        async def edit(message: Message, text: str, **kwargs: object) -> None:
            edits.append((text, kwargs.get("reply_markup")))

        async def ack(callback: CallbackQuery, **kwargs: object) -> bool:
            return True

        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "edit_text", edit)
        monkeypatch.setattr(CallbackQuery, "answer", ack)
        state = await _dispatcher_state(bot, {
            APPLICATIONS_MESSAGE_ID: 10,
            APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW,
            APPLICATIONS_APPLICATION_ID: 18,
            APPLICATIONS_OFFSET: 0,
        })

        await main_module.dp.feed_update(
            bot,
            _applications_callback_update(
                "applications:history:18:0", update_id=10, message_id=10
            ),
        )
        assert edits[-1][0] == (
            "🕘 История статусов\n\nИстория статусов пока пуста."
        )
        assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_HISTORY_VIEW
        await state.clear()
        await bot.session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["network", "500", "404"])
def test_dispatcher_history_failure_recovery(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:applications-test-token")
        api = DispatcherApi()
        answers: list[str] = []
        edits: list[str] = []

        async def fail_history(user_id: int, application_id: int) -> dict[str, object]:
            if failure == "network":
                raise httpx.ReadTimeout("test")
            response = httpx.Response(
                int(failure),
                json={"detail": {"code": "APPLICATION_NOT_FOUND"}},
                request=httpx.Request("GET", "http://api/status-history"),
            )
            response.raise_for_status()

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            answers.append(text)
            return SimpleNamespace(message_id=20 + len(answers))

        async def edit(message: Message, text: str, **kwargs: object) -> None:
            edits.append(text)

        async def ack(callback: CallbackQuery, **kwargs: object) -> bool:
            return True

        api.get_application_status_history = fail_history
        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Message, "edit_text", edit)
        monkeypatch.setattr(CallbackQuery, "answer", ack)
        state = await _dispatcher_state(bot, {
            APPLICATIONS_MESSAGE_ID: 10,
            APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW,
            APPLICATIONS_APPLICATION_ID: 18,
            APPLICATIONS_OFFSET: 5,
            APPLICATIONS_FILTER_STATUS: "offer",
        })

        await main_module.dp.feed_update(
            bot,
            _applications_callback_update(
                "applications:history:18:5", update_id=20, message_id=10
            ),
        )
        data = await state.get_data()
        assert data[APPLICATIONS_MESSAGE_ID] == 10
        assert data[APPLICATIONS_OFFSET] == 5
        assert data[APPLICATIONS_FILTER_STATUS] == "offer"
        if failure == "404":
            assert data[APPLICATIONS_VIEW] == "not_found"
            assert data[APPLICATIONS_APPLICATION_ID] is None
            assert edits[-1] == APPLICATION_NOT_FOUND_MESSAGE
        else:
            assert data[APPLICATIONS_VIEW] == APPLICATIONS_DETAIL_VIEW
            assert data[APPLICATIONS_APPLICATION_ID] == 18
            assert answers[-1] == "Не удалось загрузить историю статусов. Попробуй ещё раз."
            api.get_application_status_history = DispatcherApi.get_application_status_history.__get__(api)
            await main_module.dp.feed_update(
                bot,
                _applications_callback_update(
                    "applications:history:18:5", update_id=21, message_id=10
                ),
            )
            assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_HISTORY_VIEW
        await state.clear()
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_long_history_is_utf16_safe_and_main_menu_cleans_canonical_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:applications-test-token")
        api = DispatcherApi()
        api.history = {"items": [
            {"status": "interview", "occurred_at": "2026-09-07T17:14:00+00:00"}
            for _ in range(500)
        ]}
        edits: list[tuple[str, object | None]] = []
        cleaned: list[int] = []

        async def edit(message: Message, text: str, **kwargs: object) -> None:
            edits.append((text, kwargs.get("reply_markup")))

        async def answer(message: Message, text: str, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(message_id=30)

        async def cleanup(
            _bot: Bot, *, chat_id: int, message_id: int, reply_markup: object | None = None
        ) -> None:
            assert reply_markup is None
            cleaned.append(message_id)

        async def ack(callback: CallbackQuery, **kwargs: object) -> bool:
            return True

        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "edit_text", edit)
        monkeypatch.setattr(Message, "answer", answer)
        monkeypatch.setattr(Bot, "edit_message_reply_markup", cleanup)
        monkeypatch.setattr(CallbackQuery, "answer", ack)
        state = await _dispatcher_state(bot, {
            APPLICATIONS_MESSAGE_ID: 10,
            APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW,
            APPLICATIONS_APPLICATION_ID: 18,
            APPLICATIONS_OFFSET: 0,
        })

        await main_module.dp.feed_update(
            bot,
            _applications_callback_update(
                "applications:history:18:0", update_id=30, message_id=10
            ),
        )
        text, markup = edits[-1]
        assert len(text.encode("utf-16-le")) // 2 <= 4096
        assert text.endswith("Показана только часть истории.")
        assert markup.inline_keyboard[0][0].text == "⬅️ К вакансии"

        await main_module.dp.feed_update(bot, _menu_update("💼 Добавить вакансию", 31))
        assert cleaned == [10]
        calls = list(api.history_calls)
        await main_module.dp.feed_update(
            bot,
            _applications_callback_update(
                "applications:detail:18:0", update_id=32, message_id=10
            ),
        )
        assert api.history_calls == calls
        await state.clear()
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_status_update_then_history_contains_new_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:applications-test-token")
        api = DispatcherApi()
        api.detail["application"] = {"id": 18, "status": "saved", "note": None}
        api.history = {"items": [
            {"status": "saved", "occurred_at": "2026-09-02T08:05:00+00:00"}
        ]}
        edits: list[str] = []

        async def put_status(
            user_id: int, application_id: int, status: str
        ) -> dict[str, object]:
            api.detail["application"]["status"] = status
            api.history["items"].insert(0, {
                "status": status, "occurred_at": "2026-09-07T17:14:00+00:00"
            })
            return api.detail

        async def edit(message: Message, text: str, **kwargs: object) -> None:
            edits.append(text)

        async def ack(callback: CallbackQuery, **kwargs: object) -> bool:
            return True

        api.put_application_status = put_status
        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "edit_text", edit)
        monkeypatch.setattr(CallbackQuery, "answer", ack)
        state = await _dispatcher_state(bot, {
            APPLICATIONS_MESSAGE_ID: 10,
            APPLICATIONS_VIEW: APPLICATIONS_DETAIL_VIEW,
            APPLICATIONS_APPLICATION_ID: 18,
            APPLICATIONS_OFFSET: 0,
        })

        await main_module.dp.feed_update(
            bot,
            _applications_callback_update(
                "applications:status:18:0", update_id=40, message_id=10
            ),
        )
        token = (await state.get_data())[APPLICATIONS_STATUS_TOKEN]
        await main_module.dp.feed_update(
            bot,
            _applications_callback_update(
                f"applications:set:{token}:offer", update_id=41, message_id=10
            ),
        )
        await main_module.dp.feed_update(
            bot,
            _applications_callback_update(
                "applications:history:18:0", update_id=42, message_id=10
            ),
        )
        assert "07.09.2026 17:14 UTC — Оффер" in edits[-1]
        assert edits[-1].index("Оффер") < edits[-1].index("Сохранена")
        await state.clear()
        await bot.session.close()

    asyncio.run(scenario())
