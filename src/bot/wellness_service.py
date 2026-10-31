"""Сервис самочувствия: команда, кнопка и показ плана.

Своей записи в базу здесь нет: разбор — в `core/wellness.py`, запись — в
`repository`, тексты — в `core/messages.py`. Поэтому сценарии проверяются
строкой на входе и текстом на выходе, без Telegram и без мока базы.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from src.core.messages import (
    format_plan,
    format_plan_without_score,
    format_stale_wellness_button,
    format_weekly_review,
    format_wellness_command_hint,
    format_wellness_saved,
)
from src.core.plan import build_plan, build_weekly_review, week_window
from src.core.wellness import (
    WellnessCommandError,
    WellnessScore,
    parse_wellness_command,
)
from src.db import repository


@dataclass(frozen=True, slots=True)
class WellnessButtonResult:
    """Итог нажатия кнопки.

    `accepted=False` значит «это вопрос другого дня»: текст нужно показать
    всплывающим ответом, а не переписывать им старую карточку.
    """

    text: str
    accepted: bool


class WellnessService:
    """Оценка за сегодня и план на завтра в терминах команд пользователя."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        zone: ZoneInfo,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        # Зона нужна, чтобы «сегодня» и «завтра» были днями пользователя, а не
        # UTC-сутками (AGENTS.md §8.4).
        self._zone = zone
        # Часы — отдельная точка входа для тестов хендлеров: без неё проверка
        # «кнопка от вчерашнего вопроса» зависела бы от дня запуска.
        self._clock = clock or (lambda: datetime.now(UTC))

    def record_command(self, text: str, now_utc: datetime | None = None) -> str:
        """`/wellness <1–5> [заметка]` → подтверждение и план (R-21, R-25)."""
        try:
            score, note = parse_wellness_command(text)
        except WellnessCommandError:
            return format_wellness_command_hint()

        moment = now_utc or self._clock()
        today = self._today(moment)
        with self._session_factory() as session:
            repository.save_wellness(session, today, score.value, note, moment)

        return self._with_plan(score.value, today)

    def record_from_button(
        self, day: date, score: int, now_utc: datetime | None = None
    ) -> WellnessButtonResult:
        """Нажатие кнопки 1–5 под вопросом (R-20).

        День приходит из `callback_data`: кнопка вчерашнего вопроса не должна
        перезаписывать сегодняшнюю оценку. Оценка проверяется здесь же, до
        записи: сервис — граница входа, а не место, где доверяют аргументам.
        """
        validated = WellnessScore(score)
        moment = now_utc or self._clock()
        today = self._today(moment)
        if day != today:
            return WellnessButtonResult(format_stale_wellness_button(), accepted=False)

        with self._session_factory() as session:
            repository.save_wellness(session, today, validated.value, None, moment)

        return WellnessButtonResult(self._with_plan(validated.value, today), accepted=True)

    def show_plan(self, now_utc: datetime | None = None) -> str:
        """`/plan`: план на завтра или подсказка, если оценки нет (R-25).

        Пишет только снимок показанного плана (R-36); расписание, тренировки
        и напоминания не трогает (R-24).
        """
        moment = now_utc or self._clock()
        today = self._today(moment)
        with self._session_factory() as session:
            entry = repository.wellness_for_day(session, today)
            if entry is None or not entry.is_answered:
                return format_plan_without_score()
            # `is_answered` уже гарантирует значение; `or 0` скрыл бы поломку
            # инварианта и превратил её в ValueError из WellnessScore.
            assert entry.score is not None
            return self._render_plan(session, today, entry.score)

    def weekly_review(self, now_utc: datetime | None = None) -> str:
        """`/week`: разбор последней завершённой недели (R-37).

        Только чтение и арифметика: модели здесь нет, поэтому команда работает
        и без Ollama (D6).
        """
        moment = now_utc or self._clock()
        today = self._today(moment)
        week_start, week_end = week_window(today)
        return self.weekly_review_for(week_start, week_end)

    def weekly_review_for(
        self, week_start: date, week_end: date, until: datetime | None = None
    ) -> str:
        """Разбор за явные границы недели: нужен и `/week`, и воскресному тику.

        `/week` спрашивает про прошлую неделю, авторазбор — про текущую,
        которая заканчивается сегодня (D3).
        """
        start_utc = datetime.combine(week_start, time.min, tzinfo=self._zone)
        end_utc = datetime.combine(week_end + timedelta(days=1), time.min, tzinfo=self._zone)
        with self._session_factory() as session:
            snapshots = repository.week_snapshots(session, week_start, week_end)
            reminders = repository.week_reminders(session, start_utc, end_utc)
            wellness = {
                entry.day: entry
                for entry in repository.wellness_range(session, week_start, week_end)
            }

        review = build_weekly_review(
            snapshots, reminders, wellness, week_start, week_end, self._zone, until
        )
        return format_weekly_review(review)

    def _today(self, now_utc: datetime) -> date:
        """Календарный день пользователя для момента в UTC."""
        return now_utc.astimezone(self._zone).date()

    def _with_plan(self, score: int, today: date) -> str:
        with self._session_factory() as session:
            plan = self._render_plan(session, today, score)
        return f"{format_wellness_saved(score)}\n\n{plan}"

    def _render_plan(self, session: Session, today: date, score: int) -> str:
        tomorrow = today + timedelta(days=1)
        workouts = repository.workouts_for_day(session, tomorrow)
        plan = build_plan(WellnessScore(score), workouts)
        # Снимок пишется здесь, в единственной точке показа плана (R-36):
        # и вечерний ответ, и /plan проходят через неё, поэтому «показали, но
        # не сохранили» невозможно.
        repository.save_plan_snapshot(session, tomorrow, plan)
        return format_plan(plan)
