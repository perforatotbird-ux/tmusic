# Активы и каталоги runtime

#tmusic #infra

Не код, но критично для эксплуатации.

---

## На диске репозитория

| Путь | Что | Git |
|------|-----|-----|
| `backend/` | Python app + tests | yes |
| `frontend/src` | React sources | yes |
| `frontend/dist` | Prod bundle | often yes/depends .gitignore |
| `scripts/` | ops tools | yes |
| `cache/music_covers` | Covers (TTL=0) | no (runtime) |
| `cache/music_images` | Images cache | no |
| `data/` | runtime data | no |
| `data/sessions/` | legacy sessions | no |
| `music.env` | secrets/config | **no** |
| `music.env.example` | template | yes |
| `.sec_audit/` | audit + PoC | yes (no live secrets) |
| `obsidian/` | this knowledge base | optional |
| `.mimocode/` | agent project config | local |

---

## Docker volumes (compose)

| Host | Container |
|------|-----------|
| `./music.env` | `/app/music.env` |
| `./cache` | `/app/cache` |
| `./data` | `/app/data` |

---

## Backup-набор (минимальный)

1. `music.env`
2. `pg_dump` базы `tgmusic`
3. `cache/music_covers` (иначе обложки пересобираются/теряются custom art)
4. Каталог с офлайн-копиями backup `SESSION_SECRET` / recovery keys — **отдельно от веб-доступа**

---

## Что не синхронизировать в облако/гит

- `music.env`
- private keys, TG session strings
- дампы PostgreSQL с зашифрованными/расшифрованными секретами

См. [[Архитектура безопасности]] · [[PostgreSQL]] · [[music.env]]

[[00-Главная]]
