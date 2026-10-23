"""Serving the models of the configured packages over MCP, by the executor."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from .config import Config
from .library import deployment


class ServeError(Exception):
    """The models cannot be served as configured."""


def model_files(cfg: Config, library: Path) -> list[Path]:
    """The model files the configured packages declare, each once."""
    files: list[Path] = []
    for package in cfg.packages():
        model = deployment(cfg.models, library, package).model
        path = library / model
        if not path.is_file():
            raise ServeError(
                f"{package}: the models library has no {model}; "
                "check models.version or models.path."
            )
        if path not in files:
            files.append(path)
    return files


def url(cfg: Config) -> str:
    """The executor's ClickHouse URL: the canonical database is its path.

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
    question is the date in store.timezone.
    """
    from dactopus_ossie_clickhouse import cli as executor

    files = [str(path) for path in model_files(cfg, library)]
    return executor.main(["serve", *files, "--url", url(cfg), "--time-zone", cfg.store.timezone])
