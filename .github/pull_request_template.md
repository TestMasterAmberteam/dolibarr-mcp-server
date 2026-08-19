## Summary

Describe the problem and the smallest solution.

## Security and privacy impact

- Does this change authentication, credential handling, logging, tenancy, or upstream access?
- If yes, link the ADR and threat analysis.

## Verification

- [ ] `uv lock --check`
- [ ] `uv run ruff format --check .`
- [ ] `uv run ruff check .`
- [ ] `uv run mypy --strict src tests`
- [ ] `uv run pytest`
- [ ] `uv run pre-commit run --all-files`
- [ ] package and clean-wheel smoke tests
- [ ] relevant container/workflow/security checks

## Completion

- [ ] Tests cover the behavior through the correct boundary (HTTP for auth).
- [ ] Documentation and `CHANGELOG.md` are updated.
- [ ] No credentials, owner placeholders, unrelated changes, or generated artifacts are included.
