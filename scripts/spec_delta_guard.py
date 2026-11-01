#!/usr/bin/env python3
"""Проверка дельты перед архивацией (skill spec-delta-guard).

`openspec validate <change>` проверяет дельту саму по себе. Он не замечает,
что MODIFIED-блок написан поверх прошлой дельты и потерял условия из
смерженной спеки. Скрипт сравнивает блоки и падает, если условия пропали.

Использование:
    python scripts/spec_delta_guard.py <change-id>
    python scripts/spec_delta_guard.py --root /path/to/repo <change-id>
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REQUIREMENT = re.compile(r"^### Requirement: (?P<key>R-\d+)", re.MULTILINE)
SCENARIO = re.compile(r"^#### Scenario: (?P<title>.+)$", re.MULTILINE)
AND_LINE = re.compile(r"^\s*- \*\*AND\*\*\s*(?P<text>.+)$", re.MULTILINE)
MAX_TEXT = 500


def normalize(value: str) -> str:
    return " ".join(value.split())


def split_requirements(text: str) -> dict[str, str]:
    """Блоки требований по ключу R-NN, без служебных заголовков разделов."""
    matches = list(REQUIREMENT.finditer(text))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.start() : end]
        lines = [line for line in block.splitlines() if not line.startswith("## ")]
        result[match.group("key")] = "\n".join(lines).strip()
    return result


def requirement_text(block: str) -> str:
    """Текст требования до первого сценария."""
    return block.split("#### Scenario:", 1)[0].strip()


def scenario_titles(block: str) -> list[str]:
    return [normalize(match) for match in SCENARIO.findall(block)]


def and_conditions(block: str) -> list[str]:
    """Уникальные AND-условия в порядке появления: дубли — шум в отчёте."""
    seen: dict[str, None] = {}
    for match in AND_LINE.findall(block):
        seen.setdefault(normalize(match), None)
    return list(seen)


def check_delta(change: Path, root: Path) -> list[str]:
    problems: list[str] = []
    for delta_path in sorted(change.glob("specs/*/spec.md")):
        capability = delta_path.parent.name
        delta_text = delta_path.read_text(encoding="utf-8")
        if "## MODIFIED Requirements" not in delta_text:
            continue
        modified = delta_text.split("## MODIFIED Requirements", 1)[1]
        deltas = split_requirements(modified)

        merged_path = root / "openspec" / "specs" / capability / "spec.md"
        if not merged_path.exists():
            problems.append(f"{capability}: нет смерженной спеки {merged_path}")
            continue
        merged = split_requirements(merged_path.read_text(encoding="utf-8"))

        for key, block in deltas.items():
            size = len(requirement_text(block))
            if size > MAX_TEXT:
                problems.append(
                    f"{capability} {key}: текст требования {size} символов "
                    f"(лимит {MAX_TEXT})"
                )
            if key not in merged:
                problems.append(f"{capability} {key}: требования нет в смерженной спеке")
                continue

            old = merged[key]
            lost_titles = [t for t in scenario_titles(old) if t not in scenario_titles(block)]
            for title in lost_titles:
                problems.append(f"{capability} {key}: потерян сценарий «{title}»")

            lost_conditions = [c for c in and_conditions(old) if c not in and_conditions(block)]
            for condition in lost_conditions:
                problems.append(f"{capability} {key}: потеряно AND «{condition}»")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверить дельты перед архивацией")
    parser.add_argument("change", help="id change'а или путь к его папке")
    parser.add_argument("--root", default=".", help="корень репозитория")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    candidate = Path(args.change)
    change = candidate if candidate.is_dir() else root / "openspec" / "changes" / args.change
    if not change.is_dir():
        print(f"change не найден: {change}", file=sys.stderr)
        return 2

    problems = check_delta(change, root)
    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        return 1

    print(f"OK: дельта {change.name} не теряет условия и укладывается в лимит")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
