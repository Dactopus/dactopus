import json

import yaml

from dactopus import build, config


def _config(tmp_path):
    path = tmp_path / "dactopus.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "clickhouse": {
                    "url": "http://loader@db.local:18123",
                    "password_env": "CH_PASSWORD",
                    "input_database": "raw",
                    "database": "canonical",
                },
                "models": {"version": "v0.5.3"},
                "packages": {"shopify": {"shop": "s", "vars": {"shopify_timezone": "UTC"}}},
            }
        )
    )
    return config.load(path, environ={"CH_PASSWORD": "s3cret"})


def _library(tmp_path):
    project = tmp_path / "lib/sources/shopify"
    project.mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: dactopus_shopify\nprofile: dactopus\n")
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


def test_runner_sets_the_input_database(tmp_path):
    cfg = _config(tmp_path)
    argv, _ = build.command(cfg, _library(tmp_path), "shopify")
    variables = json.loads(argv[argv.index("--vars") + 1])
    assert variables == {"shopify_timezone": "UTC", "shopify_input_database": "raw"}
