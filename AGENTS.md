# Project workflow

## Obsidian documentation (required)

The project knowledge base lives in `obsidian/`. Read the relevant existing notes
before changing code or configuration. After each completed stage of work, update
the affected notes immediately, not only at the end of a task.

Document actual behavior, configuration changes, safe operational instructions,
checks performed, and unresolved deployment actions. Link new notes from
`obsidian/00-Главная.md` and related notes. Follow the existing Russian-language
style and wikilinks. See `obsidian/08-Разработка/Чеклист разработки.md`.

Never copy real passwords, tokens, connection strings, private keys or credential
hashes into code, documentation, logs or chat. Use placeholders/variable names.
Do not claim production rotation/deployment or Git history cleanup was completed
when only local files were changed. History rewriting, force-push, and live
credential rotation require explicit coordination; never delete DB volumes to
rotate a password.
