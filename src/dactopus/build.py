"""Building the canonical tables: dbt build of each package."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from .config import INPUT_DATABASE_VARS, Config


class BuildError(Exception):
    """dbt could not build a package."""


def _dbt() -> str:
    # The dbt installed with the runner, next to its Python.
    local = Path(sys.executable).parent / "dbt"
    if local.exists():
        return str(local)
    found = shutil.which("dbt")
    if found is None:
        raise BuildError("build: dbt is not installed next to the runner; reinstall dactopus.")
    return found


def write_profile(config: Config, project: Path) -> Path:
    """Write the dbt profile the package names, in the workdir; return its directory.

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
                    # The database the canonical tables are written to.
                    "schema": ch.database,
                }
            },
        }
    }
    config.workdir.mkdir(exist_ok=True)
    (config.workdir / "profiles.yml").write_text(yaml.safe_dump(profile, sort_keys=False))
    return config.workdir


def target(config: Config, package: str) -> Path:
    """Where dbt writes the package's run_results.json, manifest.json and sources.json."""
    return config.workdir / package / "target"


def command(
    config: Config, library: Path, package: str, *dbt_args: str
) -> tuple[list[str], dict[str, str]]:
    """The dbt command line of a package (build by default) and its environment."""
    project = library / "sources" / package
    profiles = write_profile(config, project)
    output = config.workdir / package
    variables = dict(config.packages()[package].vars)
    variables[INPUT_DATABASE_VARS[package]] = config.clickhouse.input_database
    argv = [
        _dbt(),
        *(dbt_args or ("build",)),
        "--project-dir",
        str(project),
        "--profiles-dir",
        str(profiles),
        "--target-path",
        str(output / "target"),
        "--log-path",
        str(output / "logs"),
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


def _run(config: Config, library: Path, package: str, *dbt_args: str, writes: str) -> None:
    """Run dbt on a package; raise when it could not run at all.

    dbt exits with 1 when a model, test or source fails and with 2 when
    it could not run (dbt 1.11.16, 2026-10-22); the file it writes tells the two
    apart. Failures are read from that file by the checks.
    """
    argv, env = command(config, library, package, *dbt_args)
    written = target(config, package) / writes
    written.unlink(missing_ok=True)
    print(f"build: dbt {' '.join(dbt_args)} of {package}", file=sys.stderr)
    # dbt prints its own progress; the password is only in the environment.
    result = subprocess.run(argv, env=env, cwd=config.workdir)
    if result.returncode not in (0, 1) or not written.exists():
        raise BuildError(
            f"build: dbt {' '.join(dbt_args)} of {package} could not run (exit "
            f"{result.returncode}); see its output above and "
            f"{config.workdir / package / 'logs' / 'dbt.log'}."
        )


def build(config: Config, library: Path, package: str, full_refresh: bool = False) -> None:
    """dbt build: the models, their tests and unit tests.

    A full refresh rebuilds the canonical tables from the input tables as
    they are; dbt-clickhouse builds each new table next to the old one and
    swaps them when it is complete.
    """
    args = ("build", "--full-refresh") if full_refresh else ("build",)
    _run(config, library, package, *args, writes="run_results.json")


def test(config: Config, library: Path, package: str) -> None:
    """dbt test: the tests and unit tests over the tables as they are."""
    _run(config, library, package, "test", writes="run_results.json")


def freshness(config: Config, library: Path, package: str) -> None:
    """dbt source freshness: the age of the input against the package's thresholds."""
    _run(config, library, package, "source", "freshness", writes="sources.json")
