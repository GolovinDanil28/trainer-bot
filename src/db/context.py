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
from zoneinfo import ZoneInfo

DEFAULT_USER_ID = 1

_current: ContextVar[int] = ContextVar("current_user_id", default=DEFAULT_USER_ID)
_zone: ContextVar[str | None] = ContextVar("current_user_tz", default=None)


def current_user_id() -> int:
    """Пользователь, от имени которого выполняется операция."""
    return _current.get()


@contextmanager
def acting_as(user_id: int, tz: str | None = None) -> Iterator[None]:
    """Выполнить блок от имени пользователя и в его таймзоне (R-45)."""
    user_token = _current.set(user_id)
    zone_token = _zone.set(tz)
    try:
        yield
    finally:
        _zone.reset(zone_token)
        _current.reset(user_token)


def current_zone(fallback: ZoneInfo) -> ZoneInfo:
    """Зона пользователя из контекста или зона процесса, если контекста нет."""
    tz = _zone.get()
    return ZoneInfo(tz) if tz else fallback
