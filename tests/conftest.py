"""Fixtures for Ting integration tests."""

import pytest

pytest_plugins = ("pytest_homeassistant_custom_component",)


@pytest.fixture(autouse=True)
def enable_ting_integration(enable_custom_integrations):
    """Allow Home Assistant to load the custom integration under test."""
