# Юридическое и реестровое досье PU Workspace

Статус: проекты документов для заполнения правообладателем и проверки российским юристом по интеллектуальной собственности, ИТ и персональным данным. Материалы не подтверждают включение PU Workspace в Реестр российского ПО или российское происхождение сторонних компонентов.

## Юридический комплект

1. [01_RIGHTS_CONFIRMATION_RU.md](01_RIGHTS_CONFIRMATION_RU.md) — происхождение исключительных прав.
2. [02_NONEXCLUSIVE_LICENSE_AGREEMENT_RU.md](02_NONEXCLUSIVE_LICENSE_AGREEMENT_RU.md) — проект неисключительной лицензии.
3. [03_EXCLUSIVE_LICENSE_OPTION_RU.md](03_EXCLUSIVE_LICENSE_OPTION_RU.md) — альтернативные сценарии прав.
4. [04_DELIVERY_SPECIFICATION_RU.md](04_DELIVERY_SPECIFICATION_RU.md) — состав поставки.
5. [05_TRANSFER_ACCEPTANCE_ACT_RU.md](05_TRANSFER_ACCEPTANCE_ACT_RU.md) — акт передачи.
6. [06_THIRD_PARTY_COMPONENTS_RU.md](06_THIRD_PARTY_COMPONENTS_RU.md) — зависимости и лицензии.
7. [07_AI_AND_CLIENT_DATA_POLICY_RU.md](07_AI_AND_CLIENT_DATA_POLICY_RU.md) — AI и данные.
8. [08_INSTALLATION_RECOVERY_RU.md](08_INSTALLATION_RECOVERY_RU.md) — установка и восстановление.
9. [09_RUSSIAN_SOFTWARE_REGISTER_READINESS_RU.md](09_RUSSIAN_SOFTWARE_REGISTER_READINESS_RU.md) — checklist Реестра.

## Дополнение для Реестра

- [registry/REQUIREMENTS_MATRIX_RU.md](registry/REQUIREMENTS_MATRIX_RU.md) — требования и предполагаемая классификация;
- [registry/FUNCTIONAL_AND_TECHNICAL_DESCRIPTION_RU.md](registry/FUNCTIONAL_AND_TECHNICAL_DESCRIPTION_RU.md) — функции и размещение;
- [registry/PUBLIC_INSTALLATION_AND_OPERATION_RU.md](registry/PUBLIC_INSTALLATION_AND_OPERATION_RU.md) — публичная инструкция;
- [registry/ARCHITECTURE_RU.md](registry/ARCHITECTURE_RU.md) — схема;
- [registry/COMPONENTS_AND_AI_MATRIX_RU.md](registry/COMPONENTS_AND_AI_MATRIX_RU.md) — SBOM/AI-матрица;
- [registry/EXPERT_ACCESS_PROCEDURE_RU.md](registry/EXPERT_ACCESS_PROCEDURE_RU.md) — экспертный доступ;
- [registry/OWNER_INPUT_FORM_RU.md](registry/OWNER_INPUT_FORM_RU.md) — решения владельца;
- [registry/PUBLIC_MATERIALS_INDEX_RU.md](registry/PUBLIC_MATERIALS_INDEX_RU.md) — публикации.

Реестровые материалы ссылаются на юридические документы и не создают их копии. Все отсутствующие сведения консолидируются также в `../release/00_OWNER_INPUT_REGISTER_RU.md`.

## Release gate

До сделки подтвердить цепочку прав, выбрать одну модель сделки, проверить SBOM и лицензии, исключить секреты/production-данные, согласовать обработку данных и пройти юридическую проверку. Повторяемый комплект создаётся командой `python scripts/legal_release_kit.py all --ref <FULL_SHA> --out <EMPTY_DIR>` только после чистого commit.
