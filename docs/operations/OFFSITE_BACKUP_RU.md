# Внешние резервные копии базы данных

## Что копируется

Вся рабочая база PostgreSQL `pu_workspace` из контейнера `puw-primary-next-primary-db`:
проекты, задачи, письма, договоры, черновики ответов, журнал действий и всё остальное.
Файлы, лежащие в Google Drive / Яндекс Диске, сюда не входят: они и так хранятся вне сервера.

Локальные копии `/opt/pu-workspace-primary/backups/*.dump`, которые делает деплой, остаются.
Но они лежат на том же диске: если диск умрёт, пропадут вместе с базой. Поэтому нужна внешняя копия.

## Как это работает

Каждую ночь в 03:30 (Москва) запускается `scripts/backup/offsite_backup.sh`:

1. Снимает дамп `pg_dump -Fc` и сразу шифрует его через `gpg --symmetric --cipher-algo AES256`.
   Ключом служит фраза-пароль из файла. Незашифрованный дамп на диск не пишется.
2. Проверяет, что зашифрованный файл расшифровывается и читается как архив `pg_restore`.
3. Загружает файл в Google Drive владельца в папку **«PU Workspace — резервные копии БД»**.
   Если папки нет, создаёт её. Загрузка идёт через Google-подключение самого приложения
   (токен `PU_BACKUP_GOOGLE_TOKEN_ID`, по умолчанию 11).
4. Сверяет размер и md5, которые вернул Drive, с локальными. Если они не совпали, загрузка
   удаляется, а запуск считается неудачным.
5. Только после успешной проверки чистит старые копии.

Имя файла: `pu_workspace-ГГГГММДДTЧЧММССZ.dump.gpg` (время UTC). В свойствах файла на Drive
записано, сколько строк было в ключевых таблицах в момент копирования.

## Сколько копий хранится

- последние **7 дней**: по одной копии за день;
- **4 недели**: копии за воскресенье;
- **6 месяцев**: копии за 1-е число.

Даты считаются по московскому времени. Лишние копии переносятся в корзину Drive.
Drive сам очищает корзину через 30 дней, так что ошибку удаления можно успеть исправить.
Примерный объём: 16–17 файлов, сейчас по ~14 МБ каждый.

## Ежемесячная проверка восстановления

1-го числа в 05:00 запускается `scripts/backup/restore_check.sh`:

1. Скачивает самую свежую копию и сверяет её md5.
2. Расшифровывает её и восстанавливает во временный контейнер `postgres:16-alpine`
   (без сети, не больше 600 МБ памяти). Рабочую базу проверка не трогает.
3. Сравнивает число строк в таблицах `projects`, `tasks`, `messages`, `contracts`,
   `response_drafts`, `audit_logs` с числами на момент копирования.
4. Печатает одну строку `PASS …` или `FAIL …` и удаляет временный контейнер.

Если копирование или проверка не удались, в Telegram придёт короткое сообщение
без данных из базы. Подробности смотрите в журнале:
`journalctl -u pu-offsite-backup` или `journalctl -u pu-restore-check`.

## Где фраза-пароль. ВАЖНО

Фраза-пароль лежит на сервере в файле:

    /root/.config/pu-workspace/backup/passphrase   (права 600, папка 700)

**Владелец обязан хранить копию этой фразы вне сервера.** Подойдёт менеджер паролей
или распечатка в сейфе. Хранить её рядом с копиями в том же Google Drive нельзя.
Если сервер умрёт, а копии фразы нет, расшифровать копии в Drive будет невозможно.
Тогда они бесполезны.

Посмотреть фразу (только на сервере, только под root): `cat /root/.config/pu-workspace/backup/passphrase`.
Если сменить фразу, старые копии всё равно расшифровываются только старой фразой. Сохраните обе.

## Установка таймеров (делает оператор)

    install -d -m 755 /usr/local/lib/pu-workspace-backup
    install -m 755 scripts/backup/offsite_backup.sh scripts/backup/restore_check.sh /usr/local/lib/pu-workspace-backup/
    install -m 644 scripts/backup/common.sh scripts/backup/drive_ops.py scripts/backup/rotation.py /usr/local/lib/pu-workspace-backup/
    install -m 644 scripts/backup/pu-*.service scripts/backup/pu-*.timer /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now pu-offsite-backup.timer pu-restore-check.timer
    systemctl list-timers 'pu-*'

Настройки можно переопределить в файле `/root/.config/pu-workspace/backup/backup.env`
(необязательный): `PU_BACKUP_GOOGLE_TOKEN_ID`, `PU_BACKUP_DRIVE_FOLDER`,
`PU_BACKUP_PASSPHRASE_FILE`, `PU_BACKUP_KEEP_DAILY`/`WEEKLY`/`MONTHLY`.

Запустить вручную: `systemctl start pu-offsite-backup.service` и `systemctl start pu-restore-check.service`.

## Восстановление вручную, по шагам

На новом сервере нужны Docker и gpg.

1. Скачайте нужный файл `pu_workspace-…dump.gpg` из папки «PU Workspace — резервные копии БД»
   в Google Drive, например в `/root/restore/`.
2. Положите фразу-пароль из своей внешней копии в файл:

       install -d -m 700 /root/.config/pu-workspace/backup
       nano /root/.config/pu-workspace/backup/passphrase     # вставить фразу, сохранить
       chmod 600 /root/.config/pu-workspace/backup/passphrase

3. Расшифруйте файл:

       gpg --batch --pinentry-mode loopback \
           --passphrase-file /root/.config/pu-workspace/backup/passphrase \
           --decrypt /root/restore/pu_workspace-….dump.gpg > /root/restore/pu_workspace.dump

4. Поднимите приложение обычным деплоем, чтобы появился контейнер базы
   `puw-primary-next-primary-db`. Затем остановите backend и воркеры, чтобы никто не писал в базу.
5. Восстановите дамп в базу. Существующие объекты будут пересозданы:

       docker exec -i puw-primary-next-primary-db \
           pg_restore -U pu_user -d pu_workspace --clean --if-exists --no-owner \
           < /root/restore/pu_workspace.dump

6. Запустите backend и воркеры. Проверьте вход и наличие проектов, задач и писем.
7. Удалите расшифрованный файл: `shred -u /root/restore/pu_workspace.dump`.

Сначала можно проверить копию без риска: запустите `scripts/backup/restore_check.sh`.
Он восстанавливает копию только во временный контейнер.
