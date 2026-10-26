"""Тесты хендлеров: тонкий слой, поэтому и проверок немного.

Telegram не поднимается: вместо `Update` и `Context` — подделки из
`tests/fakes.py`. Хендлер их не различает, потому что ничего, кроме
`message.text` и `reply_text`, не использует.
"""

import pytest
from sqlalchemy import func, select

from src.bot.access import OWNER_KEY, REFUSAL_TEXT
from src.bot.assistant_service import AssistantService
from src.bot.handlers import cmd_history, cmd_list, cmd_new, cmd_off, on_free_text
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


# Все команды — через один декоратор, поэтому отказ должен быть буквально
# одинаковым. Параметризация ловит случай, когда новую команду забыли закрыть.
COMMANDS = [
    pytest.param(cmd_new, "/new strength Пн 19:30", id="new"),
    pytest.param(cmd_list, "/list", id="list"),
    pytest.param(cmd_off, "/off 1", id="off"),
    pytest.param(cmd_history, "/history", id="history"),
]


@pytest.mark.parametrize(("handler", "text"), COMMANDS)
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


async def test_free_text_stranger_does_not_call_model(service, session_factory):
    llm = FakeLLM(answers={"покажи расписание": '{"action":"list"}'})
    update = make_update("покажи расписание", user_id=999)
    bot = FakeBot()

    await on_free_text(update, free_text_context(service, llm, bot))

    assert update.message.sent == [REFUSAL_TEXT]
    assert llm.calls == []
    assert bot.actions == []
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Workout)) == 0


async def test_free_text_ignores_commands(service):
    llm = FakeLLM()
    update = make_update("/list")
    bot = FakeBot()

    await on_free_text(update, free_text_context(service, llm, bot))

    assert update.message.sent == []
    assert llm.calls == []
    assert bot.actions == []
