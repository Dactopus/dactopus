import pytest

from dactopus.cli import COMMANDS, main


def test_commands_are_the_public_surface():
    assert list(COMMANDS) == ["init", "run", "check", "serve"]


@pytest.mark.parametrize("command", COMMANDS)
def test_command_is_declared_but_not_built(command, capsys):
    assert main([command]) == 2
    assert "not implemented yet" in capsys.readouterr().err


def test_unknown_command_is_refused():
    with pytest.raises(SystemExit) as exit_info:
        main(["load"])
    assert exit_info.value.code == 2
