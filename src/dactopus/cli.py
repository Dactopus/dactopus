"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys

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
        sub.add_parser(name, help=help_)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(f"dactopus {args.command}: not implemented yet", file=sys.stderr)
    return 2
