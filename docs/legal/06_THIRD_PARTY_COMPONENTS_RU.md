# Сторонние компоненты и лицензии

Статус на 2026-09-04: **технический реестр и fail-closed packaging contract обновлены; внешняя коммерческая выдача заблокирована до runtime evidence, решений владельца и юриста**.

Этот документ не утверждает лицензию за правообладателя и не подменяет юридическое заключение. Точный машинный состав формируется командой:

```text
python scripts/legal_release_kit.py sbom --ref <FULL_SHA> --out <EMPTY_DIR> --license-evidence docs/release/generated/license-evidence.json
```

## Доказательства текущего кандидата

| Слой | Точный охват | Результат | Остаточный риск |
|---|---:|---|---|
| Backend | 14 прямых требований | 13 SPDX-деклараций подтверждены metadata PyPI; `xlrd==2.0.2` оставлен unresolved, потому что источник говорит только `BSD` | Нет lock-файла транзитивных Python-зависимостей; точный воспроизводимый граф не доказан |
| Frontend | 216 записей секции `packages:` pnpm lock | 216/216 SPDX-деклараций подтверждены metadata npm | `licenseConcluded` требует юридической проверки; package-specific texts ещё не приложены |
| Containers | 3 image declarations и 6 apt declarations | Все 9 перечислены адресно | Images не закреплены digest; apt-версии определяются при build, поэтому точный layer SBOM не доказан |
| Assets | 5 уникальных PWA-файлов и их build-копии | Хеши, первые git-коммиты и происхождение описаны в `docs/release/generated/ASSET_PROVENANCE.md` | Правообладатель должен подтвердить авторство/права на PU-графику |

Авторитетные приложения для этой проверки:

- `docs/release/generated/sbom-backend.spdx.json`;
- `docs/release/generated/sbom-frontend.spdx.json`;
- `docs/release/generated/sbom-containers.spdx.json`;
- `docs/release/generated/license-evidence.json`;
- `docs/release/generated/third-party-license-matrix.json`;
- `docs/release/generated/THIRD_PARTY_NOTICES.md`;
- `docs/release/generated/ASSET_PROVENANCE.md`;
- `docs/audits/v54-wave3-sbom-legal.md`.
- `docs/release/generated/release-packaging-status.json`;
- `docs/release/REPRODUCIBLE_PACKAGING_RU.md`;
- `docs/release/CONTAINER_EVIDENCE_CONTRACT_RU.md`.

Каждый оставшийся `NOASSERTION` имеет адресное объяснение в package `comment` соответствующего SPDX и в `third-party-license-matrix.json`. Значение не заменялось предположением.

## Copyleft-классификация

- В 229 подтверждённых package declarations **не заявлены GPL, AGPL или SSPL**.
- `psycopg==3.2.9` заявляет `LGPL-3.0-only`; это weak-copyleft и требует проверки способа распространения, dynamic linking и исполнения notice/source-offer обязанностей юристом.
- Отсутствие GPL/AGPL/SSPL во всей поставке **не доказано**, пока не получены digest-based container SBOM, точный Python transitive lock и package-specific license texts.
- `poppler-utils` и `antiword` нельзя выпускать на основании названия пакета: версия и фактическая лицензия должны быть сняты с собранного образа.

## Чек-лист выдачи

- [x] **Готово:** исправлен парсер pnpm lock; `snapshots:` и вложенные peer-поля не становятся ложными пакетами.
- [x] **Готово:** зафиксированы 216 frontend-компонентов с точными версиями из lock-файла.
- [x] **Готово:** зафиксированы 14 прямых backend requirements и 9 container manifest declarations.
- [x] **Готово:** у каждой неподтверждённой декларации есть причина и источник/отсутствие источника.
- [x] **Готово:** выполнена отдельная проверка GPL/AGPL/SSPL по подтверждённым declarations.
- [x] **Готово:** проверены repository assets, отсутствие vendored fonts и внешних media-файлов.
- [x] **Готово:** добавлен генератор Python lock, который отклоняет не-Linux report, sdist, yanked и неполные hashes.
- [x] **Готово:** добавлен строгий контракт digest/layer/dpkg/SPDX container evidence.
- [x] **Готово:** package-specific metadata provenance добавлен в `THIRD_PARTY_NOTICES.md`.
- [x] **Готово:** manifest v2 и archive verifier проверяют topology, все file hashes, `.env.example`, secret/PII signatures.
- [ ] **Блокер product source:** заменить client-like пример в `frontend/src/modules/contracts/ContractDocumentPicker.tsx`; упаковщик корректно отказывает текущему кандидату.
- [ ] **Требуется runtime-документ:** создать в Linux x86_64 и закоммитить Python 3.12 transitive lock с hashes; пересобрать backend SPDX по lock.
- [ ] **Требуется runtime-документ:** закрепить container image digest и снять SBOM фактически собранных слоёв с версиями apt packages.
- [ ] **Требуется документ:** приложить package-specific LICENSE/COPYING/NOTICE texts для всего фактического графа.
- [ ] **Требуется решение владельца:** заполнить правообладателя и год в корневом `LICENSE`, выбрать модель лицензирования 02 или 03.
- [ ] **Требуется юрист:** утвердить `licenseConcluded`, LGPL-обязанности, container copyleft и итоговый NOTICE.

До закрытия шести последних пунктов комплект пригоден для внутренней проверки и due diligence, но не должен обозначаться как полностью готовый к коммерческой передаче.
