# MVP1 storage mutation wiring

Дата: 2026-09-05

База: `cc81e02dc44fa13c2976ec40926dc376db62e060`

## Результат

Существующий durable worker получил отдельный kind `workspace.storage_mutation`.
Job payload имеет строгий allowlist и содержит только project/proposal/action IDs,
command key, CAS version и `apply|rollback`. Connection/folder/source revision,
provider locator, путь, имя, содержимое и token через очередь запрещены.

Worker вызывает установленный runtime, который должен внутри одной server-side
операции загрузить exact binding/source pins и вызвать `storage_mutations`
coordinator. При отсутствии runtime, неизвестном/частичном результате или лишнем
поле обработка закрывается fail-closed. Безопасный result также ограничен receipt
ID, outcome и resulting version.

## Проверки

- synthetic handler/IDs-only/denylist/missing-runtime/UNKNOWN: 8 сценариев подготовлены;
- runtime invocation не выполнен из-за отсутствующего локального native libpq;
- Python compile и `git diff --check`: PASS.

## Оставшийся live gate

Статус `CONDITIONAL`. API enqueue и UI намеренно не активированы, пока нет durable
DB runtime/repository, который атомарно разрешает IDs в exact project/provider/
connection/folder/source revision и сохраняет immutable receipt. Также нужны
PostgreSQL lease/crash tests и synthetic browser acceptance. Real Google/Яндекс,
OAuth, production и секреты не использовались.
