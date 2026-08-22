"""Small rate-limiting helpers for Ting."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Generic, TypeVar

_T = TypeVar("_T")


class LatestValueThrottle(Generic[_T]):
    """Emit immediately, then coalesce bursts to the latest value."""

    def __init__(self, interval: float, callback: Callable[[_T], None]) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._interval = interval
        self._callback = callback
        self._last_emit: float | None = None
        self._pending: _T | None = None
        self._handle: asyncio.TimerHandle | None = None

    def submit(self, value: _T) -> None:
        """Submit a value for immediate or delayed delivery."""
        loop = asyncio.get_running_loop()
        self._pending = value
        if self._last_emit is None:
            self._emit(loop.time())
            return

        delay = self._interval - (loop.time() - self._last_emit)
        if delay <= 0:
            if self._handle is not None:
                self._handle.cancel()
                self._handle = None
            self._emit(loop.time())
        elif self._handle is None:
            self._handle = loop.call_later(delay, self._emit_scheduled)

    def cancel(self) -> None:
        """Discard a pending value and cancel its timer."""
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None
        self._pending = None

    def _emit_scheduled(self) -> None:
        self._handle = None
        self._emit(asyncio.get_running_loop().time())

    def _emit(self, now: float) -> None:
        value = self._pending
        if value is None:
            return
        self._pending = None
        self._last_emit = now
        self._callback(value)
