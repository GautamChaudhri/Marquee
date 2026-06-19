"""Process-wide gate for request-path media-binary work.

The API runs single-worker uvicorn (one event loop) over a single disk, so
preview/detect/inspect operations that shell out to ffmpeg/ffprobe must not
dogpile: a 4K HDR ``signalstats`` decode is slow, and a dozen firing at once
(two preview ``<img>`` loads × six brightness probes, plus a batch detect, plus
a running encode) saturate the disk and starve each other into timeouts.

Every such operation is offloaded with :func:`gated`, which both bounds
concurrency (``LETTERBOX_FFMPEG_CONCURRENCY``) and runs the blocking call in a
worker thread so it never blocks the event loop. The encode itself is *not*
gated here — it's already serialized to one by the media-job worker loop, and
it uses async ``create_subprocess_exec`` directly. See [[letterbox-pipeline-concurrency]].
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from marquee.config import settings

_semaphore: asyncio.Semaphore | None = None


def _gate() -> asyncio.Semaphore:
    """Lazily build the semaphore on first use (needs a running event loop)."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, settings.LETTERBOX_FFMPEG_CONCURRENCY))
    return _semaphore


async def gated[T](fn: Callable[..., T], /, *args, **kwargs) -> T:
    """Run blocking *fn* in a worker thread, bounded by the ffmpeg gate.

    Acquires the shared semaphore before offloading so at most
    ``LETTERBOX_FFMPEG_CONCURRENCY`` of these run concurrently; the rest queue.
    Use for every request-path call that shells out to ffmpeg/ffprobe.
    """
    async with _gate():
        return await asyncio.to_thread(fn, *args, **kwargs)
