"""Exceptions for the Ting integration."""

from __future__ import annotations


class TingError(Exception):
    """Base Ting error."""


class TingAuthError(TingError):
    """Authentication failed."""


class TingConnectionError(TingError):
    """Ting service connection failed."""


class TingResponseError(TingError):
    """Ting returned an unexpected response."""
