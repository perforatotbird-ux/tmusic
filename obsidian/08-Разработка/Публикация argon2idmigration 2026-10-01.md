# Публикация argon2idmigration 2026-10-01

#tmusic #dev #release

По явному запросу пользователя код опубликован в GitHub `perforatotbird-ux/tmusic`, ветка `argon2idmigration`.

- Кодовый коммит: `1dfdf16d6c13d8f29028969fc364a9cfd2b8a00d` — Argon2id migration и накопленные security/edge/async/business/error-handling исправления, permanent tests и конфигурационные примеры. 79 code/test/config files.
- Исходная удалённая база: `e832b308b88e4906a5b9e6938c1ce5c39a4a1745`; её root tree сверено с локальным baseline, содержимое совпадает. Публикация идёт поверх текущей ветки, без force-push/history rewrite.
- Obsidian отчёты и этот журнал включены отдельным документационным коммитом в ту же ветку.
- Проверки до отправки: 317 backend tests, 72 frontend tests, production build, два headless UI smoke с mocked API; compileall/pip check/diff check. В staged additions не обнаружены private-key/common token patterns; .env/music.env, приватные key files, build/dist/node_modules не добавлялись. Это pre-publish sanity check, не заявление о server-side secret scanning всей истории.
- CORS/main.py не менялись. master не сливается и не обновляется; PR/merge/deployment не выполняются.
- Локальный Git не имеет CLI credential helper. Публикация выполнена через подключённый GitHub MCP; локальные mirror commits могут иметь другие SHA, чем GitHub commits. Содержимое синхронизируется и проверяется по tree/blob SHA; для обычного CLI push нужен отдельный authentication/fetch/reconciliation, не force-push.
- Рабочая БД, ключи и production secrets не менялись. Live PostgreSQL/Telegram/Last.fm/proxy/Android проверки и repair ранее double-wrapped credentials остаются отдельными действиями.

Связано: [[ADR-005 Argon2id Migration]] · [[Ревью граничных значений 2026-10-01]] · [[Ревью async 2026-10-01]] · [[Ревью бизнес-логики 2026-10-01]] · [[Ревью обработки ошибок 2026-10-01]] · [[Тестирование]]

[[00-Главная]]
