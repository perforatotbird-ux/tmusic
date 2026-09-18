"""
T-Music Decryption Service
High-performance streaming AES-256-GCM block decryption with multi-key fallback,
accurate HTTP Range seeking (RFC 7233), and zero-copy buffering.
"""
import struct
import logging
from typing import AsyncGenerator, Optional, Tuple, List
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidTag

from app.core.config import settings

logger = logging.getLogger(__name__)

HEADER_SIZE = 32           # 16 bytes salt + 12 bytes base_nonce + 4 bytes block_size
TAG_SIZE = 16              # GCM auth tag
DEFAULT_BLOCK_SIZE = 1048576  # 1 MiB block size


class DecryptionService:
    """AES-256-GCM streaming decryption engine."""

    @staticmethod
    def parse_header(header_bytes: bytes) -> Tuple[bytes, bytes, int]:
        """Parses the 32-byte header into (salt, base_nonce, block_size)."""
        if len(header_bytes) < HEADER_SIZE:
            raise ValueError(f"Header too short: {len(header_bytes)} < {HEADER_SIZE}")
        salt = header_bytes[0:16]
        base_nonce = header_bytes[16:28]
        block_size = struct.unpack("<I", header_bytes[28:32])[0]
        if block_size <= 0 or block_size > 64 * 1024 * 1024:
            block_size = DEFAULT_BLOCK_SIZE
        return salt, base_nonce, block_size

    @staticmethod
    def derive_file_key(master_key: bytes, file_salt: bytes) -> bytes:
        """Derives per-file AES-256 key via HKDF SHA-256."""
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=file_salt,
            info=b"tgcloud_file_v1",
        )
        return hkdf.derive(master_key)

    @staticmethod
    def encrypted_block_size(block_size: int) -> int:
        return block_size + TAG_SIZE

    @staticmethod
    def plain_to_encrypted_offset(plain_offset: int, block_size: int = DEFAULT_BLOCK_SIZE) -> int:
        """Maps plaintext byte offset to encrypted file offset."""
        block_idx = plain_offset // block_size
        enc_bs = block_size + TAG_SIZE
        return HEADER_SIZE + block_idx * enc_bs

    @staticmethod
    def encrypted_size_to_plain_size(enc_size: Optional[int], block_size: int = DEFAULT_BLOCK_SIZE) -> int:
        """Converts encrypted Telegram file size to original plaintext audio size."""
        if not enc_size or enc_size <= HEADER_SIZE:
            return 0
        payload = enc_size - HEADER_SIZE
        enc_bs = block_size + TAG_SIZE
        num_full_blocks = payload // enc_bs
        remainder = payload % enc_bs
        last_plain = max(0, remainder - TAG_SIZE) if remainder > 0 else 0
        return num_full_blocks * block_size + last_plain

    @staticmethod
    def _block_nonce_v2(base_nonce: bytes, block_index: int) -> bytes:
        nonce_int = (int.from_bytes(base_nonce, "big") + block_index) % (2**96)
        return nonce_int.to_bytes(12, "big")

    @staticmethod
    def _block_nonce_legacy(base_nonce: bytes, block_index: int) -> bytes:
        idx_bytes = block_index.to_bytes(12, "little")
        return bytes(a ^ b for a, b in zip(base_nonce, idx_bytes))

    @classmethod
    def _try_master_keys(
        cls,
        enc_block: bytes,
        base_nonce: bytes,
        block_index: int,
        salt: bytes,
        master_keys: List[bytes],
    ) -> Tuple[bytes, bytes]:
        """Decrypt with the first working master key.

        Returns (plain, file_key). Raises InvalidTag when no key fits —
        single choke point for both the main loop and the trailing block.
        """
        for mk in master_keys:
            cand_fk = cls.derive_file_key(mk, salt)
            try:
                return cls.decrypt_block(enc_block, cand_fk, base_nonce, block_index), cand_fk
            except Exception:
                continue
        logger.error(
            "Failed to decrypt audio block %d with any configured master keys.", block_index)
        raise InvalidTag(
            f"Authentication failed: unable to decrypt audio with available keys at block {block_index}.")

    @staticmethod
    def _apply_limit(plain: bytes, total_yielded: int, plain_limit: Optional[int]) -> Tuple[bytes, int, bool]:
        """Trim a plaintext chunk to plain_limit. Returns (chunk, new_total, done)."""
        if plain_limit is not None:
            remaining = plain_limit - total_yielded
            if len(plain) > remaining:
                plain = plain[:remaining]
        total_yielded += len(plain)
        done = plain_limit is not None and total_yielded >= plain_limit
        return plain, total_yielded, done

    @classmethod
    def decrypt_block(
        cls,
        enc_block: bytes,
        file_key: bytes,
        base_nonce: bytes,
        block_index: int
    ) -> bytes:
        """Decrypts a single block, trying v2 nonce first, then legacy nonce."""
        aesgcm = AESGCM(file_key)
        # 1. Try modern additive v2 nonce
        nonce_v2 = cls._block_nonce_v2(base_nonce, block_index)
        try:
            return aesgcm.decrypt(nonce_v2, enc_block, None)
        except InvalidTag:
            pass

        # 2. Try legacy XOR nonce
        nonce_legacy = cls._block_nonce_legacy(base_nonce, block_index)
        return aesgcm.decrypt(nonce_legacy, enc_block, None)

    @classmethod
    async def decrypt_stream(
        cls,
        raw_stream: AsyncGenerator[bytes, None],
        salt: Optional[bytes] = None,
        base_nonce: Optional[bytes] = None,
        block_size: int = DEFAULT_BLOCK_SIZE,
        start_block: int = 0,
        plain_offset_in_first_block: int = 0,
        plain_limit: Optional[int] = None,
        skip_header: bool = False
    ) -> AsyncGenerator[bytes, None]:
        """
        Streaming block decryptor:
        - Reads chunks from raw_stream.
        - Automatically parses 32-byte header if skip_header is False.
        - Tests candidate master keys from settings on the first block.
        - Emits decrypted audio chunks bounded by plain_limit.
        """
        master_keys = settings.get_master_keys()
        if not master_keys:
            raise RuntimeError("No decryption master keys configured in music.env.")

        buffer = bytearray()
        enc_bs = cls.encrypted_block_size(block_size)
        block_index = start_block
        total_yielded = 0
        header_skipped = skip_header or (start_block > 0 and base_nonce is not None)

        active_file_key: Optional[bytes] = None

        async for chunk in raw_stream:
            buffer.extend(chunk)

            # 1. Parse header if not already consumed
            if not header_skipped:
                if len(buffer) < HEADER_SIZE:
                    continue
                header_data = bytes(buffer[:HEADER_SIZE])
                del buffer[:HEADER_SIZE]
                h_salt, h_nonce, h_bs = cls.parse_header(header_data)
                salt = h_salt
                base_nonce = h_nonce
                block_size = h_bs
                enc_bs = cls.encrypted_block_size(block_size)
                header_skipped = True

            # 2. Decrypt full blocks
            while len(buffer) >= enc_bs:
                enc_block = bytes(buffer[:enc_bs])
                del buffer[:enc_bs]

                # Identify working key if not yet found
                if active_file_key is None:
                    if not salt:
                        raise ValueError("Salt required for key derivation.")
                    plain, active_file_key = cls._try_master_keys(
                        enc_block, base_nonce, block_index, salt, master_keys)
                else:
                    try:
                        plain = cls.decrypt_block(enc_block, active_file_key, base_nonce, block_index)
                    except Exception as e:
                        logger.error("Decryption error at block %d: %s", block_index, e)
                        raise

                # Trim first block if seeking into the middle of a block
                if block_index == start_block and plain_offset_in_first_block > 0:
                    plain = plain[plain_offset_in_first_block:]

                plain, total_yielded, done = cls._apply_limit(plain, total_yielded, plain_limit)
                if plain:
                    yield plain
                if done:
                    return

                block_index += 1

        # 3. Process trailing (last partial) block
        if buffer and len(buffer) > TAG_SIZE and header_skipped and base_nonce:
            if active_file_key is None:
                if not salt:
                    raise ValueError("Salt required for trailing block.")
                plain, active_file_key = cls._try_master_keys(
                    bytes(buffer), base_nonce, block_index, salt, master_keys)
            else:
                plain = cls.decrypt_block(bytes(buffer), active_file_key, base_nonce, block_index)

            if block_index == start_block and plain_offset_in_first_block > 0:
                plain = plain[plain_offset_in_first_block:]

            plain, total_yielded, _done = cls._apply_limit(plain, total_yielded, plain_limit)
            if plain:
                yield plain


decryption_service = DecryptionService()
