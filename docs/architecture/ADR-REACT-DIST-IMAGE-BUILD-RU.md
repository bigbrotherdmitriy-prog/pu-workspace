# ADR Сборка фронтенда внутри релизного образа

Статус: утверждён владельцем 01.10.2026; первый этап реализуется отдельным PR. Merge и production-выкатка требуют отдельного решения.

Принцип уже утверждён владельцем: собранный фронтенд больше не хранится в git, а создаётся при сборке образа. Этот ADR фиксирует утверждённый порядок перехода, проверок и отката. На первом этапе `react_dist` остаётся tracked и неизменным; удаление — отдельный второй этап только после принятой реальной выкатки первого.

## Причина и проверенные исходные факты

Репозиторий main и production `/api/status` на момент проверки указывают на `c661b9005d6d5f1321c35ca4e66acaad02fcb53d`. Работающий образ имеет ID `sha256:87f747ab9c5bc05b5af04b79080854e5706a918070f280de5d9be51fe630f7b8`. Источники кода далее привязаны к этому SHA.

На предыдущем production-релизе `1fc3c345` было настоящее расхождение на одном SHA: production отдавал `index-z9QkdH6b.js` и `index-DFp58kBr.css`, а git ссылался на `index-B0Ztm8I-.js` и `index-CktYYqdn.css`. Причина не установлена. Это фиксируется как причина отказа от двух источников собранной статики.

К проверке 01.10.2026 production уже сменил релиз с `1fc3c345` на `c661b9005d6d5f1321c35ca4e66acaad02fcb53d`. На новом релизе публичный HTTPS и образ ссылались на пару B0Ztm8I-/CktYYqdn. **«Не воспроизводится после смены релиза» не доказывает, что прежней проблемы не было**, и не устанавливает её причину. Кеш браузера или иной артефакт не объявляются установленной причиной.

| Релиз / источник | JavaScript | CSS | Вывод |
|---|---|---|---|
| Прежний production `1fc3c345` | `index-z9QkdH6b.js` | `index-DFp58kBr.css` | Настоящее прежнее расхождение; причина неизвестна |
| Git того же прежнего SHA | `index-B0Ztm8I-.js` | `index-CktYYqdn.css` | Не совпадал с прежним production |
| Новый production и git `c661b900…` | `index-B0Ztm8I-.js` | `index-CktYYqdn.css` | Совпадение после смены релиза не опровергает прежний дефект |

Независимо от совпадения текущих имён архитектурная неоднозначность присутствует: production Dockerfile копирует готовый `backend/app` из build context, а CI Dockerfile сам собирает фронтенд. Одного Git SHA и имени Vite-бандла недостаточно для идентификации фактических байтов поставленного образа.

## Что сломается при простом удалении каталога

| Контур | Текущее поведение | Последствие удаления без перехода | Необходимое изменение после утверждения |
|---|---|---|---|
| Production image | [backend/Dockerfile:9](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/backend/Dockerfile#L9) копирует `app`; Node-сборки в нём нет | В образе нет React UI. [main.py:239](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/backend/app/main.py#L239) монтирует `/new` только при наличии каталога, поэтому API может быть готов, а `/new/` отсутствовать | Добавить frontend build stage, всегда копировать его результат в `/app/app/react_dist` после backend source |
| Build context и запуск | Runbook собирает образ из [каталога backend](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/docs/PRIMARY_FIRST_HOST_DEPLOYMENT.md#L67); локальный [Compose](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/docker-compose.yml#L18) использует тот же context | Соседний `frontend` недоступен такому Docker build | Использовать корень точного release как context и выбранный Dockerfile явно. Запуск deploy остаётся из release, не из `/root` |
| Cutover и SHA-256 | Использовавшаяся обёртка [pr107-production-deploy.sh:57](<C:/Users/dpush/OneDrive/Документы/ChatGPT/Workspace/pr107-production-deploy.sh:57>) сравнивает HTTPS со статикой на диске release; primary deploy принимает уже существующий образ | В source archive статических файлов больше нет; проверка либо падает, либо может быть ошибочно отключена | Эталон брать из закреплённого образа и его manifest; отсутствие manifest/статических файлов блокирует cutover |
| Docker smoke | [Dockerfile.ci:1](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/Dockerfile.ci#L1) уже собирает frontend и [исключает react_dist из context](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/Dockerfile.ci.dockerignore#L18) | Сам CI frontend не обязан сломаться, но отдельные production и CI recipes сохраняют риск различий | Smoke должен проверять production recipe и сформированный frontend manifest, а не наличие файлов в git |
| Local development | [Vite outDir](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/frontend/vite.config.mjs#L8) пишет в `backend/app/react_dist`; README предлагает Compose и `pnpm run build` | После чистого clone Python backend без локальной сборки не обслуживает React UI | Описать два режима: локальный backend плюс отдельно выполненная frontend-сборка; либо полный image build. Добавить generated output в `.gitignore` |
| Incremental hotfix images | [Dockerfile.incremental:4](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/backend/Dockerfile.incremental#L4) и organizer hotfix наследуют `app-backend:latest` | Можно незаметно унаследовать frontend другой ревизии | Исключить эти recipes из стандартного релиза; их отдельное использование требует закреплённого base digest и явного происхождения frontend |

Архив исходников и его SHA-256 остаются обязательными. Архив перестаёт быть эталоном готового frontend, но остаётся доказательством исходников конкретного SHA. Бэкап БД, проверка восстановления, runtime environment, четыре компонента на одном SHA, readiness и `GMAIL_AUTO_SYNC_ENABLED=true` не отменяются. Изменение упаковки не требует новой миграции БД.

## Предлагаемая сборка и идентификация артефакта

Предлагается единый production build recipe с frontend stage. CI smoke использует этот же recipe; при необходимости тестовые файлы добавляются отдельным CI target, без повторной frontend-сборки другим рецептом. Не заменять production runtime целиком текущим `Dockerfile.ci`: нужно сохранить Java/MPXJ, OCR и остальные системные зависимости из production Dockerfile.

Frontend собирается из `frontend` того же полного Git SHA. Версии Node, pnpm и base images фиксируются точными версиями и digest; install использует frozen lockfile и учитывает `pnpm-workspace.yaml`. Переменные сборки фиксируются и содержат только разрешённую публичную конфигурацию. Секреты, ключи, `.env`, локальные build outputs и `node_modules` не попадают в context. Production build обязан работать из чистого source archive без ранее собранного `react_dist`.

На первом переходе runtime путь `/app/app/react_dist` и Vite `base=/new/` не меняются. Это уменьшает число одновременно изменяемых контрактов. Локальная QA-сборка в прежний outDir допустима, но не является релизным артефактом. На первом этапе не коммитить её generated изменения: tracked output пока сохранён. Ignore и удаление tracked output относятся только ко второму этапу.

Build формирует внутренний manifest вне публичного дерева, например `/app/frontend-build-manifest.json`: версия формата, source SHA, frontend source revision, hash lockfile, toolchain/build parameters и отсортированный список относительных путей, размеров и SHA-256 всех файлов React output. В список входят `index.html`, JS/CSS, lazy chunks, видео, иконки, webmanifest и service worker. Абсолютные пути, `..`, дубли и symlinks запрещены.

Image digest нельзя записывать в manifest внутри самого образа как собственный digest: это циклическая зависимость. Отдельная release receipt вне образа связывает source SHA, hash source archive, точный image ID или registry digest, hash внутреннего manifest и результаты CI/smoke. Tag `app-backend:<SHA>` не считается достаточным закреплением. Повторная сборка того же SHA с другими байтами не заменяет уже принятую receipt и сохранённый образ; для другого артефакта требуется отдельное утверждение и новый идентификатор релиза, совместимый с deploy-контрактом.

## SHA-256 по источнику из образа

1. До cutover извлечь manifest и React output из exact candidate image без запуска штатного entrypoint, миграций и соединения с production БД. Сверить все файлы с внутренним manifest; пустой список или несоответствие блокируют выкатку. Проверить, что HTML ссылается на существующие файлы этого же списка.
2. Сохранить release receipt, извлечённый manifest и image artifact для отката. В deployment не подменять каталог frontend локальной сборкой или bind mount.
3. После cutover подтвердить image identity и release SHA backend, двух worker и scheduler, а также `/api/status` и `readiness ready:true` со всеми required-проверками.
4. Для каждого файла manifest запросить его публичный HTTPS URL под `/new/`. `index.html` сопоставить с фактически обслуживаемым `/new/`. Требовать HTTP 200, допустимый origin без перехода на login/иной хост, проверку TLS, `Accept-Encoding: identity`, отсутствие неожиданного content encoding и подходящий content type. SHA-256 считать по полученным байтам. HEAD, ETag и совпадение имени файла не заменяют проверку содержимого.
5. Проверить полноту: количество совпало с manifest; проверены не только главный JS/CSS, но и все lazy/public assets. Manifest не включать в собственный список hashes. На public root не публиковать приватные build receipts.
6. Проверить обновление service worker обычным браузерным проходом. Это отдельная проверка пользовательского кеша, не замена серверной HTTPS-сверке. Для HTML/service worker согласовать политику revalidation, для content-hashed assets — immutable caching. Общая проверка кода кеша не доказывает, что именно кеш вызвал прежнее наблюдение владельца.

Любое несоответствие manifest, HTTP или readiness — прекращение выкатки и возврат предыдущего полного релиза. Не отключать checks и не пересобирать frontend поверх активного release, чтобы «свести» hashes.

## Совместимость со старым релизом

Откат использует предыдущий release directory, его private runtime environment и сохранённый точный image, не новый Dockerfile из main. Симлинк сам по себе недостаточен: все четыре приложения должны быть восстановлены на прежний SHA и прежний image identity. Standard schema compatibility guard сохраняется.

Старый `react_dist`, находившийся в git, не мешает откату: он уже находится в старом образе. Для старого образа без внутреннего manifest compatibility verifier извлекает весь `/app/app/react_dist` именно из этого immutable image и формирует внешнюю legacy receipt. Git-каталог и source archive могут быть дополнительными свидетельствами, но не заменяют фактически поставленные байты. Затем проверяются public HTTPS hashes и readiness предыдущего релиза.

Если предыдущий образ утрачен, его восстановление из сохранённого image artifact допустимо после проверки digest. Пересборка старого commit не объявляется тем же артефактом: её байты могут отличаться, даже если SHA исходников прежний. Такой релиз сначала проходит полный отдельный build/smoke и утверждение. Нельзя смешивать старый backend с новым frontend или игнорировать schema compatibility. Этот ADR не разрешает автоматический downgrade БД.

Сохранять как минимум текущий и два предыдущих успешно проверенных полных комплекта image/receipt/manifest/source archive/runtime references. Секреты остаются в существующем защищённом runtime каталоге. Очистка старых комплектов — отдельная разрешённая операция, не часть перехода.

## Порядок перехода в два PR

ADR утверждён до кода. Переход выполняется в два implementation PR с отдельными CI и решениями о merge/deploy.

**PR 1 Совместимая упаковка.** Добавить source-only frontend stage, безопасный root build context, согласовать production/local/CI recipes, manifest/receipt, image-derived HTTPS verifier и legacy verifier. Обновить runbooks и smoke/tests. `react_dist` пока остаётся tracked, но исключён из build context и не используется как frontend source. Развернуть только после отдельного разрешения; убедиться в рабочих `/new/`, всех required gates, static SHA-256 и проверяемом откате на сохранённый прежний image. Не менять схему БД.

**PR 2 Удаление generated output из git.** После принятия PR 1 удалить tracked `backend/app/react_dist`, добавить ignore для этого output и других согласованных build outputs, убрать ожидания tracked bundle из package/CI checks и документации. Доказать сборку из чистого checkout/archive без локальных ignored файлов, зелёный production-image smoke и точность public verification. Не удалять старые release directories или образы. История git и возможность чтения старых commits сохраняются; историю репозитория не переписывать.

| Момент остановки | Действие |
|---|---|
| До PR 1 или при его незелёном CI | Production остаётся прежним, tracked output пока есть |
| PR 1 принят, но candidate ещё не активирован | Остановить подготовку; текущие release/image не затронуты |
| PR 1 активирован и gates не прошли | Вернуть предыдущий комплект release/image/runtime; legacy verifier проверяет его статику |
| Между PR 1 и PR 2 | PR 2 можно отложить; source-image путь уже работает, tracked output не используется |
| PR 2 принят или активирован и нужна отмена | Вернуться на PR 1 либо более старый сохранённый комплект. Удаление output из текущего git не меняет старые images |
| Нет exact предыдущего image либо schema несовместима | Не выполнять неподтверждённый rollback; остановиться и сообщить владельцу |

Один PR технически возможен, но одновременно удаляет прежний источник и меняет build/deploy/verifier/local contracts. Два PR предпочтительнее: совместимость и rollback проверяются до удаления tracked output. Если PR 2 долго отложен, generated каталог не должен снова стать источником frontend или основанием для HTTPS hashes.

## Приёмка

- Чистый source archive и локальная машина без `react_dist` создают полный image; недостающая Node-сборка приводит к build failure, не к API-only «успеху».
- CI проверяет TypeScript, frontend tests/build, production image runtime и `/new/`; production Java/MPXJ/OCR и существующие backend tests сохранены.
- Старый tracked output намеренно исключён при тесте PR 1; после PR 2 в git нет generated output, но в образе `/app/app/react_dist` есть.
- Проверка выявляет повреждённый CSS, lazy chunk, index, отсутствующий manifest и чужой image; cutover не продолжается после отказа.
- Проверка отката охватывает и новый manifest image, и старый image без manifest; возвращены четыре компонента, public UI, SHA-256 и readiness.
- Release report всегда содержит source SHA, image identity, frontend revision/manifest hash, активный release, public verification и rollback target. Не утверждает равенство готовых файлов git и image, когда git их больше не хранит.

## Ссылки на действующие контракты

[Production Dockerfile](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/backend/Dockerfile), [Docker smoke workflow](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/.github/workflows/docker-smoke.yml), [CI build recipe](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/Dockerfile.ci), [primary deploy](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/scripts/deploy-primary-first-host.sh), [public smoke из image index](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/scripts/check_public_smoke.py#L191), [legacy deploy](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/scripts/deploy-production.sh), [service worker](https://github.com/bigbrotherdmitriy-prog/pu-workspace/blob/c661b9005d6d5f1321c35ca4e66acaad02fcb53d/frontend/public/service-worker.js).
