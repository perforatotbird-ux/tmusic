"""
T-Music LIKE-pattern escaping helper.
Prevents user-controlled '%' and '_' wildcards from altering search semantics.
"""


def escape_like(value: str) -> str:
    """Escape LIKE wildcards so a search string is matched literally (PostgreSQL backslash escape)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_pattern(term: str) -> str:
    """Build a case-insensitive ``%term%`` LIKE pattern with wildcards escaped.

    Centralizes the ``search.strip().lower()`` + ``escape_like`` dance used
    by the artist/album repositories so both stay consistent.
    """
    return f"%{escape_like(term.strip().lower())}%"
