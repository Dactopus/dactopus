import pytest
from clickhouse_connect.driver.exceptions import DatabaseError

from dactopus import cli, config
from dactopus.cli import COMMANDS, main


def test_commands_are_the_public_surface():
    assert list(COMMANDS) == ["init", "run", "check", "serve"]


@pytest.mark.parametrize("command", ["check", "serve"])
def test_command_is_declared_but_not_built(command, capsys):
    assert main([command]) == 2
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


def test_run_without_configuration_points_to_init(tmp_path, capsys):
    assert main(["run", "--config", str(tmp_path / "dactopus.yaml")]) == 1
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
