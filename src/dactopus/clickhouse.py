"""The connection to the client's ClickHouse and the packages' input tables."""

from __future__ import annotations

import re
from pathlib import Path

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from .config import ClickHouse

_COMMENT = re.compile(r"--[^\n]*")


def connect(settings: ClickHouse) -> Client:
    return clickhouse_connect.get_client(
        host=settings.host,
        port=settings.port,
        username=settings.user,
        password=settings.password,
        secure=settings.secure,
    )


def create_input(client: Client, library: Path, package: str, database: str) -> None:
    """Create the package's input tables by running its input.sql from the library."""
    script = (library / "sources" / package / "input.sql").read_text()
    # input.sql holds no string literals with -- or ;, only comments do.
    for statement in _COMMENT.sub("", script).split(";"):
        if statement.strip():
            client.command(statement, parameters={"db": database})
