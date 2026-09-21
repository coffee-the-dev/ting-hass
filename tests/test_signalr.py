"""Tests for Ting SignalR framing and connection lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock, Mock, patch, sentinel

import pytest
from pysignalr.messages import HandshakeRequestMessage, InvocationMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ting.const import CONF_REFRESH_TOKEN, DOMAIN
from custom_components.ting.exceptions import TingStaleDataError
from custom_components.ting.signalr import TingMessagepackProtocol, TingSignalRClient


def test_messagepack_protocol_uses_json_handshake() -> None:
    """SignalR requires a JSON handshake even for MessagePack connections."""
    protocol = TingMessagepackProtocol()

    encoded = protocol.encode(
        HandshakeRequestMessage(protocol="messagepack", version=1)
    )

    assert encoded == b'{"protocol":"messagepack","version":1}\x1e'


def test_messagepack_protocol_handles_multibyte_frame_length() -> None:
    """Large realtime samples use a multi-byte SignalR length prefix."""
    protocol = TingMessagepackProtocol()
    original = InvocationMessage(
        headers={},
        invocation_id="1",
        target="sample",
        arguments=["x" * 300],
    )

    encoded = protocol.encode(original)
    decoded = protocol.decode(encoded)

    assert encoded[0] >= 128
    assert len(decoded) == 1
    assert decoded[0].target == "sample"
    assert decoded[0].arguments == ["x" * 300]


@pytest.fixture
def transport():
    """Simulate a transport that keeps running until explicitly cancelled."""
    started = asyncio.Event()

    async def run():
        started.set()
        await asyncio.Event().wait()

    transport = Mock()
    transport.run = AsyncMock(side_effect=run)
    transport.send = AsyncMock()
    transport._transport._ws.close = AsyncMock()
    with (
        patch("custom_components.ting.signalr.SignalRClient", return_value=transport),
        patch("custom_components.ting.signalr.ssl.create_default_context", return_value=sentinel.ssl),
    ):
        yield transport, started


@pytest.fixture
def client():
    """Create a stream with synthetic credentials."""
    auth = Mock()
    auth.async_ensure_tokens = AsyncMock()
    return TingSignalRClient(auth, station_id="TEST-001", callback=Mock())


def stream_tasks():
    """Return the client and watchdog tasks belonging to a connection."""
    return [task for task in asyncio.all_tasks() if task.get_name().startswith("ting_signalr_")]


@pytest.mark.parametrize("stop_first", [False, True])
async def test_parent_cancellation_cleans_up_children(client, transport, stop_first):
    """Shutdown and unload cancellation must await both connection children."""
    mock_transport, started = transport
    parent = asyncio.create_task(client.async_run())
    children = []
    try:
        await asyncio.wait_for(started.wait(), 1)
        children = stream_tasks()
        assert len(children) == 2
        if stop_first:
            await client.async_stop()
        parent.cancel()
        with suppress(asyncio.CancelledError):
            await parent

        assert all(task.done() for task in children)
        assert stream_tasks() == []
        mock_transport._transport._ws.close.assert_awaited()
    finally:
        parent.cancel()
        for task in children:
            task.cancel()
        await asyncio.gather(parent, *children, return_exceptions=True)


async def test_stop_ends_connection_without_parent_cancellation(client, transport):
    """Stopping wakes the watchdog immediately and shuts down the transport."""
    mock_transport, started = transport
    parent = asyncio.create_task(client.async_run())
    try:
        await asyncio.wait_for(started.wait(), 1)
        await client.async_stop()
        await asyncio.wait_for(parent, 1)

        assert stream_tasks() == []
        mock_transport.run.assert_awaited_once()
    finally:
        parent.cancel()
        await asyncio.gather(parent, return_exceptions=True)


async def test_watchdog_failure_cleans_up_transport(client, transport):
    """Staleness tears down the existing client before a reconnect can start."""
    mock_transport, started = transport

    async def stale():
        await started.wait()
        raise TingStaleDataError("No data")

    with patch.object(client, "_watchdog", side_effect=stale):
        with pytest.raises(TingStaleDataError, match="No data"):
            await client._run_once()

    assert stream_tasks() == []
    mock_transport._transport._ws.close.assert_awaited()


async def test_entry_unload_cleans_up_stream(hass, transport):
    """Home Assistant can unload an active stream without leaving child tasks."""
    _, started = transport
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_REFRESH_TOKEN: "test-token"})
    entry.add_to_hass(hass)
    with (
        patch("custom_components.ting.TingAuth.async_ensure_tokens"),
        patch(
            "custom_components.ting.TingApi.async_get_user",
            return_value={"devices": [{"serialNumber": "TEST-001"}]},
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await asyncio.wait_for(started.wait(), 1)
        assert len(stream_tasks()) == 2

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert stream_tasks() == []
    assert entry.entry_id not in hass.data[DOMAIN]
