"""Контракт данных кнопки: что собрали, то и разобрали.

`callback_data` — единственное место, где Telegram передаёт нам данные от
пользователя. Если формат поедет, кнопки начнут «не работать» молча, поэтому
он зафиксирован тестом как договор, а не как деталь реализации.
"""

import pytest

from src.core.reminders import (
    CallbackError,
    CheckinAction,
    checkin_callback,
    parse_checkin_callback,
)


@pytest.mark.parametrize("action", list(CheckinAction))
def test_callback_round_trip(action):
    data = checkin_callback(42, action)

    assert parse_checkin_callback(data) == (42, action)


def test_callback_format_is_stable():
    # Формат `ci:<id>:<done|skip>` виден в отладке и в старых сообщениях:
    # если его поменять, кнопки в уже отправленных напоминаниях перестанут
    # разбираться.
    assert checkin_callback(7, CheckinAction.DONE) == "ci:7:done"
    assert checkin_callback(7, CheckinAction.SKIP) == "ci:7:skip"


@pytest.mark.parametrize(
    "data",
    [
        "",
        "done",
        "ci:7",  # нет действия
        "ci:7:done:extra",  # лишняя часть
        "xx:7:done",  # чужой префикс
        "ci:семь:done",  # id не число
        "ci:7:maybe",  # неизвестное действие
    ],
)
def test_garbage_is_rejected(data):
    # Мусор в callback_data — либо старый формат, либо подделка. В обоих случаях
    # отвечать пользователю бессмысленно: это не его действие.
    with pytest.raises(CallbackError):
        parse_checkin_callback(data)
