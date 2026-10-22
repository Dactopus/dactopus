import pytest
from clickhouse_connect.driver.exceptions import DatabaseError

from dactopus import cli, config
from dactopus.cli import COMMANDS, main


def test_commands_are_the_public_surface():
    assert list(COMMANDS) == ["init", "run", "check", "serve"]


def test_serve_is_declared_but_not_built(capsys):
    assert main(["serve", "--config", "nowhere.yaml"]) == 2
    assert "not implemented yet" in capsys.readouterr().err


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


@pytest.mark.parametrize("command", ["run", "check"])
def test_command_without_configuration_points_to_init(command, tmp_path, capsys):
    assert main([command, "--config", str(tmp_path / "dactopus.yaml")]) == 1
    assert "dactopus init" in capsys.readouterr().err


def test_clickhouse_error_names_the_step(tmp_path, monkeypatch, capsys):
    path = tmp_path / "dactopus.yaml"
    path.write_text(config.SAMPLE.replace("version: v0.4.1", f"path: {tmp_path}"))
    (tmp_path / "sources").mkdir()

    def refuse(client, library, package, database):
        raise DatabaseError("Code: 497. Not enough privileges")

    monkeypatch.setattr(cli.clickhouse, "connect", lambda settings: object())
    monkeypatch.setattr(cli.clickhouse, "create_input", refuse)
    assert main(["run", "--config", str(path)]) == 1
    err = capsys.readouterr().err
    assert "ga4: creating the input tables in dactopus_raw: ClickHouse refused it" in err
    assert "Not enough privileges" in err
    assert "run again" in err


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
        .replace("shopify_timezone: UTC", "shopify_timezone: America/New_York")
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
