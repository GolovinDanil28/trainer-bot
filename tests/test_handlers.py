"""Тесты хендлеров: тонкий слой, поэтому и проверок немного.

Telegram не поднимается: вместо `Update` и `Context` — подделки из
`tests/fakes.py`. Хендлер их не различает, потому что ничего, кроме
`message.text` и `reply_text`, не использует.
"""

from sqlalchemy import func, select

from src.bot.handlers import cmd_list, cmd_new, cmd_off
from src.db.models import Workout
from tests.fakes import make_context, make_update


def service_context(service, args: list[str] | None = None):
    return make_context({"workout_service": service}, args)


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
