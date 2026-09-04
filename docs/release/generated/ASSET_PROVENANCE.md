# Asset provenance — PU Workspace v5.4 Wave 3

Дата проверки: 2026-09-04. Проверенный source ref: `0a0f42ac5cb2b93cff2215c0ef384d11aa5854d2`.

## Собственные PWA assets

| Уникальный asset | SHA-256 | Первое появление в Git | Доказательство | Статус прав |
|---|---|---|---|---|
| `frontend/public/pu-icon.svg` | `6913a5ae760f21012df9a461459c12b2283435b8286ac871f42723895f58ee97` | `a65a7bcc63bfc55a8b900795eb06c2f9c0c2b748` | Простой SVG `PU`, добавлен commit author `Codex` | Требуется письменное подтверждение правообладателя |
| `frontend/public/pu-icon-maskable.svg` | `ae3de03125660ed599f2b52f3649a5c91e7656a27e1146ef14f7adf8b9a19417` | `f80f3ac409ea4f3b8ce8e0657c99bb3bb44369a1` | Repository history | Требуется письменное подтверждение правообладателя |
| `frontend/public/pu-icon-192.png` | `0ea9c8c460846cdf9dd3428b8156769158c3593d1890bb5848115c6366632ead` | `82954192821956927f8c6f3c6d8a5dc6ab2d4ba1` | Repository history; raster PWA icon | Требуется письменное подтверждение правообладателя |
| `frontend/public/pu-icon-512.png` | `3ffb6d470c39a6f24465aef9c203442d8e05177320aeab9082d85d9613e2080f` | `82954192821956927f8c6f3c6d8a5dc6ab2d4ba1` | Repository history; raster PWA icon | Требуется письменное подтверждение правообладателя |
| `frontend/public/pu-icon-maskable-512.png` | `614ccdd85c75cae4fe693df70bad1c6b35faa9f6b90f40090af94cf9f8a3f4d3` | `f80f3ac409ea4f3b8ce8e0657c99bb3bb44369a1` | Repository history; raster PWA icon | Требуется письменное подтверждение правообладателя |

Файлы с теми же именами в `backend/app/react_dist/` имеют те же SHA-256 и являются build-копиями, а не отдельными произведениями. `manifest.webmanifest` — конфигурационный JSON, одинаковый в source и build.

## Сторонние assets

- Vendored font-файлы и `@font-face` не обнаружены; CSS использует системные семейства.
- Отдельные фотографии, видео, аудио и демоматериалы в tracked frontend assets не обнаружены.
- Иконки интерфейса поставляются кодом пакета `lucide-react@0.453.0`; лицензия и exact-version evidence находятся в license matrix.
- Google Drive URL в UI является ссылкой на внешний сервис, не включённым asset.

Git history подтверждает происхождение файлов внутри репозитория, но сама по себе не доказывает исключительные права. Закрывающий документ — подписанное подтверждение правообладателя в досье `docs/legal/01_RIGHTS_CONFIRMATION_RU.md`.
