"""Вычисление моментов напоминания в таймзоне пользователя.

Всё, что связано с временем, живёт здесь: база хранит UTC, расписание хранит
локальное «Пн 19:30», а конкретный момент рождается на стыке этих двух миров.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.core.schedule import Weekday

# Половина окна тика: планировщик просыпается раз в 30 секунд (design.md D2).
DEFAULT_TICK_WINDOW = timedelta(seconds=30)


def next_occurrence(
    now_utc: datetime,
    zone: ZoneInfo,
    weekday: Weekday,
    at: time,
) -> datetime:
    """Ближайший момент строго после `now_utc`, когда в `zone` наступает `weekday` в `at`.

    Возвращает время в UTC: в базу попадает только UTC (R-04).
    """
    if now_utc.tzinfo is None:
        raise ValueError("now_utc должен быть timezone-aware, иначе непонятно, чей это час")

    local_now = now_utc.astimezone(zone)
    days_ahead = (int(weekday) - local_now.weekday()) % 7

    candidate = _local_moment(local_now.date() + timedelta(days=days_ahead), at, zone)
    if candidate <= local_now:
        # Сегодняшний момент уже прошёл — значит, следующий через неделю.
        candidate = _local_moment(local_now.date() + timedelta(days=days_ahead + 7), at, zone)

    return candidate.astimezone(UTC)


def _local_moment(day, at: time, zone: ZoneInfo) -> datetime:
    """Локальный момент в зоне.

    Переход на летнее время даёт два особых случая:
    - несуществующее время (02:30 в ночь перевода часов вперёд) сдвигается
      вперёд на час — лучше напомнить позже, чем не напомнить;
    - существующее дважды (02:30 в ночь перевода назад) берётся первое, `fold=0`.
    """
    return datetime.combine(day, at, tzinfo=zone)


def moment_in_window(
    now_utc: datetime,
    zone: ZoneInfo,
    weekday: Weekday,
    at: time,
    window: timedelta = DEFAULT_TICK_WINDOW,
) -> datetime | None:
    """Момент напоминания, попавший в окно тика `(now - window, now + window)`.

    Окно нужно, потому что тик не обязан совпасть с минутой напоминания:
    планировщик просыпается раз в 30 секунд, а расписание задано с точностью
    до минуты. Окно открыто с обеих сторон, потому что `next_occurrence`
    возвращает момент строго позже переданного времени — граница ровно `now`
    достаётся следующему тику, и момент не теряется.
    """
    candidate = next_occurrence(now_utc - window, zone, weekday, at)
    if candidate < now_utc + window:
        return candidate
    return None


def waiting_cutoff(
    now_utc: datetime,
    zone: ZoneInfo,
    miss_window: timedelta | None,
) -> datetime:
    """Момент, раньше которого отправленное напоминание считается `missed`.

    По умолчанию окно — «до конца суток пользователя» (R-08): ответить можно
    до местной полуночи, всё, что отправлено раньше начала сегодняшнего дня,
    уже не ответить. `miss_window` (часы) существует только для отладки.
    """
    if miss_window is not None:
        return now_utc - miss_window

    local_midnight = datetime.combine(
        now_utc.astimezone(zone).date(), time.min, tzinfo=zone
    )
    return local_midnight.astimezone(UTC)

