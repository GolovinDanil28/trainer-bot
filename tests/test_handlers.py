"""Тесты хендлеров: тонкий слой, поэтому и проверок немного.

Telegram не поднимается: вместо `Update` и `Context` — подделки из
`tests/fakes.py`. Хендлер их не различает, потому что ничего, кроме
`message.text` и `reply_text`, не использует.
"""

import pytest
from sqlalchemy import func, select

from src.bot.access import OWNER_KEY, REFUSAL_TEXT
from src.bot.assistant_service import AssistantService
from src.bot.handlers import (
    cmd_help,
    cmd_history,
    cmd_list,
    cmd_new,
    cmd_off,
    cmd_start,
    on_free_text,
)
from src.core.messages import format_help
from src.db.base import Base
from src.db.models import Workout
from tests.fakes import FakeBot, FakeLLM, make_context, make_update

OWNER_ID = 42


def service_context(service, args: list[str] | None = None):
    return make_context({"workout_service": service, OWNER_KEY: OWNER_ID}, args)


async def test_cmd_new_creates_workout_and_replies(service, session_factory):
    update = make_update("/new strength Пн 19:30")

    await cmd_new(update, service_context(service))

    assert update.message.sent == ["Готово: #1 — силовая, Пн, 19:30 (45 мин)."]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Workout)) == 1


async def test_cmd_new_error_is_returned_as_reply(service, session_factory):
    update = make_update("/new strength Пн 25:00")

    await cmd_new(update, service_context(service))

    assert "вне диапазона" in update.message.sent[0]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Workout)) == 0


async def test_cmd_off_without_argument_asks_for_id(service):
    update = make_update("/off")

    await cmd_off(update, service_context(service))

    assert "Нужен номер тренировки" in update.message.sent[0]


async def test_cmd_off_disables_existing(service):
    service.create_from_command("/new cardio Вт 08:00")
    update = make_update("/off 1")

    await cmd_off(update, service_context(service, ["1"]))

    assert update.message.sent == ["Тренировка #1 отключена. История отметок сохранена."]


async def test_cmd_list_uses_service(service):
    service.create_from_command("/new cardio Вт 08:00")
    update = make_update("/list")

    await cmd_list(update, service_context(service))

    assert "Твои тренировки:" in update.message.sent[0]


async def test_cmd_history_replies_with_journal(service):
    update = make_update("/history")

    await cmd_history(update, service_context(service))

    assert update.message.sent == [service.show_history()]


async def test_cmd_help_replies_with_card(service):
    update = make_update("/help")

    await cmd_help(update, service_context(service))

    assert update.message.sent == [format_help()]


async def test_cmd_start_greets_and_shows_card(service):
    update = make_update("/start")

    await cmd_start(update, service_context(service))

    assert update.message.sent[0].startswith("Привет!")
    assert format_help() in update.message.sent[0]


# Все команды — через один декоратор, поэтому отказ должен быть буквально
# одинаковым. Параметризация ловит случай, когда новую команду забыли закрыть.
OWNER_COMMANDS = [
    pytest.param(cmd_start, "/start", id="start"),
    pytest.param(cmd_help, "/help", id="help"),
    pytest.param(cmd_new, "/new strength Пн 19:30", id="new"),
    pytest.param(cmd_list, "/list", id="list"),
    pytest.param(cmd_off, "/off 1", id="off"),
    pytest.param(cmd_history, "/history", id="history"),
]


@pytest.mark.parametrize(("handler", "text"), OWNER_COMMANDS)
async def test_stranger_gets_same_neutral_refusal(
    service, session_factory, handler, text
):
    update = make_update(text, user_id=999)

    await handler(update, service_context(service))

    assert update.message.sent == [REFUSAL_TEXT]
    # Ни расписания, ни истории: отказ не меняет состояние.
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Workout)) == 0
    assert "трениров" not in REFUSAL_TEXT.lower()


def free_text_context(service, llm, bot):
    assistant = AssistantService(llm, service, "qwen2.5:7b", 5)
    return make_context(
        {"assistant_service": assistant, OWNER_KEY: OWNER_ID},
        bot=bot,
    )


async def test_free_text_owner_gets_answer_and_typing_first(service):
    phrase = "покажи расписание"
    llm = FakeLLM(answers={phrase: '{"action":"list"}'})
    events: list[str] = []
    update = make_update(phrase, events=events)
    bot = FakeBot(events)

    await on_free_text(update, free_text_context(service, llm, bot))

    assert update.message.sent == [service.show_schedule()]
    assert llm.calls == [(phrase, service.schedule_snapshot())]
    # «Печатает…» уходит до ответа, а не после (R-17).
    assert events == ["typing", "reply"]
    assert bot.actions == [(update.message.chat_id, "typing")]


@pytest.mark.parametrize(
    "phrase",
    ["покажи расписание", "помоги", "HELP!"],
    ids=["фраза-ассистента", "просьба-о-справке", "капсом"],
)
async def test_free_text_stranger_does_not_call_model(
    service, session_factory, phrase
):
    llm = FakeLLM(answers={phrase: '{"action":"list"}'})
    update = make_update(phrase, user_id=999)
    bot = FakeBot()
    before = db_snapshot(session_factory)

    await on_free_text(update, free_text_context(service, llm, bot))

    assert update.message.sent == [REFUSAL_TEXT]
    # Справка не должна утечь постороннему ни по какому пути (R-31).
    assert "Что я умею" not in update.message.sent[0]
    assert llm.calls == []
    assert bot.actions == []
    assert db_snapshot(session_factory) == before


async def test_free_text_ignores_commands(service):
    llm = FakeLLM()
    update = make_update("/list")
    bot = FakeBot()

    await on_free_text(update, free_text_context(service, llm, bot))

    assert update.message.sent == []
    assert llm.calls == []
    assert bot.actions == []


def db_snapshot(session_factory) -> tuple[int, ...]:
    """Счётчики всех таблиц: справка не должна писать никуда (R-30).

    Таблицы берутся из метадаты, а не перечисляются руками: новая таблица должна
    попадать в проверку сама, иначе «ничего не изменилось» тихо сузится.
    """
    with session_factory() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(table))
            for table in Base.metadata.sorted_tables
        )


async def test_free_text_help_does_not_call_model(service, session_factory):
    llm = FakeLLM()
    bot = FakeBot()
    update = make_update("помоги")
    before = db_snapshot(session_factory)

    await on_free_text(update, free_text_context(service, llm, bot))

    assert update.message.sent == [format_help()]
    assert llm.calls == []
    # Справка — мгновенный ответ, «печатает…» здесь только шумит.
    assert bot.actions == []
    assert db_snapshot(session_factory) == before


async def test_free_text_help_works_without_model(service):
    llm = FakeLLM(mode="unreachable")
    update = make_update("help")

    await on_free_text(update, free_text_context(service, llm, FakeBot()))

    assert update.message.sent == [format_help()]
    assert llm.calls == []


async def test_free_text_help_like_phrase_goes_to_model(service):
    phrase = "помоги составить тренировку на среду"
    llm = FakeLLM()
    update = make_update(phrase)

    await on_free_text(update, free_text_context(service, llm, FakeBot()))

    assert [text for text, _ in llm.calls] == [phrase]
