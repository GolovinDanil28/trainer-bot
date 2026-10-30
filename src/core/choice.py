"""Кнопки уточнения типа (R-34): формат callback_data — домен, не Telegram.

Телеграму всё равно, что лежит в `callback_data`; формат нужен нам, чтобы
разбор был чистым и тестируемым. Ограничение Telegram — 64 байта на строку:
`tc:<8 hex>:<цифра>` заведомо короче.
"""

from src.core.reminders import CallbackError

PREFIX = "tc"


def build_type_choice_callback(pending_id: str, index: int) -> str:
    """Собрать данные кнопки: какой pending и какой вариант выбран."""
    return f"{PREFIX}:{pending_id}:{index}"


def parse_type_choice_callback(data: str) -> tuple[str, int]:
    """Разобрать данные кнопки. Подделка и старые форматы — `CallbackError`."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise CallbackError(f"не типовая кнопка «{data}»")
    pending_id, raw_index = parts[1], parts[2]
    if not pending_id or not raw_index.isdigit():
        raise CallbackError(f"невалидные данные кнопки «{data}»")
    return pending_id, int(raw_index)
