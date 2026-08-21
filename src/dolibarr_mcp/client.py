"""Asynchronous Dolibarr API client with a shared connection pool."""

from __future__ import annotations

import ssl
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Self, TypeVar

import httpx2
from pydantic import TypeAdapter, ValidationError

from dolibarr_mcp.errors import (
    DolibarrNotFoundError,
    DolibarrPermissionDeniedError,
    DolibarrRateLimitedError,
    DolibarrResultLimitError,
    DolibarrUnavailableError,
    InvalidDolibarrCredentialsError,
    InvalidDolibarrResponseError,
)
from dolibarr_mcp.models import (
    DolibarrProjectPayload,
    DolibarrTaskPayload,
    DolibarrTimeEntryPayload,
    DolibarrUserPayload,
    VerifiedIdentity,
)

if TYPE_CHECKING:
    from types import TracebackType

    from dolibarr_mcp.config import Settings

_CONTROL_CHARACTER_BOUNDARY = 32
_DELETE_CHARACTER = 127
_MAX_SUCCESS_STATUS = 299
_MAX_CLIENT_ERROR_STATUS = 499
_MAX_SERVER_ERROR_STATUS = 599
_UPSTREAM_PAGE_SIZE = 100
_MAX_TASKS = 1000
_MAX_USERS = 1000
_MAX_TIME_LINES = 50_000

T = TypeVar("T")

_IDENTITY_ADAPTER = TypeAdapter(DolibarrUserPayload)
_PROJECT_ADAPTER = TypeAdapter(DolibarrProjectPayload)
_TASK_ADAPTER = TypeAdapter(DolibarrTaskPayload)
_TASK_LIST_ADAPTER = TypeAdapter(list[DolibarrTaskPayload])
_TIME_LIST_ADAPTER = TypeAdapter(list[DolibarrTimeEntryPayload])
_USER_LIST_ADAPTER = TypeAdapter(list[DolibarrUserPayload])


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
        self._api_base_url = settings.api_base_url
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
        payload = await self._get_typed(
            api_key,
            url=self._users_info_url,
            adapter=_IDENTITY_ADAPTER,
            identity_request=True,
        )
        return payload.to_identity()

    async def get_project(self, api_key: str, project_id: int) -> DolibarrProjectPayload:
        """Return allowlisted metadata for one authorized project."""
        return await self._get_api(
            api_key,
            path=f"projects/{project_id}",
            adapter=_PROJECT_ADAPTER,
        )

    async def get_task(self, api_key: str, task_id: int) -> DolibarrTaskPayload:
        """Return allowlisted metadata for one authorized task."""
        return await self._get_api(
            api_key,
            path=f"tasks/{task_id}",
            adapter=_TASK_ADAPTER,
        )

    async def get_task_timespent(
        self,
        api_key: str,
        task_id: int,
    ) -> list[DolibarrTimeEntryPayload]:
        """Return bounded detailed time lines for one authorized task."""
        lines = await self._get_api(
            api_key,
            path=f"tasks/{task_id}/timespent",
            adapter=_TIME_LIST_ADAPTER,
        )
        if len(lines) > _MAX_TIME_LINES:
            raise DolibarrResultLimitError
        return lines

    async def get_project_tasks(
        self,
        api_key: str,
        project_id: int,
    ) -> list[DolibarrTaskPayload]:
        """Return bounded project tasks with detailed time lines."""
        tasks = await self._get_api(
            api_key,
            path=f"projects/{project_id}/tasks",
            params={"includetimespent": 2},
            adapter=_TASK_LIST_ADAPTER,
        )
        if len(tasks) > _MAX_TASKS:
            raise DolibarrResultLimitError
        if sum(len(task.lines or []) for task in tasks) > _MAX_TIME_LINES:
            raise DolibarrResultLimitError
        return tasks

    async def list_tasks(self, api_key: str) -> list[DolibarrTaskPayload]:
        """Return all accessible task metadata within a fixed local bound."""
        tasks: list[DolibarrTaskPayload] = []
        for page in range(_MAX_TASKS // _UPSTREAM_PAGE_SIZE):
            current = await self._get_api(
                api_key,
                path="tasks",
                params={"limit": _UPSTREAM_PAGE_SIZE, "page": page},
                adapter=_TASK_LIST_ADAPTER,
            )
            tasks.extend(current)
            if len(current) < _UPSTREAM_PAGE_SIZE:
                return tasks
        raise DolibarrResultLimitError

    async def list_users(self, api_key: str) -> list[DolibarrUserPayload]:
        """Return allowlisted accessible user labels within a fixed local bound."""
        users: list[DolibarrUserPayload] = []
        for page in range(_MAX_USERS // _UPSTREAM_PAGE_SIZE):
            current = await self._get_api(
                api_key,
                path="users",
                params={
                    "limit": _UPSTREAM_PAGE_SIZE,
                    "page": page,
                    "properties": "id,login,firstname,lastname",
                },
                adapter=_USER_LIST_ADAPTER,
            )
            users.extend(current)
            if len(current) < _UPSTREAM_PAGE_SIZE:
                return users
        raise DolibarrResultLimitError

    async def _get_api(
        self,
        api_key: str,
        *,
        path: str,
        adapter: TypeAdapter[T],
        params: dict[str, str | int] | None = None,
    ) -> T:
        if not path or path.startswith("/") or ".." in path:
            message = "Invalid internal Dolibarr API path"
            raise RuntimeError(message)
        return await self._get_typed(
            api_key,
            url=f"{self._api_base_url}/{path}",
            params=params,
            adapter=adapter,
            identity_request=False,
        )

    async def _get_typed(
        self,
        api_key: str,
        *,
        url: str,
        adapter: TypeAdapter[T],
        params: dict[str, str | int] | None = None,
        identity_request: bool,
    ) -> T:
        """Perform one credential-isolated GET and validate its allowlisted payload."""
        try:
            response = await self._client.get(
                url,
                headers={"DOLAPIKEY": api_key},
                params=params,
            )
        except httpx2.TimeoutException:
            raise DolibarrUnavailableError from None
        except httpx2.RequestError:
            raise DolibarrUnavailableError from None

        if response.status_code == HTTPStatus.UNAUTHORIZED or (
            identity_request and response.status_code == HTTPStatus.FORBIDDEN
        ):
            raise InvalidDolibarrCredentialsError
        if response.status_code == HTTPStatus.FORBIDDEN:
            raise DolibarrPermissionDeniedError
        if not identity_request and response.status_code == HTTPStatus.NOT_FOUND:
            raise DolibarrNotFoundError
        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            raise DolibarrRateLimitedError(retry_after=validated_retry_after(response))
        if HTTPStatus.INTERNAL_SERVER_ERROR <= response.status_code <= _MAX_SERVER_ERROR_STATUS:
            raise DolibarrUnavailableError
        if HTTPStatus.BAD_REQUEST <= response.status_code <= _MAX_CLIENT_ERROR_STATUS:
            raise InvalidDolibarrResponseError
        if not HTTPStatus.OK <= response.status_code <= _MAX_SUCCESS_STATUS:
            raise InvalidDolibarrResponseError

        try:
            return adapter.validate_python(response.json())
        except (ValueError, ValidationError):
            raise InvalidDolibarrResponseError from None
