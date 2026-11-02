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

import yaml

from .config import Config, ConfigError, Models, Source

ARCHIVE_URL = "https://github.com/Dactopus/dactopus-data-models/archive/refs/tags/{version}.tar.gz"

# The first release with labels (the role source_id) and unions.
LIBRARY_SINCE = "v0.9.0"

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
        if (root / "sources" / package).is_dir():
            raise LibraryError(
                f"{package}: the models library has no {relative}; {_upgrade(models)}."
            )
        found = sorted(p.parent.name for p in (root / "sources").glob("*/deployment.yml"))
        raise LibraryError(
            f"{package} is not a package of the models library {_where(models)}; its "
            f"packages: {', '.join(found)}."
        )
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
    for role in ("source_id", "time_zone"):
        if role in variables:
            _name(variables, role, f"{where}: vars")
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


@dataclass(frozen=True)
class Union:
    """What the runner sets and reads of a union: unions/<name>/deployment.yml."""

    name: str
    # The Ossie model the union's views serve, from the library root.
    model: str
    # The dbt variable that takes the databases of the sources it adds up.
    databases: str


@functools.cache
def union_deployments(models: Models, root: Path) -> tuple[Union, ...]:
    """The unions of the library at root, by name; read once per process."""
    found = []
    for path in sorted((root / "unions").glob("*/deployment.yml")):
        name = path.parent.name
        where = f"unions/{name}/deployment.yml"
        try:
            section = _mapping(yaml.safe_load(path.read_text()), where)
        except yaml.YAMLError as exc:
            raise LibraryError(f"{where} is not valid YAML: {exc}; report it.") from None
        model = section.get("model")
        if not isinstance(model, str) or not model:
            raise LibraryError(f"{where}: model: missing; expected a path; report it.")
        variables = _mapping(section.get("vars"), f"{where}: vars")
        found.append(Union(name, model, _name(variables, "input_databases", f"{where}: vars")))
    return tuple(found)


def unions_of(config: Config, root: Path) -> dict[Union, list[Source]]:
    """The unions that have sources, each with its sources in the configuration's order.

    A source belongs to the union that serves the model its package declares.
    """
    found: dict[Union, list[Source]] = {}
    for union in union_deployments(config.models, root):
        members = [
            source
            for source in config.sources.values()
            if deployment(config.models, root, source.package).model == union.model
        ]
        if members:
            found[union] = members
    return found


def _where(models: Models) -> str:
    return models.version if models.path is None else f"at {models.path}"


def _upgrade(models: Models) -> str:
    if models.path is None:
        return f"set models.version to {LIBRARY_SINCE} or later"
    return f"update the checkout at models.path to {LIBRARY_SINCE} or later"


def check(config: Config, root: Path) -> None:
    """Check the library against the sources: their packages, labels and vars.

    The runner sets the variables of the roles input_database, source_id
    and time_zone, so a source's vars may not.
    """
    if not (root / "unions").is_dir():
        raise LibraryError(
            f"The models library {_where(config.models)} has no unions/, which came in "
            f"{LIBRARY_SINCE} with the labels of the sources; {_upgrade(config.models)}."
        )
    served = {union.model for union in union_deployments(config.models, root)}
    for label, source in config.sources.items():
        package = deployment(config.models, root, source.package)
        if package.model not in served:
            raise LibraryError(
                f"{source.package}: no union of the models library serves {package.model}, so "
                f"{label} could not be served; {_upgrade(config.models)}."
            )
        declared = package.vars
        if "source_id" not in declared:
            raise LibraryError(
                f"{source.package}: sources/{source.package}/deployment.yml maps no "
                f"variable to the role source_id; {_upgrade(config.models)}."
            )
        for role, setting in (
            ("input_database", f"sources.{label}.input_database or clickhouse.database"),
            ("source_id", "the label"),
            ("time_zone", f"sources.{label}.timezone or timezone"),
        ):
            reserved = declared.get(role)
            if reserved is not None and reserved in source.vars:
                raise ConfigError(
                    f"sources.{label}.vars: remove {reserved}; the runner sets it from {setting}."
                )


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
