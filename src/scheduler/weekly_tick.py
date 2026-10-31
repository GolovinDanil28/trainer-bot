"""Воскресный разбор недели: право на отправку, текст, доставка (R-40).

Как и вопрос о самочувствии: порог — свойство дня («воскресенье, время ≥
WEEK_REVIEW_TIME»), а не точный момент. Право на отправку даёт база, поэтому
рестарт и второй процесс разбор не дублируют.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from src.core.plan import current_week
from src.db import repository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeeklyTickResult:
    """Что сделал проход: неделя, попытка отправки, успех."""

    week_start: date | None
    sent: bool = False


class WeeklyTick:
    """Раз в неделю: воскресенье после порога — разбор владельцу."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        send_text: Callable[[str], object],
        zone: ZoneInfo,
        at: time,
        render: Callable[[date, date, datetime], str],
    ) -> None:
        self._session_factory = session_factory
        self._send_text = send_text
        self._zone = zone
        self._at = at
        self._render = render

    async def run(self, now_utc: datetime) -> WeeklyTickResult:
        local = now_utc.astimezone(self._zone)
        # Воскресенье — шестой день недели в Python (пн = 0).
        if local.weekday() != 6 or local.time() < self._at:
            return WeeklyTickResult(week_start=None)

        week_start, week_end = current_week(local.date())
        with self._session_factory() as session:
            claimed = repository.claim_weekly_report(session, week_start, now_utc)
        if not claimed:
            return WeeklyTickResult(week_start=week_start)

        try:
            # `until` — чтобы тренировка, до которой ещё не дошло время, не
            # считалась пропущенной (сценарий «день ещё не закончился»).
            text = self._render(week_start, week_end, now_utc)
        except Exception as exc:
            # Отдельный лог: сборка сломалась — это данные, а не Telegram.
            logger.warning("недельный разбор за %s не собран: %s", week_start, exc)
            self._release(week_start)
            return WeeklyTickResult(week_start=week_start)

        try:
            await self._send_text(text)
        except Exception as exc:
            # Транспорт падает чем угодно; потерять разбор молча нельзя.
            logger.warning("недельный разбор за %s не доставлен: %s", week_start, exc)
            self._release(week_start)
            return WeeklyTickResult(week_start=week_start)

        with self._session_factory() as session:
            repository.mark_weekly_report_sent(session, week_start, now_utc)
        logger.info("недельный разбор за %s отправлен", week_start)
        return WeeklyTickResult(week_start=week_start, sent=True)

    def _release(self, week_start: date) -> None:
        with self._session_factory() as session:
            repository.release_weekly_report(session, week_start)

    async def run_now(self) -> WeeklyTickResult:
        """Проход по текущим часам — то, что вызывает APScheduler."""
        return await self.run(datetime.now(UTC))
