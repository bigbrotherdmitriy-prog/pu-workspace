# PU Workspace v5.4 Wave 3 — SBOM/LICENSE audit

Дата: 2026-09-04  
Ветка: `codex/v54-wave3-sbom-legal`  
База: `0a0f42ac5cb2b93cff2215c0ef384d11aa5854d2`

## Решение

**Техническая доказательная часть: PASS WITH EXPLICIT GAPS. Коммерческая выдача: BLOCKED.**

Исправлена воспроизводимая ошибка pnpm parser, регенерированы SPDX и создан точный registry-evidence реестр. Неподтверждённые лицензии не заменялись предположениями. Блокировка коммерческой выдачи сохраняется по независимым основаниям: незаполненный правообладатель в `LICENSE`, отсутствие package-specific texts, отсутствие Python transitive lock и digest/layer SBOM контейнеров.

## Что исправлено

До исправления `parse_pnpm_lock` продолжал чтение после top-level `packages:` и принимал вложенные peer-поля/`snapshots:` за пакеты. В текущем lock это создавало семь строк с именем из пробелов/кавычки и неверными purl. Regression fixtures покрывают scoped package, вложенный peer metadata, `snapshots:` и дедупликацию.

После исправления:

- frontend: 216 реальных exact-version packages;
- backend: 14 прямых exact-version requirements;
- containers: 3 image и 6 apt declarations;
- всего в матрице: 239 записей;
- валидное package-declared SPDX evidence: 229;
- unresolved: 10 (`xlrd` с неоднозначным `BSD` и 9 container declarations).

`licenseConcluded` намеренно остаётся `NOASSERTION` у всех компонентов до решения владельца/юриста. Каждый такой случай объяснён в SPDX `comment`; десять отсутствующих `licenseDeclared` имеют отдельную причину в license matrix.

## Evidence и воспроизводимость

`scripts/release/collect_license_evidence.py` получает metadata только для точной версии из PyPI/npm, сохраняет публичный URL и SHA-256 канонического metadata JSON. Произвольный free text, `SEE LICENSE`, generic `BSD` и неизвестный SPDX identifier остаются unresolved. Сборщик не извлекает токены, credentials или package bodies.

Из-за отсутствия Python lock попытка получить новый pip resolution не считается авторитетной и не включена в release artifacts: solver может изменить transitive versions без изменения `requirements.txt`. Для закрытия пробела нужен проверяемый Python 3.12 lock с hashes.

## GPL / AGPL / SSPL

В 229 подтверждённых declarations: GPL 0, AGPL 0, SSPL 0. Выявлен `psycopg==3.2.9` с `LGPL-3.0-only`; требуется юридическая оценка исполнения обязанностей. Глобальное утверждение «риска GPL/AGPL/SSPL нет» запрещено до анализа собранных container layers и полного Python transitive graph.

## Assets

Проверены tracked `frontend/public`, production build assets, CSS `url()`/`@font-face` и imports. Внешние fonts/media отсутствуют; пять уникальных PU PWA icons зафиксированы хешами. История Git не заменяет подтверждение исключительных прав владельцем.

## Изменённые файлы

- `scripts/legal_release_kit.py`;
- `scripts/release/collect_license_evidence.py`;
- `scripts/release/tests/test_legal_release_kit.py`;
- `scripts/release/tests/test_collect_license_evidence.py`;
- три `docs/release/generated/sbom-*.spdx.json`;
- `docs/release/generated/license-evidence.json`;
- `docs/release/generated/third-party-license-matrix.json`;
- `docs/release/generated/THIRD_PARTY_NOTICES.md`;
- `docs/release/generated/ASSET_PROVENANCE.md`;
- `docs/legal/06_THIRD_PARTY_COMPONENTS_RU.md`;
- `NOTICE`;
- этот отчёт.

## Команды проверки

```text
python -m pytest scripts/release/tests -q --basetemp .pytest-sbom-legal
python scripts/release/collect_license_evidence.py --ref HEAD --as-of 2026-09-04 --out docs/release/generated/license-evidence.json
python scripts/legal_release_kit.py sbom --ref HEAD --out docs/release/generated --license-evidence docs/release/generated/license-evidence.json
python -m json.tool docs/release/generated/license-evidence.json
python -m json.tool docs/release/generated/third-party-license-matrix.json
git diff --check
```

## Gate статусы

- **Готово:** pnpm parser и regression tests.
- **Готово:** exact frontend lock inventory и direct backend inventory.
- **Готово:** адресные evidence/risk для всех unresolved declarations.
- **Готово:** assets provenance и declared-license copyleft scan.
- **Требуется документ:** Python transitive lock с hashes.
- **Требуется документ:** digest-pinned container/layer SBOM и apt versions.
- **Требуется документ:** package-specific LICENSE/COPYING/NOTICE bundle.
- **Требуется решение владельца:** правообладатель, год и модель лицензии.
- **Требуется юрист:** утверждение `licenseConcluded`, LGPL/container obligations и финального NOTICE.
- **Блокер:** до пяти последних решений внешний коммерческий релиз не выпускать.
