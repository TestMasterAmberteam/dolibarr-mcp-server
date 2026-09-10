# Publishing on GitHub

This checklist covers the one-time publication of the existing local repository and the first
public release. It intentionally separates source publication from releasing packages. Do not
create a version tag merely to make the repository visible on GitHub.

## 1. Decide the public identity

Before changing remote state, decide and record:

- whether the commit author name and email in the complete Git history are suitable for public
  disclosure;
- a private contact method for Code of Conduct reports;
- whether the Python package will later be published to PyPI.

The confirmed GitHub organization login is `TestMasterAmberteam`, with the public organization
name `AmberTeam Testing`. Canonical repository links in `pyproject.toml` and README workflow badges
must use the login, not the display name.

## 2. Audit the complete Git history

Run these read-only checks from the repository root:

```powershell
git status --short --branch
git remote -v
git log --all --format='%h %ad %an <%ae> %s' --date=short
git log --all -- .playwright-mcp
git ls-files
```

The worktree must be clean. Review every public author identity. The `.playwright-mcp` history
query must return no commits: browser captures can expose private hosts, page contents, and form
metadata even when the files were deleted later. A normal deletion does not remove a file from Git
history.

If history must be rewritten, make a recoverable backup first and use a reviewed history-rewrite
procedure. Re-run secret scanning after the rewrite. Never push the original refs, backup refs, or
tags containing removed material.

## 3. Run the local release-readiness gates

Run the complete command list in [`development.md`](development.md). At minimum, do not publish
until the following pass from a clean checkout:

```powershell
uv lock --check
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict src tests
uv run pytest
uv run pre-commit run --all-files
uv build
uv run twine check dist/*.whl dist/*.tar.gz
uv run pip-audit
uv run codespell
git diff --check
```

The CI workflows additionally run Linux, macOS, Windows, Docker, Hadolint, Actionlint, CodeQL,
Gitleaks, Trivy, and OpenSSF Scorecard checks. Local success is not a substitute for the first
green GitHub Actions run.

## 4. Create the empty public repository

Authenticate GitHub CLI, then create the remote from the existing checkout:

```powershell
$RepoOwner = "TestMasterAmberteam"
$RepoName = "dolibarr-mcp-server"
gh auth status
gh repo create "$RepoOwner/$RepoName" --public --source . --remote origin --push --description "Stateless MCP server for per-user Dolibarr API access"
git remote -v
```

Do not ask GitHub to add a README, `.gitignore`, or license: all three already exist locally. If
GitHub CLI is not used, create an empty public repository in the browser, then run:

```powershell
git remote add origin https://github.com/TestMasterAmberteam/dolibarr-mcp-server.git
git remote -v
git push -u origin main
```

## 5. Configure repository settings

Complete these settings before accepting contributions:

1. In the repository **About** panel, set the description and topics such as `dolibarr`, `mcp`,
   `model-context-protocol`, `python`, and `asgi`.
2. Keep Issues enabled, disable the wiki unless it will be maintained, and enable Discussions for
   usage questions described in `SUPPORT.md`.
3. Under Actions settings, keep the default `GITHUB_TOKEN` permission read-only and do not allow
   workflows to approve pull requests. Individual release jobs already request only the extra
   permissions they need.
4. Under Advanced Security, enable Dependabot alerts and security updates, secret scanning with
   push protection, code scanning, and Private Vulnerability Reporting.
5. Enable release immutability before creating the first GitHub Release. It applies only to future
   releases.
6. Enable automatic deletion of merged head branches and allow squash merging. Keep the
   Conventional Commit title when squash-merging a pull request.

Create any custom labels referenced by `.github/dependabot.yml` (`dependencies`, `python`,
`github-actions`, and `docker`) or remove those custom label entries before Dependabot's first run.

## 6. Run CI once, then protect `main`

Allow the initial workflows to finish before selecting required checks; GitHub only offers checks
that have run recently. Create an active branch ruleset targeting the default branch with:

- pull requests required;
- conversation resolution required;
- force pushes and branch deletion blocked;
- linear history required if squash merging is the selected merge strategy;
- strict status checks required, including `quality-gate`, `CodeQL Python`, `Runtime dependency
  audit`, `Gitleaks`, `Workflow audit`, and `Trivy filesystem`.

If there is an independent reviewer, require at least one approval and dismissal of stale
approvals. A single-maintainer project should not create an approval rule that nobody can satisfy;
instead, keep administrator bypass visible and use it only for documented emergencies.

Open a small documentation pull request after enabling the ruleset. Merge it only after every
required check passes. This proves that the contribution path works, rather than only proving that
the initial direct push worked.

## 7. Prepare the first release separately

When the repository source is stable and CI is green:

1. Choose the release version according to Semantic Versioning.
2. Update `pyproject.toml`, `src/dolibarr_mcp/__init__.py`, the CLI version test, Docker and Compose
   examples, and any version-specific documentation.
3. Move the relevant `CHANGELOG.md` entries from `Unreleased` to
   `## [VERSION] - YYYY-MM-DD`, using the actual release date.
4. Run every local gate again and merge the release pull request.
5. Create and push an annotated tag matching the package version:

   ```powershell
   $Version = "0.4.1"
   git tag -a "v$Version" -m "Release v$Version"
   git push origin "v$Version"
   ```

The tag starts the release workflow. It rebuilds and verifies the project, creates Python
artifacts, checksums, an SBOM, attestations, a multi-architecture GHCR image, and a GitHub Release.
Do not claim the release is complete until every release job is green and the assets are visible.

PyPI is deliberately opt-in. Configure a PyPI Trusted Publisher for the exact GitHub owner,
repository, `release.yml` workflow, and `pypi` environment before setting the repository variable
`PUBLISH_TO_PYPI=true`. Test the GitHub-only release path first.

## 8. Verify the public result

From a fresh directory, clone the public repository and repeat the documented setup. Verify:

- the README renders correctly and all relative links work;
- Community Standards recognizes the license, Code of Conduct, contributing guide, issue forms,
  pull-request template, support policy, and security policy;
- CI, security, and CodeQL checks are green;
- no internal URL, credential, browser capture, build artifact, or private author identity appears
  in the public history;
- Discussions and Private Vulnerability Reporting lead to the intended channels;
- a release, if created, contains checksums, SBOM, attestations, package artifacts, and the GHCR
  image, all for the same version.
