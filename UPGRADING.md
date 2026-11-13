# Upgrading

`uv tool upgrade dactopus` installs the latest release. To move across
several releases, do the steps of every release in between, oldest
first. A release not listed here needs only the upgrade. What each
release changed:
[CHANGELOG.md](https://github.com/Dactopus/dactopus/blob/main/CHANGELOG.md).

A release of the runner that raises the models library's floor sends
its installations to that library's
[UPGRADING.md](https://github.com/Dactopus/dactopus-data-models/blob/main/UPGRADING.md)
too: the steps below say what the runner does for them, and the
library's say what changes in the tables and the models.

## 0.5.0

The runner reads the models library v0.13.0 or later and installs
dactopus-ossie-clickhouse 0.16.

- Set `models.version` to v0.13.0 and run `dactopus run` once before
  `dactopus serve`: the build creates the view of orders by discount
  code, and until it exists the model is served without that dataset.
  No `--full-refresh` is needed, and the numbers of the other tables do
  not change.
- `dactopus serve` writes to stderr how many fields and metrics of the
  model the serve user is not served, because a table is not built or
  not granted. Restart it after a build that adds tables.
- Expect an answer where an agent got a refusal: a metric of orders by
  a field of their lines (`orders.order_count` by
  `order_lines.product_id`), and a filter on products in a question
  about orders. The changelogs of dactopus-ossie-clickhouse 0.15.0 and
  0.16.0 define both.

## 0.4.0

The runner reads the models library v0.12.2 or later and refuses an
earlier `models.version` before anything runs.

- Run `dactopus run --full-refresh` once after setting
  `models.version`, for the sources whose first incremental build
  would stop: every source built by a release before v0.12.0, whose
  tables lack its columns, and GA4 and Sensor.Community sources built
  before v0.12.2, which record their label from that release on. Shopify
  and WooCommerce sources built by v0.12.0 or v0.12.1 need none. The
  rebuild reads the input already loaded: nothing is fetched again from
  Shopify or BigQuery.
- Grant the user of `clickhouse.url` `ALTER MODIFY COMMENT` on the
  tables it builds (part of `ALTER TABLE`): the record of the settings is
  the table's comment.
- Rename 18 metrics of the commerce model in saved questions and agent
  prompts: they moved to the datasets they count, so a question asks for
  `order_lines.items_sold`, not `items_sold`. The numbers are the same;
  the library's
  [UPGRADING.md, 0.12.0](https://github.com/Dactopus/dactopus-data-models/blob/main/UPGRADING.md#0120)
  lists them.

## 0.3.0

The runner refuses a configuration of 0.2.

- Write the configuration again from `dactopus init`, with each entry
  of `packages` as a source under a label and `store.timezone` as
  `timezone`.
- Keep the input already loaded where it is: give each source
  `input_database: dactopus_raw` (the old `clickhouse.input_database`),
  and the next load is incremental. Sources of different packages may
  share an input database; two of one package may not.
- The canonical tables are built again, in the database of each source;
  no `--full-refresh` is needed.
- Give `clickhouse.database` another name, or drop the old canonical
  tables in it if nothing reads them: it is now the database of the
  unions, and `dactopus run` refuses to build views next to tables
  before loading anything.

## 0.2.0

The runner is on PyPI from this release.

- Reinstall an installation of 0.1.0 or 0.1.1, which came from GitHub,
  with `uv tool install --force --python 3.13 dactopus`:
  `uv tool upgrade` keeps it on the GitHub tag.
