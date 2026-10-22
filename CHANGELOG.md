# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). While the major version is 0,
a minor release may change the command line and the configuration file.

## [Unreleased]

### Added

- The repository: the `dactopus` command with `init`, `run`, `check` and
  `serve`.
- The configuration file `dactopus.yaml`: the ClickHouse connection, over
  TLS for an `https://` URL (port 8443 by default), with the password from
  an environment variable, the databases for input and
  canonical tables, the models library by release or by path, and the GA4
  and Shopify packages with their dbt variables.
- `dactopus init` writes a commented sample configuration.
- A release of the models library is downloaded once from GitHub into
  `~/.cache/dactopus/`.
- `dactopus run` loads GA4 and Shopify and builds their packages. GA4: the
  daily tables of the BigQuery export through the Storage Read API, the
  last `ga4_lookback_days` reread and replaced on every run, as the
  Google account the load names; a gcloud login bills the reads to the
  configured project. Shopify:
  orders with lines, shipping and refunds from the GraphQL Admin API
  (2026-10), from an hour before the latest loaded change, with a token
  from the app's client credentials. Each package is built with
  `dbt build` and a dbt profile the runner writes, not the one in the
  package; the profile and dbt's output go to `.dactopus/` next to the
  configuration.
- Checks and the report. A check is a function of a ClickHouse client and
  the configuration that returns findings of one shape (check, object,
  severity, observed, expected, a sentence for the owner), registered
  through the entry point group `dactopus.checks`. Built in: the models,
  tests and unit tests of the last dbt run, the freshness of the input
  against the package's thresholds (`dbt source freshness`), and a day
  without rows in the last seven days that are over of `events` or
  `orders`, unless the same weekday had none in the four weeks before.
  `dactopus run` ends with the report, `dactopus check` runs `dbt test`
  and the checks without loading or building; both write it to the
  terminal and to `.dactopus/report.md` (freshness, what did not pass,
  rows per day for the last seven days that are over, in the shop's time
  zone for Shopify, against the four weeks before, then every finding) and
  exit with 1 on a `fail` or `error` or when no check ran. An unknown
  `shopify_timezone` is refused when the configuration is read. A dbt run
  with failing tests or models no longer stops the command: its results
  are in the report.
- `dactopus serve` runs the MCP server of dactopus-ossie-clickhouse 0.9.5
  over stdio with the models of the configured packages
  (`entities/web_analytics.yaml` for GA4, `entities/commerce.yaml` for
  Shopify) read as one model, on the canonical database, over TLS for an
  `https://` URL on any port. The password reaches the executor inside
  the process, never on a command line. The sample configuration names
  the models library v0.5.3, the first release whose GA4 and commerce
  models can be served together.
