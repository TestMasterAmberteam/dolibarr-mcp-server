# Ubuntu LAN deployment and Jenkins updates

## Installed service

| Item | Value |
| --- | --- |
| Ubuntu host | `root@10.0.1.95` |
| MCP Streamable HTTP URL | `http://10.0.1.95:8080/mcp` |
| Liveness / readiness | `http://10.0.1.95:8080/health/live`, `/health/ready` |
| Direct Dolibarr upstream | `https://erp.amberteam.pl/dolibarr` |
| Service | `dolibarr-mcp.service`, user `dolibarr-mcp` |
| Configuration | `/etc/dolibarr-mcp/config.toml` |
| TLS CA bundle | `/etc/dolibarr-mcp/ca-bundle.pem` |
| Active release / preceding release | `/opt/dolibarr-mcp/current`, `/opt/dolibarr-mcp/previous` |
| Release metadata | `/opt/dolibarr-mcp/current/DEPLOYMENT` |
| Update command | `/usr/local/sbin/dolibarr-mcp-deploy` |

The service starts automatically at boot. It connects directly to the HTTPS
upstream; there is no upstream proxy, tunnel, or loopback forwarder. Retain the
`/dolibarr` path because the client appends `/api/index.php` to that base URL.
Local health probes use loopback only to check the MCP listener itself.

Clients send `Authorization: Bearer <their Dolibarr API key>`. Keys are validated
on every MCP request and are never server settings. The HTTP client-to-MCP segment
is the operator-requested trusted LAN transport; the upstream uses verified TLS.
The example configuration has no installation-specific stage catalog; add verified
values separately if needed.

## TLS certificate chain

On 2026-09-16 the upstream omitted the chain needed by Ubuntu to verify its
certificate. The application-specific CA bundle combines Ubuntu's system CA
bundle with the public Let's Encrypt YR1 intermediate and the cross-signed Root YR.
The latter chains to the already-trusted ISRG Root X1. Both certificates are saved
in `deploy/lan/dolibarr-intermediates.pem`; they contain no private keys.

The certificates were retrieved from the certificate issuer URLs
`http://yr1.i.lencr.org/` and `http://yr.i.lencr.org/`, then the complete leaf chain
was verified against Ubuntu's existing CA store before use. TLS and hostname
verification remain enabled. No additional system-wide trust anchor was installed.

To recreate the bundle after copying the public PEM file to the Ubuntu host:

```bash
openssl s_client -connect erp.amberteam.pl:443 -servername erp.amberteam.pl \
  -showcerts </dev/null 2>/dev/null | openssl x509 -out /tmp/dolibarr-leaf.pem
openssl verify -verify_hostname erp.amberteam.pl \
  -CAfile /etc/ssl/certs/ca-certificates.crt \
  -untrusted dolibarr-intermediates.pem /tmp/dolibarr-leaf.pem
# Continue only if verification reports OK.
cat /etc/ssl/certs/ca-certificates.crt dolibarr-intermediates.pem \
  > /etc/dolibarr-mcp/ca-bundle.pem
chmod 0644 /etc/dolibarr-mcp/ca-bundle.pem
```

Rebuild the bundle after system CA updates. If the upstream changes intermediate
issuers, obtain and validate its new chain. Fixing the web server to serve its full
certificate chain would allow using the standard CA store and removing the
`dolibarr_ca_bundle` override.

## Update from GitHub

Run as root on the Ubuntu host:

```bash
dolibarr-mcp-deploy latest
# Or deploy a particular published stable release:
dolibarr-mcp-deploy v0.4.1
```

The helper resolves `latest` once using the GitHub Releases API. Explicit tags must
also identify published, non-draft, non-prerelease GitHub releases. It fetches tags
from the fixed project repository, rejects moved existing tags, checks the package
version against the tag, and installs with `uv sync --locked --no-dev --no-editable`.
The installed uv version is 0.12.5; Python is Ubuntu's `/usr/bin/python3` (3.12).

Each attempt builds a separate directory before touching the running service.
A host-level lock prevents overlapping deployments. Once configuration and version
validation pass, the helper switches `current` atomically and restarts the service.
Failed startup, readiness or missing-authentication checks restore the previous
release and verify its health. First-install failures stop the service.

Old and failed candidate directories are retained for inspection; no automatic
deletion or scheduled update is configured. Updates preserve configuration, the CA
bundle, and systemd units. A pre-switch build failure leaves the running release intact.

Inspect or restart:

```bash
systemctl status dolibarr-mcp
cat /opt/dolibarr-mcp/current/DEPLOYMENT
journalctl -u dolibarr-mcp --since today
systemctl restart dolibarr-mcp
```

To return to an older published version, pass its tag to `dolibarr-mcp-deploy`.
Do not edit the active release or run an in-place `git pull` / dependency upgrade.

## Jenkins setup

The repository-root `Jenkinsfile` is self-contained and can be pasted into a
Jenkins **Pipeline** job as **Pipeline script**. It does not require SCM checkout;
the Ubuntu host fetches the selected application release directly from GitHub.

1. Select an existing Linux Jenkins agent with label `linux`, or change the
   `agent` label to your Linux agent's label. The agent needs `ssh`, `curl`, and
   access to `10.0.1.95` on ports 22 and 8080.
2. Make sure the Pipeline and Credentials Binding plugins are installed.
3. Create an **SSH Username with private key** credential with ID
   `dolibarr-mcp-ssh`, username `root`, and a key authorized on `10.0.1.95`.
   An unencrypted deployment key avoids an interactive passphrase prompt.
   The credential ID is configurable through `SSH_CREDENTIALS_ID`.
4. Paste the local `Jenkinsfile` into the job. The first run registers the
   parameters with their defaults; subsequent runs use **Build with Parameters**.
5. Set `RELEASE_TAG=latest` or an explicit `vMAJOR.MINOR.PATCH`.

Only trusted operators should be able to edit or run this job: its SSH credential
allows root deployment on the target. The file pins the target's public Ed25519
host key, read from the host during installation. If the host is rebuilt, verify
the replacement key independently before updating the file.

The pipeline invokes the already-installed helper. Deployment infrastructure is
updated separately by reviewing and reinstalling the files under `deploy/lan`.

The remote helper performs startup checks and rollback. The final Jenkins stage
checks both health endpoints and the expected 401 without credentials from the
agent's network. An agent-side network failure reports a failed job but does not
roll back a healthy service. No Dolibarr key is required by Jenkins.

These checks establish service availability and the auth boundary only. They do
not prove upstream login or business operations. A separate client check with its
own key can initialize MCP, list tools, and call `dolibarr_whoami`. Never put that
key in build parameters, console commands, or server configuration.

## Initial installation files

The deployed copies come from `deploy/lan`:

- `deploy-release.sh` -> `/usr/local/sbin/dolibarr-mcp-deploy` (root, mode 0755)
- `config.toml.example` -> `/etc/dolibarr-mcp/config.toml` (root:dolibarr-mcp, 0640)
- `dolibarr-mcp.service` -> `/etc/systemd/system/` (root, 0644)
- `dolibarr-intermediates.pem` -> used to build the CA bundle as described above

Install Git, curl, CA certificates, Python 3.12 with venv support, and uv.
Create the `dolibarr-mcp` system user with no login shell, install the files and
CA bundle, load units with `systemctl daemon-reload`, run the first deployment,
then `systemctl enable dolibarr-mcp.service`.

## Verification record: 2026-09-16

- Installed GitHub release `v0.4.1`, commit
  `f74442e8b767b32a84ee831846ed90d61b617bb3`.
- External LAN liveness/readiness returned HTTP 200; missing credentials returned 401.
- The final direct HTTPS upstream completed authenticated MCP initialization,
  listed 30 tools, and returned a successful `dolibarr_whoami` result. Credentials
  and identity values were neither printed nor saved.
- Updating via `latest` succeeded and retained the previous release.
- Invalid and missing release tags failed while preserving the active service.
- ShellCheck, Bash syntax, systemd unit validation and Groovy syntax compilation passed.
- Application regression suite: 163 tests, 96.31% branch-enabled coverage.
- Autostart is enabled; a container reboot has not been tested.
- Jenkins runtime execution has not been performed; import the file and supply its
  SSH credential. Groovy compilation does not replace validation in Jenkins.

References: [Jenkinsfile documentation](https://www.jenkins.io/doc/book/pipeline/jenkinsfile/),
[uv deployment guidance](https://docs.astral.sh/uv/guides/integration/docker/).
