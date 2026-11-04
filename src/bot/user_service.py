"""Регистрация по инвайт-коду (R-44).

Код проверяется здесь, а не в хендлере: это правило домена, и его нельзя
забыть, добавив новый вход. Сравнение — `compare_digest`, чтобы код нельзя
было подбирать по времени ответа.
"""

from hmac import compare_digest

from sqlalchemy.orm import Session, sessionmaker

from src.core.users import Registration, UserProfile
from src.db import repository


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
