"""Модели данных.

Схема повторяет design.md (D3): тренировка — это шапка, расписание — строки
«день недели + время». Логики здесь нет, только хранение.
"""

from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.reminders import ReminderStatus
from src.db.base import Base


class User(Base):
    """Учётная запись (R-41): чьи тренировки, самочувствие и планы.

    Настройки живут здесь, а не в конфиге: второй человек может быть в другой
    таймзоне и спрашивать самочувствие в другое время.
    """

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(unique=True)
    tz: Mapped[str] = mapped_column(String(64))
    wellness_time: Mapped[str] = mapped_column(String(5))
    week_review_time: Mapped[str] = mapped_column(String(5))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class Workout(Base):
    __tablename__ = "workout"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Владелец (R-42). `schedule`/`reminder`/`checkin` наследуют его через
    # join: дублирование поля завело бы вторую правду.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Свободный тип (R-33): «бег», «интервальный бег»; 32 — верхняя граница.
    workout_type: Mapped[str] = mapped_column(String(32))
    duration_min: Mapped[int]
    # Дистанция (R-32): хранится рядом с минутами, а не вместо них.
    distance_km: Mapped[float | None] = mapped_column(Float, default=None)
    # Разовая тренировка (R-35): дата вместо дней недели в `schedule`, а время
    # живёт рядом с датой, потому что строки расписания у неё нет.
    date_once: Mapped[date | None] = mapped_column(Date, default=None)
    time_once: Mapped[str | None] = mapped_column(String(5), default=None)
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
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_wellness_user_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Локальная дата 'YYYY-MM-DD', а не момент: «самочувствие за 26-е» — это
    # день пользователя, и переход на летнее время не должен его менять.
    day: Mapped[str] = mapped_column(String(10))
    score: Mapped[int | None] = mapped_column(default=None)
    note: Mapped[str | None] = mapped_column(String(500), default=None)
    prompted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class PlanSnapshot(Base):
    """Что бот советовал на конкретную дату (R-36).

    Один снимок на дату: план детерминирован от (расписание, оценка), поэтому
    повторный показ — это новый ответ на тот же вопрос, а не второе мнение.
    """

    __tablename__ = "plan_snapshot"
    __table_args__ = (UniqueConstraint("user_id", "plan_date", name="uq_snapshot_user_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_date: Mapped[date] = mapped_column(Date)
    score: Mapped[int]
    is_rest: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    items: Mapped[list[PlanSnapshotItem]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PlanSnapshotItem.id",
    )


class PlanSnapshotItem(Base):
    """Элемент снимка плана: что именно было предложено."""

    __tablename__ = "plan_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("plan_snapshot.id", ondelete="CASCADE")
    )
    workout_type: Mapped[str] = mapped_column(String(32))
    time_of_day: Mapped[str] = mapped_column(String(5))
    duration_min: Mapped[int]
    # Тренировку могли отключить после показа: снимок обязан пережить это.
    workout_id: Mapped[int | None] = mapped_column(
        ForeignKey("workout.id", ondelete="SET NULL"), default=None
    )

    snapshot: Mapped[PlanSnapshot] = relationship(back_populates="items")


class WeeklyReport(Base):
    """Право на отправку недельного разбора (R-40).

    Одна строка на неделю: уникальность `week_start` делает отправку
    однократной даже после рестарта — та же механика, что у `wellness` и
    напоминаний.
    """

    __tablename__ = "weekly_report"
    __table_args__ = (UniqueConstraint("user_id", "week_start", name="uq_report_user_week"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    week_start: Mapped[date] = mapped_column(Date)
    # claim — право попробовать; sent_at заполняется только после доставки.
    # Если процесс умер между ними, claim считается протухшим и перехватывается
    # повторно — иначе неделя терялась бы молча.
    claimed_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    sent_at: Mapped[datetime | None] = mapped_column(default=None)
