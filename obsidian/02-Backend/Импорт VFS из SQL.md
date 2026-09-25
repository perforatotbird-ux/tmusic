# Импорт VFS из SQL-бэкапа Replit

#tmusic #vfs #import #replit #backup

## Назначение

Администратор T-Music может загрузить текстовый PostgreSQL dump из приложения Replit/CloudByte, выбрать исходную папку и добавить её VFS-дерево в T-Music.

Импорт выполняется только из вкладки **VFS** админ-панели:

1. Выбрать SQL-файл.
2. Указать путь исходной папки, например `Music` или `Music/Artist/Album`.
3. При необходимости выбрать целевую папку T-Music.
4. Получить preview без записи в VFS.
5. Проверить счётчики и конфликты.
6. Подтвердить импорт.

## Что импортируется

Разрешены только таблицы:

- `folders`;
- `folder_closures`;
- `vfiles`.

Для аудио переносятся метаданные и Telegram-locator:

- `name`, `mime_type`, `size`;
- `tg_message_id`, `msg_ids`, `tg_file_id`;
- `account_map`;
- encryption metadata для encrypted VFS;
- даты и пользовательские VFS-флаги.

Остальные таблицы dump анализируются как ignored и не записываются в T-Music. В частности, не импортируются `app_config`, `tg_accounts` credentials, playlists, shares, jobs и DDL/ACL.

## Важно о содержимом backup

SQL dump не содержит аудиобайты. Он содержит ссылки на Telegram-сообщения. Для воспроизведения импортированных треков T-Music должен иметь совместимые Telegram-аккаунты и ключи шифрования.

Если ID аккаунтов в T-Music отличаются, сервис пытается сопоставить аккаунты по имени. Неоднозначное или отсутствующее сопоставление показывается в preview как предупреждение.

## Поведение конфликтов

- Существующие папки с тем же именем и тем же родителем переиспользуются.
- Существующий VFile с тем же именем в целевой папке пропускается.
- Дубликаты внутри одного backup также пропускаются.
- Существующие данные не перезаписываются.
- После импорта closure table перестраивается для всей T-Music VFS.
- Импорт выполняется в одной транзакции; ошибка откатывает изменённые VFS-данные.

## API

### `POST /api/admin/vfs/import/preview`

Multipart-поля:

- `file` — текстовый PostgreSQL SQL dump;
- `source_folder_path` — логический путь источника, по умолчанию `Music`;
- `source_folder_id` — опциональный source ID;
- `target_root_id` — опциональная целевая папка T-Music;
- `include_non_audio` — `true/false`, по умолчанию `false`.

Требуется admin-сессия с permission `admin.backups.manage` и CSRF header для cookie-auth.

### `POST /api/admin/vfs/import/{import_id}/run`

JSON:

```json
{"confirm": true}
```

### `GET /api/admin/vfs/import/{import_id}`

Возвращает состояние текущей preview-сессии.

## Ограничения и безопасность

- Максимальный размер SQL: 256 МБ.
- Preview-сессия и staging-файл хранятся в `data/vfs_imports/` максимум 1 час.
- Имя файла на сервере генерируется UUID; клиентское имя используется только для отображения.
- SQL не передаётся в `psql`, `sqlalchemy.text()` или другой исполнитель.
- `COPY ... FROM PROGRAM` отклоняется.
- В базу не возвращаются session strings, API hashes, account maps или raw SQL из preview.
- Одновременный фактический импорт запрещён; следующий запрос получает conflict.
- Staging-копия удаляется после успешного импорта и по TTL.

## Проверка

```powershell
Set-Location K:\Test\tmusic\backend
python -m pytest tests/test_vfs_import.py -q
```

Для production перед использованием сделать backup текущей `tgmusic` и проверить TG account IDs, `SESSION_SECRET` и encryption keys.

## Связанные заметки

- [[Обзор Backend]]
- [[Модели БД]]
- [[Архитектура безопасности]]
- [[Чеклист безопасности]]
- [[Тестирование]]
- [[Операционные скрипты]]
- [[00-Главная]]
