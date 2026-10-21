# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). While the major version is 0,
a minor release may change the command line and the configuration file.

## [Unreleased]

### Added

- The repository: the `dactopus` command with `init`, `run`, `check` and
  `serve`, which report that they are not implemented yet.
- The configuration file `dactopus.yaml`: the ClickHouse connection with
  the password from an environment variable, the databases for input and
  canonical tables, the models library by release or by path, and the GA4
  and Shopify packages with their dbt variables.
- `dactopus init` writes a commented sample configuration.
- A release of the models library is downloaded once from GitHub into
  `~/.cache/dactopus/`.
- `dactopus run` loads GA4 and Shopify and builds their packages. GA4: the
  daily tables of the BigQuery export through the Storage Read API, the
  last `ga4_lookback_days` reread and replaced on every run. Shopify:
  orders with lines, shipping and refunds from the GraphQL Admin API
  (2026-10), from an hour before the latest loaded change, with a token
  from the app's client credentials. Each package is built with
  `dbt build` and a dbt profile the runner writes, not the one in the
  package; the profile and dbt's output go to `.dactopus/` next to the
  configuration.
