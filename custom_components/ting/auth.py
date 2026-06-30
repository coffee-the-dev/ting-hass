"""AWS Cognito authentication for Ting."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import secrets
from typing import Any

from aiohttp import ClientError, ClientSession

from .const import COGNITO_CLIENT_ID, COGNITO_ENDPOINT, COGNITO_USER_POOL_ID
from .exceptions import TingAuthError, TingConnectionError, TingResponseError

_LOGGER = logging.getLogger(__name__)

_N_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E08"
    "8A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD"
    "3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E"
    "7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899F"
    "A5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05"
    "98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961"
    "C62F356208552BB9ED529077096966D670C354E4ABC9804F174"
    "6C08CA18217C32905E462E36CE3BE39E772C180E86039B2783"
    "A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497C"
    "EA956AE515D2261898FA051015728E5A8AAAC42DAD33170D04"
    "50A33A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C"
    "7DB3970F85A6E1E4C7ABF5AE8CDB0933D71E8C94E04A25619"
    "DCEE3D2261AD2EE6BF12FFA06D98A0864D87602733EC86A645"
    "21F2B18177B200CBBE117577A615D6C770988C0BAD946E208E"
    "24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFF"
    "FFFFFFFFFFFFF"
)
_N = int(_N_HEX, 16)
_G = 2
_INFO_BITS = b"Caldera Derived Key"
_JWT_PADDING = "="
_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTH_NAMES = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def _pad_hex(value: int) -> str:
    """Return Cognito-compatible even-length hex."""
    result = f"{value:x}"
    if len(result) % 2 == 1:
        result = f"0{result}"
    if result[0] in "89ABCDEFabcdef":
        result = f"00{result}"
    return result


def _hash_sha256(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


def _hash_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hex_hash(hex_value: str) -> str:
    return _hash_hex(bytes.fromhex(hex_value))


def _compute_hkdf(ikm: bytes, salt: bytes) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    info_bits_update = _INFO_BITS + bytes([1])
    return hmac.new(prk, info_bits_update, hashlib.sha256).digest()[:16]


def _aws_timestamp() -> str:
    now = datetime.now(timezone.utc)
    return (
        f"{_WEEKDAY_NAMES[now.weekday()]} "
        f"{_MONTH_NAMES[now.month - 1]} "
        f"{now.day:d} "
        f"{now.hour:02d}:{now.minute:02d}:{now.second:02d} UTC "
        f"{now.year:d}"
    )


def _decode_jwt_unverified(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += _JWT_PADDING * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, json.JSONDecodeError) as err:
        raise TingResponseError("Could not decode Cognito token") from err


@dataclass
class TingTokens:
    """Current Ting Cognito tokens."""

    id_token: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    claims: dict[str, Any]


class TingAuth:
    """Authenticate against Ting's Cognito user pool."""

    def __init__(
        self,
        session: ClientSession,
        *,
        refresh_token: str | None = None,
    ) -> None:
        self._session = session
        self._tokens: TingTokens | None = None
        self._refresh_token = refresh_token

    @property
    def session(self) -> ClientSession:
        """Return the shared HTTP session."""
        return self._session

    @property
    def refresh_token(self) -> str:
        if self._tokens is not None:
            return self._tokens.refresh_token
        if self._refresh_token:
            return self._refresh_token
        raise TingAuthError("No refresh token available")

    @property
    def id_token(self) -> str:
        if self._tokens is None:
            raise TingAuthError("Not authenticated")
        return self._tokens.id_token

    @property
    def api_key(self) -> str:
        value = self.claims.get("custom:api_key")
        if not isinstance(value, str) or not value:
            raise TingResponseError("Cognito token did not include Ting API key")
        return value

    @property
    def user_id(self) -> str:
        value = self.claims.get("custom:user_id")
        if value is None:
            raise TingResponseError("Cognito token did not include Ting user id")
        return str(value)

    @property
    def default_device(self) -> str | None:
        value = self.claims.get("custom:default_device")
        if not isinstance(value, str) or not value:
            return None
        return value[1:] if value.startswith("0") and len(value) > 1 else value

    @property
    def claims(self) -> Mapping[str, Any]:
        if self._tokens is None:
            return {}
        return self._tokens.claims

    async def async_login(self, username: str, password: str) -> TingTokens:
        """Perform Cognito USER_SRP_AUTH login."""
        srp = _SrpSession(username=username, password=password)
        response = await self._cognito_request(
            "AWSCognitoIdentityProviderService.InitiateAuth",
            {
                "AuthFlow": "USER_SRP_AUTH",
                "ClientId": COGNITO_CLIENT_ID,
                "AuthParameters": {
                    "USERNAME": username,
                    "SRP_A": srp.public_a_hex,
                },
            },
        )

        challenge_name = response.get("ChallengeName")
        _LOGGER.debug("Cognito initiate auth returned challenge: %s", challenge_name)
        if challenge_name != "PASSWORD_VERIFIER":
            raise TingAuthError(f"Unexpected Cognito challenge: {challenge_name}")

        challenge_parameters = response.get("ChallengeParameters")
        if not isinstance(challenge_parameters, dict):
            raise TingResponseError("Cognito password challenge did not include parameters")
        _LOGGER.debug(
            "Cognito password challenge parameter keys: %s",
            sorted(challenge_parameters),
        )

        challenge_response = srp.process_password_challenge(challenge_parameters)
        auth_response = await self._cognito_request(
            "AWSCognitoIdentityProviderService.RespondToAuthChallenge",
            {
                "ChallengeName": "PASSWORD_VERIFIER",
                "ClientId": COGNITO_CLIENT_ID,
                "ChallengeResponses": challenge_response,
            },
        )
        return self._store_auth_result(auth_response)

    async def async_refresh(self) -> TingTokens:
        """Refresh Cognito tokens."""
        if not self._refresh_token:
            raise TingAuthError("No refresh token available")
        response = await self._cognito_request(
            "AWSCognitoIdentityProviderService.InitiateAuth",
            {
                "AuthFlow": "REFRESH_TOKEN_AUTH",
                "ClientId": COGNITO_CLIENT_ID,
                "AuthParameters": {
                    "REFRESH_TOKEN": self._refresh_token,
                },
            },
        )
        return self._store_auth_result(response, refresh_token=self._refresh_token)

    async def async_ensure_tokens(self) -> TingTokens:
        """Return valid tokens, refreshing when needed."""
        if self._tokens is None:
            return await self.async_refresh()
        if self._tokens.expires_at <= datetime.now(timezone.utc) + timedelta(minutes=5):
            return await self.async_refresh()
        return self._tokens

    async def _cognito_request(self, target: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": target,
        }
        try:
            async with self._session.post(COGNITO_ENDPOINT, headers=headers, json=payload) as response:
                data = await response.json(content_type=None)
        except ClientError as err:
            raise TingConnectionError("Could not connect to Cognito") from err
        except (json.JSONDecodeError, ValueError) as err:
            raise TingResponseError("Cognito returned a non-JSON response") from err

        if response.status >= 400:
            message = data.get("message") or data.get("__type") or "Cognito authentication failed"
            _LOGGER.warning("Cognito request %s failed: %s", target.rsplit(".", 1)[-1], message)
            _LOGGER.debug("Cognito error response: %s", data)
            raise TingAuthError(str(message))
        return data

    def _store_auth_result(
        self,
        response: Mapping[str, Any],
        *,
        refresh_token: str | None = None,
    ) -> TingTokens:
        result = response.get("AuthenticationResult")
        if not isinstance(result, dict):
            raise TingResponseError("Cognito response did not include AuthenticationResult")

        id_token = result.get("IdToken")
        access_token = result.get("AccessToken")
        if not isinstance(id_token, str) or not isinstance(access_token, str):
            raise TingResponseError("Cognito response did not include tokens")

        token_refresh = result.get("RefreshToken") or refresh_token
        if not isinstance(token_refresh, str):
            raise TingResponseError("Cognito response did not include refresh token")

        expires_in = result.get("ExpiresIn", 3600)
        claims = _decode_jwt_unverified(id_token)
        self._refresh_token = token_refresh
        self._tokens = TingTokens(
            id_token=id_token,
            access_token=access_token,
            refresh_token=token_refresh,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=int(expires_in)),
            claims=claims,
        )
        return self._tokens


class _SrpSession:
    """Small Cognito SRP client for USER_SRP_AUTH."""

    def __init__(self, *, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.pool_name = COGNITO_USER_POOL_ID.split("_", 1)[1]
        self._small_a = int.from_bytes(secrets.token_bytes(128), "big") % _N
        self._large_a = pow(_G, self._small_a, _N)
        if self._large_a % _N == 0:
            raise TingAuthError("Invalid SRP public value")

    @property
    def public_a_hex(self) -> str:
        return _pad_hex(self._large_a)

    def process_password_challenge(self, challenge: Mapping[str, Any]) -> dict[str, str]:
        internal_username = str(challenge.get("USERNAME", self.username))
        user_id = str(challenge["USER_ID_FOR_SRP"])
        salt_hex = str(challenge["SALT"])
        srp_b_hex = str(challenge["SRP_B"])
        secret_block_b64 = str(challenge["SECRET_BLOCK"])
        secret_block = base64.b64decode(secret_block_b64)

        large_b = int(srp_b_hex, 16)
        if large_b % _N == 0:
            raise TingAuthError("Invalid SRP challenge value")

        u_hex = _hex_hash(_pad_hex(self._large_a) + _pad_hex(large_b))
        u_value = int(u_hex, 16)
        if u_value == 0:
            raise TingAuthError("Invalid SRP scrambling parameter")

        username_password = f"{self.pool_name}{user_id}:{self.password}".encode()
        username_password_hash = _hash_sha256(username_password)
        x_value = int(_hash_hex(bytes.fromhex(salt_hex) + username_password_hash), 16)
        g_mod_pow_xn = pow(_G, x_value, _N)
        k_value = int(_hex_hash(f"00{_N_HEX}0{_G}"), 16)
        int_value = (large_b - k_value * g_mod_pow_xn) % _N
        exponent = (self._small_a + u_value * x_value) % _N
        s_value = pow(int_value, exponent, _N)
        hkdf = _compute_hkdf(bytes.fromhex(_pad_hex(s_value)), bytes.fromhex(_pad_hex(u_value)))

        timestamp = _aws_timestamp()
        signature_payload = self.pool_name.encode() + user_id.encode() + secret_block + timestamp.encode()
        signature = base64.b64encode(hmac.new(hkdf, signature_payload, hashlib.sha256).digest()).decode()

        # The proof is signed with USER_ID_FOR_SRP, but Cognito can return a
        # canonical USERNAME that must be echoed in the challenge response.
        return {
            "USERNAME": internal_username,
            "PASSWORD_CLAIM_SECRET_BLOCK": secret_block_b64,
            "TIMESTAMP": timestamp,
            "PASSWORD_CLAIM_SIGNATURE": signature,
        }
