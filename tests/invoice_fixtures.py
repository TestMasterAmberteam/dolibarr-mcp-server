"""Synthetic, API-shaped invoice fixtures without real customer information."""

from __future__ import annotations


def invoice_header(invoice_id: int = 1, /, **values: object) -> dict[str, object]:
    return {
        "id": str(invoice_id),
        "ref": f"INV-{invoice_id}",
        "socid": "42",
        "type": "0",
        "status": "1",
        "paye": "0",
        "ref_customer": "PO-ABC",
        "date": "1792101600",
        "date_lim_reglement": "1793314800",
        "total_ht": "123.45",
        "total_tva": "28.3935",
        "total_ttc": "151.8435",
        "multicurrency_code": "EUR",
        "multicurrency_total_ht": "30.00",
        "multicurrency_total_tva": "6.90",
        "multicurrency_total_ttc": "36.90",
        "note_private": "hidden invoice note",
        "online_payment_url": "hidden payment URL",
        "array_options": {"secret": "hidden extrafield"},
        **values,
    }


def invoice_line(line_id: int = 1, **values: object) -> dict[str, object]:
    return {
        "rowid": str(line_id),
        "fk_product": "0",
        "product_ref": "",
        "label": None,
        "desc": "Consulting",
        "product_type": "1",
        "qty": "1.5",
        "subprice": "82.30",
        "tva_tx": "23.000",
        "remise_percent": "0",
        "total_ht": "123.45",
        "total_tva": "28.3935",
        "total_ttc": "151.8435",
        "multicurrency_subprice": "20.00",
        "multicurrency_total_ht": "30.00",
        "multicurrency_total_tva": "6.90",
        "multicurrency_total_ttc": "36.90",
        "pa_ht": "hidden purchase price",
        "array_options": {"private": "hidden line extrafield"},
        **values,
    }
