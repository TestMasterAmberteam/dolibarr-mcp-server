"""Bounded, read-only access to customer invoices through fixed REST routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dolibarr_mcp.invoice_models import (
    InvoiceDetail,
    InvoiceLine,
    InvoiceSearchResult,
    InvoiceStatus,
    InvoiceSummary,
)

if TYPE_CHECKING:
    from dolibarr_mcp.client import DolibarrClient


class InvoiceService:
    """Stateless invoice searches and paged line output using the request key."""

    def __init__(self, client: DolibarrClient) -> None:
        self._client = client

    async def search(
        self,
        api_key: str,
        *,
        thirdparty_id: int | None = None,
        query: str | None = None,
        status: InvoiceStatus | None = None,
        offset: int = 0,
        limit: int = 200,
    ) -> InvoiceSearchResult:
        rows = await self._client.list_invoices(api_key)
        needle = (query or "").strip().casefold()
        matches = [
            InvoiceSummary.model_validate(row.model_dump())
            for row in rows
            if (thirdparty_id is None or row.thirdparty_id == thirdparty_id)
            and (status is None or row.status == status)
            and (
                not needle
                or needle in row.ref.casefold()
                or needle in (row.customer_ref or "").casefold()
            )
        ]
        selected = matches[offset : offset + limit]
        return InvoiceSearchResult(
            total_count=len(matches),
            returned_count=len(selected),
            offset=offset,
            limit=limit,
            has_more=offset + len(selected) < len(matches),
            rows=selected,
        )

    async def get(
        self,
        api_key: str,
        invoice_id: int,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> InvoiceDetail:
        invoice = await self._client.get_invoice(api_key, invoice_id)
        lines = []
        for line in invoice.lines[offset : offset + limit]:
            values = line.model_dump()
            values["description"] = (
                line.description[:4000] if line.description is not None else None
            )
            lines.append(InvoiceLine.model_validate(values))
        return InvoiceDetail(
            header=InvoiceSummary.model_validate(invoice.model_dump(exclude={"lines"})),
            total_count=len(invoice.lines),
            returned_count=len(lines),
            offset=offset,
            limit=limit,
            has_more=offset + len(lines) < len(invoice.lines),
            lines=lines,
        )
