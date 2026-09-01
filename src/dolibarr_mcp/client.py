"""Asynchronous Dolibarr API client with a shared connection pool."""

from __future__ import annotations

import ssl
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Literal, Self, TypeVar

import httpx2
from pydantic import TypeAdapter, ValidationError

from dolibarr_mcp.errors import (
    DolibarrConflictError,
    DolibarrNotFoundError,
    DolibarrPermissionDeniedError,
    DolibarrRateLimitedError,
    DolibarrResultLimitError,
    DolibarrUnavailableError,
    DolibarrWriteRejectedError,
    InvalidDolibarrCredentialsError,
    InvalidDolibarrResponseError,
)
from dolibarr_mcp.models import (
    DolibarrLeaveRequestPayload,
    DolibarrLeaveTypePayload,
    DolibarrProjectContactPayload,
    DolibarrProjectPayload,
    DolibarrTaskPayload,
    DolibarrThirdpartyPayload,
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
_MAX_SALES_RECORDS = 10_000
_MAX_LEAVE_REQUESTS = 10_000
_MAX_LEAVE_TYPES = 1_000

T = TypeVar("T")

_IDENTITY_ADAPTER = TypeAdapter(DolibarrUserPayload)
_LEAVE_ADAPTER = TypeAdapter(DolibarrLeaveRequestPayload)
_LEAVE_LIST_ADAPTER = TypeAdapter(list[DolibarrLeaveRequestPayload])
_LEAVE_TYPE_LIST_ADAPTER = TypeAdapter(list[DolibarrLeaveTypePayload])
_PROJECT_ADAPTER = TypeAdapter(DolibarrProjectPayload)
_PROJECT_LIST_ADAPTER = TypeAdapter(list[DolibarrProjectPayload])
_PROJECT_CONTACT_LIST_ADAPTER = TypeAdapter(list[DolibarrProjectContactPayload])
_TASK_ADAPTER = TypeAdapter(DolibarrTaskPayload)
_TASK_LIST_ADAPTER = TypeAdapter(list[DolibarrTaskPayload])
_TIME_LIST_ADAPTER = TypeAdapter(list[DolibarrTimeEntryPayload])
_USER_LIST_ADAPTER = TypeAdapter(list[DolibarrUserPayload])
_THIRDPARTY_ADAPTER = TypeAdapter(DolibarrThirdpartyPayload)
_THIRDPARTY_LIST_ADAPTER = TypeAdapter(list[DolibarrThirdpartyPayload])
_INTEGER_ADAPTER = TypeAdapter(int)
_ANY_ADAPTER = TypeAdapter(object)

HttpMethod = Literal["GET", "POST", "PUT", "DELETE"]


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
        payload = await self._request_typed(
            api_key,
            method="GET",
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
                    "properties": "id,login,firstname,lastname,status,statut",
                },
                adapter=_USER_LIST_ADAPTER,
            )
            users.extend(current)
            if len(current) < _UPSTREAM_PAGE_SIZE:
                return users
        raise DolibarrResultLimitError

    async def get_user(self, api_key: str, user_id: int) -> DolibarrUserPayload:
        """Return one allowlisted user used for lead assignment."""
        return await self._get_api(
            api_key,
            path=f"users/{user_id}",
            adapter=_IDENTITY_ADAPTER,
        )

    async def list_leave_types(self, api_key: str) -> list[DolibarrLeaveTypePayload]:
        """Return active leave types through the fixed setup dictionary endpoint."""
        rows: list[DolibarrLeaveTypePayload] = []
        for page in range(_MAX_LEAVE_TYPES // _UPSTREAM_PAGE_SIZE):
            current = await self._get_api(
                api_key,
                path="setup/dictionary/holiday_types",
                params={
                    "limit": _UPSTREAM_PAGE_SIZE,
                    "page": page,
                    "active": 1,
                },
                adapter=_LEAVE_TYPE_LIST_ADAPTER,
            )
            rows.extend(current)
            if len(current) < _UPSTREAM_PAGE_SIZE:
                return rows
        raise DolibarrResultLimitError

    async def list_leave_requests(
        self,
        api_key: str,
        *,
        employee_id: int | None = None,
    ) -> list[DolibarrLeaveRequestPayload]:
        """Return accessible leave requests within a fixed processing bound."""
        rows: list[DolibarrLeaveRequestPayload] = []
        properties = (
            "id,ref,fk_user,fk_validator,fk_type,date_debut,date_fin,halfday,status,statut,"
            "description,detail_refuse"
        )
        for page in range(_MAX_LEAVE_REQUESTS // _UPSTREAM_PAGE_SIZE):
            params: dict[str, str | int] = {
                "limit": _UPSTREAM_PAGE_SIZE,
                "page": page,
                "properties": properties,
            }
            if employee_id is not None:
                params["user_ids"] = employee_id
            current = await self._get_api(
                api_key,
                path="holidays",
                params=params,
                adapter=_LEAVE_LIST_ADAPTER,
            )
            rows.extend(current)
            if len(current) < _UPSTREAM_PAGE_SIZE:
                return rows
        raise DolibarrResultLimitError

    async def get_leave_request(
        self,
        api_key: str,
        request_id: int,
    ) -> DolibarrLeaveRequestPayload:
        """Return one authorized leave request."""
        return await self._get_api(
            api_key,
            path=f"holidays/{request_id}",
            adapter=_LEAVE_ADAPTER,
        )

    async def create_leave_request(self, api_key: str, payload: dict[str, object]) -> int:
        """Create one draft leave request through the fixed official endpoint."""
        return await self._request_api(
            api_key,
            method="POST",
            path="holidays",
            json_body=payload,
            adapter=_INTEGER_ADAPTER,
        )

    async def update_leave_request(
        self,
        api_key: str,
        request_id: int,
        payload: dict[str, object],
    ) -> DolibarrLeaveRequestPayload:
        """Apply one allowlisted partial update to a leave request."""
        return await self._request_api(
            api_key,
            method="PUT",
            path=f"holidays/{request_id}",
            json_body=payload,
            adapter=_LEAVE_ADAPTER,
        )

    async def submit_leave_request(
        self,
        api_key: str,
        request_id: int,
    ) -> DolibarrLeaveRequestPayload:
        """Submit a draft leave request using Dolibarr's validate action."""
        return await self._transition_leave_request(api_key, request_id, "validate")

    async def approve_leave_request(
        self,
        api_key: str,
        request_id: int,
    ) -> DolibarrLeaveRequestPayload:
        """Approve a submitted leave request."""
        return await self._transition_leave_request(api_key, request_id, "approve")

    async def cancel_leave_request(
        self,
        api_key: str,
        request_id: int,
    ) -> DolibarrLeaveRequestPayload:
        """Cancel a submitted or approved leave request."""
        return await self._transition_leave_request(api_key, request_id, "cancel")

    async def refuse_leave_request(
        self,
        api_key: str,
        request_id: int,
        refusal_reason: str,
    ) -> DolibarrLeaveRequestPayload:
        """Refuse a submitted leave request with a bounded reason."""
        return await self._transition_leave_request(
            api_key,
            request_id,
            "refuse",
            refusal_reason=refusal_reason,
        )

    async def reopen_leave_request(
        self,
        api_key: str,
        request_id: int,
    ) -> DolibarrLeaveRequestPayload:
        """Reopen a canceled leave request to submitted state."""
        return await self._transition_leave_request(api_key, request_id, "reopen")

    async def _transition_leave_request(
        self,
        api_key: str,
        request_id: int,
        action: Literal["validate", "approve", "cancel", "refuse", "reopen"],
        *,
        refusal_reason: str | None = None,
    ) -> DolibarrLeaveRequestPayload:
        body: dict[str, object] = {"notrigger": 0}
        if action == "refuse":
            if refusal_reason is None:
                message = "Refusal reason is required for the refuse action"
                raise RuntimeError(message)
            body["detail_refuse"] = refusal_reason
        elif refusal_reason is not None:
            message = "Refusal reason is allowed only for the refuse action"
            raise RuntimeError(message)
        return await self._request_api(
            api_key,
            method="POST",
            path=f"holidays/{request_id}/{action}",
            json_body=body,
            adapter=_LEAVE_ADAPTER,
        )

    async def list_thirdparties(self, api_key: str) -> list[DolibarrThirdpartyPayload]:
        """Return all accessible allowlisted third parties within a fixed bound."""
        rows: list[DolibarrThirdpartyPayload] = []
        properties = (
            "id,name,name_alias,address,zip,town,country_code,email,phone,tva_intra,client,"
            "note_public,note_private,tms,date_modification"
        )
        for page in range(_MAX_SALES_RECORDS // _UPSTREAM_PAGE_SIZE):
            current = await self._get_api(
                api_key,
                path="thirdparties",
                params={"limit": _UPSTREAM_PAGE_SIZE, "page": page, "properties": properties},
                adapter=_THIRDPARTY_LIST_ADAPTER,
            )
            rows.extend(current)
            if len(current) < _UPSTREAM_PAGE_SIZE:
                return rows
        raise DolibarrResultLimitError

    async def get_thirdparty(
        self,
        api_key: str,
        thirdparty_id: int,
    ) -> DolibarrThirdpartyPayload:
        """Return one allowlisted third party."""
        return await self._get_api(
            api_key,
            path=f"thirdparties/{thirdparty_id}",
            adapter=_THIRDPARTY_ADAPTER,
        )

    async def create_thirdparty(self, api_key: str, payload: dict[str, object]) -> int:
        """Create one third party through the fixed official endpoint."""
        return await self._request_api(
            api_key,
            method="POST",
            path="thirdparties",
            json_body=payload,
            adapter=_INTEGER_ADAPTER,
        )

    async def update_thirdparty(
        self,
        api_key: str,
        thirdparty_id: int,
        payload: dict[str, object],
    ) -> DolibarrThirdpartyPayload:
        """Apply one allowlisted partial third-party update."""
        return await self._request_api(
            api_key,
            method="PUT",
            path=f"thirdparties/{thirdparty_id}",
            json_body=payload,
            adapter=_THIRDPARTY_ADAPTER,
        )

    async def list_projects(self, api_key: str) -> list[DolibarrProjectPayload]:
        """Return accessible project metadata for lead lookup within a fixed bound."""
        rows: list[DolibarrProjectPayload] = []
        properties = (
            "id,ref,title,socid,fk_soc,status,usage_opportunity,fk_opp_status,opp_status,"
            "opp_status_code,description,opp_amount,opp_percent,date_start,date_end,"
            "note_public,note_private,tms,date_modification"
        )
        for page in range(_MAX_SALES_RECORDS // _UPSTREAM_PAGE_SIZE):
            current = await self._get_api(
                api_key,
                path="projects",
                params={"limit": _UPSTREAM_PAGE_SIZE, "page": page, "properties": properties},
                adapter=_PROJECT_LIST_ADAPTER,
            )
            rows.extend(current)
            if len(current) < _UPSTREAM_PAGE_SIZE:
                return rows
        raise DolibarrResultLimitError

    async def create_project(self, api_key: str, payload: dict[str, object]) -> int:
        """Create one project through the fixed official endpoint."""
        return await self._request_api(
            api_key,
            method="POST",
            path="projects",
            json_body=payload,
            adapter=_INTEGER_ADAPTER,
        )

    async def update_project(
        self,
        api_key: str,
        project_id: int,
        payload: dict[str, object],
    ) -> DolibarrProjectPayload:
        """Apply one allowlisted partial project update."""
        return await self._request_api(
            api_key,
            method="PUT",
            path=f"projects/{project_id}",
            json_body=payload,
            adapter=_PROJECT_ADAPTER,
        )

    async def validate_project(self, api_key: str, project_id: int) -> None:
        """Open or reopen a project using Dolibarr's dedicated transition endpoint."""
        await self._request_api(
            api_key,
            method="POST",
            path=f"projects/{project_id}/validate",
            json_body={"notrigger": 0},
            adapter=_ANY_ADAPTER,
        )

    async def get_project_contacts(
        self,
        api_key: str,
        project_id: int,
    ) -> list[DolibarrProjectContactPayload]:
        """Return allowlisted internal and external project contact relations."""
        return await self._get_api(
            api_key,
            path=f"projects/{project_id}/contacts",
            adapter=_PROJECT_CONTACT_LIST_ADAPTER,
        )

    async def add_project_leader(
        self,
        api_key: str,
        project_id: int,
        user_id: int,
    ) -> None:
        """Add one internal PROJECTLEADER relation through the official API."""
        await self._request_api(
            api_key,
            method="POST",
            path=f"projects/{project_id}/contacts",
            json_body={
                "fk_socpeople": user_id,
                "type_contact": "PROJECTLEADER",
                "source": "internal",
                "notrigger": 0,
            },
            adapter=_ANY_ADAPTER,
        )

    async def delete_project_leader(
        self,
        api_key: str,
        project_id: int,
        user_id: int,
    ) -> None:
        """Delete one PROJECTLEADER relation through the fixed official route."""
        await self._request_api(
            api_key,
            method="DELETE",
            path=f"projects/{project_id}/contact/{user_id}/PROJECTLEADER",
            adapter=_ANY_ADAPTER,
        )

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
        return await self._request_api(
            api_key,
            method="GET",
            path=path,
            params=params,
            adapter=adapter,
        )

    async def _request_api(
        self,
        api_key: str,
        *,
        method: HttpMethod,
        path: str,
        adapter: TypeAdapter[T],
        params: dict[str, str | int] | None = None,
        json_body: dict[str, object] | None = None,
    ) -> T:
        """Perform one fixed-path Dolibarr API request."""
        if not path or path.startswith("/") or ".." in path:
            message = "Invalid internal Dolibarr API path"
            raise RuntimeError(message)
        return await self._request_typed(
            api_key,
            method=method,
            url=f"{self._api_base_url}/{path}",
            params=params,
            json_body=json_body,
            adapter=adapter,
            identity_request=False,
        )

    async def _request_typed(  # noqa: PLR0912 - explicit status mapping is security-sensitive
        self,
        api_key: str,
        *,
        method: HttpMethod,
        url: str,
        adapter: TypeAdapter[T],
        params: dict[str, str | int] | None = None,
        json_body: dict[str, object] | None = None,
        identity_request: bool,
    ) -> T:
        """Perform one credential-isolated request and validate its allowlisted payload."""
        try:
            if json_body is None:
                response = await self._client.request(
                    method,
                    url,
                    headers={"DOLAPIKEY": api_key},
                    params=params,
                )
            else:
                response = await self._client.request(
                    method,
                    url,
                    headers={"DOLAPIKEY": api_key},
                    params=params,
                    json=json_body,
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
        if not identity_request and response.status_code == HTTPStatus.CONFLICT:
            raise DolibarrConflictError
        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            raise DolibarrRateLimitedError(retry_after=validated_retry_after(response))
        if HTTPStatus.INTERNAL_SERVER_ERROR <= response.status_code <= _MAX_SERVER_ERROR_STATUS:
            raise DolibarrUnavailableError
        if method != "GET" and response.status_code in {
            HTTPStatus.BAD_REQUEST,
            HTTPStatus.METHOD_NOT_ALLOWED,
            HTTPStatus.UNPROCESSABLE_ENTITY,
        }:
            raise DolibarrWriteRejectedError
        if HTTPStatus.BAD_REQUEST <= response.status_code <= _MAX_CLIENT_ERROR_STATUS:
            raise InvalidDolibarrResponseError
        if not HTTPStatus.OK <= response.status_code <= _MAX_SUCCESS_STATUS:
            raise InvalidDolibarrResponseError

        try:
            return adapter.validate_python(response.json())
        except (ValueError, ValidationError):
            raise InvalidDolibarrResponseError from None
