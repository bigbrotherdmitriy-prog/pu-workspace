# Документация PU Workspace

Этот индекс различает действующую архитектурную базу, требования новых срезов и
эксплуатационные материалы. Документ с требованиями сам по себе не означает, что
функция реализована или разрешена в production.

## Архитектура и требования

- [Архитектура v5.2 — provider agnostic](architecture-v5.2-provider-agnostic.md)
- [Карта реализации ТЗ v5.1](architecture-v5.1.md)
- [Контракты адаптеров и каталог ошибок](contracts-and-errors.md)
- [Mini-ТЗ «Почтовый клиент / Communication Center»](MAIL_CLIENT_COMMUNICATION_CENTER_MINI_TZ_RU.md)
- [UX-спецификация Communication Center](ux/MAIL_CLIENT_COMMUNICATION_CENTER_UX_RU.md)
- [MVP5 — стабилизация](mvp5-stabilization.md)
- [MVP6 — документный контроль](mvp6-document-control.md)
- [MVP7 — AI Secretary](mvp7-ai-secretary-control.md)

## Приёмка и эксплуатация

- [Итоговая приёмка текущего scope](acceptance-v5.1.md)
- [Текущий аудит MVP](CURRENT_MVP_AUDIT_RU.md)
- [Пилотный runbook](PILOT_RUNBOOK_RU.md)
- [Демонстрационный runbook](DEMO_RUNBOOK_RU.md)
- [Операции](operations.md)
- [Настройка автоматизаций](AUTOMATION_SETUP_RU.md)
- [Пользовательская инструкция](USER_GUIDE_RU.md)

## Коммерческая поставка

- [Коммерческий релиз](COMMERCIAL_RELEASE_RU.md)
- [Комплект продажи](sale/README_RU.md)

Материалы v5.4, существующие только в соседних ветках, не включаются в этот
индекс до интеграции. Mini-ТЗ Communication Center перечисляет такие зависимости
с точными read-only commit SHA, чтобы реализация переиспользовала их без дублей.
