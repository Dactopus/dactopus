# dactopus

[![CI](https://github.com/Dactopus/dactopus/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Dactopus/dactopus/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/Dactopus/dactopus)](LICENSE)

The runner of Dactopus: the process a client installs next to their
[ClickHouse](https://clickhouse.com/docs). It loads sources into that
ClickHouse, builds the canonical e-commerce tables, checks them, and
serves their [Apache Ossie](https://github.com/apache/ossie) models to AI
agents over [MCP](https://modelcontextprotocol.io).

Under construction: nothing is released. Every command works.

## How the parts fit

| Part | Role |
| --- | --- |
| dactopus (this repository) | Loads GA4 and Shopify, runs the packages, checks, serves |
| [dactopus-data-models](https://github.com/Dactopus/dactopus-data-models) | Canonical entities and source packages (dbt projects) |
| [dactopus-ossie-clickhouse](https://github.com/Dactopus/dactopus-ossie-clickhouse) | Executes Ossie models on ClickHouse; the MCP server |

The runner owns delivery of data to a package's input tables and running
the package. The library owns the input contract and the transformations.
The executor knows nothing of Dactopus.

## Commands

| Command | What it does |
| --- | --- |
| `dactopus init` | Writes a commented sample configuration |
| `dactopus run` | Loads the sources, builds the packages, runs the checks, writes the report |
| `dactopus check` | Runs the checks and writes the report |
| `dactopus serve` | Serves the models over MCP |

One configuration file describes an installation: the ClickHouse
connection, the databases for raw input and canonical tables, the version
of the models library, and the packages to run. `dactopus init` writes it
with a comment on every field. Secrets stay out of it: the file names the
environment variables that hold them.

`dactopus run` loads each source into its package's input tables, then
runs `dbt build` of each package. A load reads only what changed:

| Source | Read from | Next load |
| --- | --- | --- |
| GA4 | The daily `events_YYYYMMDD` tables of the BigQuery export, with the Storage Read API | The days from the latest loaded one minus `ga4_lookback_days`, which replace what was loaded for them |
| Shopify | Orders with their lines, shipping and refunds, 25 per page, from the GraphQL Admin API | The orders updated from an hour before the latest loaded change, appended |

GA4 reads need Google credentials: a service account key named in the
file, or Application Default Credentials (`gcloud auth
application-default login`). The read sessions run in the configured
project, which pays for them. Shopify needs an app with `read_orders`,
installed on the store, and its client id and secret; without
`read_all_orders` Shopify returns the last 60 days of orders only.

The runner writes the dbt profile the packages name (`profile: dactopus`)
from the configuration, without the password, which dbt reads from the
environment. The profile, dbt's output and the report go to `.dactopus/`
next to the configuration file.

## Checks and the report

After the build, `dactopus run` measures the freshness of the input
(`dbt source freshness`, with the thresholds the package sets), runs the
checks and writes the report, to the terminal and to `.dactopus/report.md`.
`dactopus check` does the same over the tables as they are, with `dbt test`
in place of the build. The exit code is 1 when a check failed or could not
run, or when no check ran at all, for cron.

The report answers three questions first: how fresh the data is, whether
every check passed and in words which did not, and how many rows each
canonical table got per day over its last seven days that are over against
the four weeks before. A Shopify day is over at midnight in the shop's time
zone (`shopify_timezone`); a GA4 daily table is a day that is over. Every
finding follows below.

The built-in checks:

| Check | Reads | Finding |
| --- | --- | --- |
| `dbt` | `run_results.json` and `manifest.json` of the last dbt run | Each model, test and unit test of the package, as a sentence with the count of failing rows |
| `freshness` | `sources.json` of `dbt source freshness` | The age of the latest input row against the package's `warn_after` and `error_after` |
| `rows_per_day` | The canonical tables | A day without rows in the last seven days that are over of `events` (GA4) or `orders` (Shopify), unless the same weekday had none in the four weeks before |

A check is a function of a ClickHouse client and the configuration that
returns findings; a finding names the check, the object, a severity
(`pass`, `skip`, `warn`, `fail`, `error`), what was observed, what was
expected, and one sentence for the store owner. A package registers a check
through the entry point group `dactopus.checks`; the entry point's name is
the check's name, and a check that raises becomes an `error` finding
without stopping the others:

```toml
[project.entry-points."dactopus.checks"]
reconciliation = "my_checks:reconcile_with_shopify"
```

## Serving the models

`dactopus serve` runs the MCP server of dactopus-ossie-clickhouse over
stdio, for an AI agent to start as a subprocess. It serves the models of
the domains the configured packages cover, read as one model:

| Package | Model file of the library |
| --- | --- |
| GA4 | `entities/web_analytics.yaml` |
| Shopify | `entities/commerce.yaml` |

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

Read grants on the canonical database are enough. Each command reads only
the password it connects with: `serve` needs the variable
`serve.password_env` names, `run` and `check` the one
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

## Development

Python 3.11 or later and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).

## License

[Apache 2.0](LICENSE).
