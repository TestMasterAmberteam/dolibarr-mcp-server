"""Structured logging with centralized secret redaction and request IDs."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_SENSITIVE_KEY = re.compile(
    r"authorization|api[_-]?key|apikey|dolapikey|token|secret|password|cookie",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/=]+")
_DOLAPIKEY_VALUE = re.compile(r"(?i)(dolapikey[\s:=]+)[^\s,;]+")
REDACTED: Final = "[REDACTED]"


def redact(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact named secret fields and recognizable credential strings."""
    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {str(item_key): redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        redacted = _BEARER_VALUE.sub(rf"\1{REDACTED}", value)
        return _DOLAPIKEY_VALUE.sub(rf"\1{REDACTED}", redacted)
    return value


class JsonFormatter(logging.Formatter):
    """Emit stable JSON records containing no exception internals or request bodies."""

    def format(self, record: logging.LogRecord) -> str:
        message = redact(record.getMessage())
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "request_id": _request_id.get(),
        }
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def configure_logging(level: str) -> None:
    """Configure application logs and quiet HTTP library diagnostics."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpcore2").setLevel(logging.WARNING)


class RequestContextMiddleware:
    """Attach a generated correlation ID and log safe request metadata."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._logger = logging.getLogger("dolibarr_mcp.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        request_id = str(uuid.uuid4())
        context_token = _request_id.set(request_id)
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self._app(scope, receive, send_with_request_id)
        finally:
            self._logger.info(
                "request_completed method=%s path=%s status=%d",
                scope.get("method", ""),
                scope.get("path", ""),
                status_code,
            )
            _request_id.reset(context_token)
