"""
Block-based AES-256-GCM encryption for CloudByte.

File format:
  [16-byte salt][12-byte base_nonce][4-byte block_size_le]  = 32 bytes header
  [block_0 ciphertext (up to block_size)][16-byte tag]
  [block_1 ciphertext (up to block_size)][16-byte tag]
  ...

Each block is encrypted independently.
"""

import os
import struct
import hashlib
import base64
import asyncio
import logging
from typing import AsyncGenerator, Optional, Tuple, Callable, Union, List

# T-018: Move cryptography imports to top-level for performance and clarity
import argon2.low_level
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

logger = logging.getLogger(__name__)

HEADER_SIZE = 32          # 16 salt + 12 nonce + 4 block_size
TAG_SIZE = 16             # GCM auth tag
DEFAULT_BLOCK_SIZE = 1024 * 1024  # 1 MB
PBKDF2_ITERATIONS = 100_000


# ─── Key Derivation ──────────────────────────────────────────────

def derive_key(password: str, salt: bytes) -> bytes:
    """Derive master key from password + salt via Argon2id (NEW DEFAULT)."""
    return argon2.low_level.hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=3,
        memory_cost=65536,
        parallelism=4,
        hash_len=32,
        type=argon2.low_level.Type.ID
    )

def derive_key_fallback(password: str, salt: bytes) -> bytes:
    """Derive master key from password + salt via PBKDF2-SHA256 (LEGACY)."""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)

def derive_file_key(master_key: Union[bytes, bytearray], file_salt: bytes) -> bytes:
    """Derive a unique per-file AES key using HKDF from the master key and the file's salt."""
    if not master_key:
        raise ValueError("Cannot derive file key: master_key is None")
    
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=file_salt,
        info=b"tgcloud_file_v1",
    )
    return hkdf.derive(bytes(master_key))

def wrap_master_key(password: str, master_salt: bytes, master_key: bytes) -> str:
    """Encrypt the Master Key using a Key Encryption Key (KEK) derived from the password."""
    kek = derive_key(password, master_salt)
    aesgcm = AESGCM(kek)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, master_key, None)
    return base64.b64encode(nonce + ciphertext).decode('ascii')

def unwrap_master_key(password: str, master_salt: bytes, wrapped_b64: str) -> bytes:
    """Decrypt the Master Key using the password-derived KEK, with fallback to legacy PBKDF2."""
    data = base64.b64decode(wrapped_b64)
    nonce = data[:12]
    ciphertext = data[12:]

    # Try Argon2id first
    kek = derive_key(password, master_salt)
    aesgcm = AESGCM(kek)
    try:
        return aesgcm.decrypt(nonce, ciphertext, None)
    except InvalidTag:
        logger.debug("unwrap_master_key: Argon2id decryption failed, trying PBKDF2")
        pass

    # Try PBKDF2 fallback
    kek_old = derive_key_fallback(password, master_salt)
    aesgcm_old = AESGCM(kek_old)
    return aesgcm_old.decrypt(nonce, ciphertext, None)

def generate_salt() -> bytes:
    return os.urandom(16)


def generate_nonce() -> bytes:
    return os.urandom(12)


# ─── Password Hashing (for storing in config) ────────────────────

def hash_enc_password(password: str) -> str:
    """Hash encryption password for verification."""
    salt = os.urandom(16)
    h = argon2.low_level.hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=3,
        memory_cost=65536,
        parallelism=4,
        hash_len=32,
        type=argon2.low_level.Type.ID
    )
    return salt.hex() + ":" + h.hex()


def verify_enc_password(password: str, stored: str) -> bool:
    """Verify password against stored hash with fallback to PBKDF2."""
    if not stored or ":" not in stored:
        logger.error("verify_enc_password: Empty or invalid stored hash")
        return False
    try:
        salt_hex, hash_hex = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        
        try:
            actual_argon2 = argon2.low_level.hash_secret_raw(
                secret=password.encode("utf-8"),
                salt=salt,
                time_cost=3,
                memory_cost=65536,
                parallelism=4,
                hash_len=32,
                type=argon2.low_level.Type.ID
            )
            if actual_argon2 == expected:
                return True
        except Exception as ae:
            logger.debug(f"verify_enc_password: Argon2 verify failed: {ae}")
            
        # Fallback to PBKDF2
        actual_pbkdf2 = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
        if actual_pbkdf2 == expected:
            return True
            
        logger.warning("verify_enc_password: All verification methods failed (wrong password)")
        return False
    except Exception as e:
        logger.error(f"verify_enc_password: Fatal error during verification: {e}")
        return False


# ─── Recovery Key ─────────────────────────────────────────────────

def export_recovery_key(key: bytes) -> str:
    """Export raw AES key as base64 string for recovery file."""
    return base64.b64encode(bytes(key)).decode("ascii")


def import_recovery_key(b64_key: str) -> bytes:
    """Import raw AES key from base64 recovery file."""
    return base64.b64decode(b64_key)


# ─── Nonce Computation ────────────────────────────────────────────

def _block_nonce_v2(base_nonce: bytes, block_index: int) -> bytes:
    """Compute per-block nonce: Additive instead of XOR for better security."""
    nonce_int = int.from_bytes(base_nonce, "big")
    nonce_int = (nonce_int + block_index) % (2**96)
    return nonce_int.to_bytes(12, "big")

def _block_nonce_legacy(base_nonce: bytes, block_index: int) -> bytes:
    """Compute per-block nonce: base_nonce XOR block_index (little-endian)."""
    idx_bytes = block_index.to_bytes(12, "little")
    return bytes(a ^ b for a, b in zip(base_nonce, idx_bytes))


# Default nonce function for new files
_block_nonce = _block_nonce_v2

# ─── File Encryption ──────────────────────────────────────────────

def encrypt_file(input_path: str, output_path: str, key: bytes,
                 block_size: int = DEFAULT_BLOCK_SIZE,
                 progress_cb: Optional[Callable] = None) -> Tuple[bytes, bytes, bytes]:
    """
    Encrypt a file using block-based AES-256-GCM (Disk to Disk).
    Returns (salt, base_nonce, full_header).
    """
    salt = generate_salt()
    base_nonce = generate_nonce()
    
    file_key = derive_file_key(key, salt)
    aesgcm = AESGCM(file_key)

    fs = os.path.getsize(input_path)
    processed = 0

    with open(input_path, "rb") as fin, open(output_path, "wb") as fout:
        header = salt + base_nonce + struct.pack("<I", block_size)
        assert len(header) == HEADER_SIZE
        fout.write(header)

        block_index = 0
        while True:
            plaintext = fin.read(block_size)
            if not plaintext:
                break

            nonce = _block_nonce(base_nonce, block_index)
            ciphertext = aesgcm.encrypt(nonce, plaintext, None)
            fout.write(ciphertext)

            block_index += 1
            processed += len(plaintext)
            if progress_cb and block_index % 10 == 0:
                if asyncio.iscoroutinefunction(progress_cb):
                    asyncio.create_task(progress_cb(processed, fs))
                else:
                    progress_cb(processed, fs)

        if progress_cb:
            if asyncio.iscoroutinefunction(progress_cb):
                asyncio.create_task(progress_cb(fs, fs))
            else:
                progress_cb(fs, fs)

    logger.info(f"Encrypted {input_path} -> {output_path} ({block_index} blocks)")
    return salt, base_nonce, header

def encrypt_and_split_file(input_path: str, output_prefix: str, chunk_limit: int, key: bytes,
                           block_size: int = DEFAULT_BLOCK_SIZE,
                           progress_cb: Optional[Callable] = None) -> Tuple[bytes, bytes, bytes, List[str]]:
    """
    Encrypts a file and splits it into multiple parts on-the-fly to save disk space.
    Returns (salt, base_nonce, full_header, parts_paths).
    """
    salt = generate_salt()
    base_nonce = generate_nonce()
    
    file_key = derive_file_key(key, salt)
    aesgcm = AESGCM(file_key)

    fs = os.path.getsize(input_path)
    processed = 0

    header = salt + base_nonce + struct.pack("<I", block_size)
    assert len(header) == HEADER_SIZE

    part_index = 0
    parts_paths = []
    
    fin = open(input_path, "rb")
    try:
        block_index = 0
        eof_reached = False
        while not eof_reached:
            part_path = f"{output_prefix}.part{part_index}"
            parts_paths.append(part_path)
            with open(part_path, "wb") as fout:
                written_in_part = 0
                if part_index == 0:
                    fout.write(header)
                    written_in_part += HEADER_SIZE

                while written_in_part < chunk_limit:
                    plaintext = fin.read(block_size)
                    if not plaintext:
                        eof_reached = True
                        break

                    nonce = _block_nonce(base_nonce, block_index)
                    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
                    fout.write(ciphertext)

                    block_index += 1
                    written_in_part += len(ciphertext)
                    processed += len(plaintext)
                    
                    if progress_cb and block_index % 10 == 0:
                        if asyncio.iscoroutinefunction(progress_cb):
                            asyncio.create_task(progress_cb(processed, fs))
                        else:
                            progress_cb(processed, fs)
            part_index += 1
    finally:
        fin.close()

    if progress_cb:
        if asyncio.iscoroutinefunction(progress_cb):
            asyncio.create_task(progress_cb(fs, fs))
        else:
            progress_cb(fs, fs)

    logger.info(f"Encrypted and split {input_path} -> {len(parts_paths)} parts ({block_index} blocks)")
    return salt, base_nonce, header, parts_paths


def encrypt_memory_to_file(data: bytes, output_path: str, key: bytes, block_size: int = DEFAULT_BLOCK_SIZE) -> Tuple[bytes, bytes, bytes]:
    """
    Encrypt data from memory directly to a file using block-based AES-256-GCM.
    Returns (salt, base_nonce, full_header).
    """
    salt = generate_salt()
    base_nonce = generate_nonce()
    
    file_key = derive_file_key(key, salt)
    aesgcm = AESGCM(file_key)

    fs = len(data)

    with open(output_path, "wb") as fout:
        header = salt + base_nonce + struct.pack("<I", block_size)
        assert len(header) == HEADER_SIZE
        fout.write(header)

        block_index = 0
        offset = 0
        while offset < fs:
            plaintext = data[offset:offset + block_size]
            nonce = _block_nonce(base_nonce, block_index)
            ciphertext = aesgcm.encrypt(nonce, plaintext, None)
            fout.write(ciphertext)
            
            offset += block_size
            block_index += 1

    logger.info(f"Encrypted IN-MEMORY buffer -> {output_path} ({block_index} blocks)")
    return salt, base_nonce, header


# ─── Header Parsing ───────────────────────────────────────────────

def parse_header(header_bytes: bytes) -> Tuple[bytes, bytes, int]:
    if len(header_bytes) < HEADER_SIZE:
        raise ValueError(f"Header too short: {len(header_bytes)} < {HEADER_SIZE}")

    salt = header_bytes[0:16]
    base_nonce = header_bytes[16:28]
    block_size = struct.unpack("<I", header_bytes[28:32])[0]
    return salt, base_nonce, block_size


# ─── Offset Mapping ──────────────────────────────────────────────

def encrypted_block_size(block_size: int) -> int:
    return block_size + TAG_SIZE


def plain_to_encrypted_offset(plain_offset: int, block_size: int) -> int:
    block_idx = plain_offset // block_size
    enc_bs = encrypted_block_size(block_size)
    return HEADER_SIZE + block_idx * enc_bs


def plain_size_to_encrypted_size(plain_size: int, block_size: int) -> int:
    if plain_size == 0:
        return HEADER_SIZE
    num_blocks = (plain_size + block_size - 1) // block_size
    enc_bs = encrypted_block_size(block_size)
    return HEADER_SIZE + num_blocks * enc_bs


def encrypted_size_to_plain_size(enc_size: int, block_size: int) -> int:
    if enc_size <= HEADER_SIZE:
        return 0
    payload = enc_size - HEADER_SIZE
    enc_bs = encrypted_block_size(block_size)
    num_full_blocks = payload // enc_bs
    remainder = payload % enc_bs
    last_plain = max(0, remainder - TAG_SIZE) if remainder > 0 else 0
    return num_full_blocks * block_size + last_plain


# ─── Block Decryption (sync, for single blocks) ──────────────────

def decrypt_block(encrypted_block: bytes, key: bytes, base_nonce: bytes, block_index: int, legacy_nonce: bool = False) -> bytes:
    nonce = _block_nonce_legacy(base_nonce, block_index) if legacy_nonce else _block_nonce_v2(base_nonce, block_index)
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, encrypted_block, None)


# ─── Streaming Decryption (for download) ─────────────────────────

async def decrypt_stream(
    encrypted_data_gen: AsyncGenerator[bytes, None],
    key: bytes,
    base_nonce: bytes,
    block_size: int,
    skip_header: bool = False,
    start_block: int = 0,
    plain_offset_in_first_block: int = 0,
    plain_limit: Optional[int] = None,
    legacy_nonce: bool = False
) -> AsyncGenerator[bytes, None]:
    if not key:
        raise ValueError("Decryption error: key is None")
        
    aesgcm = AESGCM(key)
    enc_bs = encrypted_block_size(block_size)

    buffer = bytearray()
    block_index = start_block
    total_yielded = 0
    header_skipped = skip_header or start_block > 0

    async for chunk in encrypted_data_gen:
        buffer.extend(chunk)

        if not header_skipped:
            if len(buffer) >= HEADER_SIZE:
                del buffer[:HEADER_SIZE]
                header_skipped = True
            else:
                continue

        while len(buffer) >= enc_bs:
            enc_block = bytes(buffer[:enc_bs])
            del buffer[:enc_bs]

            nonce = _block_nonce_legacy(base_nonce, block_index) if legacy_nonce else _block_nonce_v2(base_nonce, block_index)
            try:
                plain = aesgcm.decrypt(nonce, enc_block, None)
            except Exception as e:
                if not legacy_nonce:
                    # Retry with legacy nonce as fallback
                    try:
                        nonce_leg = _block_nonce_legacy(base_nonce, block_index)
                        plain = aesgcm.decrypt(nonce_leg, enc_block, None)
                    except Exception:
                        logger.error(f"Decryption failed at block {block_index} (both v2 and legacy): {e}")
                        raise
                else:
                    logger.error(f"Decryption failed at block {block_index}: {e}")
                    raise

            if block_index == start_block and plain_offset_in_first_block > 0:
                plain = plain[plain_offset_in_first_block:]

            if plain_limit is not None:
                remaining = plain_limit - total_yielded
                if len(plain) > remaining:
                    plain = plain[:remaining]

            if plain:
                yield plain
                total_yielded += len(plain)

            if plain_limit is not None and total_yielded >= plain_limit:
                return

            block_index += 1

    if buffer and len(buffer) > TAG_SIZE:
        # T-040: Critical safeguard - if we haven't even skipped the header, this isn't data.
        if not header_skipped:
            return

        nonce = _block_nonce_legacy(base_nonce, block_index) if legacy_nonce else _block_nonce_v2(base_nonce, block_index)
        try:
            plain = aesgcm.decrypt(nonce, bytes(buffer), None)
        except Exception as e:
            if not legacy_nonce:
                try:
                    nonce_leg = _block_nonce_legacy(base_nonce, block_index)
                    plain = aesgcm.decrypt(nonce_leg, bytes(buffer), None)
                except Exception:
                    logger.error(f"Decryption failed at last block {block_index} (both v2 and legacy): {e}")
                    raise
            else:
                logger.error(f"Decryption failed at last block {block_index}: {e}")
                raise

        if block_index == start_block and plain_offset_in_first_block > 0:
            plain = plain[plain_offset_in_first_block:]

        if plain_limit is not None:
            remaining = plain_limit - total_yielded
            if len(plain) > remaining:
                plain = plain[:remaining]

        if plain:
            yield plain

async def decrypt_chunk_stream(
    encrypted_gen: AsyncGenerator[bytes, None],
    master_key: bytes,
    salt_from_db: Optional[bytes] = None
) -> AsyncGenerator[bytes, None]:
    """
    High-level wrapper for generate_file_stream to handle encrypted data.
    Automatically parses header from the first chunk to get nonce and block_size.
    """
    if not master_key:
        logger.error("decrypt_chunk_stream: master_key is None")
        raise ValueError("Encryption vault is locked.")

    buffer = bytearray()
    header_data = None
    
    async for chunk in encrypted_gen:
        if header_data is None:
            buffer.extend(chunk)
            if len(buffer) >= HEADER_SIZE:
                header_data = bytes(buffer[:HEADER_SIZE])
                del buffer[:HEADER_SIZE]
                
                # Parse header
                h_salt, base_nonce, block_size = parse_header(header_data)
                
                # Use salt from header or DB (they should match)
                salt = h_salt or salt_from_db
                file_key = derive_file_key(master_key, salt)
                
                logger.debug(f"[DECRYPT] Header parsed: salt={salt.hex() if salt else 'None'}, block_size={block_size}")

                # Create a generator for the rest of the stream
                async def remaining_gen():
                    if buffer:
                        yield bytes(buffer)
                        buffer.clear()
                    async for c in encrypted_gen:
                        yield c
                
                # Delegate to decrypt_stream
                async for plain in decrypt_stream(remaining_gen(), file_key, base_nonce, block_size, skip_header=True):
                    yield plain
                return
        else:
            yield chunk

# ─── Session Key Store (in-memory, per-process) ──────────────────

_session_key: Optional[bytearray] = None


def set_session_key(key: Union[bytes, bytearray, None]):
    """Securely store the master key in memory inside a bytearray."""
    global _session_key
    
    if not key:
        clear_session_key()
        return

    new_key = bytearray(key)
    
    # Safely clear old key ONLY IF it is a different object in memory
    if _session_key is not None and id(_session_key) != id(key):
        clear_session_key()
        
    _session_key = new_key
    if _session_key:
        logger.info(f"Session key updated (hash: {hashlib.sha256(_session_key).hexdigest()[:8]})")


def get_session_key() -> Optional[bytes]:
    """Return an immutable bytes copy of the master key so callers cannot mutate the stored key in place."""
    if _session_key is None:
        return None
    return bytes(_session_key)


def clear_session_key():
    """Securely zero-out the key in memory before discarding."""
    global _session_key
    if _session_key is not None:
        # Surgical zeroing
        for i in range(len(_session_key)):
            _session_key[i] = 0
        _session_key = None
        logger.debug("Session key securely zeroed-out.")


def is_unlocked() -> bool:
    return _session_key is not None
