# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). While the major version is 0,
a minor release may change the command line and the configuration file.

## [Unreleased]

## [0.1.0] - 2026-10-30

The first release, a prototype.

### Added

- The `dactopus` command with `init`, `run`, `check` and `serve`,
  installed with `uv tool install` from a release tag on GitHub.
- The configuration file `dactopus.yaml`: the ClickHouse connection, over
  TLS for an `https://` URL (port 8443 by default), with the password from
  an environment variable, the databases for input and
  canonical tables, the store's time zone (`store.timezone`, required),
  the models library by release or by path, and the GA4 and Shopify
  packages with their dbt variables.
- `dactopus init` writes a commented sample configuration.
- A release of the models library is downloaded once from GitHub into
  `~/.cache/dactopus/`.
- `dactopus run` loads GA4 and Shopify and builds their packages. GA4: the
  daily tables of the BigQuery export through the Storage Read API, the
  last `ga4_lookback_days` reread and replaced on every run, as the
  Google account the load names; a gcloud login bills the reads to the
  configured project. Shopify: orders with lines, shipping and refunds,
  then the products, archived ones and drafts included, and the
  deletions of products from the store's events, from the GraphQL Admin
  API (2026-10), each kind from an hour before its latest loaded change,
  with a token from the app's client credentials (`read_orders` and
  `read_products`); a models library before v0.7.0, whose Shopify input
  has no `products`, is refused before anything is loaded, with the
  version to set. A refund carries its `processedAt`, the date Shopify's
  reports count it on and the models library's since v0.7.2; a library
  before it, or a refunds table an earlier library created, is refused
  the same way, naming the library's changelog for adding the column.
  Each package is built with
  `dbt build` and a dbt profile the runner writes, not the one in the
  package; the profile and dbt's output go to `.dactopus/` next to the
  configuration.
- Checks and the report. A check is a function of a ClickHouse client and
  the configuration that returns findings of one shape (check, object,
  severity, observed, expected, a sentence for the owner), registered
  through the entry point group `dactopus.checks`. Built in: the models,
  tests and unit tests of the last dbt run, the freshness of the input
  against the package's thresholds (`dbt source freshness`), and a day
  without rows in the last seven days that are over of a table the
  package marks as a gap (`events`, `orders`), unless the same weekday
  had none in the four weeks before.
  `dactopus run` ends with the report, `dactopus check` runs `dbt test`
  and the checks without loading or building; both write it to the
  terminal and to `.dactopus/report.md` (freshness, what did not pass,
  rows per day for the last seven days that are over, in
  `store.timezone` for Shopify, against the four weeks before, then every
  finding) and exit with 1 on a `fail` or `error` or when no check ran. An unknown
  `store.timezone`, and `shopify_timezone` among the package's vars, are
  refused before any connection. A dbt run
  with failing tests or models no longer stops the command: its results
  are in the report.
- `dactopus run --full-refresh [PACKAGE ...]` rebuilds the canonical
  tables of the named packages (every configured package when none is
  named) from the input the runner holds, with `dbt build --full-refresh`
  after the usual load; the input is not reloaded. For a release of the
  models library that changes a model's columns, which the `dbt` check
  reports together with this command, or a change of the store's time
  zone.
- `dactopus serve` runs the MCP server of dactopus-ossie-clickhouse 0.12.0
  over stdio with the models of the configured packages
  (`entities/web_analytics.yaml` for GA4, `entities/commerce.yaml` for
  Shopify) read as one model, on the canonical database, over TLS for an
  `https://` URL on any port, as the ClickHouse user of the optional
  `serve` section (`user`, `password_env`) or else the user of
  `clickhouse.url`, with "today" in `store.timezone` (the executor's
  `--time-zone`). Each command reads only the password it connects
  with. The password reaches the executor inside the process, never on a
  command line.
- One time zone per installation, `store.timezone`: the runner passes it
  to every package that declares a time zone variable and to `serve`.
  With Shopify configured, `dactopus run` reads the shop's zone
  (`shop { ianaTimezone }`) before loading and refuses a different
  `store.timezone`, naming both; another name of the same zone (`UTC`
  for `Etc/UTC`) passes. Before loading it also counts the rows of
  the daily tables of each package with a time zone variable whose date
  is not the day of their time in `store.timezone`, and stops on any with
  the `dactopus run --full-refresh <package>` that rebuilds them; a
  package rebuilt with `--full-refresh` is not counted. The built-in check
  `time_zone` runs the same count after the build: a `fail` for a package
  with a time zone variable, a `warn` with the share of rows for one
  without (GA4, whose days are the property's), over the days
  `rows_per_day` reads. Zone names come from the `tzdata` package on
  every platform as well, so a name newer than the machine's own tz
  database (`Europe/Kyiv`) is known.
- The runner reads what it sets and reads of a package from the
  package's `deployment.yml` in the models library: the Ossie model
  `serve` passes, the dbt variables that take the input database and the
  time zone, and the tables `rows_per_day` counts, with the ones where a
  day without rows is a finding. A renamed variable, model file or
  counted table needs no release of the runner. The models library must be
  v0.6.0 or later, the first release with the file; the sample
  configuration names v0.8.0, and an older `models.version` is refused
  with the version to set. The vars of a package are checked against
  the file before any connection.

[Unreleased]: https://github.com/Dactopus/dactopus/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Dactopus/dactopus/releases/tag/v0.1.0
