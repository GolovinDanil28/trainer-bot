"""Домен самочувствия: оценка 1–5, разбор команды и данные кнопки.

Telegram здесь нет: на вход строка, на выходе структура или
`WellnessCommandError` с текстом для пользователя. Оценка — число, а не
свободный текст: по ней считаются правила плана, а по строке их не посчитать.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from src.core.reminders import CallbackError

MIN_SCORE = 1
MAX_SCORE = 5
# Заметка — необязательное пояснение, а не дневник: 500 символов хватает,
# чтобы сказать «не выспался», и мало, чтобы превратить таблицу в медкарту.
NOTE_LIMIT = 500

SCORES: tuple[int, ...] = (1, 2, 3, 4, 5)

CALLBACK_PREFIX = "wl"
EXAMPLE = "/wellness 3"

_NOTE_SPACES = re.compile(r"\s+")


class WellnessCommandError(ValueError):
    """Ошибка ввода. Сообщение предназначено пользователю, а не в лог."""


@dataclass(frozen=True, slots=True)
class WellnessScore:
    """Оценка самочувствия. Вне 1–5 не существует: проверка в конструкторе."""

    value: int

    def __post_init__(self) -> None:
        if not MIN_SCORE <= self.value <= MAX_SCORE:
            raise ValueError(
                f"оценка самочувствия должна быть от {MIN_SCORE} до {MAX_SCORE}, "
                f"получено {self.value}"
            )


@dataclass(frozen=True, slots=True)
class WellnessEntry:
    """Самочувствие за день.

    `score is None` — это не «нет данных», а «день занят вопросом, ответа нет»:
    именно это состояние делает отправку вопроса идемпотентной.
    """

    day: date
    score: int | None
    note: str | None

    @property
    def is_answered(self) -> bool:
        return self.score is not None


@dataclass(frozen=True, slots=True)
class WellnessPrompt:
    """Вопрос, который отправляет вечерний тик."""

    day: date
    scores: tuple[int, ...] = SCORES


class PromptSender(Protocol):
    """Транспорт вопроса. Сеть живёт за этим портом, а не в домене."""

    async def send_prompt(self, prompt: WellnessPrompt) -> None:
        """Отправить вопрос. При ошибке доставки — бросить исключение."""
        ...


def parse_wellness_command(text: str) -> tuple[WellnessScore, str | None]:
    """Разобрать `/wellness <1–5> [заметка]` или бросить `WellnessCommandError`."""
    parts = text.split()
    if not parts:
        raise WellnessCommandError(f"Пустая команда. Пример: {EXAMPLE}")

    # `/wellness@TrainerBot` — обычная форма в группах, поэтому упоминание срезаем.
    command = parts[0].split("@", 1)[0].lower()
    if command != "/wellness":
        raise WellnessCommandError(
            f"Ожидалась команда /wellness, получено «{parts[0]}». Пример: {EXAMPLE}"
        )

    if len(parts) < 2:
        raise WellnessCommandError(
            f"Нужна оценка от {MIN_SCORE} до {MAX_SCORE}. Пример: {EXAMPLE}"
        )

    score = _parse_score(parts[1])
    # Заметка — остаток строки: пробелы и переводы строк схлопываем, иначе в
    # хранилище и в тестах одна и та же фраза выглядит по-разному.
    raw_note = " ".join(parts[2:])
    note = _NOTE_SPACES.sub(" ", raw_note).strip() or None
    if note is not None:
        note = note[:NOTE_LIMIT]
    return score, note


def _parse_score(raw: str) -> WellnessScore:
    try:
        value = int(raw)
    except ValueError as exc:
        raise WellnessCommandError(
            f"Оценка должна быть числом от {MIN_SCORE} до {MAX_SCORE}, "
            f"получено «{raw}». Пример: {EXAMPLE}"
        ) from exc

    try:
        return WellnessScore(value)
    except ValueError as exc:
        raise WellnessCommandError(
            f"Оценка вне диапазона {MIN_SCORE}–{MAX_SCORE}, получено «{raw}». "
            f"Пример: {EXAMPLE}"
        ) from exc


def wellness_callback(day: date, score: int) -> str:
    """`wl:<YYYY-MM-DD>:<1..5>` — данные кнопки под вопросом.

    День в кнопке обязателен: сообщение с вопросом остаётся в чате, и вчерашняя
    кнопка не должна перезаписывать сегодняшнюю оценку (R-20).
    """
    WellnessScore(score)
    return f"{CALLBACK_PREFIX}:{day.isoformat()}:{score}"


def parse_wellness_callback(data: str) -> tuple[date, int]:
    """Разобрать данные кнопки. Бросает `CallbackError` на мусор."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
        raise CallbackError(
            f"ожидался формат {CALLBACK_PREFIX}:<YYYY-MM-DD>:<оценка>, получено «{data}»"
        )

    try:
        day = date.fromisoformat(parts[1])
    except ValueError as exc:
        raise CallbackError(f"день в кнопке должен быть датой, получено «{parts[1]}»") from exc

    try:
        score = WellnessScore(int(parts[2]))
    except ValueError as exc:
        raise CallbackError(
            f"оценка в кнопке должна быть числом 1–5, получено «{parts[2]}»"
        ) from exc

    return day, score.value
