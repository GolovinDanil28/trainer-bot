"""Тесты сервиса: команда пользователя → текст ответа + состояние базы."""

from sqlalchemy import func, select

from src.core.messages import EMPTY_LIST_HINT
from src.db.models import Schedule, Workout


def test_create_from_valid_command(service, session_factory):
    reply = service.create_from_command("/new strength Пн,Ср,Пт 19:30")

    assert reply == "Готово: #1 — силовая, Пн, Ср, Пт, 19:30 (45 мин)."
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Workout)) == 1


def test_invalid_command_writes_nothing(service, session_factory):
    reply = service.create_from_command("/new strength Пн 25:00")

    assert "вне диапазона" in reply
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Workout)) == 0
        assert session.scalar(select(func.count()).select_from(Schedule)) == 0


def test_show_schedule_empty_and_filled(service):
    assert service.show_schedule() == EMPTY_LIST_HINT

    service.create_from_command("/new cardio Вт,Сб 07:00")
    text = service.show_schedule()

    assert text.startswith("Твои тренировки:")
    assert "#1 — кардио, Вт, Сб, 07:00 (30 мин)" in text


def test_disable_existing_workout(service):
    service.create_from_command("/new mobility Вс 10:00")

    assert service.disable("1") == "Тренировка #1 отключена. История отметок сохранена."
    assert service.show_schedule() == EMPTY_LIST_HINT


def test_disable_unknown_workout(service):
    assert service.disable("99") == "Тренировки #99 нет. Посмотреть список: /list"


def test_disable_with_non_numeric_argument(service):
    reply = service.disable("вторую")

    assert "Нужен номер тренировки" in reply
    assert "вторую" in reply
