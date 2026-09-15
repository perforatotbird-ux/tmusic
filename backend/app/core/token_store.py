"""
T-Music JWT revocation store
Persists revoked token IDs (jti) so logout actually invalidates access tokens.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token, token_expiry_from_payload
from app.models.auth import RevokedToken


def revoke_access_token(db: Session, token: str) -> bool:
    """Blacklist a JWT by jti. Returns True if a jti was recorded."""
    payload = decode_access_token(token)
    if not payload or "jti" not in payload:
        return False
    jti = payload["jti"]
    existing = db.execute(
        select(RevokedToken.id).where(RevokedToken.jti == jti)
    ).scalar()
    if existing is not None:
        return True
    db.add(RevokedToken(jti=jti, expires_at=token_expiry_from_payload(payload)))
    db.commit()
    return True


def is_token_revoked(db: Session, jti: str) -> bool:
    if not jti:
        return False
    return db.execute(
        select(RevokedToken.id).where(RevokedToken.jti == jti)
    ).scalar() is not None
