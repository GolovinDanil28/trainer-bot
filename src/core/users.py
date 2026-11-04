"""Пользователь как доменное понятие: профиль и регистрация (R-41, R-44).

Ни Telegram, ни БД: только данные, которые нужны планировщику и доступу.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UserProfile:
    """Всё, что планировщику нужно знать о человеке."""

    id: int
    telegram_id: int
    tz: str
    wellness_time: str
    week_review_time: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class Registration:
    """Правила входа: многоразовый код и настройки по умолчанию."""

    invite_code: str
    tz: str
    wellness_time: str
    week_review_time: str

    @property
    def enabled(self) -> bool:
        """Пустой код отключает регистрацию: остаётся только владелец."""
        return bool(self.invite_code)
