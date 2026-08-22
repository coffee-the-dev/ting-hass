"""Tests for the Ting SignalR MessagePack compatibility protocol."""

from __future__ import annotations

from pysignalr.messages import HandshakeRequestMessage, InvocationMessage

from custom_components.ting.signalr import TingMessagepackProtocol


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
