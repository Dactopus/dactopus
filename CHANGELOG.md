# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). While the major version is 0,
a minor release may change the command line and the configuration file.

## [Unreleased]

## [0.4.0] - 2026-11-09

### Changed

- The models library v0.12.2 or later: an earlier release is refused
  before anything runs. The sample configuration and the tests use
  v0.12.2. Sources built by a release before v0.12.0, and GA4 and
  Sensor.Community sources built before v0.12.2, need `dactopus run
  --full-refresh` once: their first incremental build stops until then
  (README, "Upgrading from 0.3").
- The user of `clickhouse.url` needs `ALTER MODIFY COMMENT` on the
  tables it builds: the package records the settings of a table in its
  comment.
- dactopus-ossie-clickhouse 0.14, which `serve` runs.
- A source's tables built with other settings than the run's, its zone
  or its label, stop that source's build in its package, before a row is
  touched; the other sources go on. The report names the table, each
  setting with its old and new value, and `dactopus run --full-refresh
  <label>`, where it showed the package's dbt command.

### Removed

- The count, before loading, of the rows of a source's tables on another
  day in its zone, which stopped the whole run. It missed a change of
  zone in a store without orders near midnight; the settings guard of
  the models library replaces it. The `time_zone` check of the report
  stays.

## [0.3.0] - 2026-11-03

### Added

- Any package of the models library: a source names it in `package`. The
  runner loads GA4 and Shopify; it builds, checks and serves the others
  (WooCommerce, Sensor.Community) over the input the client's transport
  (CDC) or the client writes, and leaves that database alone.
- Several stores, GA4 properties or sensor networks in one installation,
  each a source under its own label: 1 to 64 of `a-z`, `0-9` and `_`,
  passed to the package as its `source_id`. Each source builds into a
  database of its own, `<clickhouse.database>_<label>`, from its input in
  `<clickhouse.database>_<label>_raw` or the database its
  `input_database` names. A label whose database is another source's
  input, and two sources of one package reading one input database, are
  refused.
- A source takes `timezone`, the zone its days are in: a store package is
  built in it, and the time zone check counts the days of the source's
  tables in it (for GA4 the property's zone). A package that fixes the
  zone of its days declares it as `zone` in its `deployment.yml`
  (Sensor.Community: UTC) and the runner uses it; a `timezone` on a
  source of such a package is refused. The report says when a source's
  days are in another zone than the installation's.
- A source dbt cannot build, test or measure (a package's required
  variable missing, as `woocommerce_currency`) no longer stops the run:
  the other sources go on, the report shows dbt's error with the
  package's message, and the run exits with 1.
- A finding has an optional `source`, the label of the source it is
  about. The report groups the findings and the rows per day by source,
  in the order of the configuration, and names the source on each line of
  its three answers.
- The unions of the models library: `dactopus run` builds each union
  that has sources after the sources, as views in `clickhouse.database`
  over the databases of its sources, and `dactopus check` tests them.
  Their findings have `union-<name>` as their `source` (`union-commerce`),
  and the report names the union in words ("the commerce union"). The
  views belong to the user of `clickhouse.url`: to change that user, run
  `dactopus run` as the new one first, then drop the old one.

### Changed

- The configuration file. `sources`, a mapping of labels to sources,
  replaces `packages`; `timezone` at the top replaces `store.timezone`
  and is the installation's zone, "today" in `dactopus serve` and the
  zone of each source that names none; `clickhouse.input_database` is
  gone, and `clickhouse.database` is the prefix of the sources' databases.
  A configuration of 0.2 is refused: write it again from `dactopus init`.
- `dactopus run --full-refresh` takes labels.
- `dactopus serve` serves the model of each union that has sources, one
  per area (commerce, web analytics), in place of each package's model.
  The `serve` user needs `SELECT` on `clickhouse.database` alone: the
  views read the sources' databases as their owner.
- `dactopus run` refuses, before loading, a `clickhouse.database` that
  holds tables, such as the canonical tables of 0.2: the unions would
  replace those of the same name with views. Give `clickhouse.database`
  another name, or drop the old tables if nothing reads them.
- A source whose package's model no union of the library serves is
  refused before anything runs.
- dbt's output for each source goes to `.dactopus/<label>/`, with the
  profile that writes to the source's database.
- The models library v0.10.0 or later: an earlier release is refused
  before anything runs, with the version to set. The sample configuration
  and the tests use v0.10.0. The comment on `models.version` in the
  sample says to run `dactopus run --full-refresh` once after moving to a
  release that changes a model's columns.
- A package the library does not have is named with the packages it
  has; a source's `vars` may not set the variables of the label, the
  input database or the time zone.

### Fixed

- The rows per day of a GA4 source in the report: each table ended its
  week at its own latest day, so the purchases of a site whose last
  purchase came days before its last events were counted over another
  week than the events. Every table of a source without a time zone now
  ends at the latest day of any of them, and a day without rows is 0.

## [0.2.1] - 2026-11-01

### Fixed

- Python 3.11 to 3.13. On 3.14 the install succeeded and the first
  `dactopus run` failed inside dbt: dbt-core 1.11 does not start there.
  The README installs with `uv tool install --python 3.13 dactopus`;
  every command refuses 3.14 and gives that command with `--force`;
  `requires-python` is `>=3.11,<3.14`, which pip enforces and uv does
  not.
- The README says how to update (`uv tool upgrade dactopus`) and how an
  installation of 0.1.0 or 0.1.1 from GitHub moves to PyPI
  (`uv tool install --force --python 3.13 dactopus`).

## [0.2.0] - 2026-11-01

### Added

- On PyPI: `uv tool install dactopus`. A release on GitHub publishes its
  tag (`.github/workflows/publish.yml`, trusted publishing); run by hand,
  the workflow uploads a dev version to TestPyPI as a rehearsal. CI builds
  the package, checks that its README renders as the PyPI page, and runs
  the wheel outside the checkout. The README's links are absolute, so
  they work on PyPI.

### Changed

- dactopus-ossie-clickhouse 0.13.0, from PyPI in place of a git
  reference to 0.12.0. It carries its own copy of apache-ossie, so
  apache-ossie is no longer installed with the runner. `dactopus serve`
  gains the questions the executor answers since 0.12.1
  ([its changelog](https://github.com/Dactopus/dactopus-ossie-clickhouse/blob/main/CHANGELOG.md)).

## [0.1.1] - 2026-10-30

### Added

- `docs/design.md`: the decisions behind the runner, the reasons for
  each and what each was verified on. The README gains the scope of
  0.1.0: what the runner does not do and what it is untested on.

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

[Unreleased]: https://github.com/Dactopus/dactopus/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Dactopus/dactopus/releases/tag/v0.4.0
[0.3.0]: https://github.com/Dactopus/dactopus/releases/tag/v0.3.0
[0.2.1]: https://github.com/Dactopus/dactopus/releases/tag/v0.2.1
[0.2.0]: https://github.com/Dactopus/dactopus/releases/tag/v0.2.0
[0.1.1]: https://github.com/Dactopus/dactopus/releases/tag/v0.1.1
[0.1.0]: https://github.com/Dactopus/dactopus/releases/tag/v0.1.0
