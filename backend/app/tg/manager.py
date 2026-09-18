"""
T-Music Multi-Account Telegram Cluster Manager
Coordinates parallel start, MTProto session decryption, per-account FloodWait tracking,
and transparent mid-stream client failover.
"""
import time
import base64
import hashlib
import logging
import asyncio
from typing import Dict, List, Optional, Tuple

from pyrogram import Client, errors
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import engine
from app.models.vfs import TGAccount
from app.utils.msg_ids import parse_msg_ids

logger = logging.getLogger(__name__)


def _fernet_for_secret(secret: str) -> Fernet:
    """Derive the stable Fernet instance for a SESSION_SECRET (sha256 key schedule)."""
    key = hashlib.sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_session_string(plain_str: Optional[str], secret: Optional[str]) -> Optional[str]:
    """Encrypts a session string / api_hash with Fernet for at-rest storage."""
    if not plain_str:
        return plain_str
    if not secret:
        return plain_str  # Cannot encrypt without a secret — stored as-is
    return _fernet_for_secret(secret).encrypt(plain_str.encode()).decode()


def is_encrypted(value: Optional[str], secret: Optional[str]) -> bool:
    """True when `value` is a valid Fernet token under the given secret."""
    if not value or not secret:
        return False
    try:
        _fernet_for_secret(secret).decrypt(value.encode())
        return True
    except Exception:
        return False


def decrypt_session_string(encrypted_str: Optional[str], secret: Optional[str]) -> Optional[str]:
    """Decrypts Fernet-encrypted session string using SESSION_SECRET."""
    if not encrypted_str:
        return None
    if not secret:
        return encrypted_str  # Return raw if no secret provided

    try:
        return _fernet_for_secret(secret).decrypt(encrypted_str.encode()).decode()
    except Exception as e:
        logger.warning("Session string decryption failed: %s", e)
        # If decryption fails, the string may already be unencrypted
        return encrypted_str


def ensure_encrypted(value: Optional[str], secret: Optional[str]) -> Tuple[Optional[str], bool]:
    """Return ``(stored_value, changed)`` with ``value`` Fernet-sealed at rest.

    Single choke point for the "encrypt if not already encrypted" rule shared
    by the runtime self-heal (TelegramManager.start), scripts/secure_tg_accounts.py
    and scripts/migrate_data.py — keeps the plaintext-detection invariant in one place.
    """
    if not value or not secret or is_encrypted(value, secret):
        return value, False
    return encrypt_session_string(value, secret), True


def seal_tg_account(acc: "TGAccount", secret: Optional[str]) -> int:
    """Encrypt plaintext ``session_string``/``api_hash`` of a TGAccount in place.

    Returns the number of sealed fields (0..2). Never touches the DB session —
    callers decide when to commit (manager commits once via ``healed`` flag,
    CLI commits after the loop, migration commits per its own flow).
    """
    sealed = 0
    if acc.session_string:
        acc.session_string, changed = ensure_encrypted(acc.session_string, secret)
        if changed:
            sealed += 1
            logger.info("Re-encrypted plaintext session_string for TG account %d at rest.", acc.id)
    if acc.api_hash:
        acc.api_hash, changed = ensure_encrypted(acc.api_hash, secret)
        if changed:
            sealed += 1
            logger.info("Re-encrypted plaintext api_hash for TG account %d at rest.", acc.id)
    return sealed


class TelegramManager:
    """Manages pool of Pyrogram clients for streaming from Telegram Saved Messages."""

    def __init__(self):
        self.clients: Dict[int, Client] = {}
        self.primary_id: Optional[int] = None
        self._flood_cooldowns: Dict[int, float] = {}  # account_id -> cooldown_until_timestamp
        self._lock = asyncio.Lock()
        self._is_started = False

    @property
    def is_started(self) -> bool:
        return self._is_started and bool(self.clients)

    async def start(self):
        """Initializes and connects all active Telegram accounts in memory."""
        async with self._lock:
            if self.is_started:
                return

            logger.info("Initializing T-Music Telegram Multi-Account Cluster...")
            tasks = []

            # 1. Load active accounts from tgmusic database.
            # Resilience: a DB outage must not prevent the application from
            # starting (SPA + health answers stay up; streaming degrades).
            try:
                with Session(engine) as db:
                    accounts = db.scalars(select(TGAccount).where(TGAccount.is_active == True)).all()
                    healed = False
                    for acc in accounts:
                        # Per-account isolation: one corrupt row must not abort
                        # the rest of the cluster (api_id is a String column and
                        # Fernet decryption raises on tampered values).
                        try:
                            decrypted_session = decrypt_session_string(acc.session_string, settings.SESSION_SECRET)
                            api_id = acc.api_id or settings.TG_API_ID
                            # api_hash may also be Fernet-encrypted at rest
                            raw_api_hash = acc.api_hash or settings.TG_API_HASH
                            api_hash = decrypt_session_string(raw_api_hash, settings.SESSION_SECRET) if raw_api_hash else None

                            # Self-heal: persist Telegram credentials ENCRYPTED at rest.
                            # Plaintext rows (legacy installs) are transparently re-encrypted.
                            if settings.SESSION_SECRET:
                                if seal_tg_account(acc, settings.SESSION_SECRET):
                                    healed = True

                            if not api_id or not api_hash or not decrypted_session:
                                logger.warning("Skipping account %d (%s): missing credentials or session.", acc.id, acc.name)
                                continue

                            tasks.append(self._start_single_client(
                                account_id=acc.id,
                                name=acc.name,
                                session_string=decrypted_session,
                                api_id=int(api_id),
                                api_hash=api_hash,
                                is_primary=acc.is_primary
                            ))
                        except Exception as e:
                            logger.warning("Skipping TG account %d (%s): %s", acc.id, acc.name, e)
                            continue
                    if healed:
                        db.commit()
            except Exception as e:
                logger.error("Failed to load Telegram accounts from database: %s", e)

            # 2. Fallback to settings if no database accounts configured.
            # A non-numeric TG_API_ID must degrade to "no streaming", never crash startup.
            fallback_aid = None
            if settings.TG_API_ID:
                try:
                    fallback_aid = int(settings.TG_API_ID)
                except (TypeError, ValueError):
                    logger.error("Invalid TG_API_ID in music.env: %r — must be numeric.", settings.TG_API_ID)
            if not tasks and fallback_aid and settings.TG_API_HASH:
                fallback_session = settings.TG_SESSION_STRING
                if fallback_session:
                    logger.info("Starting fallback standalone Telegram client from config...")
                    tasks.append(self._start_single_client(
                        account_id=0,
                        name="Standalone",
                        session_string=fallback_session,
                        api_id=fallback_aid,
                        api_hash=settings.TG_API_HASH,
                        is_primary=True
                    ))

            if not tasks:
                logger.warning("No Telegram accounts available to initialize. Streaming will be unavailable.")
                return

            results = await asyncio.gather(*tasks, return_exceptions=True)
            connected_count = 0
            for idx, res in enumerate(results):
                if isinstance(res, Exception):
                    logger.error("Failed to initialize a Telegram client: %s", res)
                elif res:
                    connected_count += 1

            self._is_started = connected_count > 0
            logger.info("Telegram cluster initialized: %d/%d clients online (Primary: %s).",
                        connected_count, len(tasks), self.primary_id)

    async def _start_single_client(
        self,
        account_id: int,
        name: str,
        session_string: str,
        api_id: int,
        api_hash: str,
        is_primary: bool = False
    ) -> Optional[Client]:
        """Creates and starts an in-memory Pyrogram Client."""
        try:
            client = Client(
                name=f"tmusic_acc_{account_id}",
                session_string=session_string,
                api_id=api_id,
                api_hash=api_hash,
                in_memory=True,
                sleep_threshold=settings.SLEEP_THRESHOLD,
                max_concurrent_transmissions=settings.TG_MAX_CONCURRENT_TRANSMISSIONS,
            )

            await client.start()
            me = await client.get_me()
            if me is None:
                # Started but unusable: stop it here, otherwise the connected
                # client leaks (registered nowhere, stopped never).
                try:
                    await client.stop()
                except Exception:
                    pass
                raise RuntimeError(f"TG Account {account_id}: get_me() returned nothing.")
            username = getattr(me, "username", None) or getattr(me, "first_name", "Account")
            logger.info("Connected TG Account %d ('%s') as @%s (User ID: %s, Primary: %s)",
                        account_id, name, username, me.id, is_primary)

            self.clients[account_id] = client
            if is_primary or self.primary_id is None:
                self.primary_id = account_id

            return client
        except Exception as e:
            logger.error("Failed to start TG Account %d ('%s'): %s", account_id, name, e)
            return None

    async def stop(self):
        """Disconnect and cleanup all Telegram clients."""
        async with self._lock:
            stop_tasks = []
            for aid, client in list(self.clients.items()):
                try:
                    stop_tasks.append(client.stop())
                except Exception as e:
                    logger.warning("Error queuing stop for client %d: %s", aid, e)

            if stop_tasks:
                await asyncio.gather(*stop_tasks, return_exceptions=True)

            self.clients.clear()
            self.primary_id = None
            self._is_started = False
            logger.info("All Telegram cluster clients disconnected.")

    def is_healthy(self, account_id: int) -> bool:
        """Returns True if the account client exists, is connected, and not in FloodWait."""
        client = self.clients.get(account_id)
        if not client or not getattr(client, "is_connected", False):
            return False

        now = time.time()
        cooldown = self._flood_cooldowns.get(account_id, 0.0)
        return cooldown <= now

    def cluster_status(self) -> dict:
        """Public-safe cluster snapshot for the admin/system API. No credentials."""
        now = time.time()
        accounts = []
        for aid, client in self.clients.items():
            cooldown_until = self._flood_cooldowns.get(aid, 0.0)
            in_flood = cooldown_until > now
            connected = bool(getattr(client, "is_connected", False))
            accounts.append({
                "account_id": aid,
                "connected": connected,
                "healthy": connected and not in_flood,
                "is_primary": aid == self.primary_id,
                "flood_wait": in_flood,
                "flood_remaining_sec": max(0, int(cooldown_until - now)) if in_flood else 0,
            })
        return {
            "started": self._is_started,
            "clients_total": len(self.clients),
            "clients_healthy": sum(1 for a in accounts if a["healthy"]),
            "primary_id": self.primary_id,
            "accounts": accounts,
        }

    def mark_flood(self, account_id: int, seconds: int):
        """Records FloodWait cooldown for a specific account."""
        self._flood_cooldowns[account_id] = time.time() + seconds
        logger.warning("TG Account %d entered FloodWait for %ds (until +%ds)", account_id, seconds, seconds)

    async def get_client(self, account_id: Optional[int] = None) -> Optional[Client]:
        """Returns an active, healthy Client instance."""
        if not self.is_started:
            await self.start()

        # If specific account requested and healthy
        if account_id is not None and self.is_healthy(account_id):
            return self.clients[account_id]

        # Try primary
        if self.primary_id is not None and self.is_healthy(self.primary_id):
            return self.clients[self.primary_id]

        # Any healthy client
        for aid, client in self.clients.items():
            if self.is_healthy(aid):
                return client

        # Last resort: return primary client even if on cooldown, or first available
        if self.primary_id is not None and self.primary_id in self.clients:
            return self.clients[self.primary_id]

        if self.clients:
            return next(iter(self.clients.values()))

        return None

    def get_candidate_sources(self, account_map: Optional[Dict], default_msg_ids: Optional[str] = None) -> List[Tuple[int, str]]:
        """
        Extracts list of (account_id, msg_ids_str) tuples from account_map or fallback,
        ordered so healthy accounts come first.
        """
        candidates: List[Tuple[int, str]] = []

        if account_map and isinstance(account_map, dict):
            for aid_key, data in account_map.items():
                try:
                    aid = int(aid_key)
                    if aid not in self.clients:
                        continue
                    if isinstance(data, dict):
                        mids = data.get("msg_ids") or str(data.get("msg_id", ""))
                    else:
                        mids = str(data)
                    if mids:
                        candidates.append((aid, mids))
                except Exception:
                    continue

        # Fallback to primary client or any client with default_msg_ids (only if clients are active)
        if not candidates and default_msg_ids and self.clients:
            target_aid = self.primary_id if self.primary_id in self.clients else next(iter(self.clients.keys()))
            candidates.append((target_aid, default_msg_ids))

        # Sort: healthy accounts first, primary account prioritized among healthy
        def sort_key(item: Tuple[int, str]):
            aid, _ = item
            healthy = 1 if self.is_healthy(aid) else 0
            is_prim = 1 if aid == self.primary_id else 0
            return (healthy, is_prim)

        candidates.sort(key=sort_key, reverse=True)
        return candidates


tg_manager = TelegramManager()
