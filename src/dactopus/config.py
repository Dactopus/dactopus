"""The configuration file of an installation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from zoneinfo import available_timezones

import yaml

DEFAULT_PATH = Path("dactopus.yaml")

_VERSION = re.compile(r"v\d+\.\d+\.\d+")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A label, as the models library takes it in a package's source_id variable.
_LABEL = re.compile(r"[a-z0-9_]{1,64}")
# A package is a directory under sources/ of the library.
_PACKAGE = re.compile(r"[a-z0-9_]+")


class ConfigError(Exception):
    """The configuration file is missing, unreadable or wrong."""


@dataclass(frozen=True)
class ClickHouse:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    # The database of the unions, which serve reads; each source's databases
    # take it as their prefix.
    database: str
    # TLS, from an https:// URL.
    secure: bool = False


@dataclass(frozen=True)
class Serve:
    """The ClickHouse user the AI agent's questions run as, in `dactopus serve`."""

    user: str
    # Read only by `dactopus serve`; None for the other commands.
    password: str | None = field(repr=False)


@dataclass(frozen=True)
class Models:
    version: str | None
    path: Path | None


@dataclass(frozen=True)
class GA4:
    """Where the GA4 loader of a source reads."""

    label: str
    project: str
    dataset: str
    credentials_file: Path | None


@dataclass(frozen=True)
class Shopify:
    """Where the Shopify loader of a source reads."""

    label: str
    shop: str
    client_id_env: str
    client_secret_env: str


@dataclass(frozen=True)
class Source:
    """A store, property or network of the installation, built by a package of the library."""

    label: str
    # A directory under sources/ of the library.
    package: str
    # The canonical tables: <clickhouse.database>_<label>.
    database: str
    # The input: <clickhouse.database>_<label>_raw, or the one the source names.
    input_database: str
    # The zone the source's days are in: passed to a package that takes one,
    # and checked against the days of the source's daily tables.
    timezone: str
    # Whether the source names its zone, rather than taking the installation's.
    own_timezone: bool
    vars: dict[str, Any]
    # None for a package the runner has no loader for: the client's transport
    # (CDC) or the client fills its input.
    loader: GA4 | Shopify | None


@dataclass(frozen=True)
class Config:
    path: Path
    clickhouse: ClickHouse
    # The installation's zone: "today" in serve, and the zone of each source
    # that names none.
    timezone: str
    models: Models
    sources: dict[str, Source]
    serve: Serve | None = None

    @property
    def workdir(self) -> Path:
        """Where dbt output and reports go: .dactopus next to the file."""
        return self.path.parent / ".dactopus"


def load(path: Path, environ: dict[str, str] | None = None, serving: bool = False) -> Config:
    """Read the configuration; ``serving`` for `dactopus serve`.

    Each command reads only the password it connects with: an agent that
    starts `serve` has the password of the serve user, cron that starts
    `run` the password of the clickhouse.url user.
    """
    environ = os.environ if environ is None else environ
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise ConfigError(
            f"No configuration file at {path}. Write a sample with `dactopus init`."
        ) from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from None
    top = _section(raw, "the file", {"clickhouse", "timezone", "models", "sources", "serve"})
    path = path.resolve()
    serve = _serve(top["serve"], environ if serving else None) if "serve" in top else None
    clickhouse = _clickhouse(
        top.get("clickhouse"), None if serving and serve is not None else environ
    )
    zone = _zone(top.get("timezone"), "timezone")
    return Config(
        path=path,
        clickhouse=clickhouse,
        timezone=zone,
        models=_models(top.get("models"), path.parent),
        sources=_sources(top.get("sources"), clickhouse.database, zone, path.parent),
        serve=serve,
    )


def _section(value: Any, where: str, allowed: set[str]) -> dict[str, Any]:
    if value is None:
        raise ConfigError(f"{where}: missing.")
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a mapping of keys to values.")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(
            f"{where}: unknown key {', '.join(unknown)}; allowed: {', '.join(sorted(allowed))}."
        )
    return value


def _text(section: dict[str, Any], key: str, where: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}.{key}: missing; expected a string.")
    return value


def _database(section: dict[str, Any], key: str, where: str) -> str:
    value = _text(section, key, where)
    if not _IDENTIFIER.fullmatch(value):
        raise ConfigError(
            f"{where}.{key}: {value!r} is not a plain database name "
            "(letters, digits and underscores)."
        )
    return value


def _password(section: dict[str, Any], where: str, environ: dict[str, str] | None) -> str | None:
    """The password from the variable password_env names; None when environ is None."""
    if "password_env" not in section:
        return ""
    name = _text(section, "password_env", where)
    if environ is None:
        return None
    if name not in environ:
        raise ConfigError(f"{where}.password_env: the environment variable {name} is not set.")
    return environ[name]


def _clickhouse(value: Any, environ: dict[str, str] | None) -> ClickHouse:
    """The connection; with environ None the password is left unread, as ""."""
    section = _section(value, "clickhouse", {"url", "password_env", "database"})
    url = urlsplit(_text(section, "url", "clickhouse"))
    if url.scheme not in ("http", "https"):
        raise ConfigError(
            f"clickhouse.url: expected http:// or https://, got {url.scheme or 'no'} scheme."
        )
    if url.password is not None:
        raise ConfigError(
            "clickhouse.url: remove the password from the URL and name the "
            "environment variable that holds it in clickhouse.password_env."
        )
    if url.path not in ("", "/") or url.query or url.fragment:
        raise ConfigError(
            "clickhouse.url: give only the user, host and port; the databases "
            "come from clickhouse.database."
        )
    if not url.hostname:
        raise ConfigError("clickhouse.url: no host.")
    try:
        port = url.port
    except ValueError:
        raise ConfigError("clickhouse.url: the port is not a number from 1 to 65535.") from None
    password = _password(section, "clickhouse", environ) or ""
    return ClickHouse(
        host=url.hostname,
        # The default ports of the ClickHouse HTTP interface, plain and TLS.
        port=port or (8443 if url.scheme == "https" else 8123),
        user=unquote(url.username) if url.username else "default",
        password=password,
        database=_database(section, "database", "clickhouse"),
        secure=url.scheme == "https",
    )


def _serve(value: Any, environ: dict[str, str] | None) -> Serve:
    section = _section(value, "serve", {"user", "password_env"})
    return Serve(
        user=_text(section, "user", "serve"), password=_password(section, "serve", environ)
    )


def _zone(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: missing; expected a time zone like America/New_York.")
    # Not ZoneInfo(value): on a case-insensitive file system it opens
    # america/new_york, a name Shopify never answers and Linux refuses.
    if value not in available_timezones():
        raise ConfigError(f"{where}: {value!r} is not a time zone; give one like America/New_York.")
    return value


def _models(value: Any, base: Path) -> Models:
    section = _section(value, "models", {"version", "path"})
    if ("version" in section) == ("path" in section):
        raise ConfigError("models: give either version (a release tag) or path, not both.")
    if "version" in section:
        version = section["version"]
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ConfigError(f"models.version: {version!r} is not a release tag like v0.5.3.")
        return Models(version=version, path=None)
    path = (base / Path(_text(section, "path", "models")).expanduser()).resolve()
    if not (path / "sources").is_dir():
        raise ConfigError(f"models.path: {path} is not a checkout of dactopus-data-models.")
    return Models(version=None, path=path)


# The settings every source takes, and those of the packages the runner
# loads: a loader is a property of the runner, not of the configuration.
_COMMON = {"package", "timezone", "input_database", "vars"}
_LOADERS = {
    "ga4": {"project", "dataset", "credentials_file"},
    "shopify": {"shop", "client_id_env", "client_secret_env"},
}


def _sources(value: Any, prefix: str, zone: str, base: Path) -> dict[str, Source]:
    if value is None:
        raise ConfigError("sources: missing; name at least one source.")
    if not isinstance(value, dict) or not value:
        raise ConfigError("sources: expected a mapping of labels to sources; name at least one.")
    sources: dict[str, Source] = {}
    for label, section in value.items():
        if not isinstance(label, str) or not _LABEL.fullmatch(label):
            raise ConfigError(
                f"sources: the label {label!r} is not 1 to 64 of a-z, 0-9 and _; agents "
                "and BI see it, so give the name people call the store by."
            )
        sources[label] = _source(label, section, prefix, zone, base)
    _check_databases(sources, prefix)
    return sources


def _source(label: str, value: Any, prefix: str, zone: str, base: Path) -> Source:
    where = f"sources.{label}"
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a mapping of keys to values.")
    package = _text(value, "package", where)
    if not _PACKAGE.fullmatch(package):
        raise ConfigError(
            f"{where}.package: {package!r} is not a package name; give a directory "
            "under sources/ of the models library, such as shopify."
        )
    section = _section(value, where, _COMMON | _LOADERS.get(package, set()))
    variables = section.get("vars", {})
    if not isinstance(variables, dict):
        raise ConfigError(f"{where}.vars: expected a mapping of dbt variables.")
    own = "timezone" in section
    loader: GA4 | Shopify | None = None
    if package == "ga4":
        loader = _ga4(label, section, base)
    elif package == "shopify":
        loader = _shopify(label, section)
    return Source(
        label=label,
        package=package,
        database=f"{prefix}_{label}",
        input_database=(
            _database(section, "input_database", where)
            if "input_database" in section
            else f"{prefix}_{label}_raw"
        ),
        timezone=_zone(section["timezone"], f"{where}.timezone") if own else zone,
        own_timezone=own,
        vars=dict(variables),
        loader=loader,
    )


def _check_databases(sources: dict[str, Source], unions: str) -> None:
    """Refuse a database that two roles share.

    The canonical tables of a source have a database of their own, so its
    tables are not the input of another source or the views of a union.
    Two sources of one package read the same table names, so they do not
    share an input database; sources of different packages may.
    """
    canonical = {source.database: label for label, source in sources.items()}
    readers: dict[tuple[str, str], str] = {}
    for label, source in sources.items():
        where = f"sources.{label}"
        database = source.input_database
        if database == unions:
            raise ConfigError(
                f"{where}: its input database {database} is clickhouse.database, which "
                "holds the unions; name another in input_database."
            )
        if database in canonical:
            raise ConfigError(
                f"{where}: its input database {database} holds the canonical tables of "
                f"{canonical[database]}; name another in input_database, or give one of "
                "them another label."
            )
        shared = readers.setdefault((source.package, database), label)
        if shared != label:
            raise ConfigError(
                f"{where}: {shared} reads the same input database {database} with the "
                f"same package, {source.package}; each would take the other's rows as "
                "its own. Name another in input_database."
            )


def _ga4(label: str, section: dict[str, Any], base: Path) -> GA4:
    where = f"sources.{label}"
    dataset = _text(section, "dataset", where)
    if dataset.count(".") != 1:
        raise ConfigError(f"{where}.dataset: expected <project>.<dataset>, got {dataset!r}.")
    credentials = None
    if "credentials_file" in section:
        credentials = base / Path(_text(section, "credentials_file", where)).expanduser()
    return GA4(
        label=label,
        project=_text(section, "project", where),
        dataset=dataset,
        credentials_file=credentials,
    )


def _shopify(label: str, section: dict[str, Any]) -> Shopify:
    where = f"sources.{label}"
    shop = _text(section, "shop", where)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", shop):
        raise ConfigError(
            f"{where}.shop: give the store's subdomain, as in <shop>.myshopify.com; got {shop!r}."
        )
    return Shopify(
        label=label,
        shop=shop,
        client_id_env=section.get("client_id_env", "SHOPIFY_CLIENT_ID"),
        client_secret_env=section.get("client_secret_env", "SHOPIFY_CLIENT_SECRET"),
    )


SAMPLE = """\
# Configuration of a dactopus installation. Secrets are never written here:
# name the environment variables or files that hold them.

clickhouse:
  # User, host and port of the ClickHouse HTTP interface; https:// for TLS
  # (ClickHouse Cloud: https://default@<host>:8443).
  url: http://default@127.0.0.1:8123
  # The environment variable with the user's password; leave it out for none.
  # password_env: CLICKHOUSE_PASSWORD
  # The databases of the installation: this one for the unions of the
  # sources, which `dactopus serve` reads; <this>_<label> for the canonical
  # tables of each source, and <this>_<label>_raw for its input.
  database: dactopus

# The installation's time zone: "today" in an AI agent's questions, and the
# zone of the days of each source that names none.
timezone: UTC

models:
  # A release of dactopus-data-models, downloaded once into ~/.cache/dactopus.
  # When a release changes a model's columns (its changelog says so), run
  # `dactopus run --full-refresh` once after changing it.
  version: v0.9.0
  # Or a checkout of the library, for development:
  # path: ../dactopus-data-models

# The ClickHouse user an AI agent's questions run as in `dactopus serve`, on
# the host of clickhouse.url: give it read grants on the databases of the
# installation only. Without this section, serve runs as the user of
# clickhouse.url.
# serve:
#   user: agent
#   password_env: AGENT_CLICKHOUSE_PASSWORD

# The sources of the installation, each under its own label: 1 to 64 of a-z,
# 0-9 and _. Agents and BI see the label; give the name people call the
# store, site or network by. `package` is a package of the models library.
# Every source takes:
#   timezone: the zone its days are in, when not the installation's. A store
#     package builds its days in it: Settings > General in the Shopify admin,
#     and after changing it run `dactopus run --full-refresh <label>` once.
#     For GA4, the property's zone (Admin > Property details); for
#     Sensor.Community, UTC.
#   input_database: the database of its input, when not <database>_<label>_raw;
#     for a package the runner does not load, the one the client's transport
#     (CDC) writes.
#   vars: dbt variables of the package, passed as they are.
sources:
  web:
    package: ga4
    # The Google Cloud project the read sessions run in; reading is billed to it.
    project: my-project
    # The GA4 export: <project>.<dataset> holding the events_YYYYMMDD tables.
    dataset: bigquery-public-data.ga4_obfuscated_sample_ecommerce
    # A service account key; without it, Application Default Credentials
    # (gcloud auth application-default login).
    # credentials_file: ~/keys/ga4-reader.json
    vars:
      ga4_lookback_days: 3

  shop:
    package: shopify
    # The store, as in <shop>.myshopify.com.
    shop: my-store
    # The environment variables with the app's client id and secret.
    client_id_env: SHOPIFY_CLIENT_ID
    client_secret_env: SHOPIFY_CLIENT_SECRET

  # A WooCommerce store, its tables replicated into ClickHouse by CDC (PeerDB,
  # ClickPipes); the runner builds, checks and serves it, and loads nothing.
  # woo_eu:
  #   package: woocommerce
  #   timezone: Europe/Berlin
  #   input_database: woocommerce_raw
  #   vars:
  #     woocommerce_currency: EUR
"""


def write_sample(path: Path) -> None:
    if path.exists():
        raise ConfigError(f"{path} exists; remove it or pass another path with --config.")
    path.write_text(SAMPLE)
