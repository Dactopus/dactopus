"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from clickhouse_connect.driver.exceptions import ClickHouseError

from . import build, checks, clickhouse, config, ga4, library, report, serve, shopify

COMMANDS = {
    "init": "write a commented sample configuration",
    "run": "load the sources, build them, run the checks, write the report",
    "check": "run the checks and write the report, without loading or building",
    "serve": "serve the models of the configured sources over MCP",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dactopus")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_ in COMMANDS.items():
        command = sub.add_parser(name, help=help_)
        command.add_argument(
            "--config",
            type=Path,
            default=config.DEFAULT_PATH,
            help=f"the configuration file (default: {config.DEFAULT_PATH})",
        )
        if name == "run":
            command.add_argument(
                "--full-refresh",
                nargs="*",
                metavar="LABEL",
                help="rebuild the canonical tables of the sources with these labels (every "
                "source when none is named) from the input, with dbt build --full-refresh",
            )
    return parser


def full_refresh(names: list[str] | None, cfg: config.Config) -> set[str]:
    """The sources `--full-refresh` rebuilds: none without the flag, all without labels."""
    if names is None:
        return set()
    configured = list(cfg.sources)
    unknown = sorted(set(names) - set(configured))
    if unknown:
        raise config.ConfigError(
            f"--full-refresh: {', '.join(unknown)} not in the configuration; "
            f"configured: {', '.join(configured)}."
        )
    return set(names or configured)


def _init(args: argparse.Namespace) -> int:
    config.write_sample(args.config)
    print(f"Wrote {args.config}; edit it before `dactopus run`.")
    return 0


class StepError(Exception):
    """ClickHouse refused a step of a command."""


@contextmanager
def _step(name: str, action: str) -> Iterator[None]:
    """Name the step and the next action when ClickHouse refuses it."""
    try:
        yield
    except ClickHouseError as exc:
        raise StepError(f"{name}: ClickHouse refused it: {exc}; {action}") from None


_AGAIN = "fix the cause and run again; a load resumes where it stopped."


def _connect(cfg: config.Config) -> clickhouse.Client:
    ch = cfg.clickhouse
    with _step(
        f"connecting to ClickHouse at {ch.host}:{ch.port}",
        "check clickhouse.url, the password and that the server is running.",
    ):
        return clickhouse.connect(ch)


def _library(cfg: config.Config) -> Path:
    """The library root, checked against the configured sources."""
    root = library.resolve(cfg.models)
    library.check(cfg, root)
    return root


def _run(args: argparse.Namespace) -> int:
    cfg = config.load(args.config)
    refresh = full_refresh(args.full_refresh, cfg)
    root = _library(cfg)
    # Refused before anything is written: a zone other than the shop's, or
    # canonical tables built in another zone.
    tokens: dict[str, str] = {}
    for label, source in cfg.sources.items():
        if isinstance(source.loader, config.Shopify):
            tokens[label] = shopify.access_token(source.loader)
            shopify.check_time_zone(
                source.loader, tokens[label], source.timezone, source.timezone_setting
            )
    client = _connect(cfg)
    with _step(
        "build: counting the days of the sources' tables",
        "check that the ClickHouse user can read the databases of the sources and run "
        "again; nothing has been loaded yet.",
    ):
        build.check_zone(client, cfg, root, refresh)
    # A source without a loader has its input written by the client's
    # transport or by the client; the runner leaves that database alone.
    for label, source in cfg.sources.items():
        loader, database = source.loader, source.input_database
        if loader is None:
            continue
        with _step(f"{label}: creating the input tables in {database}", _AGAIN):
            clickhouse.create_input(client, root, source.package, database)
        if isinstance(loader, config.GA4):
            export = ga4.BigQueryExport(loader)
            lookback = ga4.lookback_days(label, source.vars, root)
            with _step(f"{label}: loading into {database}.{ga4.TABLE}", _AGAIN):
                ga4.load(client, export, database, lookback)
        else:
            fetch = shopify.graphql(loader.shop, tokens[label])
            with _step(f"{label}: loading orders and products into {database}", _AGAIN):
                shopify.load(client, fetch, database, label)
    for label in cfg.sources:
        _each(build.build, cfg, root, label, full_refresh=label in refresh)
    return _report(cfg, root, client)


def _check(args: argparse.Namespace) -> int:
    cfg = config.load(args.config)
    root = _library(cfg)
    client = _connect(cfg)
    for label in cfg.sources:
        _each(build.test, cfg, root, label)
    return _report(cfg, root, client)


def _serve(args: argparse.Namespace) -> int:
    """MCP over stdio: stdout is the protocol, so nothing else is printed there."""
    cfg = config.load(args.config, serving=True)
    return serve.serve(cfg, _library(cfg))


def _each(step: Callable[..., None], cfg: config.Config, root: Path, label: str, **kw) -> None:
    """A dbt step of one source; when dbt cannot run, the others go on.

    The step leaves dbt's error for the checks, so the report shows it and
    the run exits with 1.
    """
    try:
        step(cfg, root, label, **kw)
    except build.BuildError as exc:
        print(f"dactopus: {exc}", file=sys.stderr)


def _report(cfg: config.Config, root: Path, client: clickhouse.Client) -> int:
    """Measure freshness, run the checks, write the report; 1 on a fail or an error."""
    for label in cfg.sources:
        _each(build.freshness, cfg, root, label)
    findings = checks.run_all(client, cfg)
    at = checks.now()
    volumes = checks.volumes(client, cfg, at)
    text = report.render(cfg, findings, volumes, at)
    path = report.write(cfg, text)
    print(text)
    print(f"report: {path}", file=sys.stderr)
    return 1 if checks.failed(findings) else 0


_FAILURES = (
    config.ConfigError,
    library.LibraryError,
    ga4.LoadError,
    shopify.LoadError,
    build.BuildError,
    serve.ServeError,
    StepError,
)


# The first Python dbt-core 1.11 does not start on. uv ignores the upper bound of
# requires-python, so a uv tool can land on it; docs/design.md, "Python 3.11 to 3.13".
UNSUPPORTED_PYTHON = (3, 14)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if sys.version_info[:2] >= UNSUPPORTED_PYTHON:
        version = ".".join(map(str, sys.version_info[:2]))
        print(
            f"dactopus {args.command}: dactopus runs on Python 3.11 to 3.13, not on {version}, "
            "where dbt does not start. Reinstall it with: "
            "uv tool install --force --python 3.13 dactopus",
            file=sys.stderr,
        )
        return 1
    try:
        if args.command == "init":
            return _init(args)
        if args.command == "run":
            return _run(args)
        if args.command == "check":
            return _check(args)
        return _serve(args)
    except _FAILURES as exc:
        print(f"dactopus {args.command}: {exc}", file=sys.stderr)
        return 1
