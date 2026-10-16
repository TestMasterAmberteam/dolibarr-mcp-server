"""Customer-invoice projections, filters, bounds, and upstream error handling."""

from __future__ import annotations

import logging
from decimal import Decimal

import httpx2
import pytest
from pydantic import ValidationError

import dolibarr_mcp.client as client_module
from dolibarr_mcp.client import DolibarrClient
from dolibarr_mcp.config import Settings
from dolibarr_mcp.errors import (
    DolibarrNotFoundError,
    DolibarrPermissionDeniedError,
    DolibarrResultLimitError,
    InvalidDolibarrResponseError,
)
from dolibarr_mcp.invoice_models import DolibarrInvoicePayload, InvoiceStatus
from dolibarr_mcp.invoices import InvoiceService
from tests.invoice_fixtures import invoice_header, invoice_line

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("thirdparty_id", "query", "status", "offset", "expected", "has_more"),
    [
        (None, None, None, 0, [1], True),
        (42, " inv- ", None, 1, [3], False),
        (42, " po-abc ", 1, 0, [1], True),
        (9, None, 2, 0, [2], False),
        (None, "absent", None, 0, [], False),
        (None, None, None, 20, [], False),
    ],
)
async def test_search_filters_and_pagination(
    settings: Settings,
    *,
    thirdparty_id: int | None,
    query: str | None,
    status: InvoiceStatus | None,
    offset: int,
    expected: list[int],
    has_more: bool,
) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json=[
                invoice_header(1),
                invoice_header(2, socid="9", status=2, ref_customer=None),
                invoice_header(3),
            ],
        )

    async with DolibarrClient(settings, transport=httpx2.MockTransport(handler)) as client:
        # Use the public service with the same constrained status type as the tool.
        result = await InvoiceService(client).search(
            "key-A",
            thirdparty_id=thirdparty_id,
            query=query,
            status=status,
            offset=offset,
            limit=1,
        )
        assert "DOLAPIKEY" not in client._client.headers
    assert [row.invoice_id for row in result.rows] == expected
    assert result.has_more is has_more
    assert result.returned_count == len(expected)
    assert result.offset == offset
    assert seen[0].url.path == "/dolibarr/api/index.php/invoices"
    assert seen[0].headers["DOLAPIKEY"] == "key-A"
    assert seen[0].method == "GET"
    assert seen[0].url.params["withLines"] == "false"
    assert seen[0].url.params["sortfield"] == "t.rowid"
    assert "thirdparty_ids" not in seen[0].url.params
    assert "sqlfilters" not in seen[0].url.params
    assert "lines" not in seen[0].url.params["properties"]
    assert "hidden" not in result.model_dump_json()


async def test_search_reads_all_pages_and_empty_collection(settings: Settings) -> None:
    pages: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        page = request.url.params["page"]
        pages.append(page)
        return httpx2.Response(
            200, json=[invoice_header(i + 1) for i in range(100)] if page == "0" else []
        )

    async with DolibarrClient(settings, transport=httpx2.MockTransport(handler)) as client:
        result = await InvoiceService(client).search("key", offset=99)
    assert pages == ["0", "1"]
    assert result.total_count == 100
    assert result.rows[0].invoice_id == 100
    assert not result.has_more
    async with DolibarrClient(
        settings, transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=[]))
    ) as client:
        result = await InvoiceService(client).search("key")
    assert result.total_count == result.returned_count == 0


async def test_search_refuses_incomplete_result_at_processing_bound(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "_MAX_INVOICES", 100)
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json=[invoice_header()] * 100))
    async with DolibarrClient(settings, transport=transport) as client:
        with pytest.raises(DolibarrResultLimitError):
            await client.list_invoices("key")


async def test_detail_preserves_decimals_order_and_truncates_description(
    settings: Settings,
) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json=invoice_header(
                lines=[
                    invoice_line(7, desc="x" * 5000),
                    invoice_line(2, fk_product="8"),
                    invoice_line(3, desc=None),
                ]
            ),
        )

    async with DolibarrClient(settings, transport=httpx2.MockTransport(handler)) as client:
        service = InvoiceService(client)
        result = await service.get("key-A", 1, limit=1)
        rest = await service.get("key-B", 1, offset=1)
        empty = await service.get("key-A", 1, offset=20)
    assert result.header.total_net == Decimal("123.45")
    assert result.header.total_vat == Decimal("28.3935")
    assert result.header.date_timestamp == 1792101600
    assert result.header.due_date_timestamp == 1793314800
    assert result.header.multicurrency_total_gross == Decimal("36.90")
    assert result.header.paid is False
    assert result.lines[0].unit_price == Decimal("82.30")
    assert result.lines[0].quantity == Decimal("1.5")
    assert result.lines[0].product_id is None
    assert result.lines[0].description == "x" * 4000
    assert result.total_count == 3
    assert result.has_more
    assert [line.line_id for line in rest.lines] == [2, 3]
    assert rest.lines[0].product_id == 8
    assert rest.lines[1].description is None
    assert not rest.has_more
    assert empty.returned_count == 0
    assert not empty.has_more
    assert '"total_net":"123.45"' in result.model_dump_json()
    assert "hidden" not in result.model_dump_json()
    assert [r.headers["DOLAPIKEY"] for r in seen] == ["key-A", "key-B", "key-A"]
    assert all(r.url.path.endswith("/invoices/1") and r.method == "GET" for r in seen)
    assert seen[0].url.params["withLines"] == "true"
    assert seen[0].url.params["contact_list"] == "-1"


async def test_detail_empty_draft_and_legacy_aliases(settings: Settings) -> None:
    values = invoice_header(
        lines=[],
        status=0,
        paye="",
        date="",
        date_lim_reglement="",
        multicurrency_code="",
        multicurrency_total_ht="",
        multicurrency_total_tva=None,
        multicurrency_total_ttc="",
        close_code="",
    )
    del values["socid"], values["status"], values["ref_customer"]
    values.update(fk_soc="42", statut="0", ref_client="LEGACY")
    parsed = DolibarrInvoicePayload.model_validate(values)
    assert parsed.customer_ref == "LEGACY"
    assert parsed.status == 0
    assert parsed.date_timestamp is None
    assert parsed.due_date_timestamp is None
    assert parsed.paid is None
    assert parsed.multicurrency_code is None
    assert parsed.multicurrency_total_net is None
    async with DolibarrClient(
        settings, transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=values))
    ) as client:
        result = await InvoiceService(client).get("key", 1)
    assert result.lines == []
    assert result.total_count == 0


@pytest.mark.parametrize("product_id", [None, "", 0, "0", 5])
def test_line_optional_product_and_blank_values(product_id: str | int | None) -> None:
    parsed = DolibarrInvoicePayload.model_validate(
        invoice_header(
            lines=[
                invoice_line(
                    fk_product=product_id,
                    description="unused alias",
                    desc="",
                    label="",
                    product_type="",
                    remise_percent="",
                    multicurrency_subprice="",
                    multicurrency_total_ht="",
                    multicurrency_total_tva="",
                    multicurrency_total_ttc="",
                )
            ]
        )
    )
    assert parsed.lines[0].product_id == (5 if product_id == 5 else None)
    assert parsed.lines[0].description is None
    assert parsed.lines[0].discount_percent is None


@pytest.mark.parametrize(
    "values",
    [
        {"lines": [invoice_line(total_ht="NaN")]},
        {"lines": [invoice_line(qty="Infinity")]},
        {"lines": [invoice_line(desc="x" * 65536)]},
        {"lines": [invoice_line()] * 1001},
        {"lines": None},
        {"lines": [], "total_ht": "not-money"},
        {"lines": [], "status": "99"},
    ],
)
def test_invalid_or_unbounded_payload_is_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DolibarrInvoicePayload.model_validate(invoice_header(**values))


@pytest.mark.parametrize("status_code", [403, 404])
async def test_invoice_errors_are_safe(settings: Settings, status_code: int) -> None:
    error = DolibarrPermissionDeniedError if status_code == 403 else DolibarrNotFoundError
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(status_code, text="hidden upstream error")
    )
    async with DolibarrClient(settings, transport=transport) as client:
        with pytest.raises(error, match=r"Dolibarr resource|Dolibarr denied|Requested Dolibarr"):
            await client.get_invoice("key", 1)
        with pytest.raises(error):
            await client.list_invoices("key")


async def test_invalid_invoice_diagnostics_do_not_log_payloads(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = invoice_header(lines=[invoice_line(qty="hidden-invalid-quantity")])
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    async with DolibarrClient(settings, transport=transport) as client:
        with (
            caplog.at_level(logging.WARNING, logger="dolibarr_mcp.upstream"),
            pytest.raises(InvalidDolibarrResponseError),
        ):
            await client.get_invoice("hidden-key", 1)
    assert "schema=invoice" in caplog.text
    assert "hidden" not in caplog.text
    assert "INV-1" not in caplog.text


async def test_oversized_or_invalid_header_page_is_rejected(settings: Settings) -> None:
    for payload in ([invoice_header()] * 101, [invoice_header(total_ht="hidden-bad-amount")]):
        transport = httpx2.MockTransport(lambda _, p=payload: httpx2.Response(200, json=p))
        async with DolibarrClient(settings, transport=transport) as client:
            with pytest.raises(InvalidDolibarrResponseError):
                await client.list_invoices("key")
