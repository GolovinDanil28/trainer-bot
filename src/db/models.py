"""Модели данных.

Схема повторяет design.md (D3): тренировка — это шапка, расписание — строки
«день недели + время». Логики здесь нет, только хранение.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.reminders import ReminderStatus
from src.db.base import Base


class Workout(Base):
    __tablename__ = "workout"

    id: Mapped[int] = mapped_column(primary_key=True)
    workout_type: Mapped[str] = mapped_column(String(16))
    duration_min: Mapped[int]
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    schedules: Mapped[list[Schedule]] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        # selectin: расписание нужно почти всегда вместе с тренировкой,
        # иначе на список из N тренировок получим N+1 запросов.
        lazy="selectin",
    )


class Schedule(Base):
    __tablename__ = "schedule"
    __table_args__ = (
        # Один и тот же день и время для одной тренировки не дублируются:
        # иначе напоминание уйдёт дважды по вине данных, а не планировщика.
        UniqueConstraint("workout_id", "weekday", "time_of_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workout_id: Mapped[int] = mapped_column(ForeignKey("workout.id", ondelete="CASCADE"))
    weekday: Mapped[int]
    # Локальное время "HH:MM": расписание недельное и повторяющееся, поэтому
    # конкретный UTC-момент здесь хранить нельзя (см. design.md D3).
    time_of_day: Mapped[str] = mapped_column(String(5))
    is_active: Mapped[bool] = mapped_column(default=True)

    workout: Mapped[Workout] = relationship(back_populates="schedules")


class Reminder(Base):
    """Событие доставки: одна тренировка в один конкретный момент.

    Именно эта таблица делает отправку идемпотентной: уникальный ключ
    `(workout_id, scheduled_at)` не даёт создать второе напоминание на тот же
    момент, даже если процесс перезапустился или тик сработал дважды.
    """

    __tablename__ = "reminder"
    __table_args__ = (UniqueConstraint("workout_id", "scheduled_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workout_id: Mapped[int] = mapped_column(ForeignKey("workout.id", ondelete="CASCADE"))
    # SQLite не хранит таймзону, поэтому в базе всегда naive-UTC,
    # а aware-UTC восстанавливается при чтении (см. repository._as_utc).
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(10), default=ReminderStatus.PENDING.value, index=True
    )
    attempts: Mapped[int] = mapped_column(default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class Checkin(Base):
    """Отметка пользователя: одна на напоминание.

    `unique=True` на `reminder_id` — это и есть вся механика R-12: вторую
    отметку не даст вставить сама база, а не проверка в коде, которая может
    не успеть между двумя нажатиями.
    """

    __tablename__ = "checkin"

    id: Mapped[int] = mapped_column(primary_key=True)
    reminder_id: Mapped[int] = mapped_column(
        ForeignKey("reminder.id", ondelete="CASCADE"), unique=True
    )
    # 'done' | 'skipped' — значения CheckinStatus, не CheckinAction.
    status: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class Wellness(Base):
    """Самочувствие за день: одна строка = один календарный день пользователя.

    Уникальность `day` — это идемпотентность вечернего вопроса (R-19): второй
    тик не вставит вторую строку, значит второго вопроса тоже не будет.

    `score is None` — отдельное состояние («вопрос задан, ответа нет»), а не
    отсутствие данных. Поэтому строка живёт дольше одного тика.
    """

    __tablename__ = "wellness"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Локальная дата 'YYYY-MM-DD', а не момент: «самочувствие за 26-е» — это
    # день пользователя, и переход на летнее время не должен его менять.
    day: Mapped[str] = mapped_column(String(10), unique=True)
    score: Mapped[int | None] = mapped_column(default=None)
    note: Mapped[str | None] = mapped_column(String(500), default=None)
    prompted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
