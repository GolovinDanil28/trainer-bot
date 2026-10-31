"""Текущий пользователь запроса (R-41, R-42).

Почему contextvar, а не параметр в каждой функции: сервисы в боте — синглтоны,
а пользователь приходит вместе с обновлением Telegram. Так обработчик (или тест)
говорит «сейчас работаем за пользователя N», и все вызовы репозитория ниже
получают владельца, не протаскивая его через десять подписей.

Значение по умолчанию — `DEFAULT_USER_ID`: его создаёт инициализация базы.
Это нужно тестам и первому запуску, когда других пользователей ещё нет.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

DEFAULT_USER_ID = 1

_current: ContextVar[int] = ContextVar("current_user_id", default=DEFAULT_USER_ID)


def current_user_id() -> int:
    """Пользователь, от имени которого выполняется операция."""
    return _current.get()


@contextmanager
def acting_as(user_id: int) -> Iterator[None]:
    """Выполнить блок от имени пользователя. Восстанавливает прежнее значение."""
    token = _current.set(user_id)
    try:
        yield
    finally:
        _current.reset(token)
