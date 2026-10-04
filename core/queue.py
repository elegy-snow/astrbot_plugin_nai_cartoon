"""A bounded, globally serial draw queue."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class QueueFullError(RuntimeError):
    pass


class DrawQueue:
    def __init__(self, timeout: float = 300, maxsize: int = 20):
        self.timeout = max(1, float(timeout))
        self._max_pending = max(1, int(maxsize))
        self._queue: asyncio.Queue[tuple[Callable[[], Awaitable[Any]], asyncio.Future[Any]]] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._closed = False
            self._worker = asyncio.create_task(self._run(), name="nai-draw-queue")

    async def submit(self, operation: Callable[[], Awaitable[Any]]) -> asyncio.Future[Any]:
        if self._closed:
            raise RuntimeError("draw queue is closed")
        await self.start()
        future = asyncio.get_running_loop().create_future()
        if self._queue.qsize() >= self._max_pending:
            raise QueueFullError("队列已满")
        self._queue.put_nowait((operation, future))
        return future

    async def _run(self) -> None:
        while True:
            operation, future = await self._queue.get()
            try:
                if future.cancelled():
                    continue
                result = await asyncio.wait_for(operation(), timeout=self.timeout)
                if not future.done():
                    future.set_result(result)
            except asyncio.CancelledError:
                if not future.done():
                    future.cancel()
                raise
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
            finally:
                self._queue.task_done()

    async def close(self) -> None:
        self._closed = True
        if self._worker and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        while not self._queue.empty():
            _, future = self._queue.get_nowait()
            if not future.done():
                future.cancel()
            self._queue.task_done()
        self._worker = None
