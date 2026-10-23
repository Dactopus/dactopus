"""The models library: a release downloaded into the cache, or a checkout."""

from __future__ import annotations

import functools
import io
import os
import re
import tarfile
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .config import Config, ConfigError, Models

ARCHIVE_URL = "https://github.com/Dactopus/dactopus-data-models/archive/refs/tags/{version}.tar.gz"

# The first release whose packages ship a deployment.yml.
DEPLOYMENT_SINCE = "v0.6.0"

Fetch = Callable[[str], bytes]

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class LibraryError(Exception):
    """The models library cannot be found or downloaded, or lacks what the runner reads."""


@dataclass(frozen=True)
class Daily:
    """A built table counted per day, by its Date column."""

    table: str
    date: str
    # The column whose day `date` is; None when the package names none.
    time: str | None = None
    # Whether a day without rows is a finding (orders), or ordinary (refunds).
    gap: bool = False


@dataclass(frozen=True)
class Deployment:
    """What the runner sets and reads of a package: sources/<package>/deployment.yml."""

    # The Ossie model the package's tables serve, from the library root.
    model: str
    # A role (input_database, time_zone) and the dbt variable that plays it.
    vars: Mapping[str, str]
    daily: tuple[Daily, ...]


def cache_dir(environ: dict[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "dactopus" / "dactopus-data-models"


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def resolve(models: Models, cache: Path | None = None, fetch: Fetch = _download) -> Path:
    """The root of the library: models.path, or the release in the cache.

    A release is downloaded once: a published tag never moves.
    """
    if models.path is not None:
        return models.path
    cache = cache_dir() if cache is None else cache
    root = cache / models.version
    if (root / "sources").is_dir():
        return root
    url = ARCHIVE_URL.format(version=models.version)
    try:
        archive = fetch(url)
    except urllib.error.HTTPError as exc:
        raise LibraryError(
            f"Downloading the models library {models.version} failed ({exc.code}): "
            "check models.version against the releases of dactopus-data-models."
        ) from None
    except OSError as exc:
        raise LibraryError(
            f"Downloading the models library {models.version} from {url} failed: {exc}."
        ) from None
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        top = _extract(archive, Path(tmp))
        if not (top / "sources").is_dir():
            raise LibraryError(f"The archive of {models.version} has no sources/ directory.")
        try:
            top.rename(root)
        except OSError:
            # Another run extracted the same release first.
            if not (root / "sources").is_dir():
                raise
    return root


@functools.cache
def deployment(models: Models, root: Path, package: str) -> Deployment:
    """The declarations of a package in the library at root.

    Read once per process, so every step of a command sees the same
    declarations even when the checkout at models.path changes meanwhile.
    Keys and roles the runner does not read are ignored, so a newer
    library does not stop an older runner.
    """
    relative = f"sources/{package}/deployment.yml"
    path = root / relative
    if not path.is_file():
        if models.path is None:
            action = f"set models.version to {DEPLOYMENT_SINCE} or later"
        else:
            action = f"update the checkout at models.path to {DEPLOYMENT_SINCE} or later"
        raise LibraryError(f"{package}: the models library has no {relative}; {action}.")
    where = f"{package}: {relative}"
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise LibraryError(f"{where} is not valid YAML: {exc}; report it.") from None
    section = _mapping(raw, where)
    model = section.get("model")
    if not isinstance(model, str) or not model:
        raise LibraryError(f"{where}: model: missing; expected a path; report it.")
    variables = _mapping(section.get("vars"), f"{where}: vars")
    _name(variables, "input_database", f"{where}: vars")
    if "time_zone" in variables:
        _name(variables, "time_zone", f"{where}: vars")
    daily = section.get("daily")
    if not isinstance(daily, list):
        raise LibraryError(f"{where}: daily: missing; expected a list of tables; report it.")
    tables = []
    for i, entry in enumerate(daily):
        at = f"{where}: daily[{i}]"
        entry = _mapping(entry, at)
        gap = entry.get("gap", False)
        if not isinstance(gap, bool):
            raise LibraryError(f"{at}.gap: expected true or false; report it.")
        tables.append(
            Daily(
                table=_name(entry, "table", at),
                date=_name(entry, "date", at),
                time=_name(entry, "time", at) if "time" in entry else None,
                gap=gap,
            )
        )
    return Deployment(
        model=model,
        vars=MappingProxyType({role: str(name) for role, name in variables.items()}),
        daily=tuple(tables),
    )


def check_vars(config: Config, root: Path) -> None:
    """Check packages.<package>.vars against the variables each package declares."""
    for package, settings in config.packages().items():
        declared = deployment(config.models, root, package).vars
        reserved = declared["input_database"]
        if reserved in settings.vars:
            raise ConfigError(
                f"packages.{package}.vars: remove {reserved}; the runner sets it "
                "from clickhouse.input_database."
            )
        zone = declared.get("time_zone")
        if zone in settings.vars:
            try:
                ZoneInfo(str(settings.vars[zone]))
            except (ZoneInfoNotFoundError, ValueError):
                raise ConfigError(
                    f"packages.{package}.vars.{zone}: {settings.vars[zone]!r} is not a time "
                    "zone; give one like America/New_York."
                ) from None


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LibraryError(f"{where}: missing; expected a mapping; report it.")
    return value


def _name(section: dict[str, Any], key: str, where: str) -> str:
    """A name the runner puts into SQL or dbt variables: a plain identifier."""
    value = section.get(key)
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise LibraryError(f"{where}.{key}: missing; expected a plain name; report it.")
    return value


def _extract(archive: bytes, into: Path) -> Path:
    """Extract a GitHub archive and return its single top directory."""
    try:
        tar = tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz")
        members = tar.getmembers()
    except (tarfile.TarError, OSError, EOFError):
        raise LibraryError("The models archive is not a readable .tar.gz; run again.") from None
    with tar:
        for member in members:
            target = (into / member.name).resolve()
            if not target.is_relative_to(into.resolve()) or not (member.isfile() or member.isdir()):
                raise LibraryError(f"Unexpected entry {member.name!r} in the models archive.")
        # The data filter exists from Python 3.11.4 and is the default in 3.14.
        safe = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        tar.extractall(into, members=members, **safe)
    tops = [path for path in into.iterdir() if path.is_dir()]
    if len(tops) != 1:
        raise LibraryError("The models archive does not hold a single directory.")
    return tops[0]
