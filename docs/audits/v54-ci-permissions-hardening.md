# PU Workspace v5.4 — CI permissions hardening

Дата: 2026-09-04

Ветка: `codex/v54-ci-permissions-hardening`

База: `b9a698905a7e0cfb8b4442b931f14aa0985b9127`

## Результат

P1-01 из `docs/audits/v54-wave3-release-audit.md` закрыт локально:

- общий `.github/workflows/ci.yml` имеет только `permissions: contents: read`;
- его `actions/checkout@v4` использует `persist-credentials: false`;
- все шесть checkout-шагов во всех tracked workflow проверяются одним
  contract-тестом и не сохраняют repository credential;
- product code, SBOM, release bundle и production не изменялись.

## Regression-first

До изменения workflow новый тест воспроизвёл оба нарушения:

```text
scripts/ci/test_ci_permissions.py
2 failed
- KeyError: permissions
- ci.yml: persist-credentials is not false
```

После минимального изменения оба сценария проходят.

## Изменённые файлы

- `.github/workflows/ci.yml` — явный read-only token и checkout без persisted
  credentials;
- `scripts/ci/test_ci_permissions.py` — regression-контракт для общего CI и
  всех checkout-шагов;
- `docs/audits/v54-ci-permissions-hardening.md` — этот отчёт.

## Проверки

```text
python -m pytest \
  scripts/ci/test_ci_permissions.py \
  scripts/ci/test_v54_wave3_ci_gate.py \
  scripts/ci/tests/test_final_candidate_triggers.py \
  scripts/ci/durable_queue/test_contract.py -q
15 passed

cd backend
PYTHONPATH=. python -m pytest tests/test_deploy_contract.py -q
2 passed

actionlint .github/workflows/*.yml
PASS
```

Дополнительный запуск всего `scripts/ci` дал `116 passed, 3 failed`. Все три
ошибки относятся к уже существующей Windows-only несовместимости кодировок:
MSYS Bash возвращает реальный путь `Документы`, а Python в assertion видит его
повреждённое code-page представление. Упавшие тесты находятся в
`scripts/ci/tests/test_smoke_workflow.py`; ни этот файл, ни проверяемый им
Docker-smoke workflow данным изменением не менялись. Целевые CI-permissions и
workflow-contract проверки зелёные; Linux GitHub runner этим ограничением не
затронут.

## Границы решения

Это изменение закрывает только P1-01 least privilege. Оно не закрывает P1-02
SBOM и P1-03 LICENSE/NOTICE, не меняет P2 raw-log policy и не является
разрешением на merge или deploy. Для окончательного runtime-доказательства
нужен обычный запуск GitHub Actions на интегрированном candidate SHA.
