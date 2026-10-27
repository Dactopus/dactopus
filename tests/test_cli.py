from pathlib import Path

import pytest
from clickhouse_connect.driver.exceptions import DatabaseError

from dactopus import cli, config
from dactopus.cli import COMMANDS, main

FIXTURE_LIBRARY = Path(__file__).parent / "fixtures" / "library"


def test_commands_are_the_public_surface():
    assert list(COMMANDS) == ["init", "run", "check", "serve"]


def test_unknown_command_is_refused():
    with pytest.raises(SystemExit) as exit_info:
        main(["load"])
    assert exit_info.value.code == 2


def test_init_writes_the_sample(tmp_path, capsys):
    path = tmp_path / "dactopus.yaml"
    assert main(["init", "--config", str(path)]) == 0
    assert "clickhouse:" in path.read_text()
    assert main(["init", "--config", str(path)]) == 1
    assert "exists" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["run", "check", "serve"])
def test_command_without_configuration_points_to_init(command, tmp_path, capsys):
    assert main([command, "--config", str(tmp_path / "dactopus.yaml")]) == 1
    assert "dactopus init" in capsys.readouterr().err


def _sample(tmp_path, library=FIXTURE_LIBRARY):
    """The sample configuration (GA4 and Shopify) over a library checkout."""
    assert "version: v0.7.1" in config.SAMPLE
    path = tmp_path / "dactopus.yaml"
    path.write_text(config.SAMPLE.replace("version: v0.7.1", f"path: {library}"))
    return path


@pytest.mark.parametrize("command", ["run", "check", "serve"])
def test_vars_are_checked_against_the_library_before_connecting(
    command, tmp_path, monkeypatch, capsys
):
    path = _sample(tmp_path)
    path.write_text(path.read_text().replace("ga4_lookback_days: 3", "ga4_input_database: x"))
    monkeypatch.setattr(cli.clickhouse, "connect", lambda settings: pytest.fail("connected"))
    monkeypatch.setattr(cli.serve, "serve", lambda cfg, root: pytest.fail("served"))
    assert main([command, "--config", str(path)]) == 1
    assert "packages.ga4.vars: remove ga4_input_database" in capsys.readouterr().err


def test_a_library_without_declarations_names_the_release(tmp_path, capsys):
    bare = tmp_path / "library"
    (bare / "sources").mkdir(parents=True)
    assert main(["check", "--config", str(_sample(tmp_path, bare))]) == 1
    assert (
        "dactopus check: ga4: the models library has no sources/ga4/deployment.yml; "
        "update the checkout at models.path to v0.6.0 or later." in capsys.readouterr().err
    )


def test_clickhouse_error_names_the_step(tmp_path, monkeypatch, capsys):
    path = _sample(tmp_path)

    def refuse(client, library, package, database):
        raise DatabaseError("Code: 497. Not enough privileges")

    monkeypatch.setattr(cli.shopify, "access_token", lambda settings: "t")
    monkeypatch.setattr(cli.shopify, "check_time_zone", lambda shop, token, zone: None)
    monkeypatch.setattr(cli.clickhouse, "connect", lambda settings: object())
    monkeypatch.setattr(cli.build, "check_zone", lambda *args: None)
    monkeypatch.setattr(cli.clickhouse, "create_input", refuse)
    assert main(["run", "--config", str(path)]) == 1
    err = capsys.readouterr().err
    assert "ga4: creating the input tables in dactopus_raw: ClickHouse refused it" in err
    assert "Not enough privileges" in err
    assert "run again" in err


def test_a_refused_count_of_the_days_says_nothing_was_loaded(tmp_path, monkeypatch, capsys):
    path = _sample(tmp_path)

    def refuse(*args):
        raise DatabaseError("Code: 497. Not enough privileges")

    monkeypatch.setattr(cli.shopify, "access_token", lambda settings: "t")
    monkeypatch.setattr(cli.shopify, "check_time_zone", lambda shop, token, zone: None)
    monkeypatch.setattr(cli.clickhouse, "connect", lambda settings: object())
    monkeypatch.setattr(cli.build, "check_zone", refuse)
    assert main(["run", "--config", str(path)]) == 1
    err = capsys.readouterr().err
    assert "build: counting the days of the tables in dactopus: ClickHouse refused it" in err
    assert (
        "check that the ClickHouse user can read dactopus and run again; nothing has been " in err
    )


@pytest.mark.parametrize(
    ("argv", "names"),
    [
        (["run"], None),
        (["run", "--full-refresh"], []),
        (["run", "--full-refresh", "shopify"], ["shopify"]),
        (["run", "--full-refresh", "ga4", "shopify"], ["ga4", "shopify"]),
    ],
)
def test_run_parses_the_full_refresh_flag(argv, names):
    assert cli._parser().parse_args(argv).full_refresh == names


@pytest.mark.parametrize("command", ["check", "serve"])
def test_only_run_takes_the_full_refresh_flag(command):
    with pytest.raises(SystemExit) as exit_info:
        cli._parser().parse_args([command, "--full-refresh"])
    assert exit_info.value.code == 2


def test_full_refresh_names_the_packages_to_rebuild(tmp_path):
    cfg = config.load(_sample(tmp_path))
    assert cli.full_refresh(None, cfg) == set()
    assert cli.full_refresh([], cfg) == {"ga4", "shopify"}
    assert cli.full_refresh(["shopify"], cfg) == {"shopify"}


def test_full_refresh_of_an_unknown_package_is_refused_before_anything_runs(
    tmp_path, monkeypatch, capsys
):
    path = _sample(tmp_path)
    monkeypatch.setattr(cli.clickhouse, "connect", lambda settings: pytest.fail("connected"))
    assert main(["run", "--config", str(path), "--full-refresh", "woocommerce"]) == 1
    assert (
        "dactopus run: --full-refresh: woocommerce not in the configuration; "
        "configured: ga4, shopify." in capsys.readouterr().err
    )


def test_run_rebuilds_the_named_packages_after_the_usual_load(tmp_path, monkeypatch):
    """The flag reaches dbt for the named packages only; the loads run as always."""
    path = _sample(tmp_path)
    steps = []
    monkeypatch.setattr(cli.clickhouse, "connect", lambda settings: object())
    monkeypatch.setattr(
        cli.build, "check_zone", lambda client, cfg, root, refresh: steps.append(("zone", refresh))
    )
    monkeypatch.setattr(
        cli.clickhouse, "create_input", lambda *args: steps.append(("input", args[2]))
    )
    monkeypatch.setattr(cli.ga4, "BigQueryExport", lambda settings: object())
    monkeypatch.setattr(cli.ga4, "lookback_days", lambda settings, root: 3)
    monkeypatch.setattr(cli.ga4, "load", lambda *args: steps.append(("load", "ga4")))
    monkeypatch.setattr(cli.shopify, "access_token", lambda settings: "t")
    monkeypatch.setattr(
        cli.shopify,
        "check_time_zone",
        lambda shop, token, zone: steps.append(("shop zone", zone)),
    )
    monkeypatch.setattr(cli.shopify, "graphql", lambda shop, token: object())
    monkeypatch.setattr(cli.shopify, "load", lambda *args: steps.append(("load", "shopify")))
    monkeypatch.setattr(
        cli.build,
        "build",
        lambda cfg, root, package, full_refresh: steps.append(("build", package, full_refresh)),
    )
    monkeypatch.setattr(cli, "_report", lambda cfg, root, client: 0)
    assert main(["run", "--config", str(path), "--full-refresh", "shopify"]) == 0
    assert steps == [
        ("shop zone", "UTC"),
        ("zone", {"shopify"}),
        ("input", "ga4"),
        ("input", "shopify"),
        ("load", "ga4"),
        ("load", "shopify"),
        ("build", "ga4", False),
        ("build", "shopify", True),
    ]


def _fixture(client, library, package, database):
    """The package's input tables with the library's hand-written rows."""
    from dactopus import clickhouse as ch

    ch.create_input(client, library, package, database)
    script = (library / "tests" / package / "fixture.sql").read_text()
    for statement in ch._COMMENT.sub("", script).split(";"):
        if statement.strip():
            client.command(statement, parameters={"db": database})


def test_check_builds_the_report_over_the_library_fixtures(
    clickhouse, models_library, tmp_path, capsys
):
    """End to end on a server: dbt test and source freshness through the runner, then the report.

    The library's GA4 rows are from March 2025, so GA4 freshness is an error
    and the exit code is 1; the Shopify rows are made current.
    """
    from dactopus import build

    client, database = clickhouse
    raw = f"{database}_raw"
    path = tmp_path / "dactopus.yaml"
    path.write_text(
        config.SAMPLE.replace("dactopus_raw", raw)
        .replace("database: dactopus\n", f"database: {database}\n")
        .replace("timezone: UTC", "timezone: America/New_York")
    )
    cfg = config.load(path)
    try:
        for package in ("ga4", "shopify"):
            _fixture(client, models_library, package, raw)
        client.command(
            f"ALTER TABLE `{raw}`.orders UPDATE loaded_at = now64(6) WHERE 1 "
            "SETTINGS mutations_sync = 2"
        )
        for package in ("ga4", "shopify"):
            build.build(cfg, models_library, package)
        assert main(["check", "--config", str(path)]) == 1
    finally:
        client.command(f"DROP DATABASE IF EXISTS `{raw}`")
    out = capsys.readouterr()
    text = (cfg.workdir / "report.md").read_text()
    assert text in out.out
    assert "The shopify input is current" in text
    assert "error: No new rows of events (ga4)" in text
    assert "- fail:" not in text
    assert "| Day | events | sessions | purchases |" in text
    assert "| Day | orders | refunds |" in text
    assert "- pass: orders.order_id: No order_id repeats in orders." in text


def _rows(client, database, table):
    return client.query(f"SELECT * FROM `{database}`.`{table}` ORDER BY ALL").result_rows


def test_full_refresh_rebuilds_the_named_package_from_the_input(
    clickhouse, models_library, tmp_path, monkeypatch
):
    """`run --full-refresh shopify` over the library fixtures, with the loads stubbed out.

    Shopify is rebuilt with `--full-refresh` and GA4 is not; the rebuilt
    tables equal, row for row, those of the incremental run before.
    """
    import json

    from dactopus import build

    client, database = clickhouse
    raw = f"{database}_raw"
    path = tmp_path / "dactopus.yaml"
    path.write_text(
        config.SAMPLE.replace("dactopus_raw", raw).replace(
            "database: dactopus\n", f"database: {database}\n"
        )
    )
    cfg = config.load(path)
    monkeypatch.setattr(cli.ga4, "BigQueryExport", lambda settings: object())
    monkeypatch.setattr(cli.ga4, "load", lambda *args: None)
    monkeypatch.setattr(cli.shopify, "access_token", lambda settings: "t")
    monkeypatch.setattr(cli.shopify, "check_time_zone", lambda shop, token, zone: None)
    monkeypatch.setattr(cli.shopify, "graphql", lambda shop, token: object())
    monkeypatch.setattr(cli.shopify, "load", lambda *args: None)
    tables = {
        "ga4": ("events", "sessions", "purchases"),
        "shopify": ("orders", "order_lines", "refunds", "sales_events"),
    }
    try:
        for package in tables:
            _fixture(client, models_library, package, raw)
        for _ in range(2):  # the first build, then an incremental run over the same input
            for package in tables:
                build.build(cfg, models_library, package)
        before = {t: _rows(client, database, t) for ts in tables.values() for t in ts}
        main(["run", "--config", str(path), "--full-refresh", "shopify"])
        after = {t: _rows(client, database, t) for ts in tables.values() for t in ts}
        args = {
            p: json.loads((build.target(cfg, p) / "run_results.json").read_text())["args"]
            for p in tables
        }
    finally:
        client.command(f"DROP DATABASE IF EXISTS `{raw}`")
    assert args["shopify"]["full_refresh"] is True
    assert "full_refresh" not in args["ga4"]
    assert all(before[t] for t in before)
    assert after == before
