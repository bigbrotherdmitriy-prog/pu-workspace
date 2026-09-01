# Сторонние компоненты и лицензии

Статус: первичный реестр по manifest-файлам. SPDX генерируется командой `python scripts/legal_release_kit.py sbom --ref <SHA> --out <DIR>`. Поля `NOASSERTION`, транзитивные зависимости, container digests и обязательные лицензионные тексты являются release gate, а не молчаливым допущением.

## Backend

| Компонент | Версия релиза | Типовая лицензия — проверить | Назначение |
|---|---:|---|---|
| FastAPI | 0.116.1 | MIT | API |
| Uvicorn | 0.35.0 | BSD-3-Clause | ASGI server |
| SQLAlchemy | 2.0.43 | MIT | ORM |
| psycopg | 3.2.9 | LGPL-3.0-or-later | PostgreSQL driver |
| google-api-python-client | 2.179.0 | Apache-2.0 | Google adapters |
| google-auth / oauthlib | 2.40.3 / 1.2.2 | Apache-2.0 | OAuth |
| Alembic | 1.16.5 | MIT | migrations |
| cryptography | 45.0.6 | Apache-2.0 OR BSD-3-Clause | encryption |
| httpx | 0.28.1 | BSD-3-Clause | HTTP client |
| pypdf | 6.0.0 | BSD-3-Clause | PDF text |
| xlrd | 2.0.2 | BSD-3-Clause | spreadsheets |

## Frontend

| Компонент | Диапазон версии | Типовая лицензия — проверить | Назначение |
|---|---:|---|---|
| React / React DOM | ^19.2.1 | MIT | UI |
| lucide-react | ^0.453.0 | ISC | icons |
| Vite / plugin-react | ^7.1.7 / ^5.0.4 | MIT | build |
| TypeScript | ^5.9.3 | Apache-2.0 | compiler |
| Vitest | ^3.2.4 | MIT | tests |

## Инфраструктура и внешние сервисы

PostgreSQL 16 (PostgreSQL License), Docker/Compose — устанавливаются покупателем. Google Workspace, Telegram и AI-провайдеры не входят в лицензию PU Workspace; использование регулируется их условиями и аккаунтами покупателя.

## Release gate

- [ ] `pip-licenses`/CycloneDX сформировали backend SBOM.
- [ ] package lock присутствует и frontend SBOM сформирован.
- [ ] Отсутствуют GPL/AGPL/SSPL-компоненты без отдельного решения.
- [ ] NOTICE и обязательные тексты лицензий включены в поставку.
- [ ] Проверены шрифты, изображения, иконки и демоматериалы.
- [ ] Зафиксированы версии контейнерных образов и их лицензии.

Технический аудит не обнаружил vendored font-файлов или `@font-face`; интерфейс ссылается на системные шрифты, а иконки — на `lucide-react`. Собственные PU-логотипы/изображения требуют подтверждения автора и прав в досье 01. Root `NOTICE` предупреждает о сторонних правах, но не заменяет полные тексты лицензий. До закрытия всех `NOASSERTION` поставка может использоваться для внутреннего пилота, но продажа релиза блокируется.
