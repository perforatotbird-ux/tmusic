"""Keep costly password operations off the event loop, with bounded workers."""
import asyncio
from functools import partial
from weakref import WeakKeyDictionary
import anyio
from app.core.config import settings

_LIMITERS = WeakKeyDictionary()


async def run_password_work(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    limiter = _LIMITERS.get(loop)
    if limiter is None:
        limiter = anyio.CapacityLimiter(settings.PASSWORD_HASH_CONCURRENCY)
        _LIMITERS[loop] = limiter
    return await anyio.to_thread.run_sync(partial(func, *args, **kwargs), limiter=limiter)
