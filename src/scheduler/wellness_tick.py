"""Вечерний вопрос о самочувствии: «наступило ли время» и отправка.

Отличие от напоминаний: у напоминания есть точный момент из расписания, поэтому
там окно ±30 секунд. У вопроса момент — свойство дня, поэтому порог здесь
«локальное время ≥ WELLNESS_TIME»: если бот спал в 21:00, вопрос уйдёт после
пробуждения, а не потеряется (R-19).

Право на отправку даёт база: `claim_wellness_prompt` вставляет строку дня
через `ON CONFLICT DO NOTHING`, поэтому рестарт и второй процесс вопрос не
дублируют.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from src.core.wellness import PromptSender, WellnessPrompt
from src.db import repository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WellnessTickResult:
    """Что сделал проход. Возвращается, чтобы это можно было проверить."""

    day: date | None
    sent: bool = False


class WellnessTick:
    """Проверка «пора спросить?» и отправка вопроса. Состояние — только в БД."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        sender: PromptSender,
        zone: ZoneInfo,
        at: time,
    ) -> None:
        self._session_factory = session_factory
        self._sender = sender
        self._zone = zone
        self._at = at

    async def run(self, now_utc: datetime) -> WellnessTickResult:
        local = now_utc.astimezone(self._zone)
        if local.time() < self._at:
            return WellnessTickResult(day=None)

        day = local.date()
        with self._session_factory() as session:
            claimed = repository.claim_wellness_prompt(session, day, now_utc)
        if not claimed:
            # День уже занят: вопрос ушёл (или оценка уже стоит).
            return WellnessTickResult(day=day)

        try:
            await self._sender.send_prompt(WellnessPrompt(day=day))
        except Exception as exc:
            # Ловим всё: транспорт падает чем угодно, а потерять вопрос молча
            # нельзя. Освобождаем день, чтобы следующий тик попробовал снова.
            logger.warning("вопрос о самочувствии за %s не отправлен: %s", day, exc)
            with self._session_factory() as session:
                repository.release_wellness_prompt(session, day)
            return WellnessTickResult(day=day)

        return WellnessTickResult(day=day, sent=True)

    async def run_now(self) -> WellnessTickResult:
        """Проход по текущим часам — то, что вызывает APScheduler."""
        return await self.run(datetime.now(UTC))
