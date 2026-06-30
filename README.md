# Ting Home Assistant Integration

Experimental HACS custom integration for Whisker Labs Ting monitors.

## What it exposes

The integration signs in with the same Cognito user pool used by the Ting mobile app, discovers Ting devices from the account profile, and subscribes to Ting's realtime SignalR websocket stream.

Realtime sensors:

- Voltage
- Hi-Fi, from Ting's `AveragePeaksMax` datapoint
- Voltage high
- Voltage low
- Last update

REST profile diagnostic sensors, refreshed every 5 minutes:

- Fire hazard severity
- Electrical fire hazard level
- Electrical fire hazard status
- Utility fire hazard level
- Utility fire hazard status

## Install with HACS

1. Add this repository as a custom HACS integration repository.
2. Install **Ting** from HACS.
3. Restart Home Assistant.
4. Add the integration from **Settings > Devices & services > Add integration > Ting**.
5. Sign in with your Ting username and password.

The password is only used during setup. Home Assistant stores the Cognito refresh token returned by Ting and uses that for future token refreshes.

## Notes

This integration is based on observed Ting app behavior as of June 30, 2026. Ting does not appear to publish this API, so endpoints, auth details, and realtime payloads may change.

The HACS brand icon is included at `custom_components/ting/brand/icon.png`.
