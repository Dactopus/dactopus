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
a configuration section and nothing else; 0.1.0 accepted `ga4` and
`shopify` only, and every package since 2 November 2026 (below). A gap between the library and the runner is fixed in the
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

## Python 3.11 to 3.13 (1 November 2026)

dbt-core 1.11 does not start on Python 3.14: it caps mashumaro below
3.15, and `dbt --version` fails in mashumaro 3.14 with
`UnserializableField`. dbt-core 1.12.6 starts on 3.14 with mashumaro
3.17 (3.15 and 3.16 fail) and built the same tables as 1.11 from the
same input: 4.3 million GA4 events and 489 Shopify orders, every table
equal in rows and in a hash of all columns, the same report. It was
not taken: 1.12 and 1.13.0b1 require `dbt-core-experimental-parser`, a
Rust binary in alpha, published on PyPI as a source package whose build
downloads a wheel from GitHub. An install then needs github.com, not
only PyPI ([dbt#15670](https://github.com/dbt-labs/dbt/issues/15670)),
and a certificate store in the Python that builds it: python.org's 3.14
without Install Certificates failed with `CERTIFICATE_VERIFY_FAILED`
([dbt#15809](https://github.com/dbt-labs/dbt/issues/15809)). The wheels
cover macOS, Linux with glibc 2.28 or later and Windows on x86_64, no
Alpine ([dbt#15607](https://github.com/dbt-labs/dbt/issues/15607)). An
install that works the first time outweighs 3.14, which uv replaces by
a Python it downloads. Revisit when the parser ships wheels on PyPI or
becomes optional.

`requires-python = ">=3.11,<3.14"` alone does not keep a uv tool off
3.14: uv 0.8.17 and 0.13.6 ignore the upper bound and installed 0.2.1
on 3.14, both with `--python 3.14` and on a machine whose only Python
was 3.14; pip refuses it. So the README passes `--python 3.13`, which
uv downloads when the machine has none, and every command refuses 3.14
with the command that reinstalls. Verified on macOS arm64.

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
question are the store's. It became `timezone` and a zone per source on
2 November 2026 (below).

Secrets never enter the file: it names the environment variables that
hold them (`password_env`, `client_id_env`, `client_secret_env`) or a
Google key file. One input database serves every package and one
canonical database holds the built tables, and they must differ: some
canonical tables carry the names of input tables (`events`, `orders`).
Each source has databases of its own since 2 November 2026 (below).

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
grants on the canonical database are enough. Since the unions the user
also needs the databases of the sources (3 November 2026, below).

## Checks are the one plugin interface

Reconciliation with a source's own reports goes to the source's API and
belongs to the paid layer, so it must plug in from outside; without an
interface it would have to be built into the open core. The prototype
ships one implementation, the built-in checks, and fixes the interface:
a check is a named object that takes a ClickHouse connection and the
configuration and returns findings of one shape (check, object,
severity, observed, expected, a sentence for the owner), registered
through the entry point group `dactopus.checks`. Everything else in the
runner is a direct call. On 2 November 2026 a finding got an optional
`source`, the source it is about, so the report can group by source; a
plugin that leaves it out keeps working.

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

## Sources under labels, with databases of their own (2 November 2026)

The models library v0.9.0 has four packages, three areas (commerce, web
analytics, IoT) and unions of several sources of one area. The runner
0.2 served one store: `packages` took `ga4` and `shopify` only, with
one input database, one canonical database and one zone. A brand with
two stores needed two runners and two MCP servers, and the agent could
not answer for both stores at once.

`sources` maps labels to sources: the key is the label, which the
package takes as its `source_id`, and `package` is its type. The name
`sources` matches the library's documents; `instances` reads as
technical, and `stores` is wrong for GA4 and sensor networks. The
databases follow from `clickhouse.database` as a prefix: the unions in
it, the canonical tables of a source in `<prefix>_<label>`, its input in
`<prefix>_<label>_raw`. The file stays short and two sources cannot
collide. A source may name its input database: a CDC transport's
database was named when the client set it up, and an upgrade from 0.2
keeps the GA4 input it loaded (a full load of the public sample takes
6 minutes, a brand's export hours). The canonical database has no
override, since only the client's BI sees its name.

Every package of the library is accepted. The runner loads GA4 and
Shopify; the input of the others comes from the client's transport or
from the client, and the runner leaves that database alone.

## Always through a union (2 November 2026)

`serve` reads the views of the unions, with one store too. One path in
the code; and an installation with a store and a GA4 property already
has two sources in two databases, which the executor could not read as
one model without a union. The cost is one more database, one more
`dbt build` per run (the views and the union's tests, such as the
uniqueness of an order key across stores) and grants on more databases.

A union is a dbt project of the library, run after the sources. The
runner matches a source to the union whose `deployment.yml` names the
model of the source's package, and passes the databases of its sources
in the order of the configuration. The views are recreated on every
run, so a union sees the columns of a new release and a new source. A
source whose model no union serves is refused before anything runs, so
it cannot drop out of `serve` unnoticed.

`clickhouse.database` held the canonical tables in 0.2, and dbt would
replace those with views of the same name without a word. `run` refuses
a `clickhouse.database` that holds any table before it loads anything:
the runner does not know the names of a union's views.

Verified on ClickHouse 26.9.27.3 with dbt-core 1.11.16 and
dbt-clickhouse 1.10.3 (3 November 2026): five sources (a Shopify
development store, two WooCommerce and two Sensor.Community fixtures)
and two unions built in one run; the library's `check_union.py`
answered 25 of 25 questions over three stores in two currencies and 5
of 5 over two sensor networks.

## The installation's zone and each source's (2 November 2026)

`timezone` at the top of the file replaces `store.timezone`, since
"store" misleads with several stores. It is "today" in `serve`, where
the executor takes one zone for every model, and the zone of every
source that names none. A source's own `timezone` goes to its package's
build and to the checks. The IoT area is in UTC: its rows keep the days
of UTC, and "today" is the installation's date.

## A dbt step that could not run does not stop the others (2 November 2026)

dbt exits with 2 when it cannot run a project, as for a WooCommerce
source without `woocommerce_currency`; the runner stopped there, with
no report and the next sources unbuilt. It now passes dbt's output on,
keeps the step and dbt's error next to the source's output, and goes on;
the checks show the error with the package's message, and the run exits
with 1. The zone check before loading still stops the whole run.

## The serve user and the views of the unions (3 November 2026)

A view without `SQL SECURITY` runs as the user who asks
(`default_normal_view_sql_security = INVOKER`; ClickHouse 26.9.27.3, 3
November 2026). A `serve` user with `SELECT` on the unions' database
alone gets "Not enough privileges" from a union's view, so it needs the
database of each source as well. The sample and the README grant them
database by database: `GRANT SELECT ON dactopus*.*` would also cover the
input databases, which hold the store's raw rows, and a name in
backticks is no pattern (``GRANT SELECT ON `dactopus*`.*`` grants a
database named `dactopus*`). Views with `SQL SECURITY DEFINER` would
need the unions' database alone: on the same server a user with `SELECT`
on it read such a view and not the source's table under it. That is a
change to the unions of the library.
