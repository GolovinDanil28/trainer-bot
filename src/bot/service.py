"""Сервис расписания: связывает разбор команды, базу и тексты ответов.

Здесь живёт вся логика обработки команды, но нет ни одного объекта Telegram.
Поэтому сценарии проверяются обычным тестом: на вход строке, на выходе текст
и состояние базы.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from src.core.messages import (
    format_already_marked,
    format_checkin_saved,
    format_created,
    format_date_unclear,
    format_disabled,
    format_history,
    format_schedule,
    format_unknown_reminder,
    format_unknown_workout,
)
from src.core.reminders import CheckinAction
from src.core.schedule import (
    CommandError,
    Weekday,
    make_once_draft,
    make_weekly_draft,
    parse_new_command,
    resolve_date,
)
from src.db import repository


class WorkoutService:
    """Операции расписания в терминах команд пользователя."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        zone: ZoneInfo,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        # Таймзона нужна только для показа времени человеку: в базе всегда UTC.
        self._zone = zone
        # Часы подменяемы ради тестов: «сегодня» у дат и фильтра разовых должно
        # быть фиксированным, иначе тест флапает на границе суток (§9).
        self._clock = clock or (lambda: datetime.now(UTC))

    def create_from_command(self, text: str) -> str:
        """`/new …` → текст подтверждения или текст ошибки."""
        try:
            draft = parse_new_command(text)
        except CommandError as exc:
            # Ошибка ввода не доходит до базы: транзакция даже не начинается.
            return str(exc)
        return self._save(draft)

    def create_weekly(
        self,
        workout_type: str,
        weekdays: tuple[Weekday, ...],
        time_of_day: time,
        distance_km: float | None = None,
        duration_min: int | None = None,
    ) -> str:
        """Создать недельную тренировку из уже проверенных значений (R-14).

        Отдельный вход для свободного текста: тип может быть фразой с пробелом
        («интервальный бег»), а строка `/new` такое не переживёт. Правила
        длительности при этом те же, что у команды, — через `make_weekly_draft`.
        """
        return self._save(
            make_weekly_draft(
                workout_type, weekdays, time_of_day, distance_km, duration_min
            )
        )

    def create_once(
        self,
        date_raw: str,
        workout_type: str,
        time_of_day: time,
        distance_km: float | None = None,
        duration_min: int | None = None,
        today: date | None = None,
    ) -> str:
        """Создать разовую тренировку на дату (R-35).

        Дата считается здесь, а не моделью (D2): `today` можно передать явно
        в тестах, а в бою берётся календарный день владельца.
        """
        day = resolve_date(date_raw, today or self._today())
        if day is None:
            return format_date_unclear(date_raw)
        return self._save(
            make_once_draft(workout_type, day, time_of_day, distance_km, duration_min)
        )

    def _save(self, draft) -> str:  # noqa: ANN001 — WorkoutDraft без цикла импортов
        with self._session_factory() as session:
            workout = repository.create_workout(session, draft)
        return format_created(workout)

    def show_schedule(self) -> str:
        with self._session_factory() as session:
            return format_schedule(repository.list_active(session, self._today()))

    def _today(self) -> date:
        """Календарный день владельца: по нему прячутся прошедшие разовые."""
        return self._clock().astimezone(self._zone).date()

    def schedule_snapshot(self) -> str:
        """Возвращает читаемый снапшот текущих активных тренировок.

        Не изменяет состояние БД. Может использоваться при построении
        пользовательского промпта (D3.4).
        """
        with self._session_factory() as session:
            return format_schedule(repository.list_active(session, self._today()))

    def disable(self, raw_id: str) -> str:
        """`/off <id>` → текст подтверждения или текст ошибки."""
        try:
            workout_id = int(raw_id)
        except ValueError:
            return f"Нужен номер тренировки, получено «{raw_id}». Посмотреть список: /list"

        with self._session_factory() as session:
            if not repository.deactivate(session, workout_id):
                return format_unknown_workout(workout_id)

        return format_disabled(workout_id)

    def record_checkin(self, reminder_id: int, action: CheckinAction) -> str:
        """Нажатие кнопки → текст ответа. Журнал меняется только в первый раз."""
        with self._session_factory() as session:
            outcome = repository.record_checkin(session, reminder_id, action)

        if outcome is None:
            return format_unknown_reminder()
        if outcome.created:
            return format_checkin_saved(outcome.status)
        return format_already_marked(outcome.status)

    def show_history(self) -> str:
        """`/history` — последние записи журнала в таймзоне пользователя (R-13)."""
        with self._session_factory() as session:
            entries = repository.recent_history(session)
        return format_history(entries, self._zone)
