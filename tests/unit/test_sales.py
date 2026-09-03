"""Sales service behavior over a stateful, API-shaped client double."""

# ruff: noqa: ARG002

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError

from dolibarr_mcp.config import LeadStageConfig
from dolibarr_mcp.errors import (
    DolibarrNotFoundError,
    DolibarrUnavailableError,
    SalesConfirmationError,
    SalesInactiveStageError,
    SalesRequestError,
    SalesStageResolutionError,
)
from dolibarr_mcp.models import (
    DolibarrProjectContactPayload,
    DolibarrProjectPayload,
    DolibarrThirdpartyPayload,
    DolibarrUserPayload,
    LeadCreateInput,
    LeadUpdateInput,
    MutationPreview,
    MutationResult,
    ThirdpartyCreateInput,
    ThirdpartyDetail,
    ThirdpartyUpdateInput,
)
from dolibarr_mcp.sales import SalesService

if TYPE_CHECKING:
    from dolibarr_mcp.client import DolibarrClient

pytestmark = pytest.mark.anyio


def thirdparty(
    thirdparty_id: int,
    name: str,
    *,
    email: str | None = None,
    vat_number: str | None = None,
    classification: int = 0,
) -> DolibarrThirdpartyPayload:
    return DolibarrThirdpartyPayload.model_validate(
        {
            "id": thirdparty_id,
            "name": name,
            "name_alias": "Alias" if thirdparty_id == 1 else "",
            "town": "Warsaw",
            "country_code": "PL",
            "email": email or "",
            "tva_intra": vat_number or "",
            "client": classification,
            "note_public": "p" * 5000,
            "note_private": "private",
        }
    )


def project(
    project_id: int,
    title: str,
    *,
    lead: bool = True,
    thirdparty_id: int = 1,
    stage_id: int = 4,
    stage_code: str = "NEW",
    status: int = 0,
) -> DolibarrProjectPayload:
    return DolibarrProjectPayload.model_validate(
        {
            "id": project_id,
            "ref": f"PJ-{project_id}",
            "title": title,
            "socid": thirdparty_id,
            "usage_opportunity": int(lead),
            "fk_opp_status": None,
            "opp_status": stage_id,
            "opp_status_code": stage_code,
            "status": status,
            "opp_amount": 1000,
            "opp_percent": 25,
            "description": "Description",
            "date_start": 1_788_577_600,
            "date_end": 1_789_009_600,
            "note_public": "public",
            "note_private": "private",
        }
    )


def stage_config(
    stage_id: int,
    *,
    label: str = "P3L - Lost",
    aliases: list[str] | None = None,
    percent: float = 0,
    position: int = 70,
    active: bool = True,
) -> LeadStageConfig:
    return LeadStageConfig(
        id=stage_id,
        label=label,
        aliases=aliases or [],
        percent=percent,
        position=position,
        active=active,
    )


def user(user_id: int, login: str, *, status: int = 1) -> DolibarrUserPayload:
    return DolibarrUserPayload.model_validate(
        {
            "id": user_id,
            "login": login,
            "firstname": login.title(),
            "lastname": "Seller",
            "status": status,
        }
    )


def leader(user_id: int, login: str) -> DolibarrProjectContactPayload:
    return DolibarrProjectContactPayload.model_validate(
        {
            "id": user_id,
            "rowid": user_id + 100,
            "code": "PROJECTLEADER",
            "source": "internal",
            "login": login,
            "firstname": login.title(),
            "lastname": "Seller",
        }
    )


class FakeSalesClient:
    """Stateful client double whose methods correspond to fixed Dolibarr API calls."""

    def __init__(self) -> None:
        self.thirdparties = {
            1: thirdparty(
                1,
                "Acme Sp. z o.o.",
                email="sales@acme.example",
                vat_number="PL-123",
                classification=2,
            ),
            2: thirdparty(2, "Customer SA", classification=1),
        }
        self.projects = {
            10: project(10, "Acme Renewal"),
            11: project(11, "Ordinary Project", lead=False),
            12: project(12, "Closed Lead", status=2, stage_id=7, stage_code="LOST"),
        }
        self.users = {
            7: user(7, "alice"),
            8: user(8, "bob"),
            9: user(9, "inactive", status=0),
        }
        self.contacts = {
            10: [leader(7, "alice")],
            11: [],
            12: [leader(8, "bob")],
        }
        self.calls: list[tuple[str, object]] = []
        self.fail_add = False
        self.fail_delete_user_id: int | None = None
        self.validate_no_effect = False
        self.close_no_effect = False
        self.stage_no_effect = False

    async def list_thirdparties(self, api_key: str) -> list[DolibarrThirdpartyPayload]:
        return list(self.thirdparties.values())

    async def get_thirdparty(
        self,
        api_key: str,
        thirdparty_id: int,
    ) -> DolibarrThirdpartyPayload:
        try:
            return self.thirdparties[thirdparty_id]
        except KeyError:
            raise DolibarrNotFoundError from None

    async def create_thirdparty(self, api_key: str, payload: dict[str, object]) -> int:
        self.calls.append(("create_thirdparty", payload))
        thirdparty_id = max(self.thirdparties) + 1
        self.thirdparties[thirdparty_id] = DolibarrThirdpartyPayload.model_validate(
            {"id": thirdparty_id, **payload}
        )
        return thirdparty_id

    async def update_thirdparty(
        self,
        api_key: str,
        thirdparty_id: int,
        payload: dict[str, object],
    ) -> DolibarrThirdpartyPayload:
        self.calls.append(("update_thirdparty", payload))
        mapping = {
            "name": "name",
            "name_alias": "alias",
            "address": "address",
            "zip": "postal_code",
            "town": "city",
            "country_code": "country_code",
            "email": "email",
            "phone": "phone",
            "tva_intra": "vat_number",
            "client": "customer_classification",
            "note_public": "public_note",
            "note_private": "private_note",
        }
        updates = {mapping[key]: value for key, value in payload.items()}
        result = self.thirdparties[thirdparty_id].model_copy(update=updates)
        self.thirdparties[thirdparty_id] = result
        return result

    async def list_users(self, api_key: str) -> list[DolibarrUserPayload]:
        return list(self.users.values())

    async def get_user(self, api_key: str, user_id: int) -> DolibarrUserPayload:
        try:
            return self.users[user_id]
        except KeyError:
            raise DolibarrNotFoundError from None

    async def list_projects(self, api_key: str) -> list[DolibarrProjectPayload]:
        return list(self.projects.values())

    async def list_lead_stage_observations(
        self,
        api_key: str,
    ) -> list[DolibarrProjectPayload]:
        return list(self.projects.values())

    async def get_project(self, api_key: str, project_id: int) -> DolibarrProjectPayload:
        try:
            return self.projects[project_id]
        except KeyError:
            raise DolibarrNotFoundError from None

    async def create_project(self, api_key: str, payload: dict[str, object]) -> int:
        self.calls.append(("create_project", payload))
        project_id = max(self.projects) + 1
        self.projects[project_id] = DolibarrProjectPayload.model_validate(
            {"id": project_id, "ref": f"PJ-{project_id}", **payload}
        )
        self.contacts[project_id] = []
        return project_id

    async def update_project(
        self,
        api_key: str,
        project_id: int,
        payload: dict[str, object],
    ) -> DolibarrProjectPayload:
        self.calls.append(("update_project", payload))
        mapping = {
            "socid": "thirdparty_id",
            "title": "label",
            "description": "description",
            "opp_amount": "amount",
            "opp_percent": "probability_percent",
            "date_start": "date_start",
            "date_end": "date_end",
            "note_public": "public_note",
            "note_private": "private_note",
            "opp_status": "stage_id",
        }
        updates = {mapping[key]: value for key, value in payload.items()}
        if self.stage_no_effect:
            updates.pop("stage_id", None)
        result = self.projects[project_id].model_copy(update=updates)
        self.projects[project_id] = result
        return result

    async def validate_project(self, api_key: str, project_id: int) -> None:
        self.calls.append(("validate_project", project_id))
        if not self.validate_no_effect:
            self.projects[project_id] = self.projects[project_id].model_copy(update={"status": 1})

    async def close_project(self, api_key: str, project_id: int) -> DolibarrProjectPayload:
        self.calls.append(("close_project", project_id))
        if not self.close_no_effect:
            self.projects[project_id] = self.projects[project_id].model_copy(update={"status": 2})
        return self.projects[project_id]

    async def get_project_contacts(
        self,
        api_key: str,
        project_id: int,
    ) -> list[DolibarrProjectContactPayload]:
        return list(self.contacts[project_id])

    async def add_project_leader(
        self,
        api_key: str,
        project_id: int,
        user_id: int,
    ) -> None:
        self.calls.append(("add_project_leader", user_id))
        if self.fail_add:
            raise DolibarrUnavailableError
        selected = self.users[user_id]
        self.contacts[project_id].append(leader(user_id, selected.login))

    async def delete_project_leader(
        self,
        api_key: str,
        project_id: int,
        user_id: int,
    ) -> None:
        self.calls.append(("delete_project_leader", user_id))
        if self.fail_delete_user_id == user_id:
            raise DolibarrUnavailableError
        self.contacts[project_id] = [
            contact for contact in self.contacts[project_id] if contact.contact_id != user_id
        ]


def service(
    fake: FakeSalesClient,
    lead_stage_catalog: dict[str, LeadStageConfig] | None = None,
) -> SalesService:
    return SalesService(
        cast("DolibarrClient", fake),
        lead_stage_catalog=lead_stage_catalog,
    )


async def test_thirdparty_search_get_and_user_search_are_allowlisted() -> None:
    fake = FakeSalesClient()
    sales = service(fake)

    result = await sales.thirdparty_search(
        "key",
        query="pl123",
        customer_status="prospect",
        offset=0,
        limit=10,
    )
    assert [row.thirdparty_id for row in result.rows] == [1]
    assert result.rows[0].customer_status == "prospect"
    assert result.has_more is False

    punctuation = await sales.thirdparty_search(
        "key",
        query="-",
        customer_status=None,
        offset=0,
        limit=10,
    )
    assert punctuation.rows == []

    detail = await sales.thirdparty_get("key", 1)
    assert isinstance(detail, ThirdpartyDetail)
    assert len(detail.public_note or "") == 4000
    assert detail.private_note == "private"
    fake.thirdparties[2] = fake.thirdparties[2].model_copy(
        update={"public_note": None, "private_note": None}
    )
    detail_without_notes = await sales.thirdparty_get("key", 2)
    assert detail_without_notes.public_note is None

    users = await sales.user_search("key", query="sell", offset=1, limit=1)
    assert users.total_count == 2
    assert users.returned_count == 1
    assert users.has_more is False
    assert users.rows[0].login == "bob"


async def test_lead_search_uses_usage_flag_and_fixed_filters() -> None:
    fake = FakeSalesClient()
    fake.contacts[10].append(leader(99, "external").model_copy(update={"source": "external"}))
    sales = service(fake)

    result = await sales.lead_search(
        "key",
        query="lead",
        thirdparty_id=None,
        stage_id=7,
        project_state="closed",
        owner_user_id=8,
        offset=0,
        limit=10,
    )
    assert [row.project_id for row in result.rows] == [12]
    assert result.rows[0].owners[0].user_id == 8
    assert result.rows[0].project_state == "closed"

    unowned = await sales.lead_search(
        "key",
        query="renewal",
        thirdparty_id=1,
        stage_id=4,
        project_state="draft",
        owner_user_id=8,
        offset=0,
        limit=10,
    )
    assert unowned.rows == []

    without_owner_filter = await sales.lead_search(
        "key",
        query=None,
        thirdparty_id=1,
        stage_id=None,
        project_state=None,
        owner_user_id=None,
        offset=0,
        limit=1,
    )
    assert without_owner_filter.returned_count == 1
    assert without_owner_filter.has_more is True

    with pytest.raises(DolibarrNotFoundError):
        await sales.lead_get("key", 11)


async def test_lead_stage_list_returns_observed_codes_and_is_explicitly_incomplete() -> None:
    fake = FakeSalesClient()
    fake.projects[13] = project(13, "Second P3L", stage_id=7, stage_code="LOST")
    sales = service(fake)

    result = await sales.lead_stage_list("key", query="lost")

    assert result.complete is False
    assert result.source == "operator_catalog_and_accessible_leads"
    assert result.count == 1
    assert result.rows[0].stage_id == 7
    assert result.rows[0].stage_code == "LOST"
    assert result.rows[0].configured is False
    assert result.rows[0].observed_lead_count == 2
    assert "no complete" in result.warnings[0].casefold()

    by_identifier = await sales.lead_stage_list("key", query="4")
    assert [row.stage_id for row in by_identifier.rows] == [4]


async def test_lead_stage_list_includes_operator_catalog_entry() -> None:
    fake = FakeSalesClient()
    sales = service(fake, {"LOST": stage_config(17, aliases=["P3L"])})

    result = await sales.lead_stage_list("key", query="p3l")

    assert result.count == 1
    assert result.rows[0].stage_id == 17
    assert result.rows[0].stage_code == "LOST"
    assert result.rows[0].label == "P3L - Lost"
    assert result.rows[0].aliases == ["P3L"]
    assert result.rows[0].probability_percent == 0
    assert result.rows[0].position == 70
    assert result.rows[0].active is True
    assert result.rows[0].configured is True
    assert result.rows[0].observed_lead_count == 0
    assert "conflicts" in " ".join(result.warnings)
    assert "operator-supplied" in result.warnings[-1]


async def test_thirdparty_create_requires_matching_preview_and_maps_fields() -> None:
    fake = FakeSalesClient()
    sales = service(fake)
    data = ThirdpartyCreateInput(
        name="Acme Sp. z o.o.",
        customer_status="prospect",
        email="sales@acme.example",
        country_code="pl",
    )

    preview = await sales.thirdparty_create("key", data, apply=False, confirmation_token=None)
    assert isinstance(preview, MutationPreview)
    assert preview.apply is False
    assert len(preview.confirmation_token) == 64
    assert "duplicate" in preview.warnings[0].casefold()
    assert fake.calls == []

    with pytest.raises(SalesConfirmationError):
        await sales.thirdparty_create("key", data, apply=True, confirmation_token="0" * 64)

    result = await sales.thirdparty_create(
        "key",
        data,
        apply=True,
        confirmation_token=preview.confirmation_token,
    )
    assert isinstance(result, MutationResult)
    assert result.outcome == "applied"
    assert result.thirdparty is not None
    assert result.thirdparty.customer_status == "prospect"
    assert fake.calls[-1][0] == "create_thirdparty"
    payload = cast("dict[str, object]", fake.calls[-1][1])
    assert payload["client"] == 2
    assert payload["country_code"] == "PL"


async def test_thirdparty_update_rejects_stale_preview_and_supports_noop() -> None:
    fake = FakeSalesClient()
    sales = service(fake)
    data = ThirdpartyUpdateInput(city="Krakow", customer_status="customer")
    preview = await sales.thirdparty_update("key", 1, data, apply=False, confirmation_token=None)
    assert isinstance(preview, MutationPreview)
    fake.thirdparties[1] = fake.thirdparties[1].model_copy(update={"phone": "changed"})

    with pytest.raises(SalesConfirmationError):
        await sales.thirdparty_update(
            "key",
            1,
            data,
            apply=True,
            confirmation_token=preview.confirmation_token,
        )

    current_preview = await sales.thirdparty_update(
        "key", 1, data, apply=False, confirmation_token=None
    )
    assert isinstance(current_preview, MutationPreview)
    applied = await sales.thirdparty_update(
        "key",
        1,
        data,
        apply=True,
        confirmation_token=current_preview.confirmation_token,
    )
    assert isinstance(applied, MutationResult)
    assert applied.outcome == "applied"
    assert applied.thirdparty is not None
    assert applied.thirdparty.city == "Krakow"

    noop_data = ThirdpartyUpdateInput(city="Krakow")
    noop_preview = await sales.thirdparty_update(
        "key", 1, noop_data, apply=False, confirmation_token=None
    )
    assert isinstance(noop_preview, MutationPreview)
    noop = await sales.thirdparty_update(
        "key",
        1,
        noop_data,
        apply=True,
        confirmation_token=noop_preview.confirmation_token,
    )
    assert isinstance(noop, MutationResult)
    assert noop.outcome == "no_op"


async def test_lead_create_is_a_draft_opportunity_project() -> None:
    fake = FakeSalesClient()
    sales = service(fake)
    data = LeadCreateInput(
        thirdparty_id=1,
        title="Acme Renewal",
        stage_id=4,
        amount=2500,
        probability_percent=40,
        date_start=date(2026, 9, 1),
        date_end=date(2026, 9, 30),
    )
    preview = await sales.lead_create("key", data, apply=False, confirmation_token=None)
    assert isinstance(preview, MutationPreview)
    assert preview.warnings

    result = await sales.lead_create(
        "key",
        data,
        apply=True,
        confirmation_token=preview.confirmation_token,
    )
    assert isinstance(result, MutationResult)
    assert result.lead is not None
    assert result.lead.project_state == "draft"
    payload = cast("dict[str, object]", fake.calls[-1][1])
    assert payload["ref"] == "auto"
    assert payload["usage_opportunity"] == 1
    assert payload["opp_status"] == 4
    assert payload["status"] == 0
    assert isinstance(payload["date_start"], int)


async def test_lead_update_and_stage_change_are_separate() -> None:
    fake = FakeSalesClient()
    sales = service(fake)
    update = LeadUpdateInput(title="Updated Lead", thirdparty_id=2, probability_percent=50)
    preview = await sales.lead_update("key", 10, update, apply=False, confirmation_token=None)
    assert isinstance(preview, MutationPreview)
    result = await sales.lead_update(
        "key", 10, update, apply=True, confirmation_token=preview.confirmation_token
    )
    assert isinstance(result, MutationResult)
    assert result.lead is not None
    assert result.lead.title == "Updated Lead"
    assert result.lead.stage_id == 4

    noop_update = LeadUpdateInput(title="Updated Lead")
    noop_update_preview = await sales.lead_update(
        "key", 10, noop_update, apply=False, confirmation_token=None
    )
    assert isinstance(noop_update_preview, MutationPreview)
    noop_update_result = await sales.lead_update(
        "key",
        10,
        noop_update,
        apply=True,
        confirmation_token=noop_update_preview.confirmation_token,
    )
    assert isinstance(noop_update_result, MutationResult)
    assert noop_update_result.outcome == "no_op"

    stage_preview = await sales.lead_change_status(
        "key",
        10,
        stage_id=7,
        stage_code=None,
        apply=False,
        confirmation_token=None,
    )
    assert isinstance(stage_preview, MutationPreview)
    stage_result = await sales.lead_change_status(
        "key",
        10,
        stage_id=7,
        stage_code=None,
        apply=True,
        confirmation_token=stage_preview.confirmation_token,
    )
    assert isinstance(stage_result, MutationResult)
    assert stage_result.lead is not None
    assert stage_result.lead.stage_id == 7

    noop_preview = await sales.lead_change_status(
        "key",
        10,
        stage_id=7,
        stage_code=None,
        apply=False,
        confirmation_token=None,
    )
    assert isinstance(noop_preview, MutationPreview)
    noop = await sales.lead_change_status(
        "key",
        10,
        stage_id=7,
        stage_code=None,
        apply=True,
        confirmation_token=noop_preview.confirmation_token,
    )
    assert isinstance(noop, MutationResult)
    assert noop.outcome == "no_op"


async def test_lead_change_status_resolves_configured_or_observed_code() -> None:
    fake = FakeSalesClient()
    configured_sales = service(fake, {"LOST": stage_config(17, aliases=["P3L"])})

    preview = await configured_sales.lead_change_status(
        "key",
        10,
        stage_id=None,
        stage_code="p3l",
        apply=False,
        confirmation_token=None,
    )
    assert isinstance(preview, MutationPreview)
    assert preview.changes[0].field == "stage_id"
    assert preview.changes[0].after == 17
    assert "operator-supplied" in preview.warnings[0]

    applied = await configured_sales.lead_change_status(
        "key",
        10,
        stage_id=None,
        stage_code="p3l",
        apply=True,
        confirmation_token=preview.confirmation_token,
    )
    assert isinstance(applied, MutationResult)
    assert applied.lead is not None
    assert applied.lead.stage_id == 17
    assert ("update_project", {"opp_status": 17}) in fake.calls

    observed_sales = service(FakeSalesClient())
    observed = await observed_sales.lead_change_status(
        "key",
        10,
        stage_id=None,
        stage_code="LOST",
        apply=False,
        confirmation_token=None,
    )
    assert isinstance(observed, MutationPreview)
    assert observed.changes[0].after == 7

    fake.projects[10] = fake.projects[10].model_copy(update={"stage_id": 4})
    fake.stage_no_effect = True
    partial_preview = await configured_sales.lead_change_status(
        "key",
        10,
        stage_id=None,
        stage_code="P3L",
        apply=False,
        confirmation_token=None,
    )
    assert isinstance(partial_preview, MutationPreview)
    partial = await configured_sales.lead_change_status(
        "key",
        10,
        stage_id=None,
        stage_code="P3L",
        apply=True,
        confirmation_token=partial_preview.confirmation_token,
    )
    assert isinstance(partial, MutationResult)
    assert partial.outcome == "partial"
    assert partial.partial_errors


async def test_lead_change_status_rejects_configured_inactive_stage() -> None:
    sales = service(
        FakeSalesClient(),
        {
            "NEGO": stage_config(
                4,
                label="Negotiation",
                percent=60,
                position=40,
                active=False,
            )
        },
    )

    with pytest.raises(SalesInactiveStageError):
        await sales.lead_change_status(
            "key",
            10,
            stage_id=None,
            stage_code="NEGO",
            apply=False,
            confirmation_token=None,
        )
    with pytest.raises(SalesInactiveStageError):
        await sales.lead_change_status(
            "key",
            10,
            stage_id=4,
            stage_code=None,
            apply=False,
            confirmation_token=None,
        )


async def test_lead_change_status_requires_one_resolvable_stage_selector() -> None:
    sales = service(FakeSalesClient())

    with pytest.raises(SalesRequestError):
        await sales.lead_change_status(
            "key",
            10,
            stage_id=None,
            stage_code=None,
            apply=False,
            confirmation_token=None,
        )
    with pytest.raises(SalesRequestError):
        await sales.lead_change_status(
            "key",
            10,
            stage_id=7,
            stage_code="P3L",
            apply=False,
            confirmation_token=None,
        )
    with pytest.raises(SalesStageResolutionError) as exc_info:
        await sales.lead_change_status(
            "key",
            10,
            stage_id=None,
            stage_code="UNKNOWN",
            apply=False,
            confirmation_token=None,
        )
    assert "not uniquely resolvable" in str(exc_info.value)


async def test_open_project_uses_validate_and_reports_noop_or_partial() -> None:
    fake = FakeSalesClient()
    sales = service(fake)
    preview = await sales.lead_open_project("key", 10, apply=False, confirmation_token=None)
    assert isinstance(preview, MutationPreview)
    applied = await sales.lead_open_project(
        "key",
        10,
        apply=True,
        confirmation_token=preview.confirmation_token,
    )
    assert isinstance(applied, MutationResult)
    assert applied.outcome == "applied"
    assert ("validate_project", 10) in fake.calls

    noop_preview = await sales.lead_open_project("key", 10, apply=False, confirmation_token=None)
    assert isinstance(noop_preview, MutationPreview)
    noop = await sales.lead_open_project(
        "key",
        10,
        apply=True,
        confirmation_token=noop_preview.confirmation_token,
    )
    assert isinstance(noop, MutationResult)
    assert noop.outcome == "no_op"

    fake.validate_no_effect = True
    closed_preview = await sales.lead_open_project("key", 12, apply=False, confirmation_token=None)
    assert isinstance(closed_preview, MutationPreview)
    partial = await sales.lead_open_project(
        "key",
        12,
        apply=True,
        confirmation_token=closed_preview.confirmation_token,
    )
    assert isinstance(partial, MutationResult)
    assert partial.outcome == "partial"
    assert partial.partial_errors


async def test_close_project_requires_open_state_and_reports_noop_or_partial() -> None:
    fake = FakeSalesClient()
    sales = service(fake)

    with pytest.raises(SalesRequestError):
        await sales.lead_close_project("key", 10, apply=False, confirmation_token=None)

    fake.projects[10] = fake.projects[10].model_copy(update={"status": 1})
    preview = await sales.lead_close_project("key", 10, apply=False, confirmation_token=None)
    assert isinstance(preview, MutationPreview)
    assert "PROJECT_CLOSE" in preview.warnings[0]

    applied = await sales.lead_close_project(
        "key",
        10,
        apply=True,
        confirmation_token=preview.confirmation_token,
    )
    assert isinstance(applied, MutationResult)
    assert applied.outcome == "applied"
    assert applied.lead is not None
    assert applied.lead.project_state == "closed"
    assert ("close_project", 10) in fake.calls

    noop_preview = await sales.lead_close_project("key", 10, apply=False, confirmation_token=None)
    assert isinstance(noop_preview, MutationPreview)
    noop = await sales.lead_close_project(
        "key",
        10,
        apply=True,
        confirmation_token=noop_preview.confirmation_token,
    )
    assert isinstance(noop, MutationResult)
    assert noop.outcome == "no_op"

    fake.projects[10] = fake.projects[10].model_copy(update={"status": 1})
    fake.close_no_effect = True
    partial_preview = await sales.lead_close_project(
        "key", 10, apply=False, confirmation_token=None
    )
    assert isinstance(partial_preview, MutationPreview)
    partial = await sales.lead_close_project(
        "key",
        10,
        apply=True,
        confirmation_token=partial_preview.confirmation_token,
    )
    assert isinstance(partial, MutationResult)
    assert partial.outcome == "partial"
    assert partial.partial_errors


async def test_assign_replaces_leader_and_reports_partial_failures() -> None:
    fake = FakeSalesClient()
    sales = service(fake)
    preview = await sales.lead_assign("key", 10, 8, apply=False, confirmation_token=None)
    assert isinstance(preview, MutationPreview)
    applied = await sales.lead_assign(
        "key",
        10,
        8,
        apply=True,
        confirmation_token=preview.confirmation_token,
    )
    assert isinstance(applied, MutationResult)
    assert applied.outcome == "applied"
    assert applied.lead is not None
    assert [owner.user_id for owner in applied.lead.owners] == [8]
    assert fake.calls[-2:] == [
        ("add_project_leader", 8),
        ("delete_project_leader", 7),
    ]

    noop_preview = await sales.lead_assign("key", 10, 8, apply=False, confirmation_token=None)
    assert isinstance(noop_preview, MutationPreview)
    noop = await sales.lead_assign(
        "key",
        10,
        8,
        apply=True,
        confirmation_token=noop_preview.confirmation_token,
    )
    assert isinstance(noop, MutationResult)
    assert noop.outcome == "no_op"

    fake.contacts[10] = [leader(7, "alice")]
    fake.fail_add = True
    failed_preview = await sales.lead_assign("key", 10, 8, apply=False, confirmation_token=None)
    assert isinstance(failed_preview, MutationPreview)
    partial_add = await sales.lead_assign(
        "key",
        10,
        8,
        apply=True,
        confirmation_token=failed_preview.confirmation_token,
    )
    assert isinstance(partial_add, MutationResult)
    assert partial_add.outcome == "partial"
    assert partial_add.lead is not None
    assert [owner.user_id for owner in partial_add.lead.owners] == [7]

    fake.fail_add = False
    fake.fail_delete_user_id = 7
    delete_preview = await sales.lead_assign("key", 10, 8, apply=False, confirmation_token=None)
    assert isinstance(delete_preview, MutationPreview)
    partial_delete = await sales.lead_assign(
        "key",
        10,
        8,
        apply=True,
        confirmation_token=delete_preview.confirmation_token,
    )
    assert isinstance(partial_delete, MutationResult)
    assert partial_delete.outcome == "partial"
    assert partial_delete.lead is not None
    assert {owner.user_id for owner in partial_delete.lead.owners} == {7, 8}

    fake.fail_delete_user_id = None
    fake.contacts[10] = [leader(8, "bob"), leader(7, "alice")]
    existing_preview = await sales.lead_assign("key", 10, 8, apply=False, confirmation_token=None)
    assert isinstance(existing_preview, MutationPreview)
    existing_result = await sales.lead_assign(
        "key",
        10,
        8,
        apply=True,
        confirmation_token=existing_preview.confirmation_token,
    )
    assert isinstance(existing_result, MutationResult)
    assert existing_result.outcome == "applied"
    assert existing_result.lead is not None
    assert [owner.user_id for owner in existing_result.lead.owners] == [8]

    with pytest.raises(DolibarrNotFoundError):
        await sales.lead_assign("key", 10, 9, apply=False, confirmation_token=None)


def test_sales_input_models_reject_unknown_empty_and_invalid_values() -> None:
    with pytest.raises(ValidationError):
        ThirdpartyCreateInput.model_validate(
            {"name": "Company", "customer_status": "prospect", "extrafields": {}}
        )
    with pytest.raises(ValidationError):
        ThirdpartyUpdateInput()
    with pytest.raises(ValidationError):
        LeadCreateInput(
            thirdparty_id=1,
            title="Lead",
            stage_id=1,
            probability_percent=101,
        )
    with pytest.raises(ValidationError):
        LeadCreateInput(
            thirdparty_id=1,
            title="Lead",
            stage_id=1,
            date_start=date(2026, 9, 2),
            date_end=date(2026, 9, 1),
        )
    with pytest.raises(ValidationError):
        LeadUpdateInput()
    with pytest.raises(ValidationError):
        LeadUpdateInput(date_start=date(2026, 9, 2), date_end=date(2026, 9, 1))
