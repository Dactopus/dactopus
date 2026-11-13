# dactopus

[![CI](https://github.com/Dactopus/dactopus/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Dactopus/dactopus/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/Dactopus/dactopus)](https://github.com/Dactopus/dactopus/blob/main/LICENSE)

The runner of Dactopus: the process a client installs next to their
[ClickHouse](https://clickhouse.com/docs). It loads sources into that
ClickHouse, builds the canonical e-commerce tables, checks them, and
serves their [Apache Ossie](https://github.com/apache/ossie) models to AI
agents over [MCP](https://modelcontextprotocol.io).

A prototype. While the major version is 0, a minor release may change
the command line and the configuration file: what changed is in the
[changelog](https://github.com/Dactopus/dactopus/blob/main/CHANGELOG.md),
what an installation does about it in
[UPGRADING.md](https://github.com/Dactopus/dactopus/blob/main/UPGRADING.md).

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

`uv tool upgrade dactopus` updates it; the steps a release asks for
are in
[UPGRADING.md](https://github.com/Dactopus/dactopus/blob/main/UPGRADING.md).

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
creates, and `input.sql` creates a table only if it is missing, so a
column a later release adds is not in a table an earlier one created.
For a column a release adds to the Shopify input (the refunds'
`processed_at` of v0.7.2, the date a refund counts on; the orders'
billing address of v0.13.1), the load stops before reading anything and
names the table, the columns, the library's UPGRADING.md, which says how
to add them, and the run that fills them in the rows loaded before.

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

`dactopus run --reload LABEL [LABEL ...]` loads the named Shopify
sources from the first order, as their first load did, then builds them
as `--full-refresh` does. Run it once when a release of the models
library adds a column to the Shopify input: the orders loaded before
have the column empty until Shopify delivers them again. The reload
appends what the store returns and deletes nothing, so the orders the
store no longer returns (older than 60 days without `read_all_orders`)
stay as they were, without the new column. It reads the whole store
again, which takes as long as the first load did. A reload that stops
starts over with `--reload`: a run without it would start after the
latest change already reloaded. GA4 is not reloaded: every run rereads
the days of `ga4_lookback_days`, and a reread of the whole export is
paid for in BigQuery.

## Models library and runner

A release of the runner reads the releases of the models library made
for the dactopus-ossie-clickhouse it installs: the two agree on the
version of the Ossie schema the models are written in.

| Runner | Models library | dactopus-ossie-clickhouse |
|---|---|---|
| 0.6 | v0.13.0 or later; v0.13.1 or later with a Shopify source | 0.17 |
| 0.5 | v0.13.0 or later | 0.16 |
| 0.4 | v0.12.2 | 0.14 |

Before its first step, every command asks the executor whether it reads
the models of the library the configuration names. When it does not,
the command stops with the executor's reason, and nothing is loaded or
built. A library release newer than the runner reads needs `uv tool
upgrade dactopus`; a release older than the runner reads needs a later
`models.version`. A `models.version` earlier than v0.13.0 is refused
by its number, before it is downloaded.

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

Setup, tests and releases:
[CONTRIBUTING.md](https://github.com/Dactopus/dactopus/blob/main/CONTRIBUTING.md);
the rules the code follows:
[AGENTS.md](https://github.com/Dactopus/dactopus/blob/main/AGENTS.md).

## License

[Apache 2.0](https://github.com/Dactopus/dactopus/blob/main/LICENSE).
