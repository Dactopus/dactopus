# Design decisions

Decisions with the reasons behind them, recorded so they are not
relitigated without new evidence. The rules themselves are in
[AGENTS.md](../AGENTS.md); the reasoning is here, with the date of a
decision where one was recorded.

## A thin runner over steps that already existed (5 October 2026)

Before the runner, the path from a source to an agent's answer was the
Quick start of dactopus-data-models: an export script per source,
`clickhouse local | clickhouse client`, `dbt build`, then `validate` and
`serve` of the executor. The runner is that path as one tool with one
configuration file. It adds no concept of its own: a new command, field
or layer needs a decision first. Separate load and build commands appear
when someone needs them apart; until then they are flags of `run`.

## The library owns the contract, the runner delivers to it

A package of dactopus-data-models is known to the runner by two files:
`sources/<package>/input.sql`, the tables the package reads, and
`sources/<package>/deployment.yml`, what an installation fills in (the
dbt variables that take the input database and the time zone, the Ossie
model the tables serve, the tables counted per day with their date and
time columns). No variable, table, column or model file name of a
package appears in the runner's code; the loaders are the exception,
since they write the tables `input.sql` defines.

The declaration is a file of its own rather than dbt `meta` because its
values are needed before the first build and in `serve`, where dbt does
not run; `meta` would be readable only from `manifest.json`, which
exists after a run. A consequence: a package without a loader
(WooCommerce, whose tables reach ClickHouse by CDC) will cost the runner
a configuration section and nothing else; 0.1.0 accepts `ga4` and
`shopify` only. A gap between the library and the runner is fixed in the
library, never patched around here.

## The executor knows nothing of Dactopus

The runner is a consumer of dactopus-ossie-clickhouse like any other.
What the prototype needed from it was proposed and shipped there as
features for every Ossie user: several model files read as one model
(0.8.0) and TLS decided by the URL scheme on every port (0.9.5, below).
The runner lists the files; the executor combines them and reads their
tables in the database of the connection URL, so no database name is
written into a model.

## dbt v1 (5 October 2026)

`dbt-core` 1.11 with `dbt-clickhouse`. dbt v2's ClickHouse adapter is in
beta: it reads LZ4-compressed responses only and `source freshness` is
broken. A package is a dbt project, so changing the transformation
engine is changing one command, not rewriting packages. Revisit when
someone other than the maintainer installs the runner: a single binary
without Python weighs more then than the adapter's maturity. SQLMesh was
ruled out: it needs a state database of its own, and ClickHouse is not
fit for that role.

## The word runner (8 October 2026)

*Agent* is taken by the AI that talks to the MCP server. *Runner* over
node, worker or runtime: it names the job, counts in units ("one runner
per store") and lives without a control plane; the runner and executor
pair is GitLab's. The repository named after the product goes to the
part the client installs, as with dlt, Airbyte and PostHog, and its
README is the map of the product.

## One configuration file, owned by the runner (21 and 22 October 2026)

`dactopus.yaml`, one per installation, stays with the client; the
repository holds only the sample `init` writes. A fact about the
installation (the store's time zone, the databases, the connection) is
written once, so two copies have nowhere to diverge. The other parts of
the product do not know the file and take their own parameters: a
package its dbt variables, the executor its flags and URL. The runner
spreads the values, which keeps the library an ordinary dbt project and
the executor free of Dactopus. Without the runner a user sets each value
in each part, and the library's README says so. The schema of the file
moves to a package of its own when a second reader appears, such as a
control plane; the library and the executor will not need that package.

The first value handled this way is `store.timezone`, passed to the
Shopify package as `shopify_timezone` and to the executor as
`--time-zone`, so the days of the tables and "today" in an agent's
question are the store's.

Secrets never enter the file: it names the environment variables that
hold them (`password_env`, `client_id_env`, `client_secret_env`) or a
Google key file. One input database serves every package and one
canonical database holds the built tables, and they must differ: some
canonical tables carry the names of input tables (`events`, `orders`).

## The runner writes the dbt profile (21 October 2026)

How to connect to ClickHouse is the installation's decision, not the
library's: TLS or ClickHouse Cloud should not wait for a release of the
models. A package only names the profile (`profile: dactopus`); the
runner writes it into `.dactopus/` next to the configuration, without
the password, which dbt reads from the environment. The library removed
its own `profiles.yml`
([dactopus-data-models#25](https://github.com/Dactopus/dactopus-data-models/issues/25)).

## TLS by URL scheme (21 October 2026)

`https://` turns TLS on for the loads and for dbt, with 8443 as the
default port; there is no separate field. Verified: the flag reaches
both clients, and `serve` over `https://` on a port other than 443 or
8443 works since executor 0.9.5
([dactopus-ossie-clickhouse#60](https://github.com/Dactopus/dactopus-ossie-clickhouse/issues/60);
before it, clickhouse-connect took TLS from the port alone). Not
verified: a connection to a server that terminates TLS, which the first
installation on ClickHouse Cloud will test.

## A ClickHouse user of its own for serve (22 October 2026)

The agent asks as a user with `SELECT` on the canonical database, named
in the `serve` section. The password with write rights never reaches the
settings of an AI client, and ClickHouse grants, settings profiles and
quotas apply to the agent apart from the loads. Each command reads only
the password it connects with. Verified on ClickHouse 26.9.5: read
grants on the canonical database are enough.

## Checks are the one plugin interface

Reconciliation with a source's own reports goes to the source's API and
belongs to the paid layer, so it must plug in from outside; without an
interface it would have to be built into the open core. The prototype
ships one implementation, the built-in checks, and fixes the interface:
a check is a named object that takes a ClickHouse connection and the
configuration and returns findings of one shape (check, object,
severity, observed, expected, a sentence for the owner), registered
through the entry point group `dactopus.checks`. Everything else in the
runner is a direct call.

## Shopify in the prototype, with a client of our own (21 October 2026)

The first conversations with brands need orders, not sessions alone, so
Shopify joined GA4 in the prototype. The loader is a GraphQL client of
this repository. dlt's Shopify source was ruled out: it reads the legacy
REST Admin API 2023-10, a dependency on an API that is going away. The
cost is on the client's side: an app with `read_orders` and
`read_products` installed on the store, and `read_all_orders` for orders
older than 60 days, which an app created in the store's own organization
gets without a request; a partner's app asks Shopify for it. Bulk
operations do not deliver refund lines, so the full load pages like the
incremental one.

## GA4 without Parquet on disk

A load reads a daily table of the BigQuery export through the Storage
Read API as Arrow and inserts it as is, with no Parquet on disk and no
ClickHouse binaries on the machine. The full load of the public sample
(4.3 million events, 92 days) took 33 minutes at first and 6 to 7 after
two changes, no longer than the library's recipe on the same machine:
LZ4 compression on the reads, and a Storage Read client opened per day,
since one client reused over many days read them slower and slower.
The runner decodes the Arrow stream itself, since the client library's
`to_arrow` is deprecated. The sample serves one stream per day; whether
that is enough for the days of a large brand is untested. The intraday
tables are not loaded: they carry no attribution. Whether to load them
is open.

## What 0.1.0 was accepted on (27 and 28 October 2026)

GA4: the public sample, whose input equalled the library's recipe row
for row, and a 2026 export of a property with synthetic e-commerce
events, loaded into databases of its own; 26 checks passed, and the
agent's answers through `serve` about sessions, purchases, sources and
devices equalled queries written by hand.

Shopify: a development store with a scripted history of 440 orders, 52
refunds and 6 cancellations, created from a registry of expected numbers
written before the script. The input the runner wrote equalled the
library's recipe row for row over five tables; 59 checks passed with one
predicted warning; the agent answered 15 owner questions with no number
wrong. A second wave (5 orders, 4 refunds, a cancellation, product edits
and a product deletion) came through the incremental load and matched
the registry, and a rerun over the unchanged store changed no table.

Not verified: a store with tens of thousands of orders (25 per page,
under the GraphQL cost limits), an export with large daily tables, a
server that terminates TLS.
