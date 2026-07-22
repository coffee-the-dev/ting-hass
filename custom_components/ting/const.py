"""Constants for the Ting integration."""

from __future__ import annotations

DOMAIN = "ting"

CONF_REFRESH_TOKEN = "refresh_token"

PLATFORMS = ["binary_sensor", "sensor"]

COGNITO_REGION = "us-east-1"
COGNITO_USER_POOL_ID = "us-east-1_trW4gH661"
COGNITO_CLIENT_ID = "4akjeqt9gtl8rgg1cksunipk9u"
COGNITO_ENDPOINT = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"

TING_API_BASE = "https://api.wskr.io"
TING_SIGNALR_WS_URL = "wss://signalr.api.wskr.io/dataHub"
TING_SIGNALR_HUB = "dataHub"
TING_COMBO_BINARY_DATA = "ComboBinaryData"

ATTR_VOLTAGE_HI = "voltage_high"
ATTR_VOLTAGE_LO = "voltage_low"
ATTR_HIFI = "hifi"
ATTR_LAST_UPDATE = "last_update"
