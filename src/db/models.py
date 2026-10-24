"""Модели данных.

Схема повторяет design.md (D3): тренировка — это шапка, расписание — строки
«день недели + время». Логики здесь нет, только хранение.
"""

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
