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
    EXTERNAL_CACHE_DIR: Path = BASE_DIR / "cache" / "external"

    # External provider disk cache (Lyrics LRCLIB + Last.fm metadata).
    # Values change on the scale of months; negative lookups expire fast to
    # avoid hammering upstreams after "no match"/outages.
    LYRICS_CACHE_TTL_SEC: int = 90 * 24 * 3600      # 90 days
    LASTFM_CACHE_TTL_SEC: int = 14 * 24 * 3600      # 14 days

    # Audio Streaming & Cache
    RAM_CACHE_MAX_ITEMS: int = 15  # Keep up to 15 active audio tracks in RAM buffer
    RAM_CACHE_MAX_BYTES: int = 512 * 1024 * 1024  # 512 MB total RAM buffer cap
    PRECACHE_CHUNK_BYTES: int = 2 * 1024 * 1024  # 2MB initial chunk for pre-caching

    # On-the-fly Audio Transcoding (m4a/opus → mp3 320kbps for browser compatibility)
    TRANSCODE_CACHE_DIR: Path = BASE_DIR / "cache" / "music_transcoded"
    TRANSCODE_ENABLED: bool = True
    TRANSCODE_BITRATE_KBPS: int = 320  # CBR 320kbps for maximum quality
    TRANSCODE_WORKERS: int = 2         # Concurrent background transcode workers
    TRANSCODE_FORMATS_NEED: list = ["m4a", "mp4", "ogg", "opus", "flac", "wma", "aac"]
    TRANSCODE_PREFETCH_COUNT: int = 2   # Number of upcoming tracks to pre-transcode
    TRANSCODE_CACHE_MAX_BYTES: int = 10 * 1024 * 1024 * 1024  # 10 GB transcode cache cap

    # API rate limits (requests per minute per client IP). Auth endpoints get a
    # stricter budget. Previously hardcoded in main.py.
    API_RATE_LIMIT_PER_MIN: int = 120
    AUTH_RATE_LIMIT_PER_MIN: int = 10

    # Telegram transport
    TG_MAX_CONCURRENT_TRANSMISSIONS: int = 3

    # ─── Logging ────────────────────────────────────────────────────────────
    # File sink is JSONL; console is human-readable. Rotated archives are
    # AES-256 ZIPs (pyzipper) protected by LOG_ARCHIVE_PASSWORD; only
    # LOG_ARCHIVE_KEEP copies are retained.
    LOG_LEVEL: str = "INFO"            # file + root level: DEBUG/INFO/WARNING/ERROR
    LOG_CONSOLE_LEVEL: str = "INFO"    # console verbosity (raise to DEBUG for dev)
    LOG_DIR: Path = BASE_DIR / "backend" / "storage" / "log"
    LOG_MAX_BYTES: int = 5 * 1024 * 1024   # rotate each stream at 5 MB
    LOG_ARCHIVE_KEEP: int = 3               # keep no more than 3 compressed copies
    LOG_ARCHIVE_PASSWORD: str = ""
    LOG_ACCESS_ENABLED: bool = True         # HTTP access-log middleware

    # Cover proxy: fetch timeouts and max accepted image size.
    COVER_HTTP_TIMEOUT_SEC: int = 20
    COVER_HTTP_CONNECT_TIMEOUT_SEC: int = 8
    COVER_MAX_IMAGE_BYTES: int = 10 * 1024 * 1024

    # Subsonic / OpenSubsonic compatibility layer (/rest/*)
    SUBSONIC_ENABLED: bool = True
    SUBSONIC_SERVER_NAME: str = "T-Music"
    SUBSONIC_API_VERSION: str = "1.16.1"
    # Auth (2026-09 audit): ONLY the per-user dedicated Subsonic token is
    # accepted (p=<token> or t=md5(token+salt)). The panel bcrypt password is
    # deliberately NOT accepted in the query string anymore. Tokens are
    # Fernet-encrypted per user and rotate via POST /api/subsonic/credentials/rotate.
    SUBSONIC_API_PASSWORD: Optional[str] = None  # legacy env name, unused, kept to not break parsers
    SUBSONIC_TOKEN: Optional[str] = None         # legacy env name, unused, kept to not break parsers

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

    # Lyrics (LRCLIB proxy — no API key, outbound HTTPS only)
    LYRICS_ENABLED: bool = True
    LYRICS_HTTP_TIMEOUT_SEC: int = 8

    # Share links (minor 3, 2026-09 audit): a share created without an explicit
    # expiry now defaults to this TTL instead of living forever. Set 0 to keep
    # the legacy "never expires" behaviour. Max explicit expiry is unchanged
    # (ShareCreate.expires_in_hours <= 24*365).
    SHARE_DEFAULT_TTL_HOURS: int = 24 * 30  # 30 days

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

    # 2026-09-29 hardening knobs (both default to previous behavior).
    # TRUSTED_HOSTS feeds Starlette TrustedHostMiddleware: requests whose
    # Host header is not listed get a 400 before reaching any route. This
    # kills Host-header poisoning of generated share URLs (request.base_url).
    # Set explicit hostnames when the server has a stable address, e.g.
    # ["tmusic.lan", "192.168.1.50:8005"]. "*" disables the check.
    TRUSTED_HOSTS: List[str] = ["*"]
    # CORS_ALLOW_LAN gates the private-range allow_origin_regex below.
    # Set false once every client uses an explicit ALLOWED_ORIGINS entry —
    # otherwise any host on the LAN can read API responses with credentials.
    CORS_ALLOW_LAN: bool = True

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

if not settings.LOG_ARCHIVE_PASSWORD:
    raise RuntimeError(
        "LOG_ARCHIVE_PASSWORD is not configured. Set an explicit password in music.env."
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
settings.EXTERNAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
settings.TRANSCODE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
