"""Allowlisted customer-invoice headers and lines, with exact decimal amounts."""

from __future__ import annotations

from decimal import Decimal  # noqa: TC003 - Pydantic resolves fields at runtime
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

InvoiceStatus = Literal[0, 1, 2, 3]
MAX_INVOICE_LINES = 1000


class InvoiceSummary(BaseModel):
    """Invoice header; total_* amounts use the installation's base currency."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    invoice_id: int = Field(gt=0)
    ref: str = Field(min_length=1, max_length=255)
    thirdparty_id: int = Field(gt=0)
    invoice_type: int = Field(ge=0)
    status: InvoiceStatus
    paid: bool | None = None
    close_code: str | None = Field(default=None, max_length=64)
    customer_ref: str | None = Field(default=None, max_length=255)
    date_timestamp: int | None = None
    due_date_timestamp: int | None = None
    total_net: Decimal
    total_vat: Decimal
    total_gross: Decimal
    multicurrency_code: str | None = Field(default=None, max_length=3)
    multicurrency_total_net: Decimal | None = None
    multicurrency_total_vat: Decimal | None = None
    multicurrency_total_gross: Decimal | None = None


class InvoiceLine(BaseModel):
    """One line; prices and total_* amounts use the installation's base currency."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    line_id: int = Field(gt=0)
    product_id: int | None = Field(default=None, gt=0)
    product_ref: str | None = Field(default=None, max_length=255)
    label: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    product_type: int | None = None
    quantity: Decimal
    unit_price: Decimal
    vat_rate: Decimal
    discount_percent: Decimal | None = None
    total_net: Decimal
    total_vat: Decimal
    total_gross: Decimal
    multicurrency_unit_price: Decimal | None = None
    multicurrency_total_net: Decimal | None = None
    multicurrency_total_vat: Decimal | None = None
    multicurrency_total_gross: Decimal | None = None


class DolibarrInvoiceHeaderPayload(InvoiceSummary):
    """Typed projection of an official Dolibarr invoice object."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    invoice_id: int = Field(alias="id", gt=0)
    thirdparty_id: int = Field(validation_alias=AliasChoices("socid", "fk_soc"), gt=0)
    invoice_type: int = Field(alias="type", ge=0)
    status: InvoiceStatus = Field(validation_alias=AliasChoices("status", "statut"))
    paid: bool | None = Field(default=None, alias="paye")
    customer_ref: str | None = Field(
        default=None, validation_alias=AliasChoices("ref_customer", "ref_client"), max_length=255
    )
    date_timestamp: int | None = Field(default=None, alias="date")
    due_date_timestamp: int | None = Field(default=None, alias="date_lim_reglement")
    total_net: Decimal = Field(alias="total_ht")
    total_vat: Decimal = Field(alias="total_tva")
    total_gross: Decimal = Field(alias="total_ttc")
    multicurrency_total_net: Decimal | None = Field(default=None, alias="multicurrency_total_ht")
    multicurrency_total_vat: Decimal | None = Field(default=None, alias="multicurrency_total_tva")
    multicurrency_total_gross: Decimal | None = Field(default=None, alias="multicurrency_total_ttc")

    @field_validator("status", mode="before")
    @classmethod
    def numeric_status(cls, value: object) -> object:
        """Database-backed REST statuses can be numeric strings."""
        return int(value) if isinstance(value, str) and value in ("0", "1", "2", "3") else value

    @field_validator(
        "paid",
        "close_code",
        "customer_ref",
        "date_timestamp",
        "due_date_timestamp",
        "multicurrency_code",
        "multicurrency_total_net",
        "multicurrency_total_vat",
        "multicurrency_total_gross",
        mode="before",
    )
    @classmethod
    def blank_optional(cls, value: object) -> object:
        """Dolibarr may encode absent optional properties as empty strings."""
        return None if value == "" else value


class DolibarrInvoiceLinePayload(InvoiceLine):
    """Typed line projection; descriptions are bounded before public output."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    line_id: int = Field(validation_alias=AliasChoices("id", "rowid"), gt=0)
    product_id: int | None = Field(default=None, alias="fk_product", gt=0)
    description: str | None = Field(
        default=None, validation_alias=AliasChoices("desc", "description"), max_length=65535
    )
    quantity: Decimal = Field(alias="qty")
    unit_price: Decimal = Field(alias="subprice")
    vat_rate: Decimal = Field(alias="tva_tx")
    discount_percent: Decimal | None = Field(default=None, alias="remise_percent")
    total_net: Decimal = Field(alias="total_ht")
    total_vat: Decimal = Field(alias="total_tva")
    total_gross: Decimal = Field(alias="total_ttc")
    multicurrency_unit_price: Decimal | None = Field(default=None, alias="multicurrency_subprice")
    multicurrency_total_net: Decimal | None = Field(default=None, alias="multicurrency_total_ht")
    multicurrency_total_vat: Decimal | None = Field(default=None, alias="multicurrency_total_tva")
    multicurrency_total_gross: Decimal | None = Field(default=None, alias="multicurrency_total_ttc")

    @field_validator("product_id", mode="before")
    @classmethod
    def absent_product(cls, value: object) -> object:
        """A free-text invoice line has no product (null, blank, or zero)."""
        return None if value in (None, "", 0, "0") else value

    @field_validator(
        "product_ref",
        "label",
        "description",
        "product_type",
        "discount_percent",
        "multicurrency_unit_price",
        "multicurrency_total_net",
        "multicurrency_total_vat",
        "multicurrency_total_gross",
        mode="before",
    )
    @classmethod
    def blank_optional(cls, value: object) -> object:
        """Normalize absent optional line properties."""
        return None if value == "" else value


class DolibarrInvoicePayload(DolibarrInvoiceHeaderPayload):
    """Detail requires the actual lines array, including an empty array for drafts."""

    lines: list[DolibarrInvoiceLinePayload] = Field(max_length=MAX_INVOICE_LINES)


class InvoiceSearchResult(BaseModel):
    """Paged headers after filtering accessible invoices."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)
    has_more: bool
    rows: list[InvoiceSummary]


class InvoiceDetail(BaseModel):
    """Invoice header and one explicit page of its lines."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    header: InvoiceSummary
    total_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)
    has_more: bool
    lines: list[InvoiceLine]
