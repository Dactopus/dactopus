import pytest

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
