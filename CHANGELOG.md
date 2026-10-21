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
