"""Test authentication recovery through Home Assistant's config entry lifecycle."""

from unittest.mock import patch

import pytest

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ting.const import CONF_REFRESH_TOKEN, DOMAIN
from custom_components.ting.exceptions import TingAuthError, TingConnectionError

PROFILE = {"devices": [{"serialNumber": "TEST-001", "isFire": False}]}


@pytest.fixture
def entry(hass):
    """Create an entry with a synthetic refresh token."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test-user",
        data={CONF_REFRESH_TOKEN: "test-refresh-token", "username": "test@example.invalid"},
    )
    entry.add_to_hass(hass)
    return entry


async def test_profile_auth_failure_starts_reauth(hass, entry):
    """A refresh token rejected after setup starts a reauth flow."""
    with (
        patch("custom_components.ting.TingAuth.async_ensure_tokens"),
        patch("custom_components.ting.TingApi.async_get_user", return_value=PROFILE) as get_user,
        patch("custom_components.ting.coordinator.TingRealtimeCoordinator.async_start"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        runtime = hass.data[DOMAIN][entry.entry_id]
        get_user.side_effect = TingAuthError("Refresh token expired")

        await runtime.profile_coordinator.async_refresh()
        await hass.async_block_till_done()

        assert isinstance(runtime.profile_coordinator.last_exception, ConfigEntryAuthFailed)
        assert not runtime.profile_coordinator.last_update_success
        assert hass.states.get("binary_sensor.ting_test_001_fire_hazard").state == STATE_UNAVAILABLE
        flows = hass.config_entries.flow.async_progress()
        assert len(flows) == 1
        assert flows[0]["context"]["source"] == SOURCE_REAUTH
        assert flows[0]["context"]["entry_id"] == entry.entry_id


async def test_stream_auth_failure_starts_reauth_without_retry(hass, entry):
    """A stream reconnect with invalid credentials prompts instead of looping."""
    with (
        patch("custom_components.ting.TingAuth.async_ensure_tokens"),
        patch("custom_components.ting.TingApi.async_get_user", return_value=PROFILE),
        patch(
            "custom_components.ting.signalr.TingSignalRClient._run_once",
            side_effect=TingAuthError("Refresh token expired"),
        ) as run_once,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

        run_once.assert_awaited_once()
        runtime = hass.data[DOMAIN][entry.entry_id]
        assert not runtime.coordinators["TEST-001"].last_update_success
        flows = hass.config_entries.flow.async_progress()
        assert len(flows) == 1
        assert flows[0]["context"]["source"] == SOURCE_REAUTH
        assert flows[0]["context"]["entry_id"] == entry.entry_id


async def test_profile_connection_failure_recovers_without_reauth(hass, entry):
    """An outage marks entities unavailable and a later successful poll recovers."""
    with (
        patch("custom_components.ting.TingAuth.async_ensure_tokens"),
        patch("custom_components.ting.TingApi.async_get_user", return_value=PROFILE) as get_user,
        patch("custom_components.ting.coordinator.TingRealtimeCoordinator.async_start"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id].profile_coordinator
        get_user.side_effect = TingConnectionError("Service unavailable")

        await coordinator.async_refresh()
        await hass.async_block_till_done()

        assert isinstance(coordinator.last_exception, UpdateFailed)
        assert not coordinator.last_update_success
        assert hass.config_entries.flow.async_progress() == []

        get_user.side_effect = None
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert coordinator.data["TEST-001"]["fire_hazard"] is False
