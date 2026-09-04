# Синтетические fixtures

Fixture: scripts/audits/tests/fixtures/mailbox_cutover.json.
Он не является production dump и намеренно может описывать состояние после
expand, которое старый global unique ещё не допускает. Все ID вымышлены;
адресов, текстов писем, тем, credentials и tokens нет.

cutover_cases.json — отдельный reconciliation oracle из шести сценариев, не
вход inventory tool. Он добавляет synthetic RFC Message-ID только с example.test
и проверяет сохранение origin/запрещённые основания. Передача его полей как
готового решения reconcile запрещена.

| Сценарий | Строки | Ожидание |
|---|---|---|
| Один Gmail ID в двух ящиках | m-001 mail-a, m-002 mail-b | две identity; legacy collision, не mailbox collision этой пары |
| Unknown старый mailbox | m-003 | origin unknown; не backfill |
| Один контакт в двух проектах | два synthetic contact link | один identity key и две project relations; DB tool без PII key отмечает это неизмеримым |
| RFC/thread collision | m-001/m-002 и unknown m-003 | cross-mailbox/unknown groups; не merge |
| Письмо перенесено | m-004: project-a→project-b relation revisions | move доказан; current mail-a виден, сохранность origin исторически ещё не доказана |
| Ambiguous mailbox | m-005 | unresolved; OAuth/current project не используется |
| Direction collision | m-001 incoming, m-006 outgoing в mail-a | mailbox collision; direction не identity |
| Связанные объекты | Task/Draft m-004, completion m-006 | Task совпал с новым project; Draft mismatch только выявляется |
| Approver gap | m-003/m-006 | recorded v5.4 approver отсутствует; trusted-human proof отсутствует у всех confirmed |

m-001/m-004 имеют recorded v5.4 approver. Это проверяет recorded против
trustworthy proof, а не объявляет fixture настоящим human approval.

Fixture/tooling PASS:

- повтор с одним HMAC key даёт byte-identical canonical JSON;
- raw Gmail/thread/mail/project/contact/actor/task/draft/source IDs не выводятся;
- aggregates совпадают с unit assertions;
- partial origin увеличивает unresolved, не выбирает mailbox;
- unsafe path/content field отвергаются;
- production-like URL отказан без exact gate;
- SQL allowlist не читает content/names/senders/URLs/attachments/audit details/
  contact email/credentials/tokens.

Fixture PASS не доказывает ORM migration, production reconciliation,
PostgreSQL CAS, provider account subject или работу Gmail.
