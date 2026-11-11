# dactopus

[![CI](https://github.com/Dactopus/dactopus/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Dactopus/dactopus/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/Dactopus/dactopus)](https://github.com/Dactopus/dactopus/blob/main/LICENSE)

The runner of Dactopus: the process a client installs next to their
[ClickHouse](https://clickhouse.com/docs). It loads sources into that
ClickHouse, builds the canonical e-commerce tables, checks them, and
serves their [Apache Ossie](https://github.com/apache/ossie) models to AI
agents over [MCP](https://modelcontextprotocol.io).

A prototype; 0.1.0 is the first release. While the major version is 0, a
minor release may change the command line and the configuration file.

## How the parts fit

| Part | Role |
| --- | --- |
| dactopus (this repository) | Loads GA4 and Shopify, runs the packages, checks, serves |
| [dactopus-data-models](https://github.com/Dactopus/dactopus-data-models) | Canonical entities and source packages (dbt projects) |
| [dactopus-ossie-clickhouse](https://github.com/Dactopus/dactopus-ossie-clickhouse) | Executes Ossie models on ClickHouse; the MCP server |

The runner owns delivery of data to a package's input tables and running
the package. The library owns the input contract and the transformations.
The executor knows nothing of Dactopus.

## Install

[uv](https://docs.astral.sh/uv/). The runner runs on Python 3.11 to 3.13;
`--python 3.13` makes uv use that version, and download it when the
machine has none.

```bash
uv tool install --python 3.13 dactopus
```

The `dactopus` command gets its own environment with dbt, the dbt
ClickHouse adapter and the executor. The machine needs no ClickHouse
binaries; the runner connects to the client's ClickHouse server over HTTP.

`uv tool upgrade dactopus` updates it. An installation of 0.1.0 or
0.1.1, which came from GitHub, moves to PyPI with
`uv tool install --force --python 3.13 dactopus`: `uv tool upgrade`
keeps it on the GitHub tag.

Then, in the folder of the installation:

```bash
dactopus init     # writes dactopus.yaml; edit it
dactopus run      # loads, builds, checks, writes the report
dactopus serve    # the MCP server for an AI agent, over stdio
```

## Commands

| Command | What it does |
| --- | --- |
| `dactopus init` | Writes a commented sample configuration |
| `dactopus run` | Loads the sources, builds them and the unions, runs the checks, writes the report |
| `dactopus check` | Tests the sources and the unions, runs the checks and writes the report |
| `dactopus serve` | Serves the models over MCP |

One configuration file describes an installation: the ClickHouse
connection and the name its databases start with, the installation's
time zone, the version of the models library, and the sources.
`dactopus init` writes it with a comment on every field. Secrets stay out
of it: the file names the environment variables that hold them.

## Sources

A source is a store, a GA4 property or a sensor network, under a label
of its own: 1 to 64 of `a-z`, `0-9` and `_`. Agents and BI see the
label, so give the name people call the store by (`main_store`,
`europe_store`, `website`). `package` names a package of the models
library; the runner passes the label to it as its `source_id`, which
tells the sources apart in the unions.

Every source has databases of its own, named after `clickhouse.database`
(`dactopus` in the sample):

| Database | Holds |
| --- | --- |
| `dactopus` | The views of the unions, which `serve` reads |
| `dactopus_<label>` | The canonical tables of the source |
| `dactopus_<label>_raw` | The input of the source, unless the source names another in `input_database` |

The runner loads GA4 and Shopify. The input of another package
(WooCommerce, Sensor.Community) is written by the client's transport
(CDC: PeerDB, ClickPipes) or by the client, into the database the
source names in `input_database`; the runner builds, checks and serves
it and leaves that database alone. A package's required variables, such
as `woocommerce_currency`, go in the source's `vars`.

`timezone` at the top of the file is the installation's time zone, an
IANA name such as `America/New_York`: "today" in an agent's question is
the date in it. A source whose days are in another zone names it in its
own `timezone`: for Shopify the store's zone (Settings > General in the
admin), for GA4 the property's (Admin > Property details). A package that
fixes the zone of its days declares it in its `deployment.yml` in the
models library (Sensor.Community: `UTC`); a source of such a package names
no `timezone`, and the runner refuses one. The rows of a source keep the
days of its zone; "today" is the installation's date for every source. The
runner passes a source's zone to its package when the package takes one
(`shopify_timezone` for Shopify, which the source's `vars` may not set).

For a Shopify source, `dactopus run` first reads the shop's zone and
refuses one that differs from the source's; another name of the same
zone (`UTC` for `Etc/UTC`) passes. The tables of a source record the
settings their rows depend on, the zone and the label among them (the
settings guard of the models library). The Shopify models are
incremental, and a build after the zone changed would leave the orders
it does not rebuild on the days of the old one, so that build stops
before it touches a row. The other sources go on, and the report names
the command that rebuilds the source, `dactopus run --full-refresh
main_store`; the agent keeps answering from the tables as they were
until then.

## Running

`dactopus run` loads each source it has a loader for into the input
tables of its package, then runs `dbt build` of each source's package
into the source's database, then of each union that has sources (see
"Serving the models"). A load reads only what changed:

| Source | Read from | Next load |
| --- | --- | --- |
| GA4 | The daily `events_YYYYMMDD` tables of the BigQuery export, with the Storage Read API | The days from the latest loaded one minus `ga4_lookback_days`, which replace what was loaded for them |
| Shopify | Orders with their lines, shipping and refunds, 25 per page, then the products, archived ones and drafts included, and the deletions of products from the store's events, from the GraphQL Admin API | The orders and the products updated from an hour before the latest loaded change of their kind, appended; a deleted product as a version with the time of the deletion |

GA4 reads need Google credentials: a service account key named in the
file, or Application Default Credentials (`gcloud auth
application-default login`). The read sessions run in the configured
project, which pays for them. Shopify needs an app with `read_orders`
and `read_products`, installed on the store, and its client id and
secret; without `read_all_orders` Shopify returns the last 60 days of
orders only. Two Shopify stores name different environment variables in
`client_id_env` and `client_secret_env`.

A load writes into the input tables the models library's `input.sql`
creates, and `input.sql` creates a table only if it is missing. When a
release of the library adds a column to a table an earlier one created,
the load stops before reading anything and names the table, the column
and the library's changelog, which says how to add it: the Shopify
refunds' `processed_at` of v0.7.2, the date a refund counts on.

A source dbt cannot build, test or measure (a required variable of its
package missing, say) does not stop the others: the report shows dbt's
error with the package's message, and the run exits with 1.

The runner writes the dbt profile the packages name (`profile: dactopus`)
from the configuration, without the password, which dbt reads from the
environment. The profile and dbt's output go to `.dactopus/<label>/` next
to the configuration file, those of a union to `.dactopus/union-<name>/`,
and the report to `.dactopus/report.md`.

`dactopus run --full-refresh [LABEL ...]` loads the sources as usual,
then builds the named sources (every source when none is named) with
`dbt build --full-refresh`: the canonical tables are rebuilt
from the input the runner already holds, which keeps every version of
every row. The input is not reloaded from the start: the usual
incremental load adds only what is new. Run it once when a
release of the models library changes a model's columns or starts to
record a setting of a source's tables, and when the time zone of a source
whose package takes one changes (Shopify, WooCommerce): the next
incremental build stops with an error, and the report names the table and
this command. Each table exists
twice on disk until its rebuild ends: dbt builds the new table next to the
old one and swaps them when it is complete, so `serve` keeps answering
from the old table meanwhile.

## Models library and runner

A release of the runner reads the releases of the models library made
for the dactopus-ossie-clickhouse it installs: the two agree on the
version of the Ossie schema the models are written in.

| Runner | Models library | dactopus-ossie-clickhouse |
|---|---|---|
| 0.5 | v0.13.0 or later | 0.16 |
| 0.4 | v0.12.2 | 0.14 |

Before its first step, every command asks the executor whether it reads
the models of the library the configuration names. When it does not,
the command stops with the executor's reason, and nothing is loaded or
built. A library release newer than the runner reads needs `uv tool
upgrade dactopus`; a release older than the runner reads needs a later
`models.version`. A `models.version` earlier than v0.13.0 is refused
by its number, before it is downloaded.

## Upgrading from 0.4

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
- An agent gets an answer where it got a refusal: a metric of orders by
  a field of their lines (`orders.order_count` by
  `order_lines.product_id`), and a filter on products in a question
  about orders. The changelogs of dactopus-ossie-clickhouse 0.15.0 and
  0.16.0 define both.

## Upgrading from 0.3

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
- The user of `clickhouse.url` needs `ALTER MODIFY COMMENT` on the
  tables it builds (part of `ALTER TABLE`): the record of the settings is
  the table's comment.
- 18 metrics of the commerce model moved to the datasets they count: a
  question asks for `order_lines.items_sold`, not `items_sold`. The
  numbers are the same; saved questions and agent prompts that name the
  old ones need the new names (the library's changelog, 0.12.0, lists
  them).

## Upgrading from 0.2

The runner refuses a configuration of 0.2: write it again from
`dactopus init`, with each entry of `packages` as a source under a
label and `store.timezone` as `timezone`.

- The input already loaded stays where it is: give each source
  `input_database: dactopus_raw` (the old `clickhouse.input_database`),
  and the next load is incremental. Sources of different packages may
  share an input database; two of one package may not.
- The canonical tables are built again, in the database of each source;
  no `--full-refresh` is needed.
- The old canonical tables are in `dactopus`, the database of the
  unions. `dactopus run` refuses to build views over them before loading
  anything: give `clickhouse.database` another name, or drop the old
  tables if nothing reads them.

## Checks and the report

After the build, `dactopus run` measures the freshness of each source's
input (`dbt source freshness`, with the thresholds the package sets), runs
the checks and writes the report, to the terminal and to
`.dactopus/report.md`. `dactopus check` does the same over the tables as
they are, with `dbt test` of the sources and the unions in place of the
build. The exit code is 1 when a check failed or could not
run, or when no check ran at all, for cron.

The report answers three questions first: how fresh the data is, whether
every check passed and in words which did not, and how many rows each
canonical table got per day over its last seven days that are over against
the four weeks before. A Shopify day is over at midnight in the source's
zone; a GA4 daily table is a day that is over. The answers name the source
on each line. Every finding follows below, by source in the order of the
configuration, then the unions; the rows per day of a source whose days
are in another zone than the installation's say so.

The built-in checks:

| Check | Reads | Finding |
| --- | --- | --- |
| `dbt` | `run_results.json` and `manifest.json` of the last dbt run of each source and union | Each model, test and unit test, as a sentence with the count of failing rows |
| `freshness` | `sources.json` of `dbt source freshness` | The age of the latest input row against the package's `warn_after` and `error_after` |
| `rows_per_day` | The canonical tables | A day without rows in the last seven days that are over of a table the package marks as a gap in its `deployment.yml` (`events` for GA4, `orders` for Shopify), unless the same weekday had none in the four weeks before |
| `time_zone` | The daily tables with a time column in `deployment.yml` | Rows whose date is not the day of their time in the source's zone: a `fail` for a package built in it (Shopify, every row) or one that fixes it (Sensor.Community), a `warn` with the share of rows for one whose days come with the data (GA4, the property's zone); the last two counted over the days `rows_per_day` reads |

A check is a function of a ClickHouse client and the configuration that
returns findings; a finding names the check, the object, a severity
(`pass`, `skip`, `warn`, `fail`, `error`), what was observed, what was
expected, one sentence for the store owner, and optionally the source it
is about (`source`: a label, or `union-<name>` for a union, such as
`union-commerce`). A package registers a check
through the entry point group `dactopus.checks`; the entry point's name is
the check's name, and a check that raises becomes an `error` finding
without stopping the others:

```toml
[project.entry-points."dactopus.checks"]
reconciliation = "my_checks:reconcile_with_shopify"
```

## Serving the models

`dactopus serve` runs the MCP server of dactopus-ossie-clickhouse over
stdio, for an AI agent to start as a subprocess. It serves the model
of each union of the library that has sources, read as one model. A
union is a set of views in `clickhouse.database` over the canonical
tables of every source of its kind, which `dactopus run` builds after
the sources:

| Union | Its sources | Model file of the library |
| --- | --- | --- |
| `web_analytics` | GA4 | `entities/web_analytics.yaml` |
| `commerce` | Shopify, WooCommerce | `entities/commerce.yaml` |
| `iot` | Sensor.Community | `entities/iot.yaml` |

The models name their tables without a database; the executor reads them
in `clickhouse.database`. The agent sees and runs only what its ClickHouse
user may: the executor leaves out of the model what the user's grants do
not cover. The user of `clickhouse.url` writes the tables, so give the
agent a user of its own in the `serve` section, on the same host:

```yaml
serve:
  user: agent
  password_env: AGENT_CLICKHOUSE_PASSWORD
```

```sql
CREATE USER agent IDENTIFIED BY '...';
GRANT SELECT ON dactopus.* TO agent;
```

One grant is enough. The views of the unions read the tables of the
sources as the user who built them, the user of `clickhouse.url`, so the
agent needs `SELECT` on `clickhouse.database` and none on the databases
of the sources or their inputs. What belongs to the agent's own user
still applies: its grants (the executor leaves out of the model what
they do not cover), its settings profile and its quotas. A row policy on
a source's table applies as the user of `clickhouse.url`, not as the
agent; a row restriction for one agent goes on the union's views, as a
policy `TO` that agent.

The views belong to the user of `clickhouse.url`, and ClickHouse does not
drop a user that owns views. To change that user, run `dactopus run` as the
new one first, which recreates the views under it; then the old one can
be dropped.

Each command reads only the password it connects with: `serve` needs the
variable `serve.password_env` names, `run` and `check` the one
`clickhouse.password_env` names. Without the `serve` section, `serve` runs
as the user of `clickhouse.url`.

An agent starts the command from a directory of its own and with a short
`PATH`, so give absolute paths to the command and the configuration, and
set the password variable in the server's environment. For
[Claude Code](https://docs.claude.com/en/docs/claude-code/mcp):

```bash
claude mcp add dactopus -e AGENT_CLICKHOUSE_PASSWORD=... -- \
  /ABSOLUTE/PATH/TO/dactopus serve --config /ABSOLUTE/PATH/TO/dactopus.yaml
```

For Claude Desktop, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "dactopus": {
      "command": "/ABSOLUTE/PATH/TO/dactopus",
      "args": ["serve", "--config", "/ABSOLUTE/PATH/TO/dactopus.yaml"],
      "env": {"AGENT_CLICKHOUSE_PASSWORD": "..."}
    }
  }
}
```

The tools the agent gets and how to check the server without one:
[MCP setup](https://github.com/Dactopus/dactopus-ossie-clickhouse/blob/main/docs/mcp-setup.md)
of the executor.

## Scope

The runner does not:

- load sources other than GA4 and Shopify. A database (WooCommerce on
  MySQL) is to reach ClickHouse by CDC the client sets up, with the
  runner building its package over the tables it finds;
- schedule runs, send alerts or keep a run history; runs are started by
  hand or by cron;
- link GA4 sessions to Shopify orders; the two models answer apart;
- migrate a client's dbt or Looker;
- ship as a Docker image or an installer.

It was accepted on the public GA4 sample, on a 2026 GA4 export with
synthetic e-commerce events and on a development store with a scripted
history of 440 orders; a store with tens of thousands of orders
and a server that terminates TLS are untested. The decisions behind the
runner and what each was verified on:
[docs/design.md](https://github.com/Dactopus/dactopus/blob/main/docs/design.md).

## Development

Python 3.11 to 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

See [CONTRIBUTING.md](https://github.com/Dactopus/dactopus/blob/main/CONTRIBUTING.md)
and [AGENTS.md](https://github.com/Dactopus/dactopus/blob/main/AGENTS.md).

## License

[Apache 2.0](https://github.com/Dactopus/dactopus/blob/main/LICENSE).
