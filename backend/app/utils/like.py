"""
T-Music LIKE-pattern escaping helper.
Prevents user-controlled '%' and '_' wildcards from altering search semantics.
"""


def escape_like(value: str) -> str:
    """Escape LIKE wildcards so a search string is matched literally (PostgreSQL backslash escape)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
