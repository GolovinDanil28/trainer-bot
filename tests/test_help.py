"""Справка: данные карточки, триггеры, тексты и README (R-28…R-31).

Домен проверяется строками, без Telegram: `is_help_request` — обычная функция,
а карточка — обычный текст, который можно сравнить целиком или по разделам.
"""

import re
from pathlib import Path

import pytest

from src.core.help import (
    COMMANDS,
    FREE_TEXT_EXAMPLES,
    HELP_TRIGGER_HINT,
    HELP_TRIGGERS,
    is_help_request,
    normalize,
)
from src.core.messages import format_help, format_start
from src.core.plan import (
    LIGHT_SCORE,
    MIN_DURATION_MIN,
    REDUCTION_PERCENT,
    REST_MAX_SCORE,
)

TELEGRAM_MESSAGE_LIMIT = 4096
# Порог «карточка не распухла»: сейчас ~950 символов. 4096 — почти тавтология,
# 1600 поймает удвоение, а не только выход за лимит Telegram.
MAX_CARD_LENGTH = 1600


def test_normalize_ignores_case_punctuation_and_yo():
    assert normalize("  Ёлка, HELP!  ") == "елка help"
    assert normalize("Что   ты умеешь?") == "что ты умеешь"


@pytest.mark.parametrize(
    "text",
    [
        "помоги",
        "Помоги!",
        "ПОМОЩЬ",
        "help",
        "HELP!",
        "инструкция",
        "Инструкция.",
        "что ты умеешь",
        "Что ты умеешь?",
        "как пользоваться",
        "какие команды",
    ],
)
def test_help_request_is_recognized(text):
    assert is_help_request(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "помогите",
        "помоги составить тренировку на среду",
        "help me",
        "как пользоваться ботом?",
        "покажи расписание",
    ],
    ids=[
        "пусто",
        "другая-форма",
        "фраза-для-ассистента",
        "английская-фраза",
        "уточнение",
        "обычная-команда",
    ],
)
def test_similar_phrase_is_not_help(text):
    # Полное совпадение, а не вхождение: иначе «помоги составить тренировку»
    # никогда не дойдёт до ассистента.
    assert is_help_request(text) is False


def test_triggers_are_normalized():
    for trigger in HELP_TRIGGERS:
        assert trigger == normalize(trigger), trigger


def test_card_hint_is_a_subset_of_real_triggers():
    # Подсказка в карточке не может обещать фразу, которая не сработает.
    assert set(HELP_TRIGGER_HINT) <= HELP_TRIGGERS
    for trigger in HELP_TRIGGER_HINT:
        assert is_help_request(trigger)


def test_commands_are_unique_and_well_formed():
    names = [entry.command for entry in COMMANDS]

    assert len(names) == len(set(names))
    for entry in COMMANDS:
        assert entry.command.startswith("/")
        assert entry.purpose
        assert entry.example.startswith(entry.command)
        assert "…" not in entry.example and "..." not in entry.example


def test_free_text_examples_point_to_real_commands():
    known = {entry.command for entry in COMMANDS}

    for _, meaning in FREE_TEXT_EXAMPLES:
        # У примера с датой команды нет — это новая возможность свободного
        # текста; у остальных примеров команда обязана существовать.
        if meaning.startswith("/"):
            assert meaning.split()[0] in known


def test_help_card_has_all_sections_and_commands():
    text = format_help()

    for section in ("Что я умею", "Команды:", "Можно фразой:", "Правила плана"):
        assert section in text, section

    for entry in COMMANDS:
        assert f"{entry.command} — {entry.purpose}" in text
        if entry.example != entry.command:
            # Пример обязан быть в карточке, только если он содержательный.
            assert f"  {entry.example}" in text
        else:
            # Дублирующий пример (example == command) не печатается (R-28).
            assert f"  {entry.command}" not in text

    for phrase, meaning in FREE_TEXT_EXAMPLES:
        assert f"«{phrase}» → {meaning}" in text


def test_help_card_is_honest_about_limits():
    text = format_help()

    assert "Чего не умею" in text
    # Ограничения — это обещание: если убрать строку, карточка начнёт врать.
    for limit in ("переносы", "несколько действий", "составление плана"):
        assert limit in text, limit
    # Разовые тренировки на дату умеем (R-35) и недельный разбор тоже (R-37):
    # карточка не должна утверждать обратное — ложное ограничение хуже
    # отсутствующего.
    assert "только недельное расписание" not in text
    assert "недельный разбор" not in text
    # Про Ollama в карточке строки нет: подсказка «модель не запущена» приходит
    # в момент, когда владелец пишет свободный текст, — там она уместнее.
    assert "Ollama" not in text


def test_help_card_can_be_asked_by_phrase():
    text = format_help()

    for trigger in HELP_TRIGGER_HINT:
        assert f"«{trigger}»" in text


def test_plan_rules_in_card_come_from_plan_constants():
    # Карточка обещает правило. Если поменять константу в plan.py, текст обязан
    # измениться вместе с ней, иначе карточка начнёт врать молча.
    text = format_help()

    assert f"оценка 1–{REST_MAX_SCORE}" in text
    assert f"{LIGHT_SCORE} — тренировка на {100 - REDUCTION_PERCENT} %" in text
    assert f"минимум {MIN_DURATION_MIN} минут" in text


def test_card_examples_are_in_owner_language():
    # R-33: тип — свободная фраза. Английские служебные значения в примерах
    # учили бы команде, которой владелец не пользуется.
    text = format_help().lower()

    for legacy in ("strength", "cardio", "mobility"):
        assert legacy not in text, legacy
    assert "/new силовая" in text
    assert "/new бег" in text


def test_week_line_explains_what_is_inside():
    text = format_help()

    assert "сколько сделано" in text
    assert "что мешало" in text
    assert "что менять" in text


def test_help_card_fits_one_message():
    text = format_help()

    assert len(text) < MAX_CARD_LENGTH
    assert len(text) < TELEGRAM_MESSAGE_LIMIT


def test_start_greets_and_reuses_the_card():
    text = format_start()

    assert text.startswith("Привет!")
    assert format_help() in text


def test_readme_mentions_every_trigger():
    # README — вторая карточка. Она уже отставала от триггеров (4 из 7),
    # поэтому проверяем так же, как команды. Перенос строк в markdown не должен
    # ломать проверку: схлопываем пробелы.
    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = re.sub(r"\s+", " ", readme.read_text(encoding="utf-8"))

    for trigger in HELP_TRIGGERS:
        assert f"«{trigger}»" in text, trigger
