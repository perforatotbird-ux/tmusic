# ADR-005 — Argon2id Migration

#tmusic #adr #security #auth

Дата: 2026-10-01. Статус: реализовано локально в `argon2idmigration`; deployment и живая БД не проверены.

## Контекст

Bcrypt 4.2.1 учитывает только первые 72 UTF-8 байта, но приложение допускает пароли до 128 символов. Длинные Unicode-пароли не защищают полный суффикс. Дополнительно JWT не требовал всех служебных claims и не связывал uid с выбранным пользователем. См. исходное [[Ревью Auth 2026-10-01]].

## Решение

- Запись Argon2id: v=19, m=65536 KiB, t=3, p=1, salt=16 bytes, hash=32 bytes. PHC помещается в существующий VARCHAR(255); schema migration не требуется.
- Читать Argon2id, bounded bcrypt ($2a/$2b/$2y, cost 4–16) и временно SECRET_KEY-salted SHA-256. Никаких plaintext/unsalted SHA-256.
- Проверять формат PHC/base64, стоимость/размер хэша до bootstrap и дорогого KDF. Аргон-verify допускает только type ID, version 19, m 8192–131072, t 1–6, p 1–4, hash 16–64 и salt 8–64 bytes.
- При успешном panel login короткий bcrypt (<72 UTF-8 байт введённого пароля), salted SHA-256 и Argon2id с прежними параметрами rehash. Тот же пароль не повышает token_version.
- Bcrypt с вводом >=72 байт нельзя однозначно мигрировать: выдать `pwd_reset=true` JWT до 10 минут. Разрешены только me/change-password/logout. Пользователь явно задаёт полный новый пароль, после чего token_version повышается и нужен повторный вход. Случайный суффикс не становится паролем молча.
- Login и смена пароля проверяют свежую DB-запись под row lock. Admin reset хеширует до захвата блокировки, без ожидания worker pool с удерживаемым row lock.
- Password work запускается в bounded AnyIO workers; process-wide semaphore ограничивает одновременно выполняемые KDF. Default concurrency=2: KDF до 128 MiB на процесс; не общий RSS.
- Bootstrap принимает Argon2id/bcrypt и сохраняет one-shot applied marker. CLI/admin установки используют Argon2id. Выделенные Subsonic-токены не меняют формат; их проверки также offload.
- JWT требует sub/exp/iat/jti/uid/tv, корректные типы, срок, подпись, uid=user.id и tv. Зарезервированные sub/exp/iat/jti нельзя переопределить при выдаче.
- **CORS не изменять** в этой реализации по запросу владельца для локального использования. AUTH-04 остаётся отложенным.

## Внедрение и откат

1. Сделать резервную копию БД и сохранить приватные runtime secrets вне Git/Obsidian.
2. Установить pinned зависимости / пересобрать backend и frontend. Для нескольких экземпляров сначала развернуть dual-reader на всех; текущая ветка одновременно включает Argon2id writer, поэтому обновлять согласованно без bcrypt-only узлов.
3. Проверить на disposable PostgreSQL login, rehash, явную смену, revocation и конкурентный reset. SQLite unit-тесты не подтверждают семантику PostgreSQL row lock.
4. Измерить память/латентность правильных/ошибочных входов на целевом устройстве; учесть число процессов и DB pool. Env knobs — [[music.env]].
5. Старые bcrypt остаются до входа/явного сброса. Невозможно преобразовать готовый хэш без исходного пароля. Legacy salted SHA-256 пока сохраняется только для перехода.
6. После записи Argon2id нельзя откатываться на bcrypt-only verifier. Rollback должен читать оба формата. Не откатывать хэши массово и не удалять DB volume.
7. JWT с отсутствующими обязательными claims потребуют повторного входа. Корректно выданные прежние токены с uid/tv остаются действительными до истечения/отзыва.

JWT signing key, cookies, Fernet/TG и audio encryption keys не меняют формат; их ротация не требуется и не выполнялась. Настройки CORS оставлены прежними. Deployment не выполнялся.

## Проверки

Результаты финального локального прогона фиксируются в [[Тестирование]] и [[Ревью Auth 2026-10-01]]. Добавлен `test_argon2_jwt_migration.py`: full Unicode password, bcrypt/SHA migration, 72-byte reset flow, JWT mandatory claims/types/signature/uid, bootstrap one-shot, limiter/heartbeat, свежий DB-хэш и порядок admin lock/hash.

Связано: [[API Auth]] · [[Архитектура безопасности]] · [[Чеклист безопасности]] · [[Устранение уязвимостей]]

[[00-Главная]]
