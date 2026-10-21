# dactopus

[![CI](https://github.com/Dactopus/dactopus/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Dactopus/dactopus/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/Dactopus/dactopus)](LICENSE)

The runner of Dactopus: the process a client installs next to their
[ClickHouse](https://clickhouse.com/docs). It loads sources into that
ClickHouse, builds the canonical e-commerce tables, checks them, and
serves their [Apache Ossie](https://github.com/apache/ossie) models to AI
agents over [MCP](https://modelcontextprotocol.io).

Under construction: nothing is released. `init` and `run` work; checks,
the report and `serve` do not yet.

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
environment. The profile and dbt's output go to `.dactopus/` next to the
configuration file.

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
