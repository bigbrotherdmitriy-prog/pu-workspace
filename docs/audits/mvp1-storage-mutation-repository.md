# MVP1 storage mutation repository

DB-backed read-only resolver загружает IDs job payload и связывает их с exact
current project/provider/connection/folder snapshot, approved organizer action,
nested VirtualNode и checksum/modified source revision. Provider calls, commit,
queue и API/UI activation отсутствуют. Любая stale/cross-project/ambiguous связь
закрывается `MutationConflict`.

SQLite regressions подготовлены для exact resolution и stale/cross-project deny.
PostgreSQL concurrency помечен conditional при отсутствии `TEST_POSTGRES_DSN`.
Immutable receipt остаётся в coordinator/wiring contract; его durable DB adapter
и provider execution по-прежнему gate перед API/UI activation.
