"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from clickhouse_connect.driver.exceptions import ClickHouseError

from . import build, clickhouse, config, ga4, library, shopify

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
    return parser


def _init(args: argparse.Namespace) -> int:
    config.write_sample(args.config)
    print(f"Wrote {args.config}; edit it before `dactopus run`.")
    return 0


def _run(args: argparse.Namespace) -> int:
    cfg = config.load(args.config)
    root = library.resolve(cfg.models)
    client = clickhouse.connect(cfg.clickhouse)
    database = cfg.clickhouse.input_database
    for package in cfg.packages():
        clickhouse.create_input(client, root, package, database)
    if cfg.ga4 is not None:
        export = ga4.BigQueryExport(cfg.ga4)
        ga4.load(client, export, database, ga4.lookback_days(cfg.ga4, root))
    if cfg.shopify is not None:
        token = shopify.access_token(cfg.shopify)
        shopify.load(client, shopify.graphql(cfg.shopify.shop, token), database)
    for package in cfg.packages():
        build.build(cfg, root, package)
    return 0


_FAILURES = (
    config.ConfigError,
    library.LibraryError,
    ga4.LoadError,
    shopify.LoadError,
    build.BuildError,
)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            return _init(args)
        if args.command == "run":
            return _run(args)
    except _FAILURES as exc:
        print(f"dactopus {args.command}: {exc}", file=sys.stderr)
        return 1
    except ClickHouseError as exc:
        print(f"dactopus {args.command}: ClickHouse: {exc}", file=sys.stderr)
        return 1
    print(f"dactopus {args.command}: not implemented yet", file=sys.stderr)
    return 2
