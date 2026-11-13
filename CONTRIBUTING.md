# Contributing

Bug reports are welcome as issues. Everything in this repository is in
English: code, comments, commit messages, issues.

Questions about using it and ideas go to
[Discussions](https://github.com/orgs/Dactopus/discussions); issues are for
bugs and concrete changes.

## Setup

Python 3.11 to 3.13 and [uv](https://docs.astral.sh/uv/).

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

Small and focused. CI runs lint, the format check and the unit tests on
Python 3.11 to 3.13, and builds the package and checks that its README
renders as the PyPI page. By
submitting a contribution you agree it is licensed under the Apache
License 2.0, like the rest of the project.

## Changelog and upgrading

`CHANGELOG.md` follows [Common Changelog](https://common-changelog.org)
and is written at release ([Releasing](#releasing)); a pull request
leaves it alone. A pull request after which an installation must act
adds a step under `## Unreleased` in `UPGRADING.md`, created if absent:
in the imperative, what to change in the configuration, a grant, a
command to run once. An item that starts with "Expect" names what
behaves differently with nothing to change, such as a question an agent
now answers.

Links in both files are absolute: a release's `CHANGELOG.md` section
becomes its notes on GitHub, where a relative link breaks.

## Releasing

For maintainers. A release is the tag `vX.Y.Z` on a commit on `main`; the
GitHub release is titled `X.Y.Z`.

1. Write the release's section of `CHANGELOG.md` from the pull requests
   merged since the last tag: `## [X.Y.Z] - YYYY-MM-DD`, with
   `[X.Y.Z]: <its GitHub release>` at the end of the file; groups
   `Changed`, `Added`, `Removed`, `Fixed`, in that order; one line per
   change, in the imperative, ending with its pull request, then any
   issue and the section of `docs/design.md` that says why; breaking
   changes first in their group, as `**Breaking:**`. A new floor of the
   models library or a new version of dactopus-ossie-clickhouse is a
   line of its own. Leave out what changes nothing for an installation,
   such as tests, CI and contributor docs. If `UPGRADING.md` has
   `## Unreleased`, rename it `## X.Y.Z`, and open the release's section
   with one sentence in italics that links to `UPGRADING.md#XYZ` by its
   absolute URL.
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
   which checks that the tag is the version in `pyproject.toml` and its
   commit is on `main`, then uploads to PyPI. The release notes are the
   changelog section without its heading, then the install line pinned
   to the version.

```bash
git switch main && git pull --ff-only
gh workflow run publish.yml --ref main   # step 4, TestPyPI
uvx pip download --no-deps --index-url https://test.pypi.org/simple/ -d testpypi "dactopus==X.Y.Z.devN"
uv tool install --force --python 3.13 testpypi/dactopus-X.Y.Z.devN-py3-none-any.whl   # dependencies from PyPI
git tag -a vX.Y.Z -m "dactopus X.Y.Z" && git push origin vX.Y.Z
gh release create vX.Y.Z --title X.Y.Z --notes-file notes.md   # publishes to PyPI
```
