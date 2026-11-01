#!/usr/bin/env bash
# Готовность change'а: тесты, линтер, спеки и архив.
# Один вход вместо четырёх команд, которые легко забыть по отдельности.
set -euo pipefail

cd "$(dirname "$0")/.."

python -m pytest -q
ruff check .
# Открытые change'и: дельта не должна терять условия смерженной спеки.
for change in openspec/changes/*/; do
  [ -d "$change" ] || continue
  python scripts/spec_delta_guard.py "$change"
done
openspec validate --all --strict
openspec validate --archived

echo "OK: тесты, ruff, спеки и архив"
