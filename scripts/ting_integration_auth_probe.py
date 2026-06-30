#!/usr/bin/env python3
"""Probe Ting auth using the custom integration's TingAuth implementation."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
from pathlib import Path
import sys
import types
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Ting auth through custom_components.ting.auth")
    parser.add_argument("-u", "--username", help="Ting username/email")
    parser.add_argument("--password", help="Ting password. Omit to prompt securely.")
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument("--debug", action="store_true", help="Enable integration debug logging")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)

    username = args.username or input("Ting username/email: ").strip()
    password = args.password or getpass.getpass("Ting password: ")

    try:
        return asyncio.run(async_main(args.repo_root, username, password))
    except RuntimeError as err:
        print(f"AUTH FAILED: {err}", file=sys.stderr)
        return 1


async def async_main(repo_root: Path, username: str, password: str) -> int:
    try:
        from aiohttp import ClientSession
    except ImportError as err:
        raise RuntimeError("aiohttp is required; run this inside Home Assistant or install aiohttp") from err

    load_ting_modules(repo_root)
    from custom_components.ting.auth import TingAuth
    from custom_components.ting.exceptions import TingAuthError, TingConnectionError, TingResponseError

    async with ClientSession() as session:
        auth = TingAuth(session)
        try:
            tokens = await auth.async_login(username, password)
        except (TingAuthError, TingConnectionError, TingResponseError) as err:
            raise RuntimeError(str(err)) from err

    print("AUTH OK")
    print("ExpiresAt:", tokens.expires_at.isoformat())
    print("ID token claims:")
    for key in ("sub", "email", "custom:user_id", "custom:default_device"):
        print(f"  {key}: {tokens.claims.get(key)}")
    print(f"  custom:api_key: {redact(tokens.claims.get('custom:api_key'))}")
    print("RefreshToken:", redact(tokens.refresh_token))
    return 0


def load_ting_modules(repo_root: Path) -> None:
    """Make custom_components.ting importable without importing HA's __init__.py."""
    component_dir = repo_root / "custom_components" / "ting"
    if not (component_dir / "auth.py").exists():
        raise RuntimeError(f"could not find Ting integration under {component_dir}")

    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = [str(repo_root / "custom_components")]  # type: ignore[attr-defined]
    sys.modules.setdefault("custom_components", custom_components)

    ting = types.ModuleType("custom_components.ting")
    ting.__path__ = [str(component_dir)]  # type: ignore[attr-defined]
    sys.modules.setdefault("custom_components.ting", ting)


def redact(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "<missing>"
    if len(value) <= 12:
        return "<redacted>"
    return f"{value[:4]}...{value[-4:]} ({len(value)} chars)"


if __name__ == "__main__":
    raise SystemExit(main())
