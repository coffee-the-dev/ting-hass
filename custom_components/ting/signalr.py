"""SignalR websocket client for Ting realtime data."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
import logging
from typing import Any

from pysignalr.client import SignalRClient
from pysignalr.messages import CompletionMessage
from pysignalr.protocol.messagepack import MessagepackProtocol

from .auth import TingAuth
from .const import TING_COMBO_BINARY_DATA, TING_SIGNALR_WS_URL

_LOGGER = logging.getLogger(__name__)

TingRealtimeCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class TingSignalRClient:
    """SignalR client for Ting's realtime hub."""

    def __init__(
        self,
        auth: TingAuth,
        *,
        station_id: str,
        callback: TingRealtimeCallback,
    ) -> None:
        self._auth = auth
        self._station_id = station_id
        self._callback = callback
        self._stopped = asyncio.Event()
        self._client: SignalRClient | None = None

    async def async_run(self) -> None:
        """Run until stopped, reconnecting after transient failures."""
        backoff = 1
        while not self._stopped.is_set():
            try:
                await self._run_once()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep realtime loop alive
                if self._stopped.is_set():
                    return
                _LOGGER.debug("Ting SignalR connection failed for %s: %s", self._station_id, err)
                await _sleep_or_stop(self._stopped, backoff)
                backoff = min(backoff * 2, 60)

    async def async_stop(self) -> None:
        """Request the SignalR loop to stop."""
        self._stopped.set()
        client = self._client
        if client is not None:
            with suppress(Exception):
                await client.send(
                    "UnInitializeStreaming",
                    [self._station_data(), self._auth.api_key, self._auth.user_id],
                )
        transport = getattr(client, "_transport", None)
        ws = getattr(transport, "_ws", None)
        if ws is not None:
            await ws.close()

    async def _run_once(self) -> None:
        await self._auth.async_ensure_tokens()
        client = SignalRClient(
            url=TING_SIGNALR_WS_URL,
            protocol=MessagepackProtocol(),
            headers={
                "Origin": "ionic://localhost",
                "User-Agent": "Home Assistant Ting Integration",
            },
            ping_interval=30,
            retry_count=1,
        )
        # Ting's app connects websocket-only with SignalR skipNegotiation.
        # pysignalr supports this at the transport layer but not its public constructor.
        client._transport._skip_negotiation = True  # noqa: SLF001
        self._client = client

        async def on_open() -> None:
            await client.send(
                "InitializeStreaming",
                [self._station_data(), self._auth.api_key, self._auth.user_id],
                on_invocation=_log_completion,
            )

        async def on_update(arguments: list[Any]) -> None:
            if not arguments:
                return
            data = arguments[0]
            if not isinstance(data, Mapping):
                return
            await self._handle_combo_binary_data(data)

        client.on_open(on_open)
        client.on("updateComboBinaryData", on_update)
        client.on_error(_log_completion)

        try:
            await client.run()
        finally:
            self._client = None

    def _station_data(self) -> dict[str, str]:
        return {
            "StationId": self._station_id,
            "DataElement": TING_COMBO_BINARY_DATA,
        }

    async def _handle_combo_binary_data(self, data: Mapping[str, Any]) -> None:
        parsed = {
            "last_update": data.get("DataTimeUtc"),
            "voltage": _number(data.get("Voltage")),
            "voltage_high": _number(data.get("VoltageHi")),
            "voltage_low": _number(data.get("VoltageLo")),
            "hifi": _number(data.get("AveragePeaksMax")),
            "raw": dict(data),
        }
        result = self._callback(parsed)
        if asyncio.iscoroutine(result):
            await result


async def _log_completion(message: CompletionMessage) -> None:
    if message.error:
        _LOGGER.debug("Ting SignalR invocation failed: %s", message.error)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _sleep_or_stop(stop_event: asyncio.Event, delay: int) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except TimeoutError:
        return
