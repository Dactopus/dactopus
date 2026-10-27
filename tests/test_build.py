import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from dactopus import build, config, library

FIXTURE_LIBRARY = Path(__file__).parent / "fixtures" / "library"


def _config(tmp_path, database="canonical", models=None):
    path = tmp_path / "dactopus.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "clickhouse": {
                    "url": "http://loader@db.local:18123",
                    "password_env": "CH_PASSWORD",
                    "input_database": "raw",
                    "database": database,
                },
                "store": {"timezone": "America/New_York"},
                "models": models or {"version": "v0.7.1"},
                "packages": {"shopify": {"shop": "s"}},
            }
        )
    )
    return config.load(path, environ={"CH_PASSWORD": "s3cret"})


def _library(tmp_path):
    project = tmp_path / "lib/sources/shopify"
    project.mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: dactopus_shopify\nprofile: dactopus\n")
    declared = FIXTURE_LIBRARY / "sources/shopify/deployment.yml"
    (project / "deployment.yml").write_text(declared.read_text())
    return tmp_path / "lib"


def test_runner_writes_the_profile_without_the_password(tmp_path):
    cfg = _config(tmp_path)
    argv, env = build.command(cfg, _library(tmp_path), "shopify")
    profiles = cfg.workdir / "profiles.yml"
    assert argv[argv.index("--profiles-dir") + 1] == str(cfg.workdir)
    assert "s3cret" not in profiles.read_text()
    output = yaml.safe_load(profiles.read_text())["dactopus"]["outputs"]["default"]
    assert output == {
        "type": "clickhouse",
        "host": "db.local",
        "port": 18123,
        "secure": False,
        "user": "loader",
        "password": "{{ env_var('DBT_ENV_SECRET_CLICKHOUSE_PASSWORD', '') }}",
        "schema": "canonical",
    }
    assert env["DBT_ENV_SECRET_CLICKHOUSE_PASSWORD"] == "s3cret"


def test_runner_sets_the_input_database_and_the_time_zone(tmp_path):
    cfg = _config(tmp_path)
    argv, _ = build.command(cfg, _library(tmp_path), "shopify")
    variables = json.loads(argv[argv.index("--vars") + 1])
    assert variables == {
        "shopify_input_database": "raw",
        "shopify_timezone": "America/New_York",
    }


def test_a_full_refresh_follows_build_on_the_command_line(tmp_path):
    cfg = _config(tmp_path)
    argv, _ = build.command(cfg, _library(tmp_path), "shopify", "build", "--full-refresh")
    assert argv[1:3] == ["build", "--full-refresh"]


def test_build_passes_the_full_refresh_to_dbt_only_when_asked(tmp_path, monkeypatch):
    runs = []
    monkeypatch.setattr(build, "_run", lambda cfg, lib, pkg, *args, writes: runs.append(args))
    cfg, lib = _config(tmp_path), _library(tmp_path)
    build.build(cfg, lib, "shopify")
    build.build(cfg, lib, "shopify", full_refresh=True)
    assert runs == [("build",), ("build", "--full-refresh")]


ORDERS = library.Daily("orders", "order_date", "order_time", gap=True)


def _orders(client, database, *times):
    """orders with the day of each time in New York, as the Shopify package builds it."""
    client.command(f"CREATE DATABASE IF NOT EXISTS `{database}`")
    client.command(
        f"CREATE TABLE `{database}`.orders (order_date Date, order_time DateTime('UTC')) "
        "ENGINE = MergeTree ORDER BY order_time"
    )
    for time, zone in times:
        client.command(
            f"INSERT INTO `{database}`.orders SELECT toDate(t, {{zone:String}}), t "
            "FROM (SELECT toDateTime({time:String}, 'UTC') AS t)",
            parameters={"time": time, "zone": zone},
        )


def test_a_table_built_in_one_zone_has_no_rows_on_another_day(clickhouse):
    client, database = clickhouse
    # 02:30 UTC on 21 October is 20 October in New York.
    _orders(
        client,
        database,
        ("2026-10-21 02:30:00", "America/New_York"),
        ("2026-10-21 15:00:00", "America/New_York"),
    )
    found = build.misdated(client, database, ORDERS, "America/New_York")
    assert found == build.Misdated(0, 2, date(2026, 10, 20), date(2026, 10, 21))
    assert build.misdated(client, database, ORDERS, "UTC").rows == 1


def test_a_table_built_in_two_zones_counts_the_rows_of_the_other(clickhouse):
    client, database = clickhouse
    _orders(
        client,
        database,
        ("2026-10-21 02:30:00", "America/New_York"),
        ("2026-10-22 02:30:00", "UTC"),
        ("2026-10-22 15:00:00", "UTC"),
    )
    assert build.misdated(client, database, ORDERS, "America/New_York").rows == 1


def test_days_count_only_the_latest_days(clickhouse):
    client, database = clickhouse
    _orders(client, database, ("2026-09-01 02:30:00", "UTC"), ("2026-10-21 15:00:00", "UTC"))
    found = build.misdated(client, database, ORDERS, "America/New_York", days=35)
    assert found == build.Misdated(0, 1, date(2026, 10, 21), date(2026, 10, 21))


def test_a_missing_table_or_database_counts_as_empty(clickhouse):
    client, database = clickhouse
    assert build.misdated(client, database, ORDERS, "UTC") == build.Misdated(0, 0)
    client.command(f"CREATE DATABASE `{database}`")
    assert build.misdated(client, database, ORDERS, "UTC") == build.Misdated(0, 0)


def test_tables_built_in_another_zone_stop_the_build(clickhouse, tmp_path):
    client, database = clickhouse
    _orders(client, database, ("2026-10-21 02:30:00", "UTC"), ("2026-10-21 15:00:00", "UTC"))
    cfg = _config(tmp_path, database, {"path": str(FIXTURE_LIBRARY)})
    with pytest.raises(build.BuildError) as refused:
        build.check_zone(client, cfg, FIXTURE_LIBRARY, set())
    assert str(refused.value) == (
        "build: the tables of shopify were built in a time zone other than store.timezone "
        "(America/New_York): 1 of 2 rows of orders (shopify) fall on another day in it. Run "
        "`dactopus run --full-refresh shopify` once to rebuild them; until then the agent "
        "keeps answering from the current tables."
    )
    # A full refresh of the package rebuilds them: nothing is counted.
    build.check_zone(client, cfg, FIXTURE_LIBRARY, {"shopify"})
