"""Test cloud error classification and the resulting Home Assistant recovery."""

import base64
import json
from unittest.mock import patch

import pytest
from aiohttp import ClientConnectionError

from homeassistant.config_entries import ConfigEntryState, SOURCE_REAUTH
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ting.api import TingApi
from custom_components.ting.auth import TingAuth
from custom_components.ting.const import (
    COGNITO_ENDPOINT,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    TING_API_BASE,
)
from custom_components.ting.exceptions import (
    TingAuthError,
    TingConnectionError,
    TingRateLimitError,
    TingResponseError,
)

PROFILE_URL = f"{TING_API_BASE}/api/v1/Users/test-user"
PROFILE = {"devices": [{"serialNumber": "TEST-001", "isFire": False}]}


@pytest.fixture
def token_response():
    """Return synthetic Cognito tokens with the claims used by Ting."""
    claims = {"custom:user_id": "test-user", "custom:api_key": "test-api-key"}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return {
        "AuthenticationResult": {
            "IdToken": f"header.{payload}.signature",
            "AccessToken": "test-access-token",
            "RefreshToken": "test-refresh-token",
            "ExpiresIn": 3600,
        }
    }


@pytest.fixture
async def auth(hass, aioclient_mock):
    """Use HA's HTTP session with a synthetic stored refresh token."""
    return TingAuth(async_get_clientsession(hass), refresh_token="test-refresh-token")


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (400, '{"__type":"NotAuthorizedException"}', TingAuthError),
        (400, '{"__type":"aws.cognito#NotAuthorizedException"}', TingAuthError),
        (400, '{"__type":"UserNotFoundException"}', TingAuthError),
        (400, '{"__type":"UserNotConfirmedException"}', TingAuthError),
        (400, '{"__type":"PasswordResetRequiredException"}', TingAuthError),
        (401, "Unauthorized", TingAuthError),
        (403, "Forbidden", TingAuthError),
        (400, '{"__type":"TooManyRequestsException"}', TingRateLimitError),
        (400, '{"__type":"aws.cognito#LimitExceededException"}', TingRateLimitError),
        (429, "Too many requests", TingRateLimitError),
        (500, '{"__type":"InternalErrorException"}', TingConnectionError),
        (503, "<html>Service unavailable</html>", TingConnectionError),
        (400, '{"__type":"InvalidParameterException"}', TingResponseError),
        (400, '{"__type":null}', TingResponseError),
        (400, "[]", TingResponseError),
        (200, "[]", TingResponseError),
        (200, "Invalid JSON", TingResponseError),
    ],
)
async def test_cognito_error_classification(auth, aioclient_mock, status, body, expected):
    """Only rejected credentials are reported as authentication failures."""
    aioclient_mock.post(COGNITO_ENDPOINT, status=status, text=body)

    with pytest.raises(expected) as error:
        await auth.async_refresh()

    assert type(error.value) is expected


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, "Unauthorized", TingAuthError),
        (403, "Forbidden", TingAuthError),
        (429, "Too many requests", TingRateLimitError),
        (500, "Internal server error", TingConnectionError),
        (503, "<html>Service unavailable</html>", TingConnectionError),
        (400, "Bad request", TingResponseError),
        (404, "Not found", TingResponseError),
        (200, "Invalid JSON", TingResponseError),
    ],
)
async def test_rest_error_classification(
    auth, token_response, aioclient_mock, status, body, expected
):
    """Classify REST HTTP failures even when the response is not JSON."""
    auth._store_auth_result(token_response)
    aioclient_mock.get(PROFILE_URL, status=status, text=body)

    with pytest.raises(expected) as error:
        await TingApi(auth).async_get_user()

    assert type(error.value) is expected


@pytest.mark.parametrize("exception", [TimeoutError, ClientConnectionError])
@pytest.mark.parametrize("endpoint", ["cognito", "profile"])
async def test_network_errors_are_retryable(
    auth, token_response, aioclient_mock, exception, endpoint
):
    """Network failures use the transient failure path, including timeouts."""
    if endpoint == "cognito":
        aioclient_mock.post(COGNITO_ENDPOINT, exc=exception())
        request = auth.async_refresh
    else:
        auth._store_auth_result(token_response)
        aioclient_mock.get(PROFILE_URL, exc=exception())
        request = TingApi(auth).async_get_user

    with pytest.raises(TingConnectionError):
        await request()


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, '{"__type":"TooManyRequestsException"}'),
        (429, "Too many requests"),
        (503, "<html>Service unavailable</html>"),
    ],
)
async def test_cognito_outage_retries_setup_without_reauth(hass, aioclient_mock, status, body):
    """A service outage during startup schedules setup retry, not a new login."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_REFRESH_TOKEN: "test-refresh-token"})
    entry.add_to_hass(hass)
    aioclient_mock.post(COGNITO_ENDPOINT, status=status, text=body)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.config_entries.flow.async_progress() == []


@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_rest_failure_uses_correct_recovery(hass, aioclient_mock, token_response, status):
    """REST credentials trigger reauth; outages and throttling recover on polling."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_REFRESH_TOKEN: "test-refresh-token"})
    entry.add_to_hass(hass)
    aioclient_mock.post(COGNITO_ENDPOINT, json=token_response)
    aioclient_mock.get(PROFILE_URL, json=PROFILE)

    with patch("custom_components.ting.coordinator.TingRealtimeCoordinator.async_start"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id].profile_coordinator
        aioclient_mock.clear_requests()
        aioclient_mock.get(PROFILE_URL, status=status, text="Synthetic HTTP error")

        await coordinator.async_refresh()
        await hass.async_block_till_done()

        assert not coordinator.last_update_success
        flows = hass.config_entries.flow.async_progress()
        if status in (401, 403):
            assert len(flows) == 1
            assert flows[0]["context"]["source"] == SOURCE_REAUTH
            assert flows[0]["context"]["entry_id"] == entry.entry_id
        else:
            assert flows == []
            assert isinstance(coordinator.last_exception, UpdateFailed)
            aioclient_mock.clear_requests()
            aioclient_mock.get(PROFILE_URL, json=PROFILE)
            await coordinator.async_refresh()
            assert coordinator.last_update_success
