# Git Workflow & Branching

## Branching Strategy: GitHub Flow

Simple, suitable for a solo/small team. `main` is always deployable.

| Branch | Purpose |
|--------|---------|
| `main` | Always deployable; protected |
| `feature/<milestone>-<short-desc>` | Milestone work, e.g. `feature/m1-data-download` |
| `fix/<issue>` | Bug fixes |
| `docs/<topic>` | Documentation only |

Branch from `main`, open a PR back into `main`, squash merge once green.

## Commit Conventions

[Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` — a new capability
- `fix:` — a bug fix
- `test:` — tests only, no behavior change
- `docs:` — documentation only
- `refactor:` — internal restructuring, no behavior change
- `chore:` — tooling/CI/dependency changes

Keep the subject line under ~72 characters; use the body to explain *why*,
not just *what* (the diff already shows *what*).

## Pull Request Rules

Before opening a PR, all of the following must pass locally (CI enforces the
same):

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m "not network"
```

- Every new module ships with unit tests in the same PR.
- No `.env` or `data/` committed (both gitignored — if you see them in
  `git status`, something is misconfigured).
- No secrets, API keys, or tokens in code, config, or commit messages.
- Squash merge to `main` — keep `main`'s history one commit per PR.

## Tags

Tag milestone completions on `main`: `v0.1.0-m0`, `v0.2.0-m1`, `v0.3.0-m2`, etc.

## CI

GitHub Actions ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs
on every push to `main` and every PR: `ruff check`, `ruff format --check`,
`mypy --strict`, and `pytest` (with coverage, network-marked tests excluded).
A PR cannot merge with a red CI run.
