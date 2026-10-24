"""Тесты вычисления момента напоминания.

Время везде фиксированное: тест, зависящий от текущего дня, ломается в
новогоднюю ночь и потом полдня ищется причина.
"""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from src.core.schedule import Weekday
from src.core.timeplan import moment_in_window, next_occurrence

YEKBURG = ZoneInfo("Asia/Yekaterinburg")  # UTC+5, перехода на летнее время нет
BERLIN = ZoneInfo("Europe/Berlin")  # UTC+1/+2, переходы есть


def test_later_today_returns_today():
    # Понедельник 26.10.2026, 08:00 UTC = 13:00 в Екатеринбурге.
    now = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)

    moment = next_occurrence(now, YEKBURG, Weekday.MONDAY, time(19, 30))

    assert moment == datetime(2026, 10, 26, 14, 30, tzinfo=UTC)


def test_passed_time_moves_to_next_week():
    # Тот же понедельник, 15:00 UTC = 20:00 в Екатеринбурге: 19:30 уже прошло.
    now = datetime(2026, 10, 26, 15, 0, tzinfo=UTC)

    moment = next_occurrence(now, YEKBURG, Weekday.MONDAY, time(19, 30))

    assert moment == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)


def test_time_equal_to_now_counts_as_passed():
    # Момент ровно «сейчас» не считается наступившим: напоминать не о чем.
    now = datetime(2026, 10, 26, 14, 30, tzinfo=UTC)

    moment = next_occurrence(now, YEKBURG, Weekday.MONDAY, time(19, 30))

    assert moment.date() == date(2026, 11, 2)


def test_next_weekday_is_found():
    # Понедельник -> среда той же недели.
    now = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)

    moment = next_occurrence(now, YEKBURG, Weekday.WEDNESDAY, time(19, 30))

    assert moment == datetime(2026, 10, 28, 14, 30, tzinfo=UTC)


def test_local_time_is_preserved_across_dst_switch():
    # 19:30 не попадает в окно перевода часов, поэтому локальное время
    # остаётся 19:30, а UTC-смещение меняется с +1 на +2.
    winter = next_occurrence(
        datetime(2026, 3, 17, 9, 0, tzinfo=UTC), BERLIN, Weekday.SUNDAY, time(19, 30)
    )
    summer = next_occurrence(
        datetime(2026, 3, 31, 9, 0, tzinfo=UTC), BERLIN, Weekday.SUNDAY, time(19, 30)
    )

    assert winter.astimezone(BERLIN).strftime("%H:%M") == "19:30"
    assert summer.astimezone(BERLIN).strftime("%H:%M") == "19:30"
    # 22.03.2026 — ещё зимнее время (18:30 UTC), 05.04.2026 — уже летнее (17:30 UTC).
    assert winter == datetime(2026, 3, 22, 18, 30, tzinfo=UTC)
    assert summer == datetime(2026, 4, 5, 17, 30, tzinfo=UTC)


def test_nonexistent_local_time_shifts_forward():
    # 29.03.2026 в Берлине 02:00 -> 03:00: время 02:30 не существует.
    now = datetime(2026, 3, 29, 0, 0, tzinfo=UTC)

    moment = next_occurrence(now, BERLIN, Weekday.SUNDAY, time(2, 30))

    assert moment.astimezone(BERLIN).hour == 3


def test_ambiguous_local_time_takes_first_occurrence():
    # 25.10.2026 в Берлине 03:00 -> 02:00: 02:30 существует дважды.
    now = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)

    moment = next_occurrence(now, BERLIN, Weekday.SUNDAY, time(2, 30))

    # Первое вхождение — по летнему времени (UTC+2), то есть 00:30 UTC.
    assert moment == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


def test_naive_now_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        next_occurrence(datetime(2026, 10, 26, 8, 0), YEKBURG, Weekday.MONDAY, time(19, 30))


# ─────────────────────────── окно тика ───────────────────────────


def test_moment_just_passed_is_still_in_window():
    # Напоминание было 10 секунд назад: тик, который его пропустил, ещё не истёк.
    just_after = datetime(2026, 10, 26, 14, 30, 10, tzinfo=UTC)  # 19:30:10 по Екатеринбургу

    assert moment_in_window(just_after, YEKBURG, Weekday.MONDAY, time(19, 30)) is not None
    assert moment_in_window(
        datetime(2026, 10, 26, 14, 29, 50, tzinfo=UTC), YEKBURG, Weekday.MONDAY, time(19, 30)
    ) is not None


def test_moment_inside_window_is_returned():
    # Тик в 19:30:20, напоминание в 19:30:00 — попадает в окно (19:29:50, 19:30:50).
    now = datetime(2026, 10, 26, 14, 30, 20, tzinfo=UTC)

    moment = moment_in_window(now, YEKBURG, Weekday.MONDAY, time(19, 30))

    assert moment == datetime(2026, 10, 26, 14, 30, tzinfo=UTC)


def test_moment_far_ahead_is_not_in_window():
    # Тик в 19:29:00, окно до 19:29:30 — напоминание в 19:30 ещё не наступило.
    now = datetime(2026, 10, 26, 14, 29, 0, tzinfo=UTC)

    assert moment_in_window(now, YEKBURG, Weekday.MONDAY, time(19, 30)) is None


def test_wrong_weekday_is_not_in_window():
    now = datetime(2026, 10, 26, 14, 29, 0, tzinfo=UTC)  # понедельник

    assert moment_in_window(now, YEKBURG, Weekday.TUESDAY, time(19, 30)) is None
