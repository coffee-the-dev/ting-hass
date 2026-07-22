"""Tests for Ting profile diagnostic normalization."""

from __future__ import annotations

from custom_components.ting.api import extract_device_diagnostics


def test_extract_device_diagnostics_matches_site() -> None:
    """Site-level power quality state is associated by siteId."""
    profile = {
        "devices": [
            {
                "serialNumber": "TEST-SERIAL-001",
                "siteId": "TEST-SITE-001",
                "isFire": False,
                "fireHazardStatus": {
                    "learningMode": True,
                    "message": "Synthetic learning message",
                    "timestampUtc": "2030-01-01T00:00:00Z",
                    "efhStatus": {"level": None, "status": None},
                    "ufhStatus": {"level": None, "status": None},
                },
            }
        ],
        "sites": [{"id": "TEST-SITE-001", "isPowerQualityHazard": True}],
    }

    assert extract_device_diagnostics(profile) == {
        "TEST-SERIAL-001": {
            "fire_hazard": False,
            "learning_mode": True,
            "hazard_message": "Synthetic learning message",
            "power_quality_hazard": True,
        }
    }


def test_extract_device_diagnostics_missing_site() -> None:
    """A missing site does not produce a false power quality state."""
    profile = {
        "devices": [
            {
                "serialNumber": "TEST-SERIAL-002",
                "siteId": "TEST-SITE-MISSING",
                "isFire": True,
                "fireHazardStatus": {
                    "learningMode": False,
                    "message": "Synthetic hazard message",
                },
            }
        ],
        "sites": [{"id": "TEST-SITE-OTHER", "isPowerQualityHazard": False}],
    }

    assert extract_device_diagnostics(profile) == {
        "TEST-SERIAL-002": {
            "fire_hazard": True,
            "learning_mode": False,
            "hazard_message": "Synthetic hazard message",
        }
    }


def test_extract_device_diagnostics_omits_null_and_malformed_fields() -> None:
    """Null and incorrectly typed values remain unknown, never truthy/falsey."""
    profile = {
        "devices": [
            {
                "serialNumber": "TEST-SERIAL-003",
                "siteId": "TEST-SITE-003",
                "isFire": "false",
                "fireHazardStatus": {
                    "learningMode": None,
                    "message": {"text": "not a valid message"},
                },
            },
            {
                "serialNumber": "TEST-SERIAL-004",
                "siteId": None,
                "isFire": None,
                "fireHazardStatus": None,
            },
        ],
        "sites": [{"id": "TEST-SITE-003", "isPowerQualityHazard": 0}],
    }

    assert extract_device_diagnostics(profile) == {
        "TEST-SERIAL-003": {},
        "TEST-SERIAL-004": {},
    }


def test_extract_device_diagnostics_excludes_personal_and_raw_values() -> None:
    """Only the four allowlisted diagnostics leave the profile response."""
    profile = {
        "email": "person@example.invalid",
        "displayName": "Synthetic Person",
        "devices": [
            {
                "serialNumber": "TEST-SERIAL-005",
                "siteId": "TEST-SITE-005",
                "name": "Synthetic Home Ting",
                "isFire": False,
                "fireHazardStatus": {
                    "learningMode": False,
                    "message": "No synthetic hazard",
                    "timestampUtc": "2030-01-01T00:00:00Z",
                    "efhStatus": {"level": None, "status": None},
                    "ufhStatus": {"level": None, "status": None},
                },
            }
        ],
        "sites": [
            {
                "id": "TEST-SITE-005",
                "address": "123 Synthetic Street",
                "isPowerQualityHazard": False,
            }
        ],
    }

    result = extract_device_diagnostics(profile)

    assert result == {
        "TEST-SERIAL-005": {
            "fire_hazard": False,
            "learning_mode": False,
            "hazard_message": "No synthetic hazard",
            "power_quality_hazard": False,
        }
    }
    serialized = repr(result)
    assert "person@example.invalid" not in serialized
    assert "Synthetic Person" not in serialized
    assert "123 Synthetic Street" not in serialized
    assert "timestampUtc" not in serialized
    assert "efhStatus" not in serialized
    assert "ufhStatus" not in serialized
