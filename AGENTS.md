# CLAUDE.md

## Dependencies
- Use `uv` for all Python dependency management. No `pip install` directly, no `requirements.txt` hand-editing.
- Pin every dependency to the **most recent stable version available** at the time of adding it. Check the actual latest release; do not guess or rely on training-data versions.
- No pre-release, beta, or RC versions unless a stable release literally does not exist for a required capability.
- Prefer the standard library before adding a dependency. Justify every new dep in the commit message.

## Python
- Target Python 3.14+. Use modern syntax (`match`, `|` unions, `type` statements) freely.
- Full type hints on all function signatures. Code must pass `mypy --strict` or `ty`.
- Async-first: all I/O (DB, Redis, HTTP, ESPN) is async. CPU-bound Pillow work goes through a threadpool, never blocking the event loop.
- Pydantic v2 for all settings and request/response models. Settings via `pydantic-settings`, never read `os.environ` directly in app code.

## Code quality
- Lint and format with `ruff` (both linter and formatter). Code must be clean before any task is considered done.
- No bare `except:`. No silent failures — log or raise.
- No magic numbers/strings in logic; lift them to named constants or config.
- Keep functions pure where the design allows it (generators especially must be pure: inputs in, `Image` out, no I/O).

## Testing
- `pytest` with `pytest-asyncio`. Every non-trivial unit gets a test.
- Generators are tested with golden-image comparison fixtures.
- Don't mock what you can run in a container; use real Postgres/Redis via fixtures over heavy mocking.
- A task isn't done until its tests pass and the full suite is green.

## Configuration & secrets
- All config through environment variables surfaced via the settings model. Nothing hardcoded.
- Never commit secrets, tokens, or `.env` files. Provide `.env.example` instead.

## HTTP & external calls
- Use a single shared async HTTP client (`httpx`) with timeouts and retries configured. No per-call client instantiation.
- Every external call (ESPN) must have a timeout and a graceful-degradation path (serve stale cache on failure).

## Containers
- Multi-stage Dockerfile. Final image runs as a non-root user. Prefer slim/distroless-style minimal base.
- Don't bake secrets or build caches into image layers.
- Everything runs via `docker compose up` with no manual host setup steps.
- nginx serves HTTP only — never configure TLS in this repo. TLS is terminated upstream; trust and honor `X-Forwarded-*` headers.

## CI / GitHub Actions
- This is a public repo. Workflows must assume anonymous forks/PRs and never expose secrets to untrusted PR runs.
- Pin every Action to a full commit SHA, not a version tag.
- Scope `GITHUB_TOKEN` permissions to the minimum each job needs; default the workflow to `permissions: {}` and grant per-job.
- Images publish to GHCR. Tag scheme: `:edge` on main, `:sha-<shortsha>`, and semver `:X.Y.Z`/`:X.Y`/`:latest` on release tags.
- Every published image is signed (cosign keyless via OIDC) and ships an SBOM. Scan (Grype/Trivy + gitleaks + pip-audit) gates publish; upload findings as SARIF.
- Keep the same security tooling versions in CI as referenced locally; pin scanner Action versions at their most recent stable release.

## Working style
- Build real, runnable artifacts over scaffolding-with-TODOs. Each change should leave the app working.
- Make the smallest change that satisfies the task; don't expand scope unprompted.
- When a decision is ambiguous, state the assumption made and proceed; don't stall.
- Use feature branches and create pull-requests; Merges to main are accomplished by a human.
