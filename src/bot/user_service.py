"""Регистрация по инвайт-коду (R-44).

Код проверяется здесь, а не в хендлере: это правило домена, и его нельзя
забыть, добавив новый вход. Сравнение — `compare_digest`, чтобы код нельзя
было подбирать по времени ответа.
"""

from hmac import compare_digest

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.core.messages import format_settings, format_settings_saved
from src.core.users import Registration, UserProfile, parse_settings_command
from src.db import repository
from src.db.models import User


class UserService:
    """Поиск профиля и регистрация нового человека."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        registration: Registration,
    ) -> None:
        self._session_factory = session_factory
        self._registration = registration

    def find(self, telegram_id: int) -> UserProfile | None:
        with self._session_factory() as session:
            return repository.user_by_telegram(session, telegram_id)

    def settings(self, user_id: int, args: list[str]) -> str:
        """Показать или изменить настройки (R-45). Ошибки — текстом."""
        update_values = parse_settings_command(args)
        with self._session_factory() as session:
            if update_values is None:
                row = session.scalar(select(User).where(User.id == user_id))
                if row is None:  # pragma: no cover — вызывается из-под доступа
                    raise RuntimeError("профиль не найден")
                return format_settings(
                    row.tz, row.wellness_time, row.week_review_time
                )
            updated = repository.update_user_settings(
                session,
                user_id,
                tz=update_values.tz,
                wellness_time=update_values.wellness_time,
                week_review_time=update_values.week_review_time,
            )
        return format_settings_saved(
            updated.tz, updated.wellness_time, updated.week_review_time
        )

    def register(self, telegram_id: int, code: str) -> UserProfile | None:
        """Создать учётную запись, если код верный. `None` — отказ."""
        if not self._registration.enabled:
            return None
        if not compare_digest(code.strip(), self._registration.invite_code):
            return None
        with self._session_factory() as session:
            return repository.create_user(
                session,
                telegram_id,
                self._registration.tz,
                self._registration.wellness_time,
                self._registration.week_review_time,
            )
