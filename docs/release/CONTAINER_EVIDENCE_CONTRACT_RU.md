# Контракт доказательств контейнерной поставки

`scripts/release/container_evidence.py` принимает только сохранённые результаты безопасных read-only команд и формирует `pu-workspace-container-evidence/v1`.

## Обязательные поля

- полный release commit;
- image reference вида `repository@sha256:<64 hex>`;
- Docker image ID;
- полный непустой список layer digest;
- точные `name`, `version`, `architecture` всех `dpkg` packages;
- SHA-256 отдельного SPDX 2.3 layer SBOM;
- количество пакетов без лицензионного вывода;
- адресные SPDX ID найденных strong/weak copyleft declarations.

Tag (`latest`, `16-alpine`) доказательством не является. `resolved-at-build`, пустая версия, дубли пакетов и отсутствующий SBOM отклоняются.

## Что намеренно не сохраняется

Environment, secret, label, history command, filesystem content, DSN, документы и production identifiers. Исходный `docker image inspect` остаётся временным локальным файлом и не входит в handover.

## Граница юридического вывода

Технический PASS означает только связь release commit → image digest → layers → package inventory → SBOM digest. `NOASSERTION`, `NONE`, LGPL/GPL/AGPL/SSPL и иные обязанности рассматривает юрист. Скрипт не заменяет и не выдумывает `licenseConcluded`.
