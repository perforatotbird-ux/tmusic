"""
T-Music Message ID Utilities
Canonical parsing and serialization of Telegram message IDs.
Supports raw ints, comma-separated IDs, and account-prefixed IDs ('40:20680').
"""
from typing import List, Union, Any, Optional


def _clean_id_token(token: str) -> Optional[int]:
    s = token.strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return int(s) if s.isdigit() else None


def parse_msg_ids(source: Any) -> List[int]:
    """Parse comma-separated msg_ids, int, or object with msg_ids/tg_message_id attributes."""
    if source is None:
        return []

    if isinstance(source, int):
        return [source]

    if isinstance(source, str):
        if source.strip() and source.strip().lower() != "none":
            result = []
            for part in source.split(","):
                val = _clean_id_token(part)
                if val is not None:
                    result.append(val)
            return result
        return []

    raw = None
    tg_id = None

    if hasattr(source, "msg_ids"):
        raw = source.msg_ids
    elif isinstance(source, dict):
        raw = source.get("msg_ids")

    if hasattr(source, "tg_message_id"):
        tg_id = source.tg_message_id
    elif isinstance(source, dict):
        tg_id = source.get("tg_message_id")

    if raw and isinstance(raw, str) and raw.strip() and raw.strip().lower() != "none":
        result = []
        for part in raw.split(","):
            val = _clean_id_token(part)
            if val is not None:
                result.append(val)
        return result

    if tg_id:
        val = _clean_id_token(str(tg_id))
        return [val] if val is not None else []

    return []


def format_msg_ids(ids: Union[List[int], int]) -> str:
    """Serialize list of integer IDs to canonical comma-separated string."""
    if not ids:
        return ""
    if isinstance(ids, int):
        return str(ids)
    return ",".join(str(i) for i in ids)
