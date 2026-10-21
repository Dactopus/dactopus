# Contributing

Bug reports and questions are welcome as issues. Everything in this
repository is in English: code, comments, commit messages, issues.

## Setup

Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Dactopus/dactopus
cd dactopus
uv sync
uv run pytest -m "not integration"     # unit tests, no server needed
```

Integration tests need a ClickHouse server reachable over HTTP at
`DACTOPUS_CLICKHOUSE_URL` (default `http://127.0.0.1:8123`) and skip
without one.

```bash
uv run ruff check src tests
uv run ruff format src tests
```

## Rules that shape the code

[AGENTS.md](AGENTS.md) is the full list. The scope section says what the
runner does and what it leaves to the library, the executor and the
client's own tools; read it before proposing a feature.

## Pull requests

Small and focused. CI runs lint, the format check and the tests. By
submitting a contribution you agree it is licensed under the Apache
License 2.0, like the rest of the project.

## Releasing

For maintainers. A release is the tag `vX.Y.Z` on a commit on `main`; the
GitHub release is titled `X.Y.Z`.

1. Move the `[Unreleased]` entries in `CHANGELOG.md` under
   `## [X.Y.Z] - YYYY-MM-DD`, leaving `[Unreleased]` empty.
2. Set `version` in `pyproject.toml` and run `uv lock`.
3. Push, wait for CI, merge with "Rebase and merge".
4. Tag the commit on `main`, never one on the branch: rebasing changes the
   hashes.

```bash
git switch main && git pull --ff-only
git tag -a vX.Y.Z -m "dactopus X.Y.Z" && git push origin vX.Y.Z
gh release create vX.Y.Z --title X.Y.Z --notes-file notes.md
```
