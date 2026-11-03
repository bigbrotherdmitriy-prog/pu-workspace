# NEXT — продолжение MASTER-PLAN

## Сделано

- P0 snapshot:437исходныхuntracked+2modified+SOURCE-MANIFEST,440files;allSHA/readbackverified. Manifest `docs/operations/P0-WORKTREE-SNAPSHOT-MANIFEST-2026-11-03.json`. RootHEAD/modifiedbytesunchanged.

- Предыдущие проверки и последний1291filecandidate описаны в HANDOFF.md.
- Состояние Git и control прочитано; mode running, пользовательская пауза не переопределялась.
- Единственный доступный агент: /root. Захвачен живой кооперативный kernel-lock `.pu-autonomy/work-owner.lock`; владелец `.pu-autonomy/work-owner.json`, control.work_owner. PID в record локален namespace, не использовать его для kill.

## В работе

P0 из раздела6 HANDOFF: исходное дерево сохранено; собрать отдельную review Git-ветку из полного immutable upstream дерева и проверенного source capsule. Root checkout не сбрасывать/не чистить. Секреты/.env не читать.

## Следующий шаг

1. Snapshot и evidence завершены; не перезаписывать архив.
2. Создать отдельный writable Git checkout в /tmp на базе86a4, сохранить происхождение и hashes1291files. Никаких push/merge/deploy.
3. Проверить интегрированную ветку; затем P0 migration/recovery/CI gates, не переписывая историю.

## Владение очередью и восстановление

- Перед изменениями читать control.mode и owner record, проверить live lock. Если он удерживается другим исполнителем, не выполнять параллельную работу.
- Временный keeper только держит lock, не запускает проектные задачи и не является системой/runner/service. Lease30минут; renew/release через `.pu-autonomy/work-owner-command.json` с точным token текущего owner. Не подменять user pause и не красть живой lease.
- При обрыве проверять record+lock, не доверять одному stale PID. После освобождения lease новый исполнитель может захватить его; snapshot/manifest/evidence — источник восстановления.
- Запрещены production/живаяDB/deploy/approval/секреты/.env/службы/боты/обход отказанных действий.

## Вопросы владельцу продукта — не блокируют независимые задачи

- Нужны реальная смета и принимаемые заказчиком КС/формыИД.
- Правило округления qty2/total2.01 без цены: сохранять исходный total или округлять unit price и пересчитывать?
- Отрицательный физический остаток допускается? Что делать с расходом при удалении связанного журнала?
- Какая конфигурация/формат1С, тестовый обмен Диадока и три приоритетные формыИД?
- Как доступна проверка реального телефона/камеры/PWA? Секреты в чат не нужны.
