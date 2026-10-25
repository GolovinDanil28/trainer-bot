"""`python -m src` — запуск бота.

Отдельный модуль нужен, чтобы `python -m src` работал без установки пакета.
"""

from src.app import main

if __name__ == "__main__":
    main()
