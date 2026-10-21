"""The configuration file of an installation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

DEFAULT_PATH = Path("dactopus.yaml")

# The dbt variables that name the input database of each package. The
# runner sets them from clickhouse.input_database.
INPUT_DATABASE_VARS = {
    "ga4": "ga4_input_database",
    "shopify": "shopify_input_database",
}

_VERSION = re.compile(r"v\d+\.\d+\.\d+")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ConfigError(Exception):
    """The configuration file is missing, unreadable or wrong."""


@dataclass(frozen=True)
class ClickHouse:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    input_database: str
    database: str
    # TLS, from an https:// URL.
    secure: bool = False


@dataclass(frozen=True)
class Models:
    version: str | None
    path: Path | None


@dataclass(frozen=True)
class GA4:
    project: str
    dataset: str
    credentials_file: Path | None
    vars: dict[str, Any]


@dataclass(frozen=True)
class Shopify:
    shop: str
    client_id_env: str
    client_secret_env: str
    vars: dict[str, Any]


@dataclass(frozen=True)
class Config:
    path: Path
    clickhouse: ClickHouse
    models: Models
    ga4: GA4 | None
    shopify: Shopify | None

    @property
    def workdir(self) -> Path:
        """Where dbt output and reports go: .dactopus next to the file."""
        return self.path.parent / ".dactopus"

    def packages(self) -> dict[str, GA4 | Shopify]:
        found = {"ga4": self.ga4, "shopify": self.shopify}
        return {name: package for name, package in found.items() if package is not None}


def load(path: Path, environ: dict[str, str] | None = None) -> Config:
    environ = os.environ if environ is None else environ
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise ConfigError(
            f"No configuration file at {path}. Write a sample with `dactopus init`."
        ) from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from None
    top = _section(raw, "the file", {"clickhouse", "models", "packages"})
    path = path.resolve()
    packages = _section(top.get("packages"), "packages", set(INPUT_DATABASE_VARS))
    if not packages:
        raise ConfigError("packages: name at least one package, ga4 or shopify.")
    return Config(
        path=path,
        clickhouse=_clickhouse(top.get("clickhouse"), environ),
        models=_models(top.get("models"), path.parent),
        ga4=_ga4(packages["ga4"], path.parent) if "ga4" in packages else None,
        shopify=_shopify(packages["shopify"]) if "shopify" in packages else None,
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


def _database(section: dict[str, Any], key: str) -> str:
    value = _text(section, key, "clickhouse")
    if not _IDENTIFIER.fullmatch(value):
        raise ConfigError(
            f"clickhouse.{key}: {value!r} is not a plain database name "
            "(letters, digits and underscores)."
        )
    return value


def _clickhouse(value: Any, environ: dict[str, str]) -> ClickHouse:
    section = _section(value, "clickhouse", {"url", "password_env", "input_database", "database"})
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
            "are clickhouse.input_database and clickhouse.database."
        )
    if not url.hostname:
        raise ConfigError("clickhouse.url: no host.")
    try:
        port = url.port
    except ValueError:
        raise ConfigError("clickhouse.url: the port is not a number from 1 to 65535.") from None
    password = ""
    if "password_env" in section:
        name = _text(section, "password_env", "clickhouse")
        if name not in environ:
            raise ConfigError(
                f"clickhouse.password_env: the environment variable {name} is not set."
            )
        password = environ[name]
    input_database = _database(section, "input_database")
    database = _database(section, "database")
    if input_database == database:
        # Packages name some canonical tables as their input (events, orders).
        raise ConfigError(
            "clickhouse: input_database and database must differ; the packages "
            "write canonical tables named like their input tables."
        )
    return ClickHouse(
        host=url.hostname,
        # The default ports of the ClickHouse HTTP interface, plain and TLS.
        port=port or (8443 if url.scheme == "https" else 8123),
        user=unquote(url.username) if url.username else "default",
        password=password,
        input_database=input_database,
        database=database,
        secure=url.scheme == "https",
    )


def _models(value: Any, base: Path) -> Models:
    section = _section(value, "models", {"version", "path"})
    if ("version" in section) == ("path" in section):
        raise ConfigError("models: give either version (a release tag) or path, not both.")
    if "version" in section:
        version = section["version"]
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ConfigError(f"models.version: {version!r} is not a release tag like v0.4.1.")
        return Models(version=version, path=None)
    path = (base / Path(_text(section, "path", "models")).expanduser()).resolve()
    if not (path / "sources").is_dir():
        raise ConfigError(f"models.path: {path} is not a checkout of dactopus-data-models.")
    return Models(version=None, path=path)


def _vars(section: dict[str, Any], package: str) -> dict[str, Any]:
    value = section.get("vars", {})
    if not isinstance(value, dict):
        raise ConfigError(f"packages.{package}.vars: expected a mapping of dbt variables.")
    reserved = INPUT_DATABASE_VARS[package]
    if reserved in value:
        raise ConfigError(
            f"packages.{package}.vars: remove {reserved}; the runner sets it "
            "from clickhouse.input_database."
        )
    return dict(value)


def _ga4(value: Any, base: Path) -> GA4:
    where = "packages.ga4"
    section = _section(value, where, {"project", "dataset", "credentials_file", "vars"})
    dataset = _text(section, "dataset", where)
    if dataset.count(".") != 1:
        raise ConfigError(f"{where}.dataset: expected <project>.<dataset>, got {dataset!r}.")
    credentials = None
    if "credentials_file" in section:
        credentials = base / Path(_text(section, "credentials_file", where)).expanduser()
    return GA4(
        project=_text(section, "project", where),
        dataset=dataset,
        credentials_file=credentials,
        vars=_vars(section, "ga4"),
    )


def _shopify(value: Any) -> Shopify:
    where = "packages.shopify"
    section = _section(value, where, {"shop", "client_id_env", "client_secret_env", "vars"})
    shop = _text(section, "shop", where)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", shop):
        raise ConfigError(
            f"{where}.shop: give the store's subdomain, as in <shop>.myshopify.com; got {shop!r}."
        )
    return Shopify(
        shop=shop,
        client_id_env=section.get("client_id_env", "SHOPIFY_CLIENT_ID"),
        client_secret_env=section.get("client_secret_env", "SHOPIFY_CLIENT_SECRET"),
        vars=_vars(section, "shopify"),
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
  # The database for the input tables of every package, and the one for the
  # canonical tables. They must differ.
  input_database: dactopus_raw
  database: dactopus

models:
  # A release of dactopus-data-models, downloaded once into ~/.cache/dactopus.
  version: v0.4.1
  # Or a checkout of the library, for development:
  # path: ../dactopus-data-models

packages:
  ga4:
    # The Google Cloud project the read sessions run in; reading is billed to it.
    project: my-project
    # The GA4 export: <project>.<dataset> holding the events_YYYYMMDD tables.
    dataset: bigquery-public-data.ga4_obfuscated_sample_ecommerce
    # A service account key; without it, Application Default Credentials
    # (gcloud auth application-default login).
    # credentials_file: ~/keys/ga4-reader.json
    # dbt variables of the package, passed as they are.
    vars:
      ga4_lookback_days: 3

  shopify:
    # The store, as in <shop>.myshopify.com.
    shop: my-store
    # The environment variables with the app's client id and secret.
    client_id_env: SHOPIFY_CLIENT_ID
    client_secret_env: SHOPIFY_CLIENT_SECRET
    vars:
      # The shop's time zone, Settings > General in the Shopify admin.
      shopify_timezone: UTC
"""


def write_sample(path: Path) -> None:
    if path.exists():
        raise ConfigError(f"{path} exists; remove it or pass another path with --config.")
    path.write_text(SAMPLE)
