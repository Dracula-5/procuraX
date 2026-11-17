"""Local stand-in for the NTA qualified-invoice issuer Web-API (v1 `num` endpoint, JSON).

Serves the fixture responses in tests/fixtures/nta over real HTTP, checks the application ID, and can
simulate an outage. Used by the integration tests; never contacts the real service.
"""

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FIXTURES = Path(__file__).parent / "fixtures" / "nta"
APP_ID = "test-application-id"
RESPONSES = {
    "T2010401000001": "announcement_active.json",
    "T1010401000002": "announcement_revoked.json",
}
FAILING = {"T7010401000005"}  # answers HTTP 503


class NtaMock:
    def __init__(self) -> None:
        self.requests: list[dict[str, list[str]]] = []
        self.down = False
        self._server: asyncio.AbstractServer | None = None
        self.base_url = ""

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line = (await reader.readline()).decode()
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        target = request_line.split(" ")[1]
        parts = urlsplit(target)
        query = parse_qs(parts.query)
        self.requests.append(query)
        number = query.get("number", [""])[0]
        if self.down or number in FAILING:
            status, body = "503 Service Unavailable", b"{}"
        elif parts.path != "/1/num" or query.get("id") != [APP_ID] or query.get("type") != ["21"]:
            status, body = "403 Forbidden", b'{"error": "invalid application id or parameters"}'
        else:
            name = RESPONSES.get(number, "announcement_not_found.json")
            status, body = "200 OK", (FIXTURES / name).read_bytes()
            json.loads(body)  # fixtures must stay valid JSON
        writer.write(
            f"HTTP/1.1 {status}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()

    async def __aenter__(self) -> "NtaMock":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}/1"
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()
