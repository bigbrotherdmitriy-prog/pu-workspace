# Воспроизводимая и безопасная упаковка PU Workspace

Статус: техническая процедура. Она не заменяет решение правообладателя или юридическое заключение.

## Зафиксированный кандидат

Проверяемый product commit: `8ccc194bc834328e51a73225981f74d81775789a`.

Сборка читает файлы только через `git show <FULL_SHA>:<path>`, поэтому незакоммиченная рабочая копия в архив не попадает. Архив имеет один корень, содержит только обычные файлы из allowlist, нормализованные uid/gid/mode/mtime и gzip `mtime=0`. Manifest v2 связывает каждый файл с размером и SHA-256, commit с Git tree, а также хеширует список исключённых путей.

## Обязательная последовательность

1. Создать чистую worktree на полном SHA.
2. Получить Python lock **в Linux x86_64 / CPython 3.12**, не на Windows:

   ```text
   python -m pip install --dry-run --ignore-installed --only-binary=:all: \
     --report /tmp/pu-pip-report.json -r backend/requirements.txt
   python scripts/release/build_python_lock.py \
     --report /tmp/pu-pip-report.json \
     --requirements backend/requirements.txt \
     --lock-out backend/requirements-linux-py312.lock \
     --provenance-out docs/release/generated/python-lock-provenance.json
   ```

   Скрипт отклоняет Windows/cross-platform report, yanked distribution, sdist, неполный hash и расхождение direct pins. Report не коммитится: в нём могут быть не нужные поставке metadata. Коммитятся только lock и обезличенный provenance.

3. Собрать backend из lock в изолированной среде. До изменения `backend/Dockerfile` интегратором текущий образ продолжает ставить `requirements.txt`, поэтому наличие lock само по себе ещё не доказывает использование lock.
4. Зафиксировать digest и фактические слои/apt:

   ```text
   docker image inspect <IMAGE@sha256:...> > /tmp/image-inspect.json
   docker run --rm <IMAGE@sha256:...> sh -c \
     "dpkg-query -W -f='${Package}\\t${Version}\\t${Architecture}\\n'" > /tmp/dpkg.tsv
   syft <IMAGE@sha256:...> -o spdx-json=/tmp/container.spdx.json
   python scripts/release/container_evidence.py capture \
     --image-ref <IMAGE@sha256:...> \
     --inspect /tmp/image-inspect.json --dpkg /tmp/dpkg.tsv \
     --sbom /tmp/container.spdx.json --release-commit <FULL_SHA> \
     --out docs/release/generated/container-evidence.json
   ```

   В shell с подстановкой `${Package}` выражение нужно экранировать по правилам этого shell. В evidence не включаются environment, history, labels, команды или filesystem content контейнера.

5. Получить license evidence и SPDX, затем собрать архив:

   ```text
   python scripts/release/collect_license_evidence.py \
     --ref <FULL_SHA> --as-of <YYYY-MM-DD> \
     --out docs/release/generated/license-evidence.json
   python scripts/legal_release_kit.py all \
     --ref <FULL_SHA> --out dist/legal-release \
     --license-evidence docs/release/generated/license-evidence.json
   python scripts/release/verify_release_package.py \
     --archive dist/legal-release/pu-workspace-<12_SHA>-commercial-source.tar.gz \
     --out dist/legal-release/VERIFICATION_RESULT.json
   ```

6. Повторить сборку в новом пустом каталоге и сравнить SHA-256 архивов.
7. Только после PASS перенести имя и SHA-256 архива в `docs/legal/05_TRANSFER_ACCEPTANCE_ACT_RU.md` конкретной сделки.

## Что проверяет verifier

- единственный корень и отсутствие path traversal;
- только regular files, без symlink/hardlink/device;
- отсутствие дублей путей и неучтённых manifest-файлов;
- размер и SHA-256 каждого файла;
- `.env.example`: чувствительные значения пусты или являются явными шаблонами;
- сигнатуры ключей/токенов/JWT/не-шаблонных DSN;
- известные клиентские маркеры, ИНН и СНИЛС;
- согласованность имени корня и полного commit из manifest.

Проверка — deny-by-default. Совпадение не «игнорируется ради релиза»: источник устраняется или документированно признаётся блокером.

## Текущие блокеры кандидата

- `frontend/src/modules/contracts/ContractDocumentPicker.tsx` содержит client-like примеры; текущая строгая упаковка останавливается до замены примеров отдельным продуктовым потоком.
- Linux-generated Python lock и доказательство его использования образом отсутствуют.
- Digest/layer/apt evidence фактически собранного образа отсутствует.
- Полные upstream LICENSE/COPYING/NOTICE texts ещё не собраны и не проверены.
- Правообладатель/год/модель лицензии и юридические выводы не утверждены.

Поэтому архив для коммерческой передачи на этом этапе **не выпускается**.
