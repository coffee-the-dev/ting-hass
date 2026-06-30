"""SignalR MessagePack websocket client for Ting realtime data."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import json
import logging
from typing import Any

from aiohttp import ClientError, ClientSession, ClientWebSocketResponse, WSMsgType
import msgpack

from .auth import TingAuth
from .const import TING_COMBO_BINARY_DATA, TING_SIGNALR_WS_URL

_LOGGER = logging.getLogger(__name__)

TingRealtimeCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class TingSignalRClient:
    """Minimal SignalR client for Ting's realtime hub."""

    def __init__(
        self,
        session: ClientSession,
        auth: TingAuth,
        *,
        station_id: str,
        callback: TingRealtimeCallback,
    ) -> None:
        self._session = session
        self._auth = auth
        self._station_id = station_id
        self._callback = callback
        self._stopped = asyncio.Event()
        self._invocation_id = 0
        self._ws: ClientWebSocketResponse | None = None

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
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()

    async def _run_once(self) -> None:
        await self._auth.async_ensure_tokens()
        headers = {
            "Origin": "ionic://localhost",
            "User-Agent": "Home Assistant Ting Integration",
        }
        async with self._session.ws_connect(TING_SIGNALR_WS_URL, headers=headers, heartbeat=30) as ws:
            self._ws = ws
            try:
                await ws.send_str('{"protocol":"messagepack","version":1}\x1e')
                await self._read_handshake(ws)
                await ws.send_bytes(
                    _pack_message(
                        [
                            1,
                            {},
                            self._next_invocation_id(),
                            "InitializeStreaming",
                            [
                                {
                                    "StationId": self._station_id,
                                    "DataElement": TING_COMBO_BINARY_DATA,
                                },
                                self._auth.api_key,
                                self._auth.user_id,
                            ],
                            [],
                        ]
                    )
                )

                async for message in ws:
                    if self._stopped.is_set():
                        break
                    if message.type == WSMsgType.BINARY:
                        await self._handle_binary(message.data)
                    elif message.type == WSMsgType.TEXT:
                        _LOGGER.debug("Ting SignalR text frame for %s: %s", self._station_id, message.data)
                    elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
            finally:
                self._ws = None

    async def _read_handshake(self, ws: Any) -> None:
        try:
            message = await ws.receive(timeout=10)
        except TimeoutError as err:
            raise ClientError("Timed out waiting for SignalR handshake") from err

        if message.type == WSMsgType.TEXT:
            payload = message.data.rstrip("\x1e")
            if not payload:
                return
            data = json.loads(payload)
            if error := data.get("error"):
                raise ClientError(f"SignalR handshake failed: {error}")
            return
        if message.type == WSMsgType.BINARY and not message.data:
            return
        raise ClientError(f"Unexpected SignalR handshake frame: {message.type}")

    async def _handle_binary(self, data: bytes) -> None:
        for hub_message in _unpack_messages(data):
            if not isinstance(hub_message, list) or not hub_message:
                continue
            message_type = hub_message[0]
            if message_type == 1:
                await self._handle_invocation(hub_message)
            elif message_type == 7:
                raise ClientError("SignalR server closed the connection")

    async def _handle_invocation(self, hub_message: list[Any]) -> None:
        if len(hub_message) < 5:
            return
        target = hub_message[3]
        args = hub_message[4]
        if target != "updateComboBinaryData" or not isinstance(args, list) or not args:
            return
        data = args[0]
        if not isinstance(data, Mapping):
            return

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

    def _next_invocation_id(self) -> str:
        self._invocation_id += 1
        return str(self._invocation_id)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pack_message(message: list[Any]) -> bytes:
    payload = msgpack.packb(message, use_bin_type=True)
    return _encode_varint(len(payload)) + payload


def _unpack_messages(data: bytes) -> list[Any]:
    messages: list[Any] = []
    offset = 0
    while offset < len(data):
        length, offset = _read_varint(data, offset)
        payload = data[offset : offset + length]
        offset += length
        messages.append(msgpack.unpackb(payload, raw=False, strict_map_key=False))
    return messages


def _encode_varint(value: int) -> bytes:
    output = bytearray()
    while value >= 0x80:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("Incomplete SignalR binary frame")
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return result, offset
        shift += 7
        if shift > 35:
            raise ValueError("Invalid SignalR binary frame length")


async def _sleep_or_stop(stop_event: asyncio.Event, delay: int) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except TimeoutError:
        return
