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
`DACTOPUS_CLICKHOUSE_URL` (user, host and port, without the password;
default `http://127.0.0.1:8123`), with the password in
`CLICKHOUSE_PASSWORD`, and skip without one.

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
4. Rehearse on `main`: run the Publish workflow by hand. It uploads the
   build to [TestPyPI](https://test.pypi.org/project/dactopus/) as
   `X.Y.Z.devN`; install that from there, its dependencies from PyPI
   (anyone may upload to TestPyPI, under any name), and look at the page.
   PyPI takes each version once, so whatever goes wrong is fixed here,
   before the tag.
5. Tag the commit on `main`, never one on the branch: rebasing changes the
   hashes. Publishing the release on GitHub runs the Publish workflow,
   which checks that the tag is the version in `pyproject.toml` and
   uploads to PyPI. The release notes are the changelog section plus the
   install line pinned to the version.

```bash
git switch main && git pull --ff-only
gh workflow run publish.yml --ref main   # step 4, TestPyPI
uvx pip download --no-deps --index-url https://test.pypi.org/simple/ -d testpypi "dactopus==X.Y.Z.devN"
uv tool install --force testpypi/dactopus-X.Y.Z.devN-py3-none-any.whl   # dependencies from PyPI
git tag -a vX.Y.Z -m "dactopus X.Y.Z" && git push origin vX.Y.Z
gh release create vX.Y.Z --title X.Y.Z --notes-file notes.md   # publishes to PyPI
```
