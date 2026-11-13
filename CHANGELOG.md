# Changelog

The format follows [Common Changelog](https://common-changelog.org); versions follow [Semantic Versioning](https://semver.org), and while the major version is 0, a minor release may change the command line and the configuration file. What an installation does to move to a release: [UPGRADING.md](https://github.com/Dactopus/dactopus/blob/main/UPGRADING.md). Why the runner is built as it is: [docs/design.md](https://github.com/Dactopus/dactopus/blob/main/docs/design.md).

## [0.6.0] - 2026-11-13

_Moving to it takes the models library v0.13.1, three columns in the input of each Shopify source, and one `dactopus run --reload` of each Shopify source and `--full-refresh` of each WooCommerce source ([Upgrading](https://github.com/Dactopus/dactopus/blob/main/UPGRADING.md#060))._

### Changed

- **Breaking:** Require the models library v0.13.1 or later for a Shopify source, and its billing address columns in the input's `orders`; an input without them is refused before anything is loaded, naming the library's upgrade steps ([#40](https://github.com/Dactopus/dactopus/pull/40))
- Install dactopus-ossie-clickhouse 0.17, which rounds a value exactly halfway away from zero and aligns the scales in `MOD` of a decimal ([#40](https://github.com/Dactopus/dactopus/pull/40))

### Added

- Load the address a Shopify order was billed to: its country, province and city ([#40](https://github.com/Dactopus/dactopus/pull/40))
- Add `dactopus run --reload LABEL ...`, which loads the named Shopify sources again from the first order, keeps what was loaded, and rebuilds them ([#40](https://github.com/Dactopus/dactopus/pull/40), [decision](https://github.com/Dactopus/dactopus/blob/main/docs/design.md#reloading-a-shopify-source-13-november-2026))

## [0.5.0] - 2026-11-11

_Moving to it takes the models library v0.13.0 and one `dactopus run` before `dactopus serve` ([Upgrading](https://github.com/Dactopus/dactopus/blob/main/UPGRADING.md#050))._

### Changed

- **Breaking:** Require the models library v0.13.0 or later, the first written in Ossie schema `0.3.0.dev0`; an earlier `models.version` is refused before anything runs ([#37](https://github.com/Dactopus/dactopus/pull/37))
- Install dactopus-ossie-clickhouse 0.16, which answers a metric of orders by a field of their lines and a filter on products in a question about orders ([#37](https://github.com/Dactopus/dactopus/pull/37))

### Added

- Ask the executor, before the first step of every command, whether it reads the models of the configured library, and stop with its reason and the action when it does not ([#37](https://github.com/Dactopus/dactopus/pull/37), [#29](https://github.com/Dactopus/dactopus/issues/29), [decision](https://github.com/Dactopus/dactopus/blob/main/docs/design.md#the-executor-says-whether-a-library-fits-11-november-2026))
- Say on stderr at `dactopus serve` how many fields and metrics of the model the serve user is not served ([#37](https://github.com/Dactopus/dactopus/pull/37))

## [0.4.0] - 2026-11-09

_Moving to it takes the models library v0.12.2, a grant and one `dactopus run --full-refresh` of sources built by an older library ([Upgrading](https://github.com/Dactopus/dactopus/blob/main/UPGRADING.md#040))._

### Changed

- **Breaking:** Require the models library v0.12.2 or later; an earlier `models.version` is refused before anything runs ([#33](https://github.com/Dactopus/dactopus/pull/33))
- **Breaking:** Require `ALTER MODIFY COMMENT` for the user of `clickhouse.url` on the tables it builds, where the package records their settings ([#33](https://github.com/Dactopus/dactopus/pull/33))
- Stop the build of a source whose tables were built with another zone or label, before a row is touched, and name `dactopus run --full-refresh <label>` in the report; the other sources go on ([#33](https://github.com/Dactopus/dactopus/pull/33), [decision](https://github.com/Dactopus/dactopus/blob/main/docs/design.md#the-zone-of-the-tables-is-the-librarys-to-guard-9-november-2026))
- Install dactopus-ossie-clickhouse 0.14 ([#33](https://github.com/Dactopus/dactopus/pull/33))

### Removed

- Remove the count, before loading, of a source's rows on another day in its zone, which stopped the whole run and missed a store without orders near midnight; the `time_zone` check stays ([#33](https://github.com/Dactopus/dactopus/pull/33))

## [0.3.0] - 2026-11-03

_Moving to it takes the configuration written again and the old canonical tables moved out of the way ([Upgrading](https://github.com/Dactopus/dactopus/blob/main/UPGRADING.md#030))._

### Changed

- **Breaking:** Replace `packages` with `sources`, labels mapped to sources, and `store.timezone` with a top-level `timezone`; remove `clickhouse.input_database`. A configuration of 0.2 is refused ([#30](https://github.com/Dactopus/dactopus/pull/30))
- **Breaking:** Build each source into a database of its own, `<clickhouse.database>_<label>`, from `<clickhouse.database>_<label>_raw` or the database its `input_database` names ([#30](https://github.com/Dactopus/dactopus/pull/30), [decision](https://github.com/Dactopus/dactopus/blob/main/docs/design.md#sources-under-labels-with-databases-of-their-own-2-november-2026))
- **Breaking:** Serve the model of each union that has sources in place of each package's model; the `serve` user needs `SELECT` on `clickhouse.database` alone ([#30](https://github.com/Dactopus/dactopus/pull/30), [#32](https://github.com/Dactopus/dactopus/pull/32), [decision](https://github.com/Dactopus/dactopus/blob/main/docs/design.md#always-through-a-union-2-november-2026))
- **Breaking:** Refuse, before loading, a `clickhouse.database` that holds tables, such as the canonical tables of 0.2 ([#30](https://github.com/Dactopus/dactopus/pull/30))
- **Breaking:** Take labels, not packages, in `dactopus run --full-refresh` ([#30](https://github.com/Dactopus/dactopus/pull/30))
- **Breaking:** Require the models library v0.10.0 or later ([#32](https://github.com/Dactopus/dactopus/pull/32))
- Go on with the other sources when dbt cannot build, test or measure one; the report shows dbt's error and the run exits with 1 ([#30](https://github.com/Dactopus/dactopus/pull/30), [decision](https://github.com/Dactopus/dactopus/blob/main/docs/design.md#a-dbt-step-that-could-not-run-does-not-stop-the-others-2-november-2026))
- Write dbt's output for each source to `.dactopus/<label>/` ([#30](https://github.com/Dactopus/dactopus/pull/30))
- Refuse a source whose package's model no union serves, a package the library does not have, and `vars` that set the label, the input database or the time zone ([#30](https://github.com/Dactopus/dactopus/pull/30))

### Added

- Build, check and serve every package of the models library; the input of a package without a loader (WooCommerce, Sensor.Community) is the client's to write ([#30](https://github.com/Dactopus/dactopus/pull/30))
- Add several sources to one installation, each under a label of 1 to 64 of `a-z`, `0-9` and `_` that the package gets as its `source_id` ([#30](https://github.com/Dactopus/dactopus/pull/30))
- Add `timezone` to a source, the zone its days are in, and take the zone of a package that fixes it from its `deployment.yml` ([#30](https://github.com/Dactopus/dactopus/pull/30), [#32](https://github.com/Dactopus/dactopus/pull/32), [decision](https://github.com/Dactopus/dactopus/blob/main/docs/design.md#a-package-that-fixes-its-zone-3-november-2026))
- Build the unions of the models library after the sources, as views in `clickhouse.database`, and test them in `dactopus check` ([#30](https://github.com/Dactopus/dactopus/pull/30))
- Add `source` to a finding, and group the report by source ([#30](https://github.com/Dactopus/dactopus/pull/30))

### Fixed

- Count the rows per day of every table of a GA4 source over the same week, the one ending at the latest day of any of them ([#30](https://github.com/Dactopus/dactopus/pull/30))

## [0.2.1] - 2026-11-01

### Fixed

- Refuse Python 3.14, where dbt-core 1.11 does not start, with the install command for 3.13 ([#28](https://github.com/Dactopus/dactopus/pull/28), [decision](https://github.com/Dactopus/dactopus/blob/main/docs/design.md#python-311-to-313-1-november-2026))

## [0.2.0] - 2026-11-01

_Moving to it from an installation from GitHub takes one reinstall ([Upgrading](https://github.com/Dactopus/dactopus/blob/main/UPGRADING.md#020))._

### Changed

- Install dactopus-ossie-clickhouse 0.13.0 from PyPI, which carries its own apache-ossie ([#20](https://github.com/Dactopus/dactopus/pull/20))

### Added

- Publish to PyPI: `uv tool install dactopus` ([#20](https://github.com/Dactopus/dactopus/pull/20))

## [0.1.1] - 2026-10-30

### Added

- Add `docs/design.md`, the decisions behind the runner and what each was verified on, and the scope of 0.1.0 to the README ([#18](https://github.com/Dactopus/dactopus/pull/18))

## [0.1.0] - 2026-10-30

_Initial release, a prototype, on the models library v0.8.0 and dactopus-ossie-clickhouse 0.12.0._

### Added

- Add the commands `init`, `run`, `check` and `serve`, and the configuration file `dactopus.yaml` ([#4](https://github.com/Dactopus/dactopus/pull/4))
- Load the daily tables of the GA4 BigQuery export with the Storage Read API, rereading the last `ga4_lookback_days` ([#4](https://github.com/Dactopus/dactopus/pull/4), [#5](https://github.com/Dactopus/dactopus/pull/5))
- Load Shopify orders with their lines, shipping and refunds, then the products and their deletions, from the GraphQL Admin API ([#4](https://github.com/Dactopus/dactopus/pull/4), [#16](https://github.com/Dactopus/dactopus/pull/16))
- Add checks behind the entry point group `dactopus.checks`, the built-in checks `dbt`, `freshness`, `rows_per_day` and `time_zone`, and the report ([#6](https://github.com/Dactopus/dactopus/pull/6), [#14](https://github.com/Dactopus/dactopus/pull/14))
- Serve the models over MCP with dactopus-ossie-clickhouse, as a ClickHouse user of its own ([#8](https://github.com/Dactopus/dactopus/pull/8), [#12](https://github.com/Dactopus/dactopus/pull/12))
- Add `dactopus run --full-refresh`, which rebuilds the canonical tables from the input already loaded ([#11](https://github.com/Dactopus/dactopus/pull/11))
- Read what the runner needs of a package from its `deployment.yml` in the models library ([#13](https://github.com/Dactopus/dactopus/pull/13))
- Pass one time zone per installation to the packages and to `serve`, and refuse a Shopify store in another ([#14](https://github.com/Dactopus/dactopus/pull/14))

[0.6.0]: https://github.com/Dactopus/dactopus/releases/tag/v0.6.0
[0.5.0]: https://github.com/Dactopus/dactopus/releases/tag/v0.5.0
[0.4.0]: https://github.com/Dactopus/dactopus/releases/tag/v0.4.0
[0.3.0]: https://github.com/Dactopus/dactopus/releases/tag/v0.3.0
[0.2.1]: https://github.com/Dactopus/dactopus/releases/tag/v0.2.1
[0.2.0]: https://github.com/Dactopus/dactopus/releases/tag/v0.2.0
[0.1.1]: https://github.com/Dactopus/dactopus/releases/tag/v0.1.1
[0.1.0]: https://github.com/Dactopus/dactopus/releases/tag/v0.1.0
