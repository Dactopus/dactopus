# AGENTS.md

Guidance for AI coding agents working in this repository. What the product
is and why: [README.md](README.md).

## Project

dactopus is the runner of Dactopus: the process a client installs next to
their ClickHouse. It loads sources into that ClickHouse, runs the source
packages of [dactopus-data-models](https://github.com/Dactopus/dactopus-data-models)
(dbt projects) to build the canonical tables, runs checks, and serves the
Ossie models of those tables to AI agents over MCP through
[dactopus-ossie-clickhouse](https://github.com/Dactopus/dactopus-ossie-clickhouse).
The command line is the whole runner: no control plane, no scheduler.

The word is *runner*. Never call it an agent: an agent is the AI that
talks to the MCP server.

Licensed under Apache 2.0.

## Language

All text in this repository is in English only: code identifiers, comments,
docstrings, commit messages, documentation, issues, and test names. Do not
introduce text in any other language, even in examples or fixtures.

## Scope

In scope:

- One configuration file per installation: the ClickHouse connection and
  the user `serve` connects as, the databases for raw input and canonical
  tables, the version of the models
  library, the packages to run and their parameters.
- Loading GA4: the BigQuery export, read with the Storage Read API, into
  the GA4 package's input table.
- Loading Shopify: orders with lines, shipping and refunds, read page by
  page from the GraphQL Admin API, into the Shopify package's input
  tables.
- Running the packages with dbt.
- Checks behind a plugin interface, built-in checks, and one report a
  store owner can read.
- Serving the models over MCP, as a thin call to dactopus-ossie-clickhouse.

Out of scope, do not build:

- Transport for other sources. A database (WooCommerce on MySQL) reaches
  ClickHouse by CDC (PeerDB, ClickPipes) the client sets up; the runner
  reads the tables it finds. No replication engine, no wrapper over
  PeerDB, Debezium or dlt.
- Transformations and model descriptions. SQL that maps a source to an
  entity belongs in a package of dactopus-data-models; a description
  belongs in its Ossie model. The runner ships neither.
- Query execution and MCP tools. That is dactopus-ossie-clickhouse.
- Scheduling, alerts, run history, a control plane, a user interface.
  Runs are started by hand or by the client's cron.
- Merging model files or overlaying fragments on them. The executor
  reads several files as one model; the runner only lists them.
- Migration from a client's dbt or Looker.
- An installer, a Docker image, a single-file build. Installation is
  `uv`, as for the executor.
- Placeholders or stubs for any of the above.

## Design principles

- Thin. Every command is the Quick start of dactopus-data-models made
  portable: the same steps, one tool, one configuration file. A new
  concept needs a decision first.
- The library owns the input contract (`sources/<source>/input.sql`:
  the tables a package reads) and the transformations; the runner owns
  delivery to that contract and running. A gap between the two is fixed
  in the library, never patched around in the runner.
- The executor knows nothing of Dactopus; the runner is a consumer like
  any other. What the runner needs from it is proposed there as a
  feature for every Ossie user.
- Data and secrets stay with the client. The runner writes only to the
  client's ClickHouse. Credentials come from environment variables or
  files the configuration names, and never appear in logs, reports or
  error messages.
- No database name is written into a model or a package; the
  configuration chooses them, the runner passes them to dbt as variables
  and to `input.sql` as a query parameter.
- A run over unchanged input changes nothing, and a second load of the
  same rows is harmless.
- One plugin interface: checks. Everything else is a direct call.
- Keep the public surface small: four commands, one configuration file.

## Stack

Python 3.11+, managed with uv. The parts that constrain code:

- **dbt v1**: `dbt-core` 1.11 with `dbt-clickhouse`. Packages are dbt
  projects and also run on dbt v2, but its ClickHouse adapter is in beta
  (reads LZ4 only, `source freshness` broken), so the runner pins v1
  until the adapter is generally available.
- **dactopus-ossie-clickhouse** for `serve` and for validating the
  models against the built tables.
- **clickhouse-connect** with the `arrow` extra for loading: Arrow
  tables go in with `insert_arrow`, which maps Arrow structs to `Tuple`
  columns by field name and drops struct fields the table lacks.
- **google-cloud-bigquery-storage** for GA4: the Storage Read API returns
  Arrow, selects columns and filters rows, and reads tables only, not
  views. The daily tables are listed with one BigQuery REST call
  (`tables.list`) through `google-auth`, which comes with it; no
  `google-cloud-bigquery`.
- The Shopify client is the standard library (`urllib`) with `certifi`
  for root certificates, as in the library's README recipe. No dlt: its Shopify source uses the legacy
  REST Admin API.
- No ClickHouse binaries on the runner's machine: `clickhouse local` and
  `clickhouse client` in the library's recipes are replaced by Python.

The models library is not a Python package. The configuration names a
version (`models.version`: a release tag, fetched as the GitHub archive
of the tag into `~/.cache/dactopus/`, no git needed) or a path
(`models.path`, for development). Packages run one at a time with
`dbt build --project-dir <library>/sources/<package>`, with a profile
the runner writes; the package names it (`profile: dactopus`). Packages
do not depend on each other; no root dbt project is generated.

## Architecture rules

- Layering: configuration -> loaders (one per source) -> build (dbt) ->
  checks -> report. `serve` is a call to the executor with a list of
  files. Lower layers never import higher ones.
- Commands: `dactopus run` (load, build, check, report), `dactopus check`
  (checks and report only), `dactopus serve` (MCP), `dactopus init`
  (writes a commented sample configuration; no dialogue). Separate load
  and build commands appear when someone needs them apart; until then
  they are flags of `run`.
- A loader creates the package's input tables by running the package's
  `input.sql` from the library, never from a copy, and writes nothing
  else. GA4: select the top-level columns of the Arrow table and insert
  it as is, no struct projection, no Parquet on disk; incremental loads
  reread the daily tables from `max(event_date)` in the input minus the
  package's `ga4_lookback_days` (default 3) and replace those days.
  Shopify: pages of 25 orders with lines, shipping and refunds; an
  incremental load filters `updated_at:>=` from the latest loaded change
  minus one hour and appends the versions it gets (the package keeps
  every version and dedupes). Bulk operations do not deliver refund
  lines; do not use them for the full load.
- A check is a named object that takes a ClickHouse connection and the
  installation's configuration and returns findings. A finding has one
  shape for built-in and plugin checks alike: check, object, severity,
  observed, expected, and a sentence for the owner. Checks register
  through Python entry points; built-in ones are dbt test results (from
  `target/run_results.json` with `manifest.json` for the test's kind,
  column and parameters), input freshness, and rows per day. The report
  is Markdown, to the terminal and to a file, three answers first (how
  fresh the data is; whether every check passed and which did not, in
  words; volumes per day this week against past weeks), details below.
  The exit code is non-zero on a `fail` or `error` finding.
- `serve` passes the model files of the domains the configured packages
  cover (`entities/web_analytics.yaml` for GA4, `entities/commerce.yaml`
  for Shopify) and the connection; the executor combines them. The
  runner does not read, merge or edit model files.
- Memory: a daily GA4 table of a mid-size brand is hundreds of megabytes
  of Arrow. Keep it; read in `ReadRows` batches when a load exceeds it.
- Errors name the step, the object and the next action in plain words;
  the owner reads the report, the engineer reads the log.

## Testing

- A loader is accepted when the input it writes equals, row for row,
  what the library's README recipe writes from the same source: the
  public GA4 sample `ga4_obfuscated_sample_ecommerce`, the Shopify rows
  in the library's `tests/shopify/fixture.sql` and a development store.
- Build and checks are accepted when the package's own `dbt build` and
  the library's `tests/<source>/check_numbers.py` pass over tables the
  runner loaded.
- Tests that use the `clickhouse` fixture are integration tests: they
  need a server at `DACTOPUS_CLICKHOUSE_URL` (default
  `http://127.0.0.1:8123`) and skip otherwise. Tests that need BigQuery
  or Shopify credentials skip without them; loader logic is tested on
  recorded Arrow and JSON fixtures.
- Every change to library code comes with a test. Facts about the
  BigQuery export, the Shopify API and ClickHouse behaviour come from
  running them, not from memory; a version and a date go with each fact
  written down.

## Development

Layout: `src/dactopus/` (library and CLI), `tests/` (pytest, fixtures in
`tests/fixtures/`), `docs/`. Keep modules flat; add a package level only
when a module outgrows one file.

```bash
uv sync                               # install with dev tools
uv run pytest                         # tests; ClickHouse-dependent ones skip when no server
uv run pytest -m "not integration"    # unit tests only
uv run ruff check src tests           # lint
uv run ruff format src tests          # format
uv run dactopus init                  # write dactopus.yaml with comments
uv run dactopus run --config dactopus.yaml
uv run dactopus check --config dactopus.yaml
uv run dactopus serve --config dactopus.yaml   # MCP over stdio
```

Issues and pull requests in this repository are opened by the
maintainer, not by an agent on its own.

## Repository status

A prototype under construction; nothing is released. Versions follow
Semantic Versioning, the changelog follows Keep a Changelog, a release is
the tag `vX.Y.Z` with the GitHub release titled `X.Y.Z`. Pull requests
are merged with rebase.
