#!/usr/bin/env python3
"""Probe Ting Cognito auth outside Home Assistant.

This script uses only the Python standard library. It does not write credentials
or tokens to disk. By default it prints redacted token metadata only.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import getpass
import hashlib
import hmac
import json
import secrets
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

COGNITO_REGION = "us-east-1"
COGNITO_USER_POOL_ID = "us-east-1_trW4gH661"
COGNITO_CLIENT_ID = "4akjeqt9gtl8rgg1cksunipk9u"
COGNITO_ENDPOINT = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"

N_HEX = (
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
    "507A33A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C"
    "7DB3970F85A6E1E4C7ABF5AE8CDB0933D71E8C94E04A25619"
    "DCEE3D2261AD2EE6BF12FFA06D98A0864D87602733EC86A645"
    "21F2B18177B200CBBE117577A615D6C770988C0BAD946E208E"
    "24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFF"
    "FFFFFFFFFFFF"
)
N = int(N_HEX, 16)
G = 2
INFO_BITS = b"Caldera Derived Key"
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class ProbeError(Exception):
    """Probe failed."""


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Ting Cognito authentication")
    parser.add_argument("-u", "--username", help="Ting username/email")
    parser.add_argument("--password", help="Ting password. Omit to prompt securely.")
    parser.add_argument("--refresh-token", help="Test REFRESH_TOKEN_AUTH instead of username/password SRP")
    parser.add_argument("--show-refresh-token", action="store_true", help="Print the refresh token on successful SRP login")
    parser.add_argument("--try-password-auth", action="store_true", help="Also try Cognito USER_PASSWORD_AUTH")
    args = parser.parse_args()

    try:
        if args.refresh_token:
            result = refresh(args.refresh_token)
        else:
            username = args.username or input("Ting username/email: ").strip()
            password = args.password or getpass.getpass("Ting password: ")
            if args.try_password_auth:
                try_password_auth(username, password)
            result = srp_login(username, password)

        auth_result = result["AuthenticationResult"]
        print("AUTH OK")
        print_token_summary(auth_result)
        if args.show_refresh_token and auth_result.get("RefreshToken"):
            print(f"RefreshToken: {auth_result['RefreshToken']}")
        return 0
    except ProbeError as err:
        print(f"AUTH FAILED: {err}", file=sys.stderr)
        return 1


def srp_login(username: str, password: str) -> dict[str, Any]:
    srp = SrpSession(username, password)
    print("SRP_A length:", len(srp.public_a_hex))
    initiate = cognito_request(
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

    challenge_name = initiate.get("ChallengeName")
    print("ChallengeName:", challenge_name)
    if challenge_name != "PASSWORD_VERIFIER":
        raise ProbeError(f"unexpected challenge {challenge_name!r}: {safe_json(initiate)}")

    challenge = initiate.get("ChallengeParameters")
    if not isinstance(challenge, dict):
        raise ProbeError(f"missing ChallengeParameters: {safe_json(initiate)}")
    print("Challenge keys:", ", ".join(sorted(challenge)))
    print("Challenge USERNAME:", challenge.get("USERNAME", "<missing>"))
    print("Challenge USER_ID_FOR_SRP:", challenge.get("USER_ID_FOR_SRP", "<missing>"))

    response = srp.process_password_challenge(challenge)
    print("Response USERNAME:", response["USERNAME"])
    print("Response TIMESTAMP:", response["TIMESTAMP"])
    return cognito_request(
        "AWSCognitoIdentityProviderService.RespondToAuthChallenge",
        {
            "ChallengeName": "PASSWORD_VERIFIER",
            "ClientId": COGNITO_CLIENT_ID,
            "ChallengeResponses": response,
        },
    )


def refresh(refresh_token: str) -> dict[str, Any]:
    return cognito_request(
        "AWSCognitoIdentityProviderService.InitiateAuth",
        {
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"REFRESH_TOKEN": refresh_token},
        },
    )


def try_password_auth(username: str, password: str) -> None:
    try:
        cognito_request(
            "AWSCognitoIdentityProviderService.InitiateAuth",
            {
                "AuthFlow": "USER_PASSWORD_AUTH",
                "ClientId": COGNITO_CLIENT_ID,
                "AuthParameters": {"USERNAME": username, "PASSWORD": password},
            },
        )
    except ProbeError as err:
        print(f"USER_PASSWORD_AUTH unavailable/failed as expected: {err}")
    else:
        print("USER_PASSWORD_AUTH unexpectedly succeeded")


def cognito_request(target: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    request = Request(
        COGNITO_ENDPOINT,
        data=body,
        headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": target,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except HTTPError as err:
        try:
            data = json.loads(err.read())
        except json.JSONDecodeError:
            raise ProbeError(f"{target.rsplit('.', 1)[-1]} HTTP {err.code}") from err
        message = data.get("message") or data.get("__type") or data
        raise ProbeError(f"{target.rsplit('.', 1)[-1]} HTTP {err.code}: {message}") from err
    except URLError as err:
        raise ProbeError(f"network error: {err}") from err
    except json.JSONDecodeError as err:
        raise ProbeError("Cognito returned non-JSON response") from err


class SrpSession:
    """Cognito SRP helper matching pycognito's AWSSRP formulas."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.pool_name = COGNITO_USER_POOL_ID.split("_", 1)[1]
        self.small_a = int.from_bytes(secrets.token_bytes(128), "big") % N
        self.large_a = pow(G, self.small_a, N)
        if self.large_a % N == 0:
            raise ProbeError("invalid SRP public value")

    @property
    def public_a_hex(self) -> str:
        return long_to_hex(self.large_a)

    def process_password_challenge(self, challenge: dict[str, Any]) -> dict[str, str]:
        internal_username = str(challenge.get("USERNAME", self.username))
        user_id_for_srp = str(challenge["USER_ID_FOR_SRP"])
        salt_hex = str(challenge["SALT"])
        srp_b_hex = str(challenge["SRP_B"])
        secret_block_b64 = str(challenge["SECRET_BLOCK"])

        server_b = int(srp_b_hex, 16)
        if server_b % N == 0:
            raise ProbeError("invalid SRP_B")

        hkdf = self.get_password_authentication_key(user_id_for_srp, server_b, salt_hex)
        secret_block = base64.standard_b64decode(secret_block_b64)
        timestamp = aws_timestamp()
        message = self.pool_name.encode() + user_id_for_srp.encode() + secret_block + timestamp.encode()
        signature = base64.standard_b64encode(hmac.new(hkdf, message, hashlib.sha256).digest()).decode()
        return {
            "USERNAME": internal_username,
            "PASSWORD_CLAIM_SECRET_BLOCK": secret_block_b64,
            "TIMESTAMP": timestamp,
            "PASSWORD_CLAIM_SIGNATURE": signature,
        }

    def get_password_authentication_key(self, username: str, server_b: int, salt_hex: str) -> bytes:
        u_value = int(hex_hash(pad_hex(self.large_a) + pad_hex(server_b)), 16)
        if u_value == 0:
            raise ProbeError("invalid SRP scrambling parameter")
        username_password = f"{self.pool_name}{username}:{self.password}"
        username_password_hash = hash_sha256_hex(username_password.encode())
        x_value = int(hex_hash(pad_hex(salt_hex) + username_password_hash), 16)
        g_mod_pow_xn = pow(G, x_value, N)
        k_value = int(hex_hash("00" + N_HEX + "0" + f"{G:x}"), 16)
        int_value = server_b - k_value * g_mod_pow_xn
        s_value = pow(int_value, self.small_a + u_value * x_value, N)
        return compute_hkdf(bytes.fromhex(pad_hex(s_value)), bytes.fromhex(pad_hex(f"{u_value:x}")))


def print_token_summary(auth_result: dict[str, Any]) -> None:
    print("ExpiresIn:", auth_result.get("ExpiresIn"))
    print("TokenType:", auth_result.get("TokenType"))
    id_token = auth_result.get("IdToken")
    if isinstance(id_token, str):
        claims = decode_jwt_unverified(id_token)
        print("ID token claims:")
        for key in ("sub", "email", "custom:user_id", "custom:default_device"):
            print(f"  {key}: {claims.get(key)}")
        api_key = claims.get("custom:api_key")
        print(f"  custom:api_key: {redact(api_key)}")
    if "RefreshToken" in auth_result:
        print("RefreshToken:", redact(auth_result["RefreshToken"]))


def decode_jwt_unverified(token: str) -> dict[str, Any]:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def aws_timestamp() -> str:
    now = datetime.now(timezone.utc)
    return (
        f"{WEEKDAY_NAMES[now.weekday()]} "
        f"{MONTH_NAMES[now.month - 1]} "
        f"{now.day:d} "
        f"{now.hour:02d}:{now.minute:02d}:{now.second:02d} UTC "
        f"{now.year:d}"
    )


def hash_sha256_hex(value: bytes) -> str:
    digest = hashlib.sha256(value).hexdigest()
    return (64 - len(digest)) * "0" + digest


def hex_hash(hex_value: str) -> str:
    return hash_sha256_hex(bytearray.fromhex(hex_value))


def compute_hkdf(ikm: bytes, salt: bytes) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, INFO_BITS + bytes([1]), hashlib.sha256).digest()[:16]


def pad_hex(value: int | str) -> str:
    result = f"{value:x}" if isinstance(value, int) else value
    if len(result) % 2 == 1:
        result = f"0{result}"
    elif result[0] in "89ABCDEFabcdef":
        result = f"00{result}"
    return result


def long_to_hex(value: int) -> str:
    return f"{value:x}"


def redact(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "<missing>"
    if len(value) <= 12:
        return "<redacted>"
    return f"{value[:4]}...{value[-4:]} ({len(value)} chars)"


def safe_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
