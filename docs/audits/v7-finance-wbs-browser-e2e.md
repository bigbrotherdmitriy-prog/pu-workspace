# V7 finance/WBS browser acceptance

Дата: 2026-09-08. Область: только синтетический browser E2E фактического экрана «Исполнение и финансы».

## Проверяемый контракт

- подтверждённый договор связывает мастер исполнения с ГПР/WBS, бюджетом и ДДС;
- сводный WBS-узел отображает roll-up, а деньги остаются на leaf-этапах;
- прогноз показывает план, обязательства, факт, прогноз, отклонение и дату первого кассового разрыва;
- решения OWNER/LEGAL видимы до использования результата;
- поздний ответ с прежним `project_id` не заменяет финансовый scope выбранного проекта;
- связь бюджетной строки со сводным WBS-узлом отклоняется fail-closed;
- экран не создаёт платежи, проводки, конвертацию или другие HTTP mutation.

## Доказательства

Fixtures не содержат клиентских данных, provider IDs, токенов или внешних URL. HTTP boundary существующего Playwright harness работает deny-by-default и прикладывает только синтетический протокол.

Проверка `check:e2e` также потребовала привести старый synthetic fixture графика к обязательным WBS-полям и задать минимальный тип только для `process.env` в `playwright.config.ts`; новая зависимость не добавлялась, product-код не менялся.

Команды проверки:

```text
pnpm exec playwright test e2e/finance-wbs.e2e.ts --config playwright.config.ts
pnpm run check:e2e
pnpm run test
pnpm run check
pnpm run build
git diff --check
```

## Границы результата

Это браузерная проверка UI с синтетическим API. Она не подтверждает PostgreSQL-конкурентность, живые Google/Яндекс/Gmail подключения, production deploy или фактическое исполнение платежей. Платёжные действия намеренно отсутствуют.

## Результат

- целевые Chromium E2E: `3 passed`;
- полный Chromium E2E: `43 passed`;
- полный Vitest: `403 passed`;
- `check:e2e`, frontend `check`, production `build`: PASS;
- build сохранил только существующее предупреждение о chunk больше 500 kB;
- `git diff --check`: PASS.
