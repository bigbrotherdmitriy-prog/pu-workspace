# PU Workspace v5.4 — финальный аудит release packaging

Дата: 2026-09-04

Ветка: `codex/v54-release-packaging-final`

База: `8ccc194bc834328e51a73225981f74d81775789a`

## Решение

**PACKAGING TOOLING: PASS. SOURCE ARCHIVE: PASS. LEGAL READINESS: NOT CLAIMED.**

Добавлены воспроизводимый manifest v2, независимый fail-closed archive verifier, контракт Linux Python transitive lock с hashes, контракт container digest/layer/dpkg/SPDX evidence и package-specific metadata provenance. После отдельной обезличивающей правки UI-placeholder архив точного кандидата `bc46e9d...` успешно собран и независимо проверен.

Проверенный архив: `pu-workspace-bc46e9d6512c-commercial-source.tar.gz`.
SHA-256: `b3ffae3e7746f8f07d983b7bfb1de643ef50f2488f3b9a8f58ecbf678a01f159`.
Проверка: 402 файла, single-root regular-files-only, 0 secret/PII/client findings,
status PASS.

## Найденное до изменений

- generated license evidence относится к старому ref `0a0f42a`, хотя dependency inputs между ним и `8ccc194` идентичны;
- backend SPDX перечисляет только 14 прямых requirements;
- Python transitive lock отсутствует;
- три container image и шесть apt packages имеют tag/build-time identity вместо digest/version;
- notices содержал только итоговые числа, без адресного package evidence;
- manifest v1 не фиксировал Git tree и хеш allowlist/exclusion identity;
- builder отмечал client-like source для review, но не блокировал архив;
- отдельной проверки tar topology, manifest completeness и `.env.example` не было.

## Выполнено

1. `build_python_lock.py` принимает только native Linux x86_64 CPython 3.12 pip report, exact wheel URL и SHA-256, проверяет direct pins и создаёт lock/provenance.
2. Локальная Windows cross-resolution из 61 package намеренно отклонена и не включена: marker evaluation не доказывает Linux graph.
3. `container_evidence.py` связывает полный commit с digest-pinned image, image ID, layer digests, точными dpkg versions и SHA-256 SPDX.
4. `legal_release_kit.py` формирует manifest v2 и теперь блокирует secret/PII/client markers, небезопасный `.env.example` и не-шаблонные credential DSN.
5. `verify_release_package.py` независимо проверяет single-root tar, regular-file-only, traversal/duplicates, manifest/hash completeness и secret/PII policy.
6. `THIRD_PARTY_NOTICES.md` расширен до package-specific metadata evidence index без выдумывания license conclusion.
7. Машинный снимок блокеров: `docs/release/generated/release-packaging-status.json`.

## Доказательства

- Dependency inputs `0a0f42a..8ccc194`: идентичны (`git diff --quiet` PASS).
- Candidate tree: `c5b5a3c4fa7898c49ca54474de62b83308925575`.
- Registry matrix: 239 entries; declared evidence 229; unresolved 10.
- Confirmed declarations: GPL 0, AGPL 0, SSPL 0; `psycopg` LGPL-3.0-only требует юриста.
- Первичный bundle был заблокирован `ContractDocumentPicker.tsx`; отдельный commit заменил пример на явно синтетический, после чего bundle и независимый verifier завершились PASS.
- Release script tests: **38 passed**.
- Python compilation: PASS.
- SPDX regeneration against `8ccc194`: 239 total / 229 declared / 10 unresolved.
- JSON parse и `git diff --check`: PASS.

Команды:

```text
python -m pytest scripts/release/tests -q --basetemp .pytest-release-packaging
python -m py_compile scripts/legal_release_kit.py scripts/release/*.py
python scripts/legal_release_kit.py sbom --ref 8ccc194bc834328e51a73225981f74d81775789a --out <TEMP> --license-evidence docs/release/generated/license-evidence.json
python scripts/legal_release_kit.py bundle --ref 8ccc194bc834328e51a73225981f74d81775789a --out <TEMP>
git diff --check
```

Финальные `bundle` и `verify_release_package.py` завершились с exit `0`.

## Остаточные блокеры

- **Linux runtime:** создать accepted Python lock/provenance; перевести build на него; проверить `pip --require-hashes`.
- **Container runtime:** собрать image, закрепить RepoDigest, снять layers, dpkg inventory и SPDX.
- **License bundle:** собрать upstream LICENSE/COPYING/NOTICE texts именно из проверенных artifacts и сверить их hashes.
- **Owner:** заполнить правообладателя, год, модель лицензии и provenance собственных assets.
- **Counsel:** установить `licenseConcluded`, проверить LGPL/container obligations и утвердить NOTICE/договоры.

Ни один из этих пунктов не считается закрытым скриптами. Архив и SHA-256 в акт передачи не вносились.

## Изменённые области

Только `scripts/release/**`, `scripts/legal_release_kit.py`, `docs/release/**`, `docs/legal/05_*`, `docs/legal/06_*`, `docs/audits/**`, `NOTICE`. Product Core, миграции, workflows, production и secrets не изменялись.
