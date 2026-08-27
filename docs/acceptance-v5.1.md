# PU Workspace — итоговая приёмка текущего Implementation Scope

## Реализовано

- проекты, организация, роли, договоры и project context;
- Google Drive OAuth, выбор папок, metadata snapshot и virtual tree;
- PDF/DOCX/XLSX/TXT/MD extraction, версии и источник;
- классификация, дубли/версии, preview, proposal, dry-run, подтверждение;
- source recheck, `conflict_source_changed`, идемпотентные операции и rollback;
- AI Secretary для web, Telegram и Gmail;
- редактируемые ответы, подтверждаемые Tasks/Calendar/Gmail действия;
- обязательства, задачи, сроки, риски, решения, совещания и уведомления;
- ГПР, бюджет, ДДС, закупки, поставки, акты и прогноз;
- AI/DLP policy, provenance, аудит, очереди, retry и dead-letter;
- серверная пагинация/фильтрация документов и нагрузочный smoke на 10 000 объектов.

## Приёмочные проверки

- [x] `main` напрямую не изменяется; релизы собираются из рабочей ветки.
- [x] Перед production deploy создаётся проверяемый PostgreSQL backup.
- [x] Readiness проверяет секреты, БД, schema, OAuth и Telegram.
- [x] Весь server test suite проходит до переключения release.
- [x] Telegram `getMe` успешен; Gemini формирует три варианта ответа.
- [x] Оригиналы не меняются до подтверждения.
- [x] Внешнее действие не дублируется и имеет обратную связь с источником.
- [x] Ошибочная массовая операция имеет ограниченный retry и dead-letter.
- [ ] Пользователь повторно подтверждает Google OAuth после добавления Gmail scopes.

## За границами текущего Implementation Scope

Расширенный production OCR/vision, полноценная PWA/нативная мобильная версия, knowledge center, Shared Drives enterprise-hardening и внешние источники относятся к разделу «Расширение». Они не являются блокером завершения зафиксированного Implementation Scope, но должны разрабатываться отдельными вертикальными срезами.
