"""SignalR websocket client for Ting realtime data."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from datetime import datetime, timezone
import logging
import time
from typing import Any

from pysignalr.client import SignalRClient
from pysignalr.messages import CompletionMessage
from pysignalr.protocol.messagepack import MessagepackProtocol

from .auth import TingAuth
from .const import TING_COMBO_BINARY_DATA, TING_SIGNALR_WS_URL
from .exceptions import TingStaleDataError

_LOGGER = logging.getLogger(__name__)

TingRealtimeCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
TingStaleCallback = Callable[[Exception], Awaitable[None] | None]

# Ting streams roughly one sample per second. If nothing arrives for this many
# seconds the subscription is considered dead even when the websocket still
# answers pings, and the client tears down and reconnects from scratch.
STALE_DATA_TIMEOUT = 120.0
# How often the watchdog checks for staleness.
WATCHDOG_INTERVAL = 15.0
# Cap for exponential reconnect backoff.
MAX_BACKOFF = 60


class TingSignalRClient:
    """SignalR client for Ting's realtime hub."""

    def __init__(
        self,
        auth: TingAuth,
        *,
        station_id: str,
        callback: TingRealtimeCallback,
        stale_callback: TingStaleCallback | None = None,
    ) -> None:
        self._auth = auth
        self._station_id = station_id
        self._callback = callback
        self._stale_callback = stale_callback
        self._stopped = asyncio.Event()
        self._client: SignalRClient | None = None
        self._last_message = 0.0
        self._had_data = False
        self._init_error: str | None = None
        self._was_connected = False

    async def async_run(self) -> None:
        """Run until stopped, reconnecting after transient failures."""
        backoff = 1
        while not self._stopped.is_set():
            self._had_data = False
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep realtime loop alive
                if self._stopped.is_set():
                    return
                self._log_connection_issue(err)
                await self._notify_stale(err)
            else:
                if self._stopped.is_set():
                    return
                # client.run() returned without raising: the connection ended.
                _LOGGER.warning(
                    "Ting SignalR stream for %s ended unexpectedly; reconnecting",
                    self._station_id,
                )
                await self._notify_stale(TingStaleDataError("SignalR stream ended"))
            # Reset backoff only if the last connection actually delivered data;
            # otherwise keep escalating so a broken endpoint is not hammered.
            backoff = 1 if self._had_data else min(backoff * 2, MAX_BACKOFF)
            await _sleep_or_stop(self._stopped, backoff)

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
        await self._close_ws(client)

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
        self._init_error = None
        self._last_message = time.monotonic()

        async def on_open() -> None:
            self._last_message = time.monotonic()
            if not self._was_connected:
                _LOGGER.info("Ting SignalR connected for %s", self._station_id)
            else:
                _LOGGER.info("Ting SignalR reconnected for %s", self._station_id)
            self._was_connected = True
            await client.send(
                "InitializeStreaming",
                [self._station_data(), self._auth.api_key, self._auth.user_id],
                on_invocation=self._on_init_completion,
            )

        async def on_update(arguments: list[Any]) -> None:
            if not arguments:
                return
            data = arguments[0]
            if not isinstance(data, Mapping):
                return
            self._last_message = time.monotonic()
            self._had_data = True
            await self._handle_combo_binary_data(data)

        client.on_open(on_open)
        client.on("updateComboBinaryData", on_update)
        client.on_error(_log_completion)

        run_task = asyncio.create_task(client.run(), name=f"ting_signalr_run_{self._station_id}")
        watchdog_task = asyncio.create_task(
            self._watchdog(), name=f"ting_signalr_watchdog_{self._station_id}"
        )
        try:
            done, pending = await asyncio.wait(
                {run_task, watchdog_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
        finally:
            await self._close_ws(client)
            self._client = None

    async def _watchdog(self) -> None:
        """Force a reconnect when the data stream goes silent."""
        while not self._stopped.is_set():
            await asyncio.sleep(WATCHDOG_INTERVAL)
            if self._init_error is not None:
                raise TingStaleDataError(
                    f"Ting streaming subscription rejected: {self._init_error}"
                )
            silent_for = time.monotonic() - self._last_message
            if silent_for > STALE_DATA_TIMEOUT:
                raise TingStaleDataError(
                    f"No Ting data for {silent_for:.0f}s (limit {STALE_DATA_TIMEOUT:.0f}s)"
                )

    async def _on_init_completion(self, message: CompletionMessage) -> None:
        if message.error:
            _LOGGER.warning(
                "Ting InitializeStreaming failed for %s: %s",
                self._station_id,
                message.error,
            )
            # Surface to the watchdog so the connection is torn down and retried.
            self._init_error = str(message.error)

    async def _close_ws(self, client: SignalRClient | None) -> None:
        transport = getattr(client, "_transport", None)
        ws = getattr(transport, "_ws", None)
        if ws is not None:
            with suppress(Exception):
                await ws.close()

    def _log_connection_issue(self, err: Exception) -> None:
        if isinstance(err, TingStaleDataError):
            _LOGGER.warning(
                "Ting SignalR stream stale for %s: %s; reconnecting",
                self._station_id,
                err,
            )
        elif self._was_connected:
            _LOGGER.warning(
                "Ting SignalR connection lost for %s: %s (%s); reconnecting",
                self._station_id,
                err,
                type(err).__name__,
            )
        else:
            # Never connected yet: keep retry noise at debug.
            _LOGGER.debug(
                "Ting SignalR connection failed for %s: %s", self._station_id, err
            )

    async def _notify_stale(self, err: Exception) -> None:
        if self._stale_callback is None:
            return
        result = self._stale_callback(err)
        if asyncio.iscoroutine(result):
            await result

    def _station_data(self) -> dict[str, str]:
        return {
            "StationId": self._station_id,
            "DataElement": TING_COMBO_BINARY_DATA,
        }

    async def _handle_combo_binary_data(self, data: Mapping[str, Any]) -> None:
        parsed = {
            "last_update": _timestamp(data.get("DataTimeUtc")),
            "voltage": _number(data.get("Voltage")),
            "voltage_high": _number(data.get("VoltageHi")),
            "voltage_low": _number(data.get("VoltageLo")),
            "hifi": _number(data.get("AveragePeaksMax")),
            "raw": _json_safe_mapping(data),
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


def _timestamp(value: Any) -> str | None:
    """Return a JSON-safe ISO timestamp from SignalR/protobuf values."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        timestamp = value
    else:
        timestamp = _protobuf_timestamp(value)
    if timestamp is None:
        return str(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat()


def _protobuf_timestamp(value: Any) -> datetime | None:
    to_datetime = getattr(value, "ToDatetime", None)
    if callable(to_datetime):
        try:
            return to_datetime(tzinfo=timezone.utc)
        except TypeError:
            return to_datetime().replace(tzinfo=timezone.utc)

    seconds = _timestamp_part(value, "seconds")
    nanoseconds = _timestamp_part(value, "nanoseconds")
    if seconds is None:
        seconds = _timestamp_part(value, "Seconds")
    if nanoseconds is None:
        nanoseconds = _timestamp_part(value, "Nanoseconds")
    if seconds is None:
        return None
    return datetime.fromtimestamp(float(seconds) + (float(nanoseconds or 0) / 1_000_000_000), timezone.utc)


def _timestamp_part(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _json_safe_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _json_safe_value(value) for key, value in data.items()}


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, Mapping):
        return _json_safe_mapping(value)
    if isinstance(value, list | tuple):
        return [_json_safe_value(item) for item in value]
    timestamp = _protobuf_timestamp(value)
    if timestamp is not None:
        return timestamp.astimezone(timezone.utc).isoformat()
    return str(value)


async def _sleep_or_stop(stop_event: asyncio.Event, delay: int) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except TimeoutError:
        return
