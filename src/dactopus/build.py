"""Building the canonical tables: dbt build of each source's package."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from clickhouse_connect.driver.client import Client

from .config import Config
from .library import Daily, Union, deployment, unions_of


class BuildError(Exception):
    """dbt could not build a source."""


def _dbt() -> str:
    # The dbt installed with the runner, next to its Python.
    local = Path(sys.executable).parent / "dbt"
    if local.exists():
        return str(local)
    found = shutil.which("dbt")
    if found is None:
        raise BuildError("build: dbt is not installed next to the runner; reinstall dactopus.")
    return found


def write_profile(config: Config, project: Path, database: str, directory: Path) -> Path:
    """Write the dbt profile the project names, writing to database; return its directory.

    The password stays out of the file: dbt reads it from the environment.
    """
    name = yaml.safe_load((project / "dbt_project.yml").read_text())["profile"]
    ch = config.clickhouse
    profile = {
        name: {
            "target": "default",
            "outputs": {
                "default": {
                    "type": "clickhouse",
                    "host": ch.host,
                    "port": ch.port,
                    "secure": ch.secure,
                    "user": ch.user,
                    # dbt scrubs DBT_ENV_SECRET_ values from its logs.
                    "password": "{{ env_var('DBT_ENV_SECRET_CLICKHOUSE_PASSWORD', '') }}",
                    # The database the project's tables are written to.
                    "schema": database,
                }
            },
        }
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "profiles.yml").write_text(yaml.safe_dump(profile, sort_keys=False))
    return directory


# A union's key in the output directory and in the results: a label cannot hold
# a hyphen, so no source's output is a union's.
UNION = "union-"


def union_key(union: Union) -> str:
    return f"{UNION}{union.name}"


def title(key: str) -> str:
    """The name of a source or a union in messages: the label, or "the commerce union"."""
    return f"the {key.removeprefix(UNION)} union" if key.startswith(UNION) else key


def output(config: Config, key: str) -> Path:
    """The profile and dbt output of a source or a union: .dactopus/<key>/."""
    return config.workdir / key


def target(config: Config, key: str) -> Path:
    """Where dbt writes the run_results.json, manifest.json and sources.json of a key."""
    return output(config, key) / "target"


def command(
    config: Config, library: Path, key: str, *dbt_args: str
) -> tuple[list[str], dict[str, str]]:
    """The dbt command line of a source by its label or a union by its key (build by default)."""
    if key in config.sources:
        project, database, variables = _source_project(config, library, key)
    else:
        project, database, variables = _union_project(config, library, key)
    where = output(config, key)
    profiles = write_profile(config, project, database, where)
    argv = [
        _dbt(),
        *(dbt_args or ("build",)),
        "--project-dir",
        str(project),
        "--profiles-dir",
        str(profiles),
        "--target-path",
        str(where / "target"),
        "--log-path",
        str(where / "logs"),
        "--vars",
        json.dumps(variables),
    ]
    env = {
        **os.environ,
        "DBT_ENV_SECRET_CLICKHOUSE_PASSWORD": config.clickhouse.password,
        # No events about the client's runs go to dbt Labs, and dbt writes no
        # .user.yml next to the profile.
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    }
    return argv, env


def _source_project(
    config: Config, library: Path, label: str
) -> tuple[Path, str, dict[str, object]]:
    """The dbt project of a source, the database it writes to and its variables."""
    source = config.sources[label]
    variables: dict[str, object] = dict(source.vars)
    declared = deployment(config.models, library, source.package).vars
    variables[declared["input_database"]] = source.input_database
    variables[declared["source_id"]] = label
    if "time_zone" in declared:
        variables[declared["time_zone"]] = source.timezone
    return library / "sources" / source.package, source.database, variables


def _union_project(config: Config, library: Path, key: str) -> tuple[Path, str, dict[str, object]]:
    """The dbt project of a union, the database of the unions and its variables.

    The databases of its sources go in the order of the configuration, so a
    rebuild with nothing changed writes the same views.
    """
    for union, members in unions_of(config, library).items():
        if union_key(union) == key:
            variables: dict[str, object] = {union.databases: [m.database for m in members]}
            return library / "unions" / union.name, config.clickhouse.database, variables
    raise BuildError(f"build: no union {title(key)} for the configured sources.")


def failure(config: Config, label: str, writes: str) -> Path:
    """Where a dbt run that could not write ``writes`` leaves its step and dbt's error."""
    return output(config, label) / f"{Path(writes).stem}.error"


# dbt's console output: colour codes, and the time before each line's text.
_COLOUR = re.compile(r"\x1b\[[0-9;]*m")
_TIME = re.compile(r"^\d\d:\d\d:\d\d  ")
# The lines of a run kept for its error, which comes last.
_KEPT = 200


def _error(lines: list[str]) -> str:
    """dbt's error, from the console lines of a run that could not run.

    dbt 1.11.16 prints "Encountered an error:" and the error below it; a
    package's own message (raise_compiler_error) is in it, followed by the
    macros that called it (2026-11-02). Without that line, the last lines.
    """
    text = [_TIME.sub("", _COLOUR.sub("", line)).rstrip() for line in lines]
    starts = [i for i, line in enumerate(text) if line.strip() == "Encountered an error:"]
    found = text[starts[-1] + 1 :] if starts else text[-5:]
    kept = [line.strip() for line in found if line.strip() and not line.strip().startswith(">")]
    return " ".join(kept) or "no output"


def _run(config: Config, library: Path, label: str, *dbt_args: str, writes: str) -> None:
    """Run dbt on a source; raise when it could not run at all.

    dbt exits with 1 when a model, test or source fails and with 2 when
    it could not run (dbt 1.11.16, 2026-10-22); the file it writes tells the two
    apart. Failures are read from that file by the checks; a run that could
    not run leaves its step and dbt's error next to it, for the checks too.
    """
    written = target(config, label) / writes
    failed = failure(config, label, writes)
    written.unlink(missing_ok=True)
    failed.unlink(missing_ok=True)
    step = f"dbt {' '.join(dbt_args)}"
    name = title(label)
    try:
        argv, env = command(config, library, label, *dbt_args)
    except BuildError as exc:
        failed.parent.mkdir(parents=True, exist_ok=True)
        failed.write_text(f"{step}\n{exc}\n")
        raise
    print(f"build: {step} of {name}", file=sys.stderr)
    # dbt prints its own progress, passed on as it comes; the password is
    # only in the environment.
    with subprocess.Popen(
        argv, env=env, cwd=config.workdir, stdout=subprocess.PIPE, text=True
    ) as process:
        assert process.stdout is not None
        lines: deque[str] = deque(maxlen=_KEPT)
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            lines.append(line)
    if process.returncode not in (0, 1) or not written.exists():
        message = _error(list(lines))
        # What dbt wrote before it stopped is not a run's results: the
        # checks report the error instead.
        written.unlink(missing_ok=True)
        failed.write_text(f"{step}\n{message}\n")
        raise BuildError(
            f"build: {step} of {name} could not run (exit {process.returncode}): {message} "
            f"See {output(config, label) / 'logs' / 'dbt.log'}."
        )


def dbt_error(config: Config, label: str, writes: str) -> tuple[str, str] | None:
    """The step and dbt's error of the last run that could not write ``writes``, if any."""
    path = failure(config, label, writes)
    if not path.is_file():
        return None
    step, _, message = path.read_text().partition("\n")
    return step, message.strip()


def build(config: Config, library: Path, label: str, full_refresh: bool = False) -> None:
    """dbt build: the models, their tests and unit tests.

    A full refresh rebuilds the canonical tables from the input tables as
    they are; dbt-clickhouse builds each new table next to the old one and
    swaps them when it is complete.
    """
    args = ("build", "--full-refresh") if full_refresh else ("build",)
    _run(config, library, label, *args, writes="run_results.json")


def test(config: Config, library: Path, label: str) -> None:
    """dbt test: the tests and unit tests over the tables as they are."""
    _run(config, library, label, "test", writes="run_results.json")


def freshness(config: Config, library: Path, label: str) -> None:
    """dbt source freshness: the age of the input against the package's thresholds."""
    _run(config, library, label, "source", "freshness", writes="sources.json")


@dataclass(frozen=True)
class Misdated:
    """Rows of a daily table whose date is not the day of their time in a zone."""

    rows: int
    # Of all the rows counted, from first to last by date; 0 and None for a
    # missing or empty table.
    of: int
    first: date | None = None
    last: date | None = None


def misdated(
    client: Client, database: str, counted: Daily, zone: str, days: int | None = None
) -> Misdated:
    """Count the rows of a daily table that fall on another day in zone.

    A table built in the zone has none. ``days`` limits the count to the
    days up to its latest date, for a table too large to read whole. A
    table or database that does not exist yet counts as empty.
    """
    exists = client.query(
        "SELECT count() FROM system.tables WHERE database = {db:String} AND name = {t:String}",
        parameters={"db": database, "t": counted.table},
    ).result_rows[0][0]
    if not exists:
        return Misdated(0, 0)
    qualified = f"`{database}`.`{counted.table}`"
    day, time = f"`{counted.date}`", f"`{counted.time}`"
    where = ""
    if days is not None:
        where = f" WHERE {day} > (SELECT max({day}) FROM {qualified}) - INTERVAL {days} DAY"
    rows, of, first, last = client.query(
        f"SELECT countIf({day} != toDate({time}, {{zone:String}})), count(), "
        f"min({day}), max({day}) FROM {qualified}{where}",
        parameters={"zone": zone},
    ).result_rows[0]
    if not of:
        return Misdated(0, 0)
    return Misdated(int(rows), int(of), first, last)


def check_unions(client: Client, config: Config, library: Path) -> None:
    """Refuse to build the unions into a database that holds tables.

    The unions are views over the sources' databases. A table in their
    database, such as the canonical tables a 0.2.x installation built there,
    would be replaced by a view of the same name without a word.
    """
    if not unions_of(config, library):
        return
    found = [
        name
        for (name,) in client.query(
            "SELECT name FROM system.tables WHERE database = {db:String} AND engine != 'View' "
            "ORDER BY name",
            parameters={"db": config.clickhouse.database},
        ).result_rows
    ]
    if found:
        listed = ", ".join(found[:5]) + (", ..." if len(found) > 5 else "")
        raise BuildError(
            f"build: {config.clickhouse.database}, the database of the unions, holds tables "
            f"({listed}), and a union would replace those it has the name of with views. "
            "Give clickhouse.database another name, or drop the old tables if nothing reads them."
        )


def check_zone(client: Client, config: Config, library: Path, refresh: set[str]) -> None:
    """Refuse to build over tables built in a zone other than the source's.

    The incremental models of a package with a time zone variable rebuild
    only the rows that changed, so a build after the zone changed would
    leave the tables with days of both zones. A source rebuilt with
    --full-refresh is not counted.
    """
    found: dict[str, list[str]] = {}
    for label, source in config.sources.items():
        declared = deployment(config.models, library, source.package)
        if "time_zone" not in declared.vars or label in refresh:
            continue
        for counted in declared.daily:
            if counted.time is None:
                continue
            off = misdated(client, source.database, counted, source.timezone)
            if off.rows:
                found.setdefault(label, []).append(
                    f"{off.rows:,} of {off.of:,} rows of {counted.table} ({label}) fall on "
                    f"another day in {source.timezone}"
                )
    if found:
        labels = " ".join(found)
        tables = "; ".join(t for ts in found.values() for t in ts)
        raise BuildError(
            f"build: the tables of {', '.join(found)} were built in a time zone other than "
            f"the source's: {tables}. Run `dactopus run --full-refresh {labels}` once to "
            "rebuild them; until then the agent keeps answering from the current tables."
        )
