from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)


def get_request_id() -> str | None:
    return _request_id.get()


def get_client_ip() -> str | None:
    return _client_ip.get()


def set_request_context(request_id: str, client_ip: str | None) -> None:
    _request_id.set(request_id)
    _client_ip.set(client_ip)
