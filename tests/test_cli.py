import sys
from pathlib import Path

import pytest
from clickhouse_connect.driver.exceptions import DatabaseError

from dactopus import cli, config, library
from dactopus.cli import COMMANDS, main

FIXTURE_LIBRARY = Path(__file__).parent / "fixtures" / "library"


def test_commands_are_the_public_surface():
    assert list(COMMANDS) == ["init", "run", "check", "serve"]


def test_unknown_command_is_refused():
    with pytest.raises(SystemExit) as exit_info:
        main(["load"])
    assert exit_info.value.code == 2


def test_an_unsupported_python_is_refused_with_the_reinstall_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "UNSUPPORTED_PYTHON", sys.version_info[:2])
    path = tmp_path / "dactopus.yaml"
    assert main(["init", "--config", str(path)]) == 1
    assert not path.exists()
    assert "uv tool install --force --python 3.13 dactopus" in capsys.readouterr().err


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
    """The sample configuration (GA4 as website, Shopify as main_store) over a library checkout."""
    assert "version: v0.10.0" in config.SAMPLE
    path = tmp_path / "dactopus.yaml"
    path.write_text(config.SAMPLE.replace("version: v0.10.0", f"path: {library}"))
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
    assert "sources.website.vars: remove ga4_input_database" in capsys.readouterr().err


def test_a_library_before_labels_names_the_release(tmp_path, capsys):
    bare = tmp_path / "library"
    (bare / "sources").mkdir(parents=True)
    assert main(["check", "--config", str(_sample(tmp_path, bare))]) == 1
    assert (
        f"dactopus check: The models library at {bare} has no unions/, which came in v0.9.0 "
        "with the labels of the sources; update the checkout at models.path to v0.10.0 or "
        "later." in capsys.readouterr().err
    )


@pytest.mark.parametrize("command", ["run", "check", "serve"])
def test_a_release_before_the_floor_is_refused_before_anything_runs(
    command, tmp_path, monkeypatch, capsys
):
    path = tmp_path / "dactopus.yaml"
    path.write_text(config.SAMPLE.replace("version: v0.10.0", "version: v0.9.0"))
    monkeypatch.setattr(library, "_download", lambda url: pytest.fail("downloaded"))
    monkeypatch.setattr(cli.clickhouse, "connect", lambda settings: pytest.fail("connected"))
    assert main([command, "--config", str(path)]) == 1
    assert capsys.readouterr().err == (
        f"dactopus {command}: The models library v0.9.0 is older than v0.10.0, the first "
        "the runner reads; set models.version to v0.10.0 or later.\n"
    )


def test_clickhouse_error_names_the_step(tmp_path, monkeypatch, capsys):
    path = _sample(tmp_path)

    def refuse(client, library, package, database):
        raise DatabaseError("Code: 497. Not enough privileges")

    monkeypatch.setattr(cli.shopify, "access_token", lambda settings: "t")
    monkeypatch.setattr(cli.shopify, "check_time_zone", lambda package, token, zone, setting: None)
    monkeypatch.setattr(cli.clickhouse, "connect", lambda settings: object())
    monkeypatch.setattr(cli.build, "check_zone", lambda *args: None)
    monkeypatch.setattr(cli.build, "check_unions", lambda *args: None)
    monkeypatch.setattr(cli.clickhouse, "create_input", refuse)
    assert main(["run", "--config", str(path)]) == 1
    err = capsys.readouterr().err
    assert (
        "website: creating the input tables in dactopus_website_raw: ClickHouse refused it" in err
    )
    assert "Not enough privileges" in err
    assert "run again" in err


def test_a_refused_count_of_the_days_says_nothing_was_loaded(tmp_path, monkeypatch, capsys):
    path = _sample(tmp_path)

    def refuse(*args):
        raise DatabaseError("Code: 497. Not enough privileges")

    monkeypatch.setattr(cli.shopify, "access_token", lambda settings: "t")
    monkeypatch.setattr(cli.shopify, "check_time_zone", lambda package, token, zone, setting: None)
    monkeypatch.setattr(cli.clickhouse, "connect", lambda settings: object())
    monkeypatch.setattr(cli.build, "check_zone", refuse)
    assert main(["run", "--config", str(path)]) == 1
    err = capsys.readouterr().err
    assert "build: counting the days of the sources' tables: ClickHouse refused it" in err
    assert (
        "check that the ClickHouse user can read the databases of the sources and run again; "
        "nothing has been loaded yet." in err
    )


@pytest.mark.parametrize(
    ("argv", "names"),
    [
        (["run"], None),
        (["run", "--full-refresh"], []),
        (["run", "--full-refresh", "main_store"], ["main_store"]),
        (["run", "--full-refresh", "website", "main_store"], ["website", "main_store"]),
    ],
)
def test_run_parses_the_full_refresh_flag(argv, names):
    assert cli._parser().parse_args(argv).full_refresh == names


@pytest.mark.parametrize("command", ["check", "serve"])
def test_only_run_takes_the_full_refresh_flag(command):
    with pytest.raises(SystemExit) as exit_info:
        cli._parser().parse_args([command, "--full-refresh"])
    assert exit_info.value.code == 2


def test_full_refresh_names_the_sources_to_rebuild(tmp_path):
    cfg = config.load(_sample(tmp_path))
    assert cli.full_refresh(None, cfg) == set()
    assert cli.full_refresh([], cfg) == {"website", "main_store"}
    assert cli.full_refresh(["main_store"], cfg) == {"main_store"}


def test_full_refresh_of_an_unknown_label_is_refused_before_anything_runs(
    tmp_path, monkeypatch, capsys
):
    path = _sample(tmp_path)
    monkeypatch.setattr(cli.clickhouse, "connect", lambda settings: pytest.fail("connected"))
    assert main(["run", "--config", str(path), "--full-refresh", "shopify"]) == 1
    assert (
        "dactopus run: --full-refresh: shopify not in the configuration; "
        "configured: website, main_store." in capsys.readouterr().err
    )


def _stub_run(monkeypatch, steps):
    """Every step of `run` recorded, none run."""
    monkeypatch.setattr(cli.clickhouse, "connect", lambda settings: object())
    monkeypatch.setattr(
        cli.build, "check_zone", lambda client, cfg, root, refresh: steps.append(("zone", refresh))
    )
    monkeypatch.setattr(
        cli.build, "check_unions", lambda client, cfg, root: steps.append(("unions database",))
    )
    monkeypatch.setattr(
        cli.clickhouse, "create_input", lambda *args: steps.append(("input", args[2], args[3]))
    )
    monkeypatch.setattr(cli.ga4, "BigQueryExport", lambda settings: object())
    monkeypatch.setattr(cli.ga4, "lookback_days", lambda label, variables, root: 3)
    monkeypatch.setattr(cli.ga4, "load", lambda *args: steps.append(("load", "ga4", args[2])))
    monkeypatch.setattr(cli.shopify, "access_token", lambda settings: "t")
    monkeypatch.setattr(
        cli.shopify,
        "check_time_zone",
        lambda package, token, zone, setting: steps.append(("shop zone", zone, setting)),
    )
    monkeypatch.setattr(cli.shopify, "graphql", lambda shop, token: object())
    monkeypatch.setattr(
        cli.shopify, "load", lambda *args: steps.append(("load", "shopify", args[2], args[3]))
    )
    monkeypatch.setattr(
        cli.build,
        "build",
        lambda cfg, root, label, full_refresh=False: steps.append(("build", label, full_refresh)),
    )
    monkeypatch.setattr(cli, "_report", lambda cfg, root, client: 0)


def test_run_loads_only_the_sources_it_has_a_loader_for(tmp_path, monkeypatch):
    """A WooCommerce source is built from the input CDC writes; nothing creates or loads it."""
    path = _sample(tmp_path)
    text = path.read_text()
    lines = text.splitlines()
    at = lines.index("  # europe_store:")
    path.write_text(
        "\n".join([*lines[:at], *(line.replace("  # ", "  ", 1) for line in lines[at:])])
    )
    steps = []
    _stub_run(monkeypatch, steps)
    assert main(["run", "--config", str(path)]) == 0
    assert [step for step in steps if step[0] in ("input", "load")] == [
        ("input", "ga4", "dactopus_website_raw"),
        ("load", "ga4", "dactopus_website_raw"),
        ("input", "shopify", "dactopus_main_store_raw"),
        ("load", "shopify", "dactopus_main_store_raw", "main_store"),
    ]
    assert [step for step in steps if step[0] == "build"] == [
        ("build", "website", False),
        ("build", "main_store", False),
        ("build", "europe_store", False),
        ("build", "union-commerce", False),
        ("build", "union-web_analytics", False),
    ]


def test_run_rebuilds_the_named_sources_after_the_usual_load(tmp_path, monkeypatch):
    """The flag reaches dbt for the named sources only; the loads run as always."""
    path = _sample(tmp_path)
    steps = []
    _stub_run(monkeypatch, steps)
    assert main(["run", "--config", str(path), "--full-refresh", "main_store"]) == 0
    assert steps == [
        ("shop zone", "UTC", "timezone"),
        ("zone", {"main_store"}),
        ("unions database",),
        ("input", "ga4", "dactopus_website_raw"),
        ("load", "ga4", "dactopus_website_raw"),
        ("input", "shopify", "dactopus_main_store_raw"),
        ("load", "shopify", "dactopus_main_store_raw", "main_store"),
        ("build", "website", False),
        ("build", "main_store", True),
        ("build", "union-commerce", False),
        ("build", "union-web_analytics", False),
    ]


def _drop(client, prefix):
    """The databases of the sources of an installation under prefix."""
    for (name,) in client.query(
        "SELECT name FROM system.databases WHERE startsWith(name, {p:String})",
        parameters={"p": f"{prefix}_"},
    ).result_rows:
        client.command(f"DROP DATABASE IF EXISTS `{name}`")


def _fixture(client, library, package, database):
    """The package's input tables with the library's hand-written rows."""
    from dactopus import clickhouse as ch

    ch.create_input(client, library, package, database)
    script = (library / "tests" / package / "fixture.sql").read_text()
    for statement in ch._COMMENT.sub("", script).split(";"):
        if statement.strip():
            client.command(statement, parameters={"db": database})


def test_check_builds_the_report_over_the_library_fixtures(
    clickhouse, models_library, sample, tmp_path, capsys
):
    """End to end on a server: dbt test and source freshness through the runner, then the report.

    The library's GA4 rows are from March 2025, so GA4 freshness is an error
    and the exit code is 1; the Shopify rows are made current.
    """
    from dactopus import build

    client, database = clickhouse
    path = tmp_path / "dactopus.yaml"
    path.write_text(
        sample.replace("database: dactopus\n", f"database: {database}\n").replace(
            "timezone: UTC", "timezone: America/New_York"
        )
    )
    cfg = config.load(path)
    try:
        for source in cfg.sources.values():
            _fixture(client, models_library, source.package, source.input_database)
        raw = cfg.sources["main_store"].input_database
        client.command(
            f"ALTER TABLE `{raw}`.orders UPDATE loaded_at = now64(6) WHERE 1 "
            "SETTINGS mutations_sync = 2"
        )
        for label in cfg.sources:
            build.build(cfg, models_library, label)
        for union in library.unions_of(cfg, models_library):
            build.build(cfg, models_library, build.union_key(union))
        assert main(["check", "--config", str(path)]) == 1
    finally:
        _drop(client, database)
    out = capsys.readouterr()
    text = (cfg.workdir / "report.md").read_text()
    assert text in out.out
    assert "- main_store: The input is current" in text
    assert "error: website: No new rows of events" in text
    assert "- fail:" not in text
    assert "| Day | events | sessions | purchases |" in text
    assert "| Day | orders | refunds |" in text
    assert "- pass: orders.order_id: No order_id repeats in orders." in text
    # The unions' views over the sources' databases were built and tested.
    assert "### the commerce union" in text and "### the web_analytics union" in text
    assert (
        "- pass: orders.source_id: The commerce_union_distinct_source_ids test on "
        "orders.source_id passes." in text.split("### the commerce union")[1]
    )


def _rows(client, database, table):
    return client.query(f"SELECT * FROM `{database}`.`{table}` ORDER BY ALL").result_rows


def test_full_refresh_rebuilds_the_named_package_from_the_input(
    clickhouse, models_library, sample, tmp_path, monkeypatch
):
    """`run --full-refresh main_store` over the library fixtures, with the loads stubbed out.

    Shopify is rebuilt with `--full-refresh` and GA4 is not; the rebuilt
    tables equal, row for row, those of the incremental run before.
    """
    import json

    from dactopus import build

    client, database = clickhouse
    path = tmp_path / "dactopus.yaml"
    path.write_text(sample.replace("database: dactopus\n", f"database: {database}\n"))
    cfg = config.load(path)
    monkeypatch.setattr(cli.ga4, "BigQueryExport", lambda settings: object())
    monkeypatch.setattr(cli.ga4, "load", lambda *args: None)
    monkeypatch.setattr(cli.shopify, "access_token", lambda settings: "t")
    monkeypatch.setattr(cli.shopify, "check_time_zone", lambda package, token, zone, setting: None)
    monkeypatch.setattr(cli.shopify, "graphql", lambda shop, token: object())
    monkeypatch.setattr(cli.shopify, "load", lambda *args: None)
    tables = {
        "website": ("events", "sessions", "purchases"),
        "main_store": ("orders", "order_lines", "refunds", "sales_events"),
    }

    def rows():
        return {
            (label, t): _rows(client, cfg.sources[label].database, t)
            for label, ts in tables.items()
            for t in ts
        }

    try:
        for source in cfg.sources.values():
            _fixture(client, models_library, source.package, source.input_database)
        for _ in range(2):  # the first build, then an incremental run over the same input
            for label in tables:
                build.build(cfg, models_library, label)
        before = rows()
        main(["run", "--config", str(path), "--full-refresh", "main_store"])
        after = rows()
        args = {
            label: json.loads((build.target(cfg, label) / "run_results.json").read_text())["args"]
            for label in tables
        }
    finally:
        _drop(client, database)
    assert args["main_store"]["full_refresh"] is True
    assert "full_refresh" not in args["website"]
    assert all(before[t] for t in before)
    assert after == before


def test_a_source_dbt_cannot_build_does_not_stop_the_others(tmp_path, monkeypatch, capsys):
    """The report is written and the run exits with 1, from the error the build left."""
    path = _sample(tmp_path)
    steps = []
    _stub_run(monkeypatch, steps)

    def build(cfg, root, label, full_refresh=False):
        steps.append(("build", label))
        if label == "website":
            raise cli.build.BuildError("build: dbt build of website could not run (exit 2): no.")

    monkeypatch.setattr(cli.build, "build", build)
    monkeypatch.setattr(cli, "_report", lambda cfg, root, client: steps.append("report") or 1)
    assert main(["run", "--config", str(path)]) == 1
    assert steps[-5:] == [
        ("build", "website"),
        ("build", "main_store"),
        ("build", "union-commerce"),
        ("build", "union-web_analytics"),
        "report",
    ]
    assert (
        "dactopus: build: dbt build of website could not run (exit 2): no."
        in capsys.readouterr().err
    )
