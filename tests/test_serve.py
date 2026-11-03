from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
import yaml
from conftest import drop_sources, load_fixture
from dactopus_ossie_clickhouse import load_model

from dactopus import config, serve
from dactopus.cli import main
from dactopus.serve import ServeError


def _config(
    tmp_path: Path,
    url: str,
    packages=("ga4", "shopify"),
    environ=None,
    database="canonical",
    serve_section=None,
) -> config.Config:
    all_packages = {
        "ga4": {"package": "ga4", "project": "p", "dataset": "bigquery-public-data.ga4_sample"},
        "shopify": {"package": "shopify", "shop": "dactopus-dev"},
    }
    clickhouse = {"url": url, "database": database}
    if environ and serve_section is None:
        clickhouse["password_env"] = "CLICKHOUSE_PASSWORD"
    data = {
        "clickhouse": clickhouse,
        "timezone": "America/New_York",
        "models": {"version": "v0.10.0"},
        "sources": {name: all_packages[name] for name in packages},
    }
    if serve_section is not None:
        data["serve"] = serve_section
    path = tmp_path / "dactopus.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return config.load(path, environ=environ or {}, serving=True)


FIXTURE_LIBRARY = Path(__file__).parent / "fixtures" / "library"

MODEL_FILES = ["entities/commerce.yaml", "entities/web_analytics.yaml"]


def _library(tmp_path: Path, files=None, ga4_model=None) -> Path:
    """The declarations of the fixture library, and empty model files."""
    root = tmp_path / "library"
    for package in ("ga4", "shopify"):
        declared = FIXTURE_LIBRARY / "sources" / package / "deployment.yml"
        (root / "sources" / package).mkdir(parents=True)
        text = declared.read_text()
        if package == "ga4" and ga4_model is not None:
            text = text.replace("model: entities/web_analytics.yaml", f"model: {ga4_model}")
        (root / "sources" / package / "deployment.yml").write_text(text)
    for union in ("commerce", "web_analytics"):
        (root / "unions" / union).mkdir(parents=True)
        declared = FIXTURE_LIBRARY / "unions" / union / "deployment.yml"
        (root / "unions" / union / "deployment.yml").write_text(declared.read_text())
    for name in MODEL_FILES if files is None else files:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("")
    return root


@pytest.mark.parametrize(
    ("packages", "files"),
    [
        (("ga4",), ["entities/web_analytics.yaml"]),
        (("shopify",), ["entities/commerce.yaml"]),
        (("ga4", "shopify"), ["entities/commerce.yaml", "entities/web_analytics.yaml"]),
    ],
)
def test_files_are_the_models_of_the_unions_that_have_sources(tmp_path, packages, files):
    cfg = _config(tmp_path, "http://default@127.0.0.1:8123", packages)
    root = _library(tmp_path)
    assert serve.model_files(cfg, root) == [root / name for name in files]


def test_packages_that_declare_one_model_are_served_by_its_union_once(tmp_path):
    cfg = _config(tmp_path, "http://default@127.0.0.1:8123")
    root = _library(tmp_path, ["entities/commerce.yaml"], ga4_model="entities/commerce.yaml")
    assert serve.model_files(cfg, root) == [root / "entities/commerce.yaml"]


def test_missing_model_file_names_the_union(tmp_path):
    cfg = _config(tmp_path, "http://default@127.0.0.1:8123")
    root = _library(tmp_path, ["entities/web_analytics.yaml"])
    with pytest.raises(ServeError, match="commerce: the models library has no entities/commerce"):
        serve.model_files(cfg, root)


def test_url_reads_the_unions_database(tmp_path):
    cfg = _config(tmp_path, "http://loader@db.local:18123")
    assert serve.url(cfg) == "http://loader@db.local:18123/canonical"


def test_url_quotes_the_credentials(tmp_path):
    secret = "p@ss:w/rd?#%"
    cfg = _config(tmp_path, "https://us%20er@db.local", environ={"CLICKHOUSE_PASSWORD": secret})
    parts = urlsplit(serve.url(cfg))
    assert (parts.scheme, parts.hostname, parts.port, parts.path) == (
        "https",
        "db.local",
        8443,
        "/canonical",
    )
    assert (unquote(parts.username), unquote(parts.password)) == ("us er", secret)


def test_url_names_the_serve_user(tmp_path):
    cfg = _config(
        tmp_path,
        "https://loader@db.local:9443",
        environ={"AGENT_PASSWORD": "re@d"},
        serve_section={"user": "agent", "password_env": "AGENT_PASSWORD"},
    )
    assert serve.url(cfg) == "https://agent:re%40d@db.local:9443/canonical"


def test_url_brackets_an_ipv6_host(tmp_path):
    cfg = _config(tmp_path, "http://default@[::1]:8123")
    assert serve.url(cfg) == "http://default@[::1]:8123/canonical"


def test_tls_follows_the_scheme_on_any_port(tmp_path):
    cfg = _config(tmp_path, "https://default@db.local:9443")
    assert serve.url(cfg) == "https://default@db.local:9443/canonical"


def test_serve_hands_the_files_and_url_to_the_executor(tmp_path, monkeypatch):
    from dactopus_ossie_clickhouse import cli as executor

    cfg = _config(tmp_path, "http://default@127.0.0.1:8123")
    root = _library(tmp_path)
    calls = []
    monkeypatch.setattr(executor, "main", lambda argv: calls.append(argv) or 0)
    assert serve.serve(cfg, root) == 0
    assert calls == [
        [
            "serve",
            str(root / "entities/commerce.yaml"),
            str(root / "entities/web_analytics.yaml"),
            "--url",
            "http://default@127.0.0.1:8123/canonical",
            "--time-zone",
            "America/New_York",
        ]
    ]


def test_serve_error_goes_to_stderr(tmp_path, monkeypatch, capsys):
    path = tmp_path / "dactopus.yaml"
    assert "version: v0.10.0" in config.SAMPLE
    root = _library(tmp_path, files=[])
    path.write_text(config.SAMPLE.replace("version: v0.10.0", f"path: {root}"))
    assert main(["serve", "--config", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "dactopus serve: commerce: the models library has no" in captured.err


@pytest.mark.parametrize("package", ["ga4", "shopify"])
def test_model_of_each_package_loads(tmp_path, models_library, package):
    cfg = _config(tmp_path, "http://default@127.0.0.1:8123", (package,))
    load_model(*serve.model_files(cfg, models_library))


def test_models_of_both_packages_load_as_one(tmp_path, models_library):
    cfg = _config(tmp_path, "http://default@127.0.0.1:8123")
    load_model(*serve.model_files(cfg, models_library))


# The smallest model the executor serves: one table of the canonical database.
_MODEL = """\
version: "0.2.0.dev0"
name: probe
datasets:
  - name: orders
    source: orders
    primary_key: [order_id]
    fields:
      - name: order_id
        expression: {dialects: [{dialect: ANSI_SQL, expression: order_id}]}
metrics:
  - name: orders
    expression: {dialects: [{dialect: ANSI_SQL, expression: COUNT(orders.order_id)}]}
"""


def test_serve_user_with_read_grants_is_enough(tmp_path, clickhouse, server_url):
    """The executor connects with the URL serve builds, as a serve user whose
    password needs quoting and who may only read the canonical database, and
    answers a question there."""
    from dactopus_ossie_clickhouse.executor import Executor, connect
    from dactopus_ossie_clickhouse.planner import Query

    server, database = clickhouse
    user, secret = f"{database}_agent", "p@ss:w/rd?#%"
    server.command(f"CREATE DATABASE `{database}`")
    server.command(
        f"CREATE TABLE `{database}`.orders (order_id Int64) ENGINE = MergeTree ORDER BY order_id"
    )
    server.command(f"INSERT INTO `{database}`.orders VALUES (1), (2)")
    server.command(f"CREATE USER `{user}` IDENTIFIED BY '{secret}'")
    try:
        server.command(f"GRANT SELECT ON `{database}`.* TO `{user}`")
        cfg = _config(
            tmp_path,
            server_url,
            # Only the serve user's password: the agent's environment has no other.
            environ={"AGENT_PASSWORD": secret},
            database=database,
            serve_section={"user": user, "password_env": "AGENT_PASSWORD"},
        )
        client = connect(serve.url(cfg))
        found = client.query("SELECT currentUser(), currentDatabase()")
        assert found.result_rows == [(user, database)]
        (tmp_path / "probe.yaml").write_text(_MODEL)
        executor = Executor(client, load_model(tmp_path / "probe.yaml"))
        assert executor.execute(Query(("orders",), (), (), (), None)).rows == [(2,)]
    finally:
        server.command(f"DROP USER IF EXISTS `{user}`")


def test_the_serve_user_reads_the_unions_and_not_the_sources(
    tmp_path, clickhouse, models_library, sample, server_url
):
    """The unions the runner builds from the library read the sources as their owner
    (SQL SECURITY DEFINER, v0.10.0; ClickHouse 26.9.27, 2026-11-03), so a user with
    SELECT on clickhouse.database alone is served and reads no source database."""
    from clickhouse_connect.driver.exceptions import DatabaseError
    from dactopus_ossie_clickhouse.executor import Executor, connect

    from dactopus import build, library

    server, database = clickhouse
    user, secret = f"{database}_agent", "agent-secret"
    path = tmp_path / "dactopus.yaml"
    path.write_text(sample.replace("database: dactopus\n", f"database: {database}\n"))
    cfg = config.load(path)
    try:
        for source in cfg.sources.values():
            load_fixture(server, models_library, source.package, source.input_database)
        for label in cfg.sources:
            build.build(cfg, models_library, label)
        unions = library.unions_of(cfg, models_library)
        for union in unions:
            build.build(cfg, models_library, build.union_key(union))

        views = [
            name
            for (name,) in server.query(
                "SELECT name FROM system.tables WHERE database = {db:String} AND engine = 'View'",
                parameters={"db": database},
            ).result_rows
        ]
        assert "orders" in views and "events" in views
        for view in views:
            shown = server.query(f"SHOW CREATE TABLE `{database}`.`{view}`").result_rows[0][0]
            assert "SQL SECURITY DEFINER" in shown, view

        server.command(f"CREATE USER `{user}` IDENTIFIED BY '{secret}'")
        server.command(f"GRANT SELECT ON `{database}`.* TO `{user}`")
        (tmp_path / "serve").mkdir()
        served = _config(
            tmp_path / "serve",
            server_url,
            environ={"AGENT_PASSWORD": secret},
            database=database,
            serve_section={"user": user, "password_env": "AGENT_PASSWORD"},
        )
        client = connect(serve.url(served))
        assert client.query("SELECT currentUser()").result_rows == [(user,)]
        [(orders,)] = client.query("SELECT count() FROM orders").result_rows
        assert orders > 0
        for source in cfg.sources.values():
            table = library.deployment(cfg.models, models_library, source.package).daily[0].table
            with pytest.raises(DatabaseError, match="Not enough privileges"):
                client.query(f"SELECT count() FROM `{source.database}`.`{table}`")

        # The executor leaves out of the model what the user may not read: for this
        # user it leaves out nothing of the unions' models.
        for file in serve.model_files(cfg, models_library):
            executor = Executor(client, load_model(file))
            assert [d.name for d in executor.model.datasets] == [
                d.name for d in executor.full_model.datasets
            ], file.name
    finally:
        server.command(f"DROP USER IF EXISTS `{user}`")
        drop_sources(server, database)
