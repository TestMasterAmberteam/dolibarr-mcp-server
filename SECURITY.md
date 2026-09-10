# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| latest published `0.x` release | Yes |
| older releases and unreleased branches | No |

Security fixes are released on the latest supported line. Users should upgrade promptly and rotate
any Dolibarr key that may have been exposed.

## Private reporting

Use GitHub Private Vulnerability Reporting in this repository's **Security → Advisories** area.
Maintainers must enable that feature before the repository is published. Do not open a public issue
containing a vulnerability, exploit, user identity, internal URL, log archive, or credential.

Include:

- affected version or commit;
- impact and realistic attack preconditions;
- minimal reproduction without real secrets or production data;
- affected endpoints and deployment assumptions;
- suggested mitigation, if known;
- whether disclosure is subject to a deadline.

This community project does not promise a response or remediation SLA. Maintainers will acknowledge,
triage, coordinate a fix and disclosure when volunteer availability permits. Duplicate, non-security,
or unverifiable reports may be closed without an advisory.

Never send a real Dolibarr API key. Revoke it in Dolibarr first if accidental exposure occurs.
