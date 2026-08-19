"""Asynchronous Dolibarr API client with a shared connection pool."""

from __future__ import annotations

import ssl
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Self

import httpx2
from pydantic import ValidationError

from dolibarr_mcp.errors import (
    DolibarrRateLimitedError,
    DolibarrUnavailableError,
    InvalidDolibarrCredentialsError,
    InvalidDolibarrResponseError,
)
from dolibarr_mcp.models import DolibarrUserPayload, VerifiedIdentity

if TYPE_CHECKING:
    from types import TracebackType

    from dolibarr_mcp.config import Settings

_CONTROL_CHARACTER_BOUNDARY = 32
_DELETE_CHARACTER = 127
_MAX_SUCCESS_STATUS = 299
_MAX_CLIENT_ERROR_STATUS = 499
_MAX_SERVER_ERROR_STATUS = 599


def validated_retry_after(response: httpx2.Response) -> str | None:
    """Return one syntactically valid Retry-After value, otherwise nothing."""
    values = response.headers.get_list("retry-after")
    if len(values) != 1:
        return None
    value = values[0]
    if not value or any(
        ord(character) < _CONTROL_CHARACTER_BOUNDARY or ord(character) == _DELETE_CHARACTER
        for character in value
    ):
        return None
    if value.isascii() and value.isdigit():
        return value
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if parsed.tzinfo is not None else None


class DolibarrClient:
    """Typed per-request access over one immutable, shared HTTP client."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        verify: ssl.SSLContext | bool = True
        if settings.dolibarr_ca_bundle is not None:
            verify = ssl.create_default_context(cafile=str(settings.dolibarr_ca_bundle))
        self._users_info_url = settings.users_info_url
        self._client = httpx2.AsyncClient(
            verify=verify,
            timeout=httpx2.Timeout(
                connect=float(settings.dolibarr_connect_timeout),
                read=float(settings.dolibarr_read_timeout),
                write=float(settings.dolibarr_write_timeout),
                pool=float(settings.dolibarr_pool_timeout),
            ),
            limits=httpx2.Limits(
                max_connections=settings.dolibarr_max_connections,
                max_keepalive_connections=settings.dolibarr_max_keepalive_connections,
            ),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._client.__aexit__(exc_type, exc_value, traceback)

    async def get_current_user(self, api_key: str) -> VerifiedIdentity:
        """Validate one request's key without mutating or caching shared client state."""
        try:
            response = await self._client.get(
                self._users_info_url,
                headers={"DOLAPIKEY": api_key},
            )
        except httpx2.TimeoutException:
            raise DolibarrUnavailableError from None
        except httpx2.RequestError:
            raise DolibarrUnavailableError from None

        if response.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
            raise InvalidDolibarrCredentialsError
        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            raise DolibarrRateLimitedError(retry_after=validated_retry_after(response))
        if HTTPStatus.INTERNAL_SERVER_ERROR <= response.status_code <= _MAX_SERVER_ERROR_STATUS:
            raise DolibarrUnavailableError
        if HTTPStatus.BAD_REQUEST <= response.status_code <= _MAX_CLIENT_ERROR_STATUS:
            raise InvalidDolibarrResponseError
        if not HTTPStatus.OK <= response.status_code <= _MAX_SUCCESS_STATUS:
            raise InvalidDolibarrResponseError

        try:
            payload = DolibarrUserPayload.model_validate(response.json())
        except (ValueError, ValidationError):
            raise InvalidDolibarrResponseError from None
        return payload.to_identity()
