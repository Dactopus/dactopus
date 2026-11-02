import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

from dactopus import build, config, library

FIXTURE_LIBRARY = Path(__file__).parent / "fixtures" / "library"


def _config(tmp_path, prefix="dactopus", models=None, label="shopify", source=None):
    path = tmp_path / "dactopus.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "clickhouse": {
                    "url": "http://loader@db.local:18123",
                    "password_env": "CH_PASSWORD",
                    "database": prefix,
                },
                "timezone": "America/New_York",
                "models": models or {"version": "v0.9.0"},
                "sources": {label: source or {"package": "shopify", "shop": "s"}},
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
    profiles = cfg.workdir / "shopify" / "profiles.yml"
    assert argv[argv.index("--profiles-dir") + 1] == str(cfg.workdir / "shopify")
    assert "s3cret" not in profiles.read_text()
    output = yaml.safe_load(profiles.read_text())["dactopus"]["outputs"]["default"]
    assert output == {
        "type": "clickhouse",
        "host": "db.local",
        "port": 18123,
        "secure": False,
        "user": "loader",
        "password": "{{ env_var('DBT_ENV_SECRET_CLICKHOUSE_PASSWORD', '') }}",
        "schema": "dactopus_shopify",
    }
    assert env["DBT_ENV_SECRET_CLICKHOUSE_PASSWORD"] == "s3cret"


def test_runner_sets_the_input_database_the_label_and_the_time_zone(tmp_path):
    cfg = _config(tmp_path)
    argv, _ = build.command(cfg, _library(tmp_path), "shopify")
    variables = json.loads(argv[argv.index("--vars") + 1])
    assert variables == {
        "shopify_input_database": "dactopus_shopify_raw",
        "shopify_source_id": "shopify",
        "shopify_timezone": "America/New_York",
    }


def test_each_source_builds_into_its_own_database_with_its_own_output(tmp_path):
    source = {
        "package": "shopify",
        "shop": "s",
        "timezone": "Europe/Berlin",
        "input_database": "shopify_raw",
        "vars": {"shopify_lookback_days": 5},
    }
    cfg = _config(tmp_path, label="shop_eu", source=source)
    argv, _ = build.command(cfg, _library(tmp_path), "shop_eu")
    assert json.loads(argv[argv.index("--vars") + 1]) == {
        "shopify_lookback_days": 5,
        "shopify_input_database": "shopify_raw",
        "shopify_source_id": "shop_eu",
        "shopify_timezone": "Europe/Berlin",
    }
    output = cfg.workdir / "shop_eu"
    assert argv[argv.index("--target-path") + 1] == str(output / "target")
    assert argv[argv.index("--log-path") + 1] == str(output / "logs")
    profile = yaml.safe_load((output / "profiles.yml").read_text())
    assert profile["dactopus"]["outputs"]["default"]["schema"] == "dactopus_shop_eu"


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
    # The source's database is the test's: <prefix>_<label>.
    prefix, label = database.rsplit("_", 1)
    cfg = _config(tmp_path, prefix, {"path": str(FIXTURE_LIBRARY)}, label=label)
    with pytest.raises(build.BuildError) as refused:
        build.check_zone(client, cfg, FIXTURE_LIBRARY, set())
    assert str(refused.value) == (
        f"build: the tables of {label} were built in a time zone other than the source's: "
        f"1 of 2 rows of orders ({label}) fall on another day in America/New_York. Run "
        f"`dactopus run --full-refresh {label}` once to rebuild them; until then the agent "
        "keeps answering from the current tables."
    )
    # A full refresh of the source rebuilds them: nothing is counted.
    build.check_zone(client, cfg, FIXTURE_LIBRARY, {label})


# What dbt 1.11.16 printed for a WooCommerce source without woocommerce_currency,
# on the stand on 2026-11-02: it could not run, and exited with 2.
DBT_COULD_NOT_RUN = (
    "\x1b[0m08:03:50  Running with dbt=1.11.16\n"
    "\x1b[0m08:03:50  Registered adapter: clickhouse=1.10.3\n"
    "\x1b[0m08:03:51  Encountered an error:\n"
    "Compilation Error in model refunds (models/refunds.sql)\n"
    "  Set woocommerce_currency to the store's currency code, such as USD: the input tables "
    "do not hold it.\n"
    "  \n"
    "  > in macro woocommerce_store_currency (macros/woocommerce.sql)\n"
    "  > called by macro woocommerce_rates (macros/woocommerce.sql)\n"
    "  > called by model refunds (models/refunds.sql)\n"
)

PACKAGE_MESSAGE = (
    "Compilation Error in model refunds (models/refunds.sql) Set woocommerce_currency to the "
    "store's currency code, such as USD: the input tables do not hold it."
)


def test_the_error_of_a_dbt_that_could_not_run_is_the_package_s_message():
    assert build._error(DBT_COULD_NOT_RUN.splitlines(True)) == PACKAGE_MESSAGE
    assert build._error(["\x1b[0m08:03:50  a\n", "b\n"]) == "a b"
    assert build._error([]) == "no output"


def _fake_dbt(monkeypatch, tmp_path, output, code, writes=None):
    """dbt replaced by a script that prints output, writes a file and exits with code."""
    script = tmp_path / "fake_dbt.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({output!r})\n"
        + (f"open({str(writes)!r}, 'w').write('{{}}')\n" if writes else "")
        + f"sys.exit({code})\n"
    )
    monkeypatch.setattr(
        build, "command", lambda *args: ([sys.executable, str(script)], dict(os.environ))
    )


def test_a_dbt_that_could_not_run_leaves_its_error_for_the_checks(tmp_path, monkeypatch, capsys):
    cfg = _config(tmp_path)
    build.output(cfg, "shopify").mkdir(parents=True)
    _fake_dbt(monkeypatch, tmp_path, DBT_COULD_NOT_RUN, 2)
    with pytest.raises(build.BuildError) as refused:
        build.build(cfg, _library(tmp_path), "shopify")
    assert str(refused.value).startswith(
        f"build: dbt build of shopify could not run (exit 2): {PACKAGE_MESSAGE} See "
    )
    assert "Set woocommerce_currency" in capsys.readouterr().out
    assert build.dbt_error(cfg, "shopify", "run_results.json") == ("dbt build", PACKAGE_MESSAGE)
    assert build.dbt_error(cfg, "shopify", "sources.json") is None


def test_a_run_that_writes_its_results_clears_the_error_before(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    build.target(cfg, "shopify").mkdir(parents=True)
    build.failure(cfg, "shopify", "run_results.json").write_text("dbt build\nold\n")
    written = build.target(cfg, "shopify") / "run_results.json"
    _fake_dbt(monkeypatch, tmp_path, "Done.\n", 1, writes=written)
    build.build(cfg, _library(tmp_path), "shopify")
    assert build.dbt_error(cfg, "shopify", "run_results.json") is None
