# ADR 0008: Read-only customer invoices

- Status: Accepted
- Date: 2026-10-16

## Context

The MCP server needs customer-invoice header and line reads without exposing a general REST
proxy or introducing financial mutations. Dolibarr 23.0.3 provides official Invoices endpoints
with invoice-read permissions and a resource access check for detail.

The list endpoint applies the internal sales-representative restriction only when no third-party
filter is supplied. Passing a customer filter upstream would change that access-selection path.
Closed status is not proof of full payment, and multicurrency totals are not base-currency totals.

## Decision

- Add only `dolibarr_invoice_search` and `dolibarr_invoice_get`, with read-only annotations.
- Use the same request-scoped key, shared pool, authentication, and safe diagnostic boundary.
- List via fixed `GET /invoices` with deterministic ID ordering, no `thirdparty_ids` or
  `sqlfilters`, fixed properties, no lines, and no linked objects. Apply customer ID, reference,
  and status filters locally. Use fixed `GET /invoices/{id}` for authorized detail, excluding
  contacts and selecting only headers plus lines.
- Project headers and lines into typed allowlists, excluding notes, bank data, payment URLs,
  contacts, purchase prices, and extrafields. Preserve signed finite decimal values as strings;
  keep base-currency and multicurrency amounts separate. Preserve raw date timestamps to avoid
  guessing the Dolibarr installation timezone. Report status, paid flag, and close code separately.
- Bound list processing to 100 pages of 100 headers; fail rather than return a partial collection
  as complete. Reject oversized pages and details above 1000 lines. Page outputs at 1-500 rows,
  retain invoice line order, and truncate descriptions to 4000 output characters after a
  65,535-character upstream limit. Missing detail lines are invalid, not an empty invoice.
- Test the actual ASGI authentication and MCP protocol using synthetic upstream transports.
  No live customer records or credentials are needed for the automated suite.

## Consequences

Authentication, tenancy, logging, and credential lifetime remain request scoped. Dolibarr remains
the authorization authority. Listing many invoices has a bounded cost, and clients may need to
page large results. Pages reread upstream state and are not transactionally consistent snapshots.
Currency codes for installation-base amounts are not inferred. Invoice writes, payments, supplier
invoices, and invoice PDFs remain outside these tools.

## Upstream reference

Verified against the official Dolibarr 23.0.3
[Invoices API source](https://github.com/Dolibarr/dolibarr/blob/23.0.3/htdocs/compta/facture/class/api_invoices.class.php)
and [invoice line source](https://github.com/Dolibarr/dolibarr/blob/23.0.3/htdocs/compta/facture/class/factureligne.class.php).
