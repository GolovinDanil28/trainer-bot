"""Вечерний вопрос о самочувствии: порог времени, идемпотентность, повтор (R-19).

Время фиксировано (`now_utc` передаётся явно), а зона — как у пользователя:
иначе тесты проходили бы только в UTC и не ловили ошибку «сутки по UTC».
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.bot.telegram_sender import TelegramWellnessSender
from src.core.wellness import WellnessEntry, WellnessPrompt
from src.db import repository
from src.scheduler.wellness_tick import WellnessTick
from tests.fakes import FakeBot, FakePromptSender

ZONE = ZoneInfo("Asia/Yekaterinburg")  # UTC+5, без перехода на летнее время
AT = time(21, 0)
TODAY = date(2026, 10, 27)


def moment(utc_hour: int, utc_minute: int = 0) -> datetime:
    # 16:00 UTC = 21:00 местного; 15:59 UTC = 20:59.
    return datetime(2026, 10, 27, utc_hour, utc_minute, tzinfo=UTC)


@pytest.fixture
def sender() -> FakePromptSender:
    return FakePromptSender()


@pytest.fixture
def tick(session_factory, sender) -> WellnessTick:
    return WellnessTick(session_factory, sender, ZONE, AT)


def entry_for(session_factory, day: date = TODAY) -> WellnessEntry | None:
    with session_factory() as session:
        return repository.wellness_for_day(session, day)


async def test_before_time_sends_nothing(tick, sender, session_factory):
    result = await tick.run(moment(15, 59))

    assert result.sent is False
    assert result.day is None
    assert sender.sent == []
    assert entry_for(session_factory) is None


async def test_at_time_sends_question_once(tick, sender, session_factory):
    result = await tick.run(moment(16, 0))

    assert result.sent is True
    assert result.day == TODAY
    assert [prompt.day for prompt in sender.sent] == [TODAY]
    assert entry_for(session_factory) == WellnessEntry(TODAY, None, None)


async def test_restart_same_evening_sends_nothing(session_factory, sender):
    # Рестарт = новый объект тика с той же базой: состояние живёт в БД, а не в
    # памяти процесса (R-19).
    await WellnessTick(session_factory, sender, ZONE, AT).run(moment(16, 0))

    restarted = WellnessTick(session_factory, sender, ZONE, AT)
    result = await restarted.run(moment(16, 0) + timedelta(seconds=30))

    assert result.sent is False
    assert sender.attempts == 1
    assert len(sender.sent) == 1


async def test_already_rated_today_skips_question(tick, sender, session_factory):
    with session_factory() as session:
        repository.save_wellness(session, TODAY, 4, None, moment(9, 0))

    result = await tick.run(moment(16, 0))

    assert result.sent is False
    assert sender.sent == []


async def test_late_start_still_sends_question(tick, sender):
    # Бот не работал в 21:00: вопрос должен уйти в ближайшем тике, а не потеряться.
    result = await tick.run(moment(17, 0))

    assert result.sent is True
    assert len(sender.sent) == 1


async def test_failed_send_frees_the_day_for_retry(session_factory):
    sender = FakePromptSender(failures=1)
    tick = WellnessTick(session_factory, sender, ZONE, AT)

    first = await tick.run(moment(16, 0))

    assert first.sent is False
    assert entry_for(session_factory) is None

    second = await tick.run(moment(16, 1))

    assert second.sent is True
    assert sender.attempts == 2
    assert entry_for(session_factory) == WellnessEntry(TODAY, None, None)


async def test_after_local_midnight_question_waits_for_evening(tick, sender):
    # 19:00 UTC = 00:00 местного 28-го: новый день, но до 21:00 ещё далеко.
    result = await tick.run(moment(19, 0))

    assert result.sent is False
    assert result.day is None
    assert sender.sent == []


async def test_dst_day_still_uses_local_wall_clock(session_factory):
    # 29.03.2026 — переход на летнее время в Europe/Berlin: 19:00 UTC = 21:00
    # местного. Если бы порог считался по UTC, вопрос не ушёл бы.
    berlin = ZoneInfo("Europe/Berlin")
    sender = FakePromptSender()
    tick = WellnessTick(session_factory, sender, berlin, AT)

    result = await tick.run(datetime(2026, 3, 29, 19, 0, tzinfo=UTC))

    assert result.sent is True
    assert sender.sent[0].day == date(2026, 3, 29)


async def test_transport_sends_one_question_with_five_buttons():
    bot = FakeBot()
    sender = TelegramWellnessSender(bot, chat_id=42)

    await sender.send_prompt(WellnessPrompt(day=TODAY))

    assert len(bot.messages) == 1
    message = bot.messages[0]
    assert message["chat_id"] == 42
    assert message["text"] == "Как самочувствие сегодня? Оцени от 1 до 5."
    buttons = message["reply_markup"].inline_keyboard[0]
    assert [button.text for button in buttons] == ["1", "2", "3", "4", "5"]
