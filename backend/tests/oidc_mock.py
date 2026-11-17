"""Local stand-in for an OpenID Connect provider (discovery, JWKS, token endpoint) over real HTTP.

It signs ID tokens with a fresh RSA key, checks the PKCE verifier against the challenge the browser was
sent with, and lets a test decide what the ID token says (e-mail, verification, nonce, audience). Never
contacts a real identity provider.
"""

import asyncio
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

CLIENT_ID = "procurax-test-client"
CLIENT_SECRET = "procurax-test-secret"  # noqa: S105 - a test stand-in, not a credential


def _b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class OidcMock:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-key-1"
        self.codes: dict[str, dict] = {}  # code -> {"claims": {...}, "challenge": str}
        self.token_requests: list[dict[str, list[str]]] = []
        self._server: asyncio.AbstractServer | None = None
        self.issuer = ""

    # --- what a test controls -------------------------------------------------------------------
    def issue_code(self, authorize_url: str, **claims: object) -> tuple[str, str]:
        """Play the provider's login page: returns (code, state) for the callback."""
        query = parse_qs(urlsplit(authorize_url).query)
        code = f"code-{len(self.codes) + 1}"
        now = int(time.time())
        body = {
            "iss": self.issuer,
            "aud": CLIENT_ID,
            "sub": "provider-user-1",
            "iat": now,
            "exp": now + 300,
            "nonce": query["nonce"][0],
            "email_verified": True,
            **claims,
        }
        self.codes[code] = {"claims": body, "challenge": query["code_challenge"][0]}
        return code, query["state"][0]

    def sign(self, claims: dict, kid: str | None = None) -> str:
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": kid or self.kid})

    # --- HTTP ----------------------------------------------------------------------------------------
    def _jwks(self) -> dict:
        numbers = self.key.public_key().public_numbers()
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": self.kid,
                    "use": "sig",
                    "alg": "RS256",
                    "n": _b64(numbers.n),
                    "e": _b64(numbers.e),
                }
            ]
        }

    def _token(self, form: dict[str, list[str]]) -> tuple[str, dict]:
        self.token_requests.append(form)
        code = form.get("code", [""])[0]
        entry = self.codes.pop(code, None)
        if (
            entry is None
            or form.get("client_id") != [CLIENT_ID]
            or form.get("client_secret") != [CLIENT_SECRET]
        ):
            return "400 Bad Request", {"error": "invalid_grant"}
        verifier = form.get("code_verifier", [""])[0]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        if challenge != entry["challenge"]:
            return "400 Bad Request", {"error": "invalid_grant", "error_description": "PKCE mismatch"}
        return "200 OK", {
            "access_token": "opaque",
            "token_type": "Bearer",
            "id_token": self.sign(entry["claims"]),
        }

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line = (await reader.readline()).decode()
        length = 0
        while (line := await reader.readline()) not in (b"\r\n", b""):
            name, _, value = line.decode().partition(":")
            if name.strip().lower() == "content-length":
                length = int(value.strip())
        body = await reader.readexactly(length) if length else b""
        method, target = request_line.split(" ")[:2]
        path = urlsplit(target).path
        if path == "/.well-known/openid-configuration":
            status, payload = (
                "200 OK",
                {
                    "issuer": self.issuer,
                    "authorization_endpoint": f"{self.issuer}/authorize",
                    "token_endpoint": f"{self.issuer}/token",
                    "jwks_uri": f"{self.issuer}/jwks",
                    "response_types_supported": ["code"],
                    "id_token_signing_alg_values_supported": ["RS256"],
                },
            )
        elif path == "/jwks":
            status, payload = "200 OK", self._jwks()
        elif path == "/token" and method == "POST":
            status, payload = self._token(parse_qs(body.decode()))
        else:
            status, payload = "404 Not Found", {"error": "not_found"}
        data = json.dumps(payload).encode()
        writer.write(
            f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n"
            "Connection: close\r\n\r\n".encode()
            + data
        )
        await writer.drain()
        writer.close()

    async def __aenter__(self) -> "OidcMock":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.issuer = f"http://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()
