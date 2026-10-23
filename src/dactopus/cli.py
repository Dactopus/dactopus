"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from clickhouse_connect.driver.exceptions import ClickHouseError

from . import build, checks, clickhouse, config, ga4, library, report, serve, shopify

COMMANDS = {
    "init": "write a commented sample configuration",
    "run": "load the sources, build the packages, run the checks, write the report",
    "check": "run the checks and write the report, without loading or building",
    "serve": "serve the models of the configured packages over MCP",
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
                metavar="PACKAGE",
                help="rebuild the canonical tables of the named packages (all configured "
                "packages when none is named) from the input, with dbt build --full-refresh",
            )
    return parser


def full_refresh(names: list[str] | None, cfg: config.Config) -> set[str]:
    """The packages `--full-refresh` rebuilds: none without the flag, all without names."""
    if names is None:
        return set()
    configured = list(cfg.packages())
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
    """The library root, with the configured vars checked against its packages."""
    root = library.resolve(cfg.models)
    library.check_vars(cfg, root)
    return root


def _run(args: argparse.Namespace) -> int:
    cfg = config.load(args.config)
    refresh = full_refresh(args.full_refresh, cfg)
    root = _library(cfg)
    client = _connect(cfg)
    database = cfg.clickhouse.input_database
    for package in cfg.packages():
        with _step(f"{package}: creating the input tables in {database}", _AGAIN):
            clickhouse.create_input(client, root, package, database)
    if cfg.ga4 is not None:
        export = ga4.BigQueryExport(cfg.ga4)
        with _step(f"ga4: loading into {database}.{ga4.TABLE}", _AGAIN):
            ga4.load(client, export, database, ga4.lookback_days(cfg.ga4, root))
    if cfg.shopify is not None:
        token = shopify.access_token(cfg.shopify)
        with _step(f"shopify: loading orders into {database}", _AGAIN):
            shopify.load(client, shopify.graphql(cfg.shopify.shop, token), database)
    for package in cfg.packages():
        build.build(cfg, root, package, full_refresh=package in refresh)
    return _report(cfg, root, client)


def _check(args: argparse.Namespace) -> int:
    cfg = config.load(args.config)
    root = _library(cfg)
    client = _connect(cfg)
    for package in cfg.packages():
        build.test(cfg, root, package)
    return _report(cfg, root, client)


def _serve(args: argparse.Namespace) -> int:
    """MCP over stdio: stdout is the protocol, so nothing else is printed there."""
    cfg = config.load(args.config, serving=True)
    return serve.serve(cfg, _library(cfg))


def _report(cfg: config.Config, root: Path, client: clickhouse.Client) -> int:
    """Measure freshness, run the checks, write the report; 1 on a fail or an error."""
    for package in cfg.packages():
        build.freshness(cfg, root, package)
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
