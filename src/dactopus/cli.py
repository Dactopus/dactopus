"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config

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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            return _init(args)
    except config.ConfigError as exc:
        print(f"dactopus {args.command}: {exc}", file=sys.stderr)
        return 1
    print(f"dactopus {args.command}: not implemented yet", file=sys.stderr)
    return 2
