"""
T-Music Fast Audio Streamer
Streams audio chunks directly from the Telegram MTProto multi-account cluster.
Supports HTTP Range requests (RFC 7233), byte-accurate seeking, and True Mid-Stream Failover.
"""
import logging
import asyncio
from typing import AsyncGenerator, Optional, List, Tuple, Union, Dict, Any

from pyrogram import errors
from app.core.config import settings
from app.tg.manager import tg_manager
from app.utils.msg_ids import parse_msg_ids

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1048576  # 1 MiB chunk size for Pyrogram stream_media seek


class FastStreamer:
    """High-speed streaming engine for audio playback with multi-account failover."""

    def __init__(self):
        # Cache for Telegram message sizes: (account_id, msg_id) -> size_in_bytes
        self._media_size_cache: Dict[Tuple[int, int], int] = {}

    async def stream_file(
        self,
        source: Union[Any, List[int], int, Dict],
        offset: int = 0,
        limit: Optional[int] = None
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream multi-chunk audio file sequentially from Telegram cluster.
        Source can be a VFile instance, dict with account_map/msg_ids, list of message IDs, or single ID.
        """
        # Ensure Telegram cluster clients are connected
        if not tg_manager.is_started:
            await tg_manager.start()

        # 1. Resolve candidates: List of (account_id, msg_ids_str)
        account_map = getattr(source, "account_map", None)
        if not account_map and isinstance(source, dict):
            account_map = source.get("account_map")

        default_msg_ids = None
        if hasattr(source, "msg_ids") and source.msg_ids:
            default_msg_ids = str(source.msg_ids)
        elif isinstance(source, dict) and source.get("msg_ids"):
            default_msg_ids = str(source["msg_ids"])
        elif hasattr(source, "tg_message_id") and source.tg_message_id:
            default_msg_ids = str(source.tg_message_id)
        elif isinstance(source, dict) and source.get("tg_message_id"):
            default_msg_ids = str(source["tg_message_id"])
        elif isinstance(source, list):
            default_msg_ids = ",".join(str(m) for m in source)
        elif isinstance(source, int):
            default_msg_ids = str(source)
        elif isinstance(source, str):
            default_msg_ids = source

        candidates = tg_manager.get_candidate_sources(account_map, default_msg_ids)

        if not candidates:
            # Retry start once if clients were somehow empty
            await tg_manager.start()
            candidates = tg_manager.get_candidate_sources(account_map, default_msg_ids)

        if not candidates:
            logger.error("No streaming candidates found. account_map=%s, default_msg_ids=%s, active_clients=%s",
                         account_map, default_msg_ids, list(tg_manager.clients.keys()))
            raise RuntimeError("Telegram client is not available or in FloodWait.")

        # 2. Build logical parts: for multi-part files, each part may be on multiple accounts
        account_mids_map: Dict[int, List[int]] = {}
        max_parts = 0
        for aid, mids_str in candidates:
            parsed = parse_msg_ids(mids_str)
            if parsed:
                account_mids_map[aid] = parsed
                max_parts = max(max_parts, len(parsed))

        if max_parts == 0:
            raise ValueError("No valid Telegram message IDs found for audio stream.")

        logical_parts: List[List[Tuple[int, int]]] = []
        for part_idx in range(max_parts):
            sources_for_part: List[Tuple[int, int]] = []
            for aid, mids_list in account_mids_map.items():
                if part_idx < len(mids_list):
                    sources_for_part.append((aid, mids_list[part_idx]))
            logical_parts.append(sources_part if (sources_part := sources_for_part) else [])

        # 3. Fetch metadata (file sizes) for each logical part
        segment_map: List[Dict[str, Any]] = []
        global_cursor = 0

        for p_idx, sources in enumerate(logical_parts):
            s_size = None

            # Check cache first
            for aid, mid in sources:
                if (aid, mid) in self._media_size_cache:
                    s_size = self._media_size_cache[(aid, mid)]
                    break

            if s_size is None:
                for aid, mid in sources:
                    try:
                        client = await tg_manager.get_client(aid)
                        if not client:
                            continue
                        m = await asyncio.wait_for(client.get_messages("me", mid), timeout=10)
                        if m and not m.empty:
                            media = m.document or m.audio or m.video or m.photo
                            s_size = getattr(media, "file_size", 0)
                            self._media_size_cache[(aid, mid)] = s_size
                            break
                    except errors.FloodWait as fw:
                        tg_manager.mark_flood(aid, fw.value)
                        logger.warning("Account %d FloodWait (%ds) during metadata fetch, switching mirror.", aid, fw.value)
                    except Exception as e:
                        logger.warning("Fetch metadata failed for part %d acc %d (mid=%d): %s", p_idx, aid, mid, e)
                        continue

            if s_size is None:
                logger.error("Could not fetch metadata for audio part %d from any Telegram account.", p_idx)
                raise RuntimeError(f"Failed to fetch Telegram metadata for audio part {p_idx}")

            segment_map.append({
                "sources": sources,
                "size": s_size,
                "start": global_cursor,
            })
            global_cursor += s_size

        # 4. Determine starting segment based on byte offset
        current_offset = offset
        start_idx = 0
        for i, seg in enumerate(segment_map):
            if current_offset < seg["size"]:
                start_idx = i
                break
            else:
                current_offset -= seg["size"]
                start_idx = i + 1

        yielded_total = 0

        # 5. Stream segments with True Mid-Stream Failover
        for i in range(start_idx, len(segment_map)):
            seg = segment_map[i]
            target_seg_offset = current_offset if i == start_idx else 0
            segment_remaining = seg["size"] - target_seg_offset

            while segment_remaining > 0:
                if limit is not None and yielded_total >= limit:
                    return

                chunk_index = target_seg_offset // CHUNK_SIZE
                extra_skip = target_seg_offset % CHUNK_SIZE

                active_aid = None
                stream_gen = None
                first_chunk = None

                # Try sources: prioritize healthy accounts, randomize among healthy for load balancing
                candidate_sources = list(seg["sources"])
                candidate_sources.sort(key=lambda s: 1 if tg_manager.is_healthy(s[0]) else 0, reverse=True)

                for aid, mid in candidate_sources:
                    try:
                        client = await tg_manager.get_client(aid)
                        if not client:
                            continue

                        msg_obj = await asyncio.wait_for(client.get_messages("me", mid), timeout=10)
                        if not msg_obj or msg_obj.empty:
                            continue

                        if chunk_index > 0:
                            temp_gen = client.stream_media(message=msg_obj, offset=chunk_index)
                        else:
                            temp_gen = client.stream_media(message=msg_obj)

                        # Pre-fetch first chunk to verify connection and catch FloodWait early
                        first_chunk = await temp_gen.__anext__()
                        active_aid = aid
                        stream_gen = temp_gen
                        break
                    except StopAsyncIteration:
                        if target_seg_offset >= seg["size"]:
                            active_aid = aid
                            break
                        continue
                    except errors.FloodWait as fw:
                        tg_manager.mark_flood(aid, fw.value)
                        logger.warning("Stream failover: Acc %d FloodWait %ds. Trying mirror account...", aid, fw.value)
                        continue
                    except Exception as e:
                        logger.warning("Stream attempt on Acc %d (mid=%d) failed: %s", aid, mid, e)
                        continue

                if not active_aid or (stream_gen is None and first_chunk is None):
                    logger.error("Audio part %d failed on ALL available Telegram accounts at offset %d.", i, target_seg_offset)
                    raise RuntimeError(f"Audio stream failed on all Telegram mirrors for part {i}")

                if first_chunk is None:
                    break

                # Stream the chunks from active_aid
                try:
                    chunk = first_chunk
                    if extra_skip > 0:
                        if extra_skip >= len(chunk):
                            extra_skip -= len(chunk)
                            chunk = b""
                        else:
                            chunk = chunk[extra_skip:]
                            extra_skip = 0

                    if chunk:
                        chunk_len = len(chunk)
                        if limit is not None:
                            remaining = limit - yielded_total
                            if chunk_len >= remaining:
                                if remaining > 0:
                                    yield chunk[:remaining]
                                    yielded_total += remaining
                                return
                        yield chunk
                        yielded_total += chunk_len
                        segment_remaining -= chunk_len
                        target_seg_offset += chunk_len

                    async for chunk in stream_gen:
                        if extra_skip > 0:
                            if extra_skip >= len(chunk):
                                extra_skip -= len(chunk)
                                continue
                            else:
                                chunk = chunk[extra_skip:]
                                extra_skip = 0

                        if chunk:
                            chunk_len = len(chunk)
                            if limit is not None:
                                remaining = limit - yielded_total
                                if chunk_len >= remaining:
                                    if remaining > 0:
                                        yield chunk[:remaining]
                                        yielded_total += remaining
                                    return
                            yield chunk
                            yielded_total += chunk_len
                            segment_remaining -= chunk_len
                            target_seg_offset += chunk_len

                except errors.FloodWait as fw:
                    tg_manager.mark_flood(active_aid, fw.value)
                    logger.warning("Mid-stream FloodWait on Acc %d (%ds). Retrying offset %d on mirror...",
                                   active_aid, fw.value, target_seg_offset)
                except Exception as e:
                    logger.warning("Acc %d disconnected during audio stream: %s. Retrying on mirror...", active_aid, e)
                finally:
                    if stream_gen and hasattr(stream_gen, "aclose"):
                        try:
                            await stream_gen.aclose()
                        except Exception:
                            pass


fast_streamer = FastStreamer()
