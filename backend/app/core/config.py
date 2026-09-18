"""
T-Music Configuration Management
Centralized typed settings loaded from music.env with strict validation.
"""
import os
import base64
import secrets
import logging
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Base directory points to tmusic root
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
ENV_FILE = BASE_DIR / "music.env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    PROJECT_NAME: str = "T-Music"
    ENVIRONMENT: str = "production"
    DEBUG: bool = False
    PORT: int = 8005
    HOST: str = "0.0.0.0"

    # Database (must be configured explicitly — no implicit credentials in code)
    DATABASE_URL: str = ""

    # Security & JWT
    SECRET_KEY: str = secrets.token_urlsafe(32)
    SESSION_SECRET: Optional[str] = None
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12  # 12 hours
    MAX_FAILED_LOGIN_ATTEMPTS: int = 5
    LOCKOUT_MINUTES: int = 15
    REQUIRE_AUTH: bool = True  # High security default for Internet access
    COOKIE_SECURE: bool = False  # set True when serving HTTPS
    TRUST_PROXY_HEADERS: bool = False  # set True only behind a trusted reverse proxy

    # One-time admin bootstrap: bcrypt hash applied at startup when the admin
    # account is missing or still carries a legacy (non-bcrypt) hash.
    ADMIN_PASSWORD_HASH: Optional[str] = None

    # End-to-End Encryption (AES-256-GCM Vault)
    ENCRYPTION_RECOVERY_KEY: Optional[str] = None
    ENCRYPTION_FALLBACK_KEYS: Optional[str] = None

    # Telegram MTProto
    TG_API_ID: Optional[str] = None
    TG_API_HASH: Optional[str] = None
    TG_SESSION_STRING: Optional[str] = None
    TG_SESSION_NAME: str = "tmusic_session"
    TG_WORKERS: int = 2
    SLEEP_THRESHOLD: int = 60

    # Paths & Cache
    BASE_DIR: Path = BASE_DIR
    DATA_DIR: Path = BASE_DIR / "data"
    SESSIONS_DIR: Path = BASE_DIR / "data" / "sessions"
    CACHE_DIR: Path = BASE_DIR / "cache"
    IMG_CACHE_DIR: Path = BASE_DIR / "cache" / "music_images"
    COVER_CACHE_DIR: Path = BASE_DIR / "cache" / "music_covers"

    # Audio Streaming & Cache
    RAM_CACHE_MAX_ITEMS: int = 15  # Keep up to 15 active audio tracks in RAM buffer
    RAM_CACHE_MAX_BYTES: int = 512 * 1024 * 1024  # 512 MB total RAM buffer cap
    PRECACHE_CHUNK_BYTES: int = 2 * 1024 * 1024  # 2MB initial chunk for pre-caching

    # API rate limits (requests per minute per client IP). Auth endpoints get a
    # stricter budget. Previously hardcoded in main.py.
    API_RATE_LIMIT_PER_MIN: int = 120
    AUTH_RATE_LIMIT_PER_MIN: int = 10

    # Telegram transport
    TG_MAX_CONCURRENT_TRANSMISSIONS: int = 3

    # Cover proxy: fetch timeouts and max accepted image size.
    COVER_HTTP_TIMEOUT_SEC: int = 20
    COVER_HTTP_CONNECT_TIMEOUT_SEC: int = 8
    COVER_MAX_IMAGE_BYTES: int = 10 * 1024 * 1024

    # Subsonic / OpenSubsonic compatibility layer (/rest/*)
    SUBSONIC_ENABLED: bool = True
    SUBSONIC_SERVER_NAME: str = "T-Music"
    SUBSONIC_API_VERSION: str = "1.16.1"
    # Optional dedicated Subsonic password (recommended for clients).
    # If unset, panel user bcrypt password is accepted via p= parameter.
    SUBSONIC_API_PASSWORD: Optional[str] = None
    # Optional token-auth secret: clients send t=md5(SUBSONIC_TOKEN+salt)
    SUBSONIC_TOKEN: Optional[str] = None

    # External APIs
    LASTFM_API_KEY: Optional[str] = None
    LASTFM_API_SECRET: Optional[str] = None
    # Official Last.fm name for the companion of api_key
    LASTFM_SHARED_SECRET: Optional[str] = None
    LASTFM_SESSION_KEY: Optional[str] = None
    LASTFM_USERNAME: Optional[str] = None
    LASTFM_ENABLED: bool = True
    DISCOGS_CONSUMER_KEY: Optional[str] = None
    DISCOGS_CONSUMER_SECRET: Optional[str] = None
    MUSICBRAINZ_RATE_LIMIT_SEC: float = 1.05

    @property
    def lastfm_secret(self) -> Optional[str]:
        """Effective Last.fm secret (shared_secret is the official env name)."""
        return self.LASTFM_API_SECRET or self.LASTFM_SHARED_SECRET

    @property
    def lastfm_metadata_ready(self) -> bool:
        return bool(self.LASTFM_ENABLED and self.LASTFM_API_KEY and self.lastfm_secret)

    @property
    def lastfm_scrobble_ready(self) -> bool:
        return bool(self.lastfm_metadata_ready and self.LASTFM_SESSION_KEY)

    @property
    def discogs_ready(self) -> bool:
        return bool(self.DISCOGS_CONSUMER_KEY and self.DISCOGS_CONSUMER_SECRET)

    # CORS & Security — never use "*" together with credentials
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:8005",
        "http://127.0.0.1:8005",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
    ]

    def get_master_keys(self) -> List[bytes]:
        """
        Returns decoded 32-byte AES Master Keys in order of priority:
        1. Primary ENCRYPTION_RECOVERY_KEY
        2. Any fallback keys from ENCRYPTION_FALLBACK_KEYS
        """
        keys: List[bytes] = []
        raw_candidates: List[str] = []

        if self.ENCRYPTION_RECOVERY_KEY:
            raw_candidates.append(self.ENCRYPTION_RECOVERY_KEY.strip())

        if self.ENCRYPTION_FALLBACK_KEYS:
            for part in self.ENCRYPTION_FALLBACK_KEYS.split(","):
                part = part.strip()
                if part and part not in raw_candidates:
                    raw_candidates.append(part)

        for candidate in raw_candidates:
            try:
                decoded = base64.b64decode(candidate)
                if len(decoded) == 32:
                    keys.append(decoded)
                else:
                    logger.warning("Skipping encryption key with invalid length (%d bytes)", len(decoded))
            except Exception as e:
                logger.warning("Failed to decode base64 encryption key: %s", e)

        return keys


settings = Settings()

# Fail fast on missing database configuration instead of silently using implicit credentials
if not settings.DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not configured. Copy music.env.example to music.env "
        "and set an explicit PostgreSQL connection URL."
    )

# REQUIRE_AUTH=false silently impersonates the admin account for every request
# (see dependencies._authenticate_request). That is a development-only escape
# hatch: refuse to boot unless ENVIRONMENT is explicitly "development".
# (An empty ENVIRONMENT must not slip through the check either.)
if not settings.REQUIRE_AUTH and (settings.ENVIRONMENT or "").lower() != "development":
    raise RuntimeError(
        "REQUIRE_AUTH=false is allowed only with ENVIRONMENT=development: it disables "
        "all API authentication."
    )

# Ensure runtime directories exist
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
settings.IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
settings.COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
