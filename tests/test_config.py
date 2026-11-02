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
        "clickhouse": {"url": "http://loader@db.local:18123", "database": "dactopus"},
        "timezone": "America/New_York",
        "models": {"version": "v0.9.0"},
        "sources": {
            "website": {
                "package": "ga4",
                "project": "p",
                "dataset": "bigquery-public-data.ga4_sample",
            },
            "main_store": {"package": "shopify", "shop": "dactopus-dev"},
        },
    }


def _load(tmp_path: Path, data: dict) -> config.Config:
    return config.load(_write(tmp_path, data), environ={})


def test_sample_loads(tmp_path):
    path = tmp_path / "dactopus.yaml"
    config.write_sample(path)
    loaded = config.load(path, environ={})
    assert loaded.clickhouse.database == "dactopus"
    assert loaded.models.version == "v0.9.0"
    assert [(s.label, s.package) for s in loaded.sources.values()] == [
        ("website", "ga4"),
        ("main_store", "shopify"),
    ]
    assert loaded.workdir == tmp_path.resolve() / ".dactopus"


def test_sample_sets_the_installation_time_zone_and_no_source_one(tmp_path):
    path = tmp_path / "dactopus.yaml"
    config.write_sample(path)
    loaded = config.load(path, environ={})
    assert loaded.timezone == "UTC"
    assert {s.timezone for s in loaded.sources.values()} == {"UTC"}
    assert not any(s.own_timezone for s in loaded.sources.values())
    assert loaded.sources["main_store"].vars == {}
    assert "dactopus run --full-refresh" in config.SAMPLE


def test_the_commented_source_of_the_sample_loads(tmp_path):
    """The WooCommerce source the sample comments out is a working example."""
    lines = config.SAMPLE.splitlines()
    at = lines.index("  # europe_store:")
    text = "\n".join([*lines[:at], *(line.replace("  # ", "  ", 1) for line in lines[at:])])
    path = tmp_path / "dactopus.yaml"
    path.write_text(text)
    woo = config.load(path, environ={}).sources["europe_store"]
    assert (woo.package, woo.timezone, woo.input_database, woo.loader) == (
        "woocommerce",
        "Europe/Berlin",
        "woocommerce_raw",
        None,
    )
    assert woo.vars == {"woocommerce_currency": "EUR"}


def test_each_source_has_databases_under_the_prefix(tmp_path):
    loaded = _load(tmp_path, _base())
    website, store = loaded.sources["website"], loaded.sources["main_store"]
    assert (website.database, website.input_database) == (
        "dactopus_website",
        "dactopus_website_raw",
    )
    assert (store.database, store.input_database) == (
        "dactopus_main_store",
        "dactopus_main_store_raw",
    )


def test_a_source_names_its_input_database_and_time_zone(tmp_path):
    data = _base()
    data["sources"]["europe_store"] = {
        "package": "woocommerce",
        "timezone": "Europe/Berlin",
        "input_database": "woocommerce_raw",
        "vars": {"woocommerce_currency": "EUR"},
    }
    loaded = _load(tmp_path, data)
    woo = loaded.sources["europe_store"]
    assert (woo.database, woo.input_database) == ("dactopus_europe_store", "woocommerce_raw")
    assert (woo.timezone, woo.own_timezone) == ("Europe/Berlin", True)
    assert (loaded.sources["website"].timezone, loaded.sources["website"].own_timezone) == (
        "America/New_York",
        False,
    )


def test_loaders_belong_to_their_packages(tmp_path):
    loaded = _load(tmp_path, _base())
    website, store = loaded.sources["website"].loader, loaded.sources["main_store"].loader
    assert isinstance(website, config.GA4) and website.label == "website"
    assert isinstance(store, config.Shopify) and store.label == "main_store"
    assert store.client_id_env == "SHOPIFY_CLIENT_ID"


@pytest.mark.parametrize(
    ("sources", "message"),
    [
        (None, "sources: missing"),
        ({}, "sources: expected a mapping of labels to sources; name at least one"),
        ({"Shop": {"package": "shopify", "shop": "s"}}, "the label 'Shop' is not 1 to 64"),
        ({"a" * 65: {"package": "shopify", "shop": "s"}}, "is not 1 to 64"),
        ({"shop-us": {"package": "shopify", "shop": "s"}}, "the label 'shop-us'"),
        ({"main_store": {"shop": "s"}}, "sources.main_store.package: missing"),
        (
            {"main_store": {"package": "../x"}},
            "sources.main_store.package: '../x' is not a package",
        ),
        ({"main_store": {"package": "shopify"}}, "sources.main_store.shop: missing"),
        (
            {"woo": {"package": "woocommerce", "shop": "s"}},
            "sources.woo: unknown key shop; allowed: input_database, package, timezone, vars",
        ),
        ({"woo": {"package": "woocommerce", "vars": []}}, "sources.woo.vars: expected a mapping"),
        (
            {"woo": {"package": "woocommerce", "timezone": "Mars/Olympus"}},
            "sources.woo.timezone: 'Mars/Olympus' is not a time zone",
        ),
        (
            {"woo": {"package": "woocommerce", "input_database": "a.b"}},
            "sources.woo.input_database: 'a.b' is not a plain database name",
        ),
    ],
)
def test_sources_are_checked(tmp_path, sources, message):
    data = _base()
    if sources is None:
        del data["sources"]
    else:
        data["sources"] = sources
    with pytest.raises(ConfigError, match=message):
        _load(tmp_path, data)


def test_a_canonical_database_is_no_input_database(tmp_path):
    """A label ending in _raw has the database another source's input defaults to."""
    data = _base()
    data["sources"]["main_store_raw"] = {"package": "woocommerce"}
    with pytest.raises(
        ConfigError,
        match="sources.main_store: its input database dactopus_main_store_raw"
        " holds the canonical tables of main_store_raw",
    ):
        _load(tmp_path, data)


def test_no_input_database_is_the_unions_database(tmp_path):
    data = _base()
    data["sources"]["main_store"]["input_database"] = "dactopus"
    with pytest.raises(ConfigError, match="its input database dactopus is clickhouse.database"):
        _load(tmp_path, data)


def test_two_sources_of_one_package_do_not_share_an_input_database(tmp_path):
    data = _base()
    data["sources"]["main_store"]["input_database"] = "shopify_raw"
    data["sources"]["outlet_store"] = {
        "package": "shopify",
        "shop": "eu",
        "input_database": "shopify_raw",
    }
    with pytest.raises(
        ConfigError, match="sources.outlet_store: main_store reads the same input database"
    ):
        _load(tmp_path, data)


def test_sources_of_different_packages_may_share_an_input_database(tmp_path):
    """An installation of 0.2 keeps its input in one database (dactopus_raw)."""
    data = _base()
    data["sources"]["website"]["input_database"] = "dactopus_raw"
    data["sources"]["main_store"]["input_database"] = "dactopus_raw"
    loaded = _load(tmp_path, data)
    assert {s.input_database for s in loaded.sources.values()} == {"dactopus_raw"}


@pytest.mark.parametrize(
    ("zone", "message"),
    [
        (None, "timezone: missing"),
        ("Mars/Olympus", "timezone: 'Mars/Olympus' is not a time zone"),
        ("../etc/passwd", "is not a time zone"),
        # A file on a case-insensitive disk, not a name Shopify answers.
        ("america/new_york", "'america/new_york' is not a time zone"),
    ],
)
def test_installation_time_zone_is_checked(tmp_path, zone, message):
    data = _base()
    if zone is None:
        del data["timezone"]
    else:
        data["timezone"] = zone
    with pytest.raises(ConfigError, match=message):
        _load(tmp_path, data)


@pytest.mark.parametrize("zone", ["America/New_York", "Europe/Kyiv"])
def test_time_zone_is_known_without_the_machine_database(tmp_path, no_system_zones, zone):
    data = _base()
    data["timezone"] = zone
    assert _load(tmp_path, data).timezone == zone


def test_the_settings_of_0_2_are_refused(tmp_path):
    data = _base()
    data["store"] = {"timezone": "UTC"}
    with pytest.raises(ConfigError, match="the file: unknown key store"):
        _load(tmp_path, data)
    data = _base()
    data["clickhouse"]["input_database"] = "dactopus_raw"
    with pytest.raises(ConfigError, match="clickhouse: unknown key input_database"):
        _load(tmp_path, data)


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
