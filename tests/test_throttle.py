"""Tests for realtime sample coalescing."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.ting.throttle import LatestValueThrottle


def test_throttle_emits_first_and_latest_value() -> None:
    """A burst emits immediately and then emits only its latest value."""

    async def run() -> None:
        emitted: list[int] = []
        throttle = LatestValueThrottle(0.02, emitted.append)

        throttle.submit(1)
        throttle.submit(2)
        throttle.submit(3)

        assert emitted == [1]
        await asyncio.sleep(0.04)
        assert emitted == [1, 3]

    asyncio.run(run())


def test_throttle_cancel_discards_pending_value() -> None:
    """Cancellation prevents a delayed value from reaching Home Assistant."""

    async def run() -> None:
        emitted: list[int] = []
        throttle = LatestValueThrottle(0.02, emitted.append)

        throttle.submit(1)
        throttle.submit(2)
        throttle.cancel()
        throttle.submit(3)

        await asyncio.sleep(0.04)
        assert emitted == [1, 3]

    asyncio.run(run())


def test_throttle_rejects_nonpositive_interval() -> None:
    """Invalid intervals fail during setup."""
    with pytest.raises(ValueError, match="positive"):
        LatestValueThrottle(0, lambda value: None)
