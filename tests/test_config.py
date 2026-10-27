from pathlib import Path

import pytest
import yaml

from dactopus import config
from dactopus.config import ConfigError


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "dactopus.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def _base() -> dict:
    return {
        "clickhouse": {
            "url": "http://loader@db.local:18123",
            "input_database": "raw",
            "database": "canonical",
        },
        "store": {"timezone": "America/New_York"},
        "models": {"version": "v0.7.1"},
        "packages": {
            "ga4": {"project": "p", "dataset": "bigquery-public-data.ga4_sample"},
            "shopify": {"shop": "dactopus-dev"},
        },
    }


def test_sample_loads(tmp_path):
    path = tmp_path / "dactopus.yaml"
    config.write_sample(path)
    loaded = config.load(path, environ={})
    assert loaded.clickhouse.database == "dactopus"
    assert loaded.models.version == "v0.7.1"
    assert list(loaded.packages()) == ["ga4", "shopify"]
    assert loaded.workdir == tmp_path.resolve() / ".dactopus"


def test_sample_sets_the_store_time_zone_and_no_package_one(tmp_path):
    path = tmp_path / "dactopus.yaml"
    config.write_sample(path)
    loaded = config.load(path, environ={})
    assert loaded.store.timezone == "UTC"
    assert loaded.shopify.vars == {}
    assert "dactopus run --full-refresh" in config.SAMPLE


@pytest.mark.parametrize(
    ("store", "message"),
    [
        (None, "store: missing"),
        ({}, "store.timezone: missing"),
        ({"timezone": "Mars/Olympus"}, "store.timezone: 'Mars/Olympus' is not a time zone"),
        ({"timezone": "../etc/passwd"}, "is not a time zone"),
        # A file on a case-insensitive disk, not a name Shopify answers.
        ({"timezone": "america/new_york"}, "'america/new_york' is not a time zone"),
        ({"timezone": "UTC", "zone": "UTC"}, "store: unknown key zone"),
    ],
)
def test_store_time_zone_is_checked(tmp_path, store, message):
    data = _base()
    if store is None:
        del data["store"]
    else:
        data["store"] = store
    with pytest.raises(ConfigError, match=message):
        config.load(_write(tmp_path, data), environ={})


@pytest.mark.parametrize("zone", ["America/New_York", "Europe/Kyiv"])
def test_store_time_zone_is_known_without_the_machine_database(tmp_path, no_system_zones, zone):
    data = _base()
    data["store"] = {"timezone": zone}
    assert config.load(_write(tmp_path, data), environ={}).store.timezone == zone


def test_init_does_not_overwrite(tmp_path):
    path = tmp_path / "dactopus.yaml"
    path.write_text("mine")
    with pytest.raises(ConfigError, match="exists"):
        config.write_sample(path)
    assert path.read_text() == "mine"


def test_connection_from_url_and_environment(tmp_path):
    data = _base()
    data["clickhouse"]["password_env"] = "CH_PASSWORD"
    loaded = config.load(_write(tmp_path, data), environ={"CH_PASSWORD": "s3cret"})
    ch = loaded.clickhouse
    assert (ch.host, ch.port, ch.user, ch.password) == ("db.local", 18123, "loader", "s3cret")
    assert "s3cret" not in repr(loaded)
    assert loaded.shopify.client_id_env == "SHOPIFY_CLIENT_ID"


@pytest.mark.parametrize(
    ("url", "port", "secure"),
    [
        ("http://db.local", 8123, False),
        ("https://db.local", 8443, True),
        ("https://db.local:9443", 9443, True),
    ],
)
def test_https_turns_on_tls(tmp_path, url, port, secure):
    data = _base()
    data["clickhouse"]["url"] = url
    ch = config.load(_write(tmp_path, data), environ={}).clickhouse
    assert (ch.port, ch.secure) == (port, secure)


def test_user_name_is_percent_decoded(tmp_path):
    data = _base()
    data["clickhouse"]["url"] = "https://analyst%40corp@db.local"
    assert config.load(_write(tmp_path, data), environ={}).clickhouse.user == "analyst@corp"


def test_missing_password_variable_is_named(tmp_path):
    data = _base()
    data["clickhouse"]["password_env"] = "CH_PASSWORD"
    with pytest.raises(ConfigError, match="CH_PASSWORD is not set"):
        config.load(_write(tmp_path, data), environ={})


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://db.local:8443", "expected http:// or https://"),
        ("http://user:pw@db.local:8123", "remove the password"),
        ("http://db.local:8123/dactopus", "only the user, host and port"),
        ("http://db.local:84433", "the port is not a number"),
        ("http://db.local:abc", "the port is not a number"),
    ],
)
def test_url_is_refused(tmp_path, url, message):
    data = _base()
    data["clickhouse"]["url"] = url
    with pytest.raises(ConfigError, match=message) as exc_info:
        config.load(_write(tmp_path, data), environ={})
    assert "pw" not in str(exc_info.value)


def test_input_and_canonical_databases_differ(tmp_path):
    data = _base()
    data["clickhouse"]["database"] = "raw"
    with pytest.raises(ConfigError, match="must differ"):
        config.load(_write(tmp_path, data), environ={})


def test_unknown_key_is_refused(tmp_path):
    data = _base()
    data["packages"]["woocommerce"] = {}
    with pytest.raises(ConfigError, match="unknown key woocommerce"):
        config.load(_write(tmp_path, data), environ={})


def test_models_version_or_path(tmp_path):
    data = _base()
    data["models"] = {"version": "v0.5.3", "path": "lib"}
    with pytest.raises(ConfigError, match="either version"):
        config.load(_write(tmp_path, data), environ={})
    data["models"] = {"version": "0.4.1"}
    with pytest.raises(ConfigError, match="not a release tag"):
        config.load(_write(tmp_path, data), environ={})
    (tmp_path / "lib" / "sources").mkdir(parents=True)
    data["models"] = {"path": "lib"}
    loaded = config.load(_write(tmp_path, data), environ={})
    assert loaded.models.path == (tmp_path / "lib").resolve()


def test_missing_file_points_to_init(tmp_path):
    with pytest.raises(ConfigError, match="dactopus init"):
        config.load(tmp_path / "absent.yaml", environ={})


def _with_serve(data: dict) -> dict:
    data["clickhouse"]["password_env"] = "CH_PASSWORD"
    data["serve"] = {"user": "agent", "password_env": "AGENT_PASSWORD"}
    return data


def test_each_command_reads_only_its_password(tmp_path):
    path = _write(tmp_path, _with_serve(_base()))
    run = config.load(path, environ={"CH_PASSWORD": "write"})
    assert (run.clickhouse.password, run.serve.user, run.serve.password) == ("write", "agent", None)
    serving = config.load(path, environ={"AGENT_PASSWORD": "s3cret-r"}, serving=True)
    assert (serving.serve.user, serving.serve.password) == ("agent", "s3cret-r")
    assert "s3cret-r" not in repr(serving)


def test_serve_password_variable_is_named(tmp_path):
    path = _write(tmp_path, _with_serve(_base()))
    with pytest.raises(ConfigError, match="serve.password_env: the environment variable AGENT"):
        config.load(path, environ={"CH_PASSWORD": "write"}, serving=True)


def test_serving_without_serve_section_reads_the_clickhouse_password(tmp_path):
    data = _base()
    data["clickhouse"]["password_env"] = "CH_PASSWORD"
    with pytest.raises(ConfigError, match="clickhouse.password_env"):
        config.load(_write(tmp_path, data), environ={}, serving=True)


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ({"password_env": "AGENT_PASSWORD"}, "serve.user: missing"),
        ({"user": "agent", "url": "http://agent@h"}, "serve: unknown key url"),
        (None, "serve: missing"),
    ],
)
def test_serve_section_is_checked(tmp_path, section, message):
    data = _base()
    data["serve"] = section
    with pytest.raises(ConfigError, match=message):
        config.load(_write(tmp_path, data), environ={})
