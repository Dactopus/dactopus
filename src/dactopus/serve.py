"""Serving the models of the configured sources over MCP, by the executor."""

from __future__ import annotations

import os
from importlib.metadata import version
from pathlib import Path
from urllib.parse import quote

from .config import Config
from .library import unions_of

EXECUTOR = "dactopus-ossie-clickhouse"


class ServeError(Exception):
    """The models cannot be served as configured."""


def model_files(cfg: Config, library: Path) -> list[Path]:
    """The model file of each union that has sources, in the order of the library's unions.

    A union's views read its sources' databases, so every source is served
    through its union, one model per area (commerce, web analytics).
    """
    files: list[Path] = []
    for union in unions_of(cfg, library):
        path = library / union.model
        if not path.is_file():
            raise ServeError(
                f"{union.name}: the models library has no {union.model}; "
                "check models.version or models.path."
            )
        files.append(path)
    return files


def check(cfg: Config, library: Path) -> None:
    """Refuse a library whose models the executor of this runner does not read.

    A release of the library is made for a release of the executor: the Ossie
    schema version of its models is the one that executor supports. The
    executor is asked, no version is compared, so a load and a build never
    end in tables no agent can ask about.
    """
    from dactopus_ossie_clickhouse import ModelError, load_model

    files = model_files(cfg, library)
    try:
        load_model(*files)
    except ModelError as error:
        # The executor names each file by its full path, and a single file not
        # at all; the library is named below.
        first = error.problems[0]
        if len(files) == 1 and str(files[0]) not in first:
            first = f"{files[0]}: {first}"
        first = first.replace(f"{library}{os.sep}", "")
        more = len(error.problems) - 1
        problems = first if not more else f"{first} (and {more} more)"
        models = cfg.models
        if models.path is None:
            where = f"The models library {models.version}"
            other = "set models.version to a release this runner reads"
        else:
            where = f"The models library at {models.path}"
            other = "check out a release of the library this runner reads at models.path"
        raise ServeError(
            f"{where} has models that {EXECUTOR} {version(EXECUTOR)}, which this runner "
            f"serves them with, does not read: {problems}. Upgrade dactopus (uv tool upgrade "
            f'dactopus), or {other} (README, "Models library and runner").'
        ) from None


def url(cfg: Config) -> str:
    """The executor's ClickHouse URL: clickhouse.database, the unions', is its path.

    The models name their tables without a database; the executor reads
    them in the database of the URL, over TLS for https, as the serve user
    when the configuration names one.
    """
    ch = cfg.clickhouse
    user, password = (cfg.serve.user, cfg.serve.password) if cfg.serve else (ch.user, ch.password)
    scheme = "https" if ch.secure else "http"
    credentials = quote(user, safe="")
    if password:
        credentials += ":" + quote(password, safe="")
    host = f"[{ch.host}]" if ":" in ch.host else ch.host  # IPv6
    return f"{scheme}://{credentials}@{host}:{ch.port}/{ch.database}"


def serve(cfg: Config, library: Path) -> int:
    """Run the executor's MCP server over stdio until the client closes it.

    The URL holds the password, so it goes to the executor in-process,
    never in a command line other processes can read. "Today" in a
    question is the date in the installation's time zone.
    """
    from dactopus_ossie_clickhouse import cli as executor

    files = [str(path) for path in model_files(cfg, library)]
    return executor.main(["serve", *files, "--url", url(cfg), "--time-zone", cfg.timezone])
