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
                    "user": ch.user,
                    "password": "{{ env_var('CLICKHOUSE_PASSWORD', '') }}",
                    # The database the canonical tables are written to.
                    "schema": ch.database,
                }
            },
        }
    }
    config.workdir.mkdir(exist_ok=True)
    (config.workdir / "profiles.yml").write_text(yaml.safe_dump(profile, sort_keys=False))
    return config.workdir


def command(config: Config, library: Path, package: str) -> tuple[list[str], dict[str, str]]:
    """The dbt build command line of a package and the environment it runs in."""
    project = library / "sources" / package
    profiles = write_profile(config, project)
    output = config.workdir / package
    variables = dict(config.packages()[package].vars)
    variables[INPUT_DATABASE_VARS[package]] = config.clickhouse.input_database
    argv = [
        _dbt(),
        "build",
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
        "CLICKHOUSE_PASSWORD": config.clickhouse.password,
        # No events about the client's runs go to dbt Labs, and dbt writes no
        # .user.yml next to the profile.
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    }
    return argv, env


def build(config: Config, library: Path, package: str) -> None:
    argv, env = command(config, library, package)
    print(f"build: dbt build of {package}", file=sys.stderr)
    # dbt prints its own progress; the password is only in the environment.
    result = subprocess.run(argv, env=env, cwd=config.workdir)
    if result.returncode != 0:
        raise BuildError(
            f"build: dbt build of {package} failed (exit {result.returncode}); see its "
            f"output above and {config.workdir / package / 'logs' / 'dbt.log'}."
        )
