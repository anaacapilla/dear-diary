"""A single FIFO worker. Durable entry data lives in SQLite, not in this queue."""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Job:
    key: str
    run: Callable[[], Awaitable[None]]


class ProcessingQueue:
    def __init__(self):
        self._queue = asyncio.Queue()
        self._keys = set()
        self._task = None
        self._closing = False

    @property
    def pending_count(self):
        """Includes the entry currently being processed."""
        return len(self._keys)

    def contains(self, key):
        return key in self._keys

    def enqueue(self, key, run):
        if self._closing:
            raise RuntimeError("La cola se está cerrando")
        if key in self._keys:
            return False
        self._keys.add(key)
        self._queue.put_nowait(Job(key, run))
        return True

    def start(self):
        if self._closing:
            raise RuntimeError("La cola está cerrada")
        if self._task is None:
            # Owned explicitly by post_stop, not Application.create_task:
            # Application.stop must not wait forever for an idle worker.
            self._task = asyncio.create_task(self._work(), name="diary-audio-worker")

    async def _work(self):
        while True:
            job = await self._queue.get()
            try:
                if job is None:
                    return
                try:
                    await job.run()
                except Exception as error:
                    # Never log exception bodies, which can contain diary content.
                    logger.error("Trabajo de la cola fallido: %s", type(error).__name__)
            finally:
                if job is not None:
                    self._keys.discard(job.key)
                self._queue.task_done()

    async def join(self):
        await self._queue.join()

    async def stop(self):
        if not self._closing:
            self._closing = True
            # Leave unstarted entries in SQLite for recovery on the next startup.
            # Do not cancel inference running in asyncio.to_thread.
            while True:
                try:
                    job = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if job is not None:
                    self._keys.discard(job.key)
                self._queue.task_done()
            if self._task is not None:
                self._queue.put_nowait(None)
        if self._task is not None:
            await self._task
