import asyncio
import os

import httpx
import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Message

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:learning-summary-test-token")

import app.main as main_module
from app.applications import (
    APPLICATIONS_LIST_VIEW,
    APPLICATIONS_MESSAGE_ID,
    APPLICATIONS_OFFSET,
    APPLICATIONS_SUMMARY_VIEW,
    APPLICATIONS_VIEW,
)
from app.jobs import AddJobStates, REQUEST_URL_MESSAGE
from app.menu import ADD_JOB_BUTTON, main_menu_action
from test_applications import DispatcherApi, _applications_callback_update, _dispatcher_state


def _outcomes(denominator: int, *, interview: int = 0) -> dict[str, object]:
    return {
        status: {
            "numerator": interview if status == "interview" else 0,
            "denominator": denominator,
            "percentage": (
                round(interview * 100 / denominator, 1)
                if status == "interview" and denominator >= 5 else
                0.0 if denominator >= 5 else None
            ),
        }
        for status in ("recruiter_response", "interview", "offer", "hired", "withdrawn")
    }


def _version(
    name: str, *, high: int = 0, medium: int = 0, low: int = 0,
    interview: int = 0, insufficient: int = 0,
) -> dict[str, object]:
    return {
        "algorithm_version": name,
        "captured_count": high + medium + low + insufficient,
        "scored_count": high + medium + low,
        "insufficient_data_count": insufficient,
        "score_buckets": [
            {
                "bucket": bucket,
                "score_min": score_min,
                "score_max": score_max,
                "application_count": count,
                "outcomes": _outcomes(
                    count, interview=interview if bucket == "high" else 0
                ),
            }
            for bucket, score_min, score_max, count in (
                ("high", 75, 100, high),
                ("medium", 50, 74, medium),
                ("low", 0, 49, low),
            )
        ],
    }


def _match_summary(*versions: dict[str, object]) -> dict[str, object]:
    captured = sum(int(version["captured_count"]) for version in versions)
    return {
        "as_of": "2026-09-22T12:00:00+00:00",
        "snapshot_coverage": {
            "applied_application_count": captured + 3,
            "captured_count": captured,
            "unavailable_count": 1,
            "legacy_without_snapshot_count": 2,
            "invalid_anchor_count": 0,
        },
        "algorithm_versions": list(versions),
    }


def test_dispatcher_summary_renders_null_denominator_incomplete_warning_and_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:learning-summary-test-token")
        api = DispatcherApi()
        api.pages = [{"items": [], "has_next": False}]
        api.learning_summary = {
            "total_applications": 3,
            "applied_count": 0,
            "recruiter_response_count": 0,
            "interview_count": 0,
            "offer_count": 0,
            "hired_count": 0,
            "withdrawn_count": 0,
            "applied_to_recruiter_response": {"numerator": 0, "denominator": 0, "percentage": None},
            "applied_to_interview": {"numerator": 0, "denominator": 0, "percentage": None},
            "applied_to_offer": {"numerator": 0, "denominator": 0, "percentage": None},
            "applied_to_hired": {"numerator": 0, "denominator": 0, "percentage": None},
            "history_missing_count": 1,
            "funnel_incomplete_count": 2,
            "as_of": "2026-09-21T00:00:00+00:00",
        }
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
            APPLICATIONS_VIEW: APPLICATIONS_LIST_VIEW,
            APPLICATIONS_OFFSET: 0,
        })

        await main_module.dp.feed_update(
            bot, _applications_callback_update("applications:list:initial-list:summary", update_id=1, message_id=10),
        )
        assert api.learning_summary_calls == [4]
        assert api.match_learning_summary_calls == [4]
        assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_SUMMARY_VIEW
        assert "Отклик → интервью: нет данных" in edits[-1][0]
        assert "Отклик → оффер: нет данных" in edits[-1][0]
        assert "Отклик → ответ HR: нет данных" in edits[-1][0]
        assert "Отклик → выход на работу: нет данных" in edits[-1][0]
        assert "⚠️ Неполная история: 3" in edits[-1][0]
        assert "Данных для анализа: 0 из 0 откликов" in edits[-1][0]
        back = edits[-1][1].inline_keyboard[0][0]
        assert (back.text, back.callback_data) == ("⬅️ К списку", "applications:page:0")

        await main_module.dp.feed_update(
            bot, _applications_callback_update("applications:list:initial-list:summary", update_id=2, message_id=10),
        )
        assert api.learning_summary_calls == [4]
        await main_module.dp.feed_update(
            bot, _applications_callback_update(back.callback_data, update_id=3, message_id=10),
        )
        assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_LIST_VIEW
        assert api.list_calls == [4]
        await state.clear()
        await bot.session.close()

    asyncio.run(scenario())


def test_dispatcher_summary_api_error_keeps_a_back_navigation_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:learning-summary-test-token")
        api = DispatcherApi()
        api.pages = [{"items": [], "has_next": False}]
        edits: list[tuple[str, object | None]] = []

        async def fail_summary(user_id: int) -> dict[str, object]:
            raise httpx.ConnectError("test")

        async def edit(message: Message, text: str, **kwargs: object) -> None:
            edits.append((text, kwargs.get("reply_markup")))

        async def ack(callback: CallbackQuery, **kwargs: object) -> bool:
            return True

        api.get_application_learning_summary = fail_summary
        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "edit_text", edit)
        monkeypatch.setattr(CallbackQuery, "answer", ack)
        state = await _dispatcher_state(bot, {
            APPLICATIONS_MESSAGE_ID: 10,
            APPLICATIONS_VIEW: APPLICATIONS_LIST_VIEW,
            APPLICATIONS_OFFSET: 0,
        })

        await main_module.dp.feed_update(
            bot, _applications_callback_update("applications:list:initial-list:summary", update_id=10, message_id=10),
        )
        assert "Не удалось загрузить статистику" in edits[-1][0]
        assert (await state.get_data())[APPLICATIONS_VIEW] == "learning_summary_error"
        back = edits[-1][1].inline_keyboard[0][0]
        await main_module.dp.feed_update(
            bot, _applications_callback_update(back.callback_data, update_id=11, message_id=10),
        )
        assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_LIST_VIEW
        await state.clear()
        await bot.session.close()

    asyncio.run(scenario())


def test_learning_summary_formatter_displays_new_explicit_counts_and_conversions() -> None:
    from app.applications import _format_application_learning_summary

    text = _format_application_learning_summary({
        "total_applications": 10,
        "applied_count": 5,
        "recruiter_response_count": 3,
        "interview_count": 2,
        "offer_count": 1,
        "hired_count": 1,
        "withdrawn_count": 2,
        "applied_to_recruiter_response": {"numerator": 3, "denominator": 5, "percentage": 60.0},
        "applied_to_interview": {"numerator": 2, "denominator": 5, "percentage": 40.0},
        "applied_to_offer": {"numerator": 1, "denominator": 5, "percentage": 20.0},
        "applied_to_hired": {"numerator": 1, "denominator": 5, "percentage": 20.0},
        "history_missing_count": 0,
        "funnel_incomplete_count": 0,
        "as_of": "2026-09-21T00:00:00+00:00",
    })

    assert "Ответов HR: 3" in text
    assert "Выходов на работу: 1" in text
    assert "Процессов прекращено мной: 2" in text
    assert "Отклик → ответ HR: 3 / 5 (60%)" in text
    assert "Отклик → выход на работу: 1 / 5 (20%)" in text


def test_match_learning_formatter_shows_coverage_buckets_guardrail_and_disclaimer() -> None:
    from app.applications import _format_application_learning_summary

    funnel = DispatcherApi().learning_summary
    match = _match_summary(_version(
        "job-match-v2.1", high=7, medium=3, interview=2, insufficient=2,
    ))

    text = _format_application_learning_summary(funnel, match)

    assert "📊 Статистика поиска" in text
    assert "📊 Matching на момент отклика" in text
    assert "Данных для анализа: 12 из 15 откликов" in text
    assert "Matching был недоступен для 1 отклика." in text
    assert "2 отклика были сохранены до появления истории Matching." in text
    assert "Высокое совпадение · 75–100" in text
    assert "7 откликов" in text
    assert "Интервью: 2 из 7 · 28,6%" in text
    assert "Среднее совпадение · 50–74" in text
    assert "3 отклика\n\nДля 2 откликов" in text
    assert "Пока мало данных для сравнения." not in text
    assert "Низкое совпадение" not in text
    assert "Для 2 откликов не хватило данных для оценки Matching." in text
    assert "Версия job-match-v2.1" not in text
    assert text.endswith("Это наблюдения по вашим отметкам на текущий момент, не прогноз.")
    assert "вероятност" not in text.casefold()
    assert "эффективност" not in text.casefold()


def test_match_learning_formatter_separates_versions_warns_once_and_keeps_plain_text_safe() -> None:
    from app.applications import _format_application_learning_summary

    dynamic_version = "<b>job-match-v3.0 & test</b>"
    match = _match_summary(
        _version(dynamic_version, high=5, interview=1),
        _version("job-match-v2.1", low=1),
    )

    text = _format_application_learning_summary(DispatcherApi().learning_summary, match)

    warning = "Оценки рассчитаны разными версиями Matching, поэтому результаты показаны отдельно."
    assert text.count(warning) == 1
    assert dynamic_version in text
    assert "Версия job-match-v2.1" in text
    assert "Интервью: 1 из 5 · 20%" in text
    assert "Низкое совпадение · 0–49" in text
    assert "1 отклик" in text
    assert "Пока мало данных для сравнения." not in text


def test_match_learning_empty_state_keeps_human_readable_coverage_visible() -> None:
    from app.applications import _format_application_learning_summary

    match = _match_summary()
    match["snapshot_coverage"] = {
        "applied_application_count": 4,
        "captured_count": 0,
        "unavailable_count": 1,
        "legacy_without_snapshot_count": 2,
        "invalid_anchor_count": 1,
    }

    text = _format_application_learning_summary(DispatcherApi().learning_summary, match)

    assert "Данных для анализа: 0 из 4 откликов" in text
    assert "Matching был недоступен для 1 отклика." in text
    assert "2 отклика были сохранены до появления истории Matching." in text
    assert "1 запись не удалось включить в анализ." in text
    assert "invalid" not in text.casefold()
    assert "anchor" not in text.casefold()
    assert "Пока данных недостаточно, чтобы сравнивать результаты" not in text


def test_match_learning_formatter_hides_zero_coverage_lines_and_compacts_small_sample() -> None:
    from app.applications import _format_application_learning_summary

    match = _match_summary(_version("job-match-v2.1", low=1))
    match["snapshot_coverage"] = {
        "applied_application_count": 3,
        "captured_count": 1,
        "unavailable_count": 0,
        "legacy_without_snapshot_count": 0,
        "invalid_anchor_count": 0,
    }

    text = _format_application_learning_summary(DispatcherApi().learning_summary, match)

    assert "Данных для анализа: 1 из 3 откликов" in text
    assert "Недоступен" not in text
    assert "сохранены до появления" not in text
    assert "Низкое совпадение · 0–49\n1 отклик" in text
    assert "Пока мало данных для сравнения." not in text
    assert "Ответ HR:" not in text
    assert "%" not in text
    assert "Score" not in text


def test_match_learning_formatter_global_small_sample_message_is_shown_once() -> None:
    from app.applications import _format_application_learning_summary

    match = _match_summary(_version("job-match-v2.1", medium=2, low=1))
    text = _format_application_learning_summary(DispatcherApi().learning_summary, match)

    global_message = (
        "Пока данных недостаточно, чтобы сравнивать результаты для разных уровней совпадения."
    )
    assert text.count(global_message) == 1
    assert "Нужно больше откликов с сохранённым Matching." not in text
    assert "Пока мало данных для сравнения." not in text
    assert "Ответ HR:" not in text
    assert "Среднее совпадение · 50–74" in text
    assert "Низкое совпадение · 0–49" in text
    assert text.index("Низкое совпадение · 0–49") < text.index(global_message)


def test_match_learning_api_error_keeps_existing_funnel_and_back_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        bot = Bot("123456:learning-summary-test-token")
        api = DispatcherApi()
        api.pages = [{"items": [], "has_next": False}]
        edits: list[tuple[str, object | None]] = []

        async def fail_match_summary(user_id: int) -> dict[str, object]:
            raise httpx.ConnectError("test")

        async def edit(message: Message, text: str, **kwargs: object) -> None:
            edits.append((text, kwargs.get("reply_markup")))

        async def ack(callback: CallbackQuery, **kwargs: object) -> bool:
            return True

        api.get_application_match_learning_summary = fail_match_summary
        monkeypatch.setattr(main_module, "api_client", api)
        monkeypatch.setattr(Message, "edit_text", edit)
        monkeypatch.setattr(CallbackQuery, "answer", ack)
        state = await _dispatcher_state(bot, {
            APPLICATIONS_MESSAGE_ID: 10,
            APPLICATIONS_VIEW: APPLICATIONS_LIST_VIEW,
            APPLICATIONS_OFFSET: 0,
        })
        try:
            await main_module.dp.feed_update(
                bot,
                _applications_callback_update(
                    "applications:list:initial-list:summary", update_id=20, message_id=10,
                ),
            )
            assert "📊 Статистика поиска" in edits[-1][0]
            assert "Не удалось загрузить исторические данные Matching." in edits[-1][0]
            assert (await state.get_data())[APPLICATIONS_VIEW] == APPLICATIONS_SUMMARY_VIEW
            assert edits[-1][1].inline_keyboard[0][0].text == "⬅️ К списку"
        finally:
            await state.clear()
            await bot.session.close()

    asyncio.run(scenario())


def test_match_learning_message_limit_is_utf16_safe_and_preserves_disclaimer() -> None:
    from app.applications import _format_application_learning_summary, _utf16_units

    versions = [
        _version(f"job-match-{index}-" + "🚀" * 40, high=5, interview=1)
        for index in range(40)
    ]

    text = _format_application_learning_summary(
        DispatcherApi().learning_summary, _match_summary(*versions)
    )

    assert _utf16_units(text) <= 4096
    assert "Показана только часть статистики." in text
    assert text.endswith("Это наблюдения по вашим отметкам на текущий момент, не прогноз.")


def test_menu_navigation_away_cleans_summary_keyboard() -> None:
    class CleanupBot:
        def __init__(self) -> None:
            self.cleaned: list[tuple[int, int]] = []

        async def edit_message_reply_markup(
            self, *, chat_id: int, message_id: int, reply_markup: object | None = None,
        ) -> None:
            assert reply_markup is None
            self.cleaned.append((chat_id, message_id))

    class MenuMessage:
        def __init__(self, bot: CleanupBot) -> None:
            self.text = ADD_JOB_BUTTON
            self.bot = bot
            self.chat = type("Chat", (), {"id": 456})()
            self.from_user = type("User", (), {"id": 123})()
            self.answers: list[str] = []

        async def answer(self, text: str, **kwargs: object) -> object:
            self.answers.append(text)
            return object()

    async def scenario() -> None:
        bot = CleanupBot()
        message = MenuMessage(bot)
        telegram_bot = Bot("123456:learning-summary-test-token")
        state = await _dispatcher_state(telegram_bot, {
            APPLICATIONS_MESSAGE_ID: 10,
            APPLICATIONS_VIEW: APPLICATIONS_SUMMARY_VIEW,
            APPLICATIONS_OFFSET: 0,
        })
        try:
            await main_menu_action(message, state)
            assert bot.cleaned == [(456, 10)]
            assert await state.get_state() == AddJobStates.waiting_for_url.state
            assert message.answers == [REQUEST_URL_MESSAGE]
        finally:
            await state.clear()
            await telegram_bot.session.close()

    asyncio.run(scenario())
