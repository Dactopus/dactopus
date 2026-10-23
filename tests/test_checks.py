import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from clickhouse_connect.driver.exceptions import DatabaseError

from dactopus import checks, config, library
from dactopus.build import Misdated

# Output of dbt 1.11.16 with dbt-clickhouse 1.10 over the shopify package of
# dactopus-data-models v0.4.1 and the library's tests/shopify/fixture.sql,
# recorded on 2026-10-22 and cut down to what the checks read:
# - run_results_*.json: metadata, and of each result unique_id, status,
#   message and failures (not the compiled SQL, timing or adapter response);
# - sources_*.json: metadata, and of each result unique_id, status,
#   max_loaded_at, max_loaded_at_time_ago_in_s, criteria and error;
# - manifest.json: metadata; of a test node name, resource_type,
#   test_metadata, column_name, attached_node and depends_on.nodes; of a
#   model node name and resource_type; of a unit test name and model.
# The cases: a clean build (one package warning), dbt test with duplicated
# rows, a build stopped by that failure, a build with a dropped input table,
# and freshness within, past and without its source table. The build stopped
# by a model whose columns changed (`on_schema_change: fail`) was recorded
# on 2026-10-23 with dbt-clickhouse 1.10.3 over v0.5.3, by dropping a column
# of the built `orders`; its nodes are those of the manifest.
FIXTURES = Path(__file__).parent / "fixtures" / "dbt" / "shopify"

# The deployment.yml of the GA4 and Shopify packages of dactopus-data-models
# v0.6.0, and one without vars.input_database.
FIXTURE_LIBRARY = Path(__file__).parent / "fixtures" / "library"

# The day after the latest day of _daily(date(2026, 10, 20), ...), at noon.
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(checks, "now", lambda: NOW)


def _config(tmp_path, *packages, zone="UTC"):
    path = tmp_path / "dactopus.yaml"
    known = {
        "shopify": {"shop": "s"},
        "ga4": {"project": "p", "dataset": "a.b"},
    }
    path.write_text(
        yaml.safe_dump(
            {
                "clickhouse": {
                    "url": "http://db.local:8123",
                    "input_database": "raw",
                    "database": "canonical",
                },
                "store": {"timezone": zone},
                "models": {"path": str(FIXTURE_LIBRARY)},
                "packages": {p: known[p] for p in packages or ("shopify",)},
            }
        )
    )
    return config.load(path, environ={})


def _target(cfg, package="shopify", run_results=None, sources=None):
    target = cfg.workdir / package / "target"
    target.mkdir(parents=True)
    (target / "manifest.json").write_bytes((FIXTURES / "manifest.json").read_bytes())
    if run_results:
        (target / "run_results.json").write_bytes((FIXTURES / run_results).read_bytes())
    if sources:
        (target / "sources.json").write_bytes((FIXTURES / sources).read_bytes())


def _count(findings):
    return {s: sum(1 for f in findings if f.severity == s) for s in checks.SEVERITIES}


def _sentences(findings, severity):
    return [f.sentence for f in findings if f.severity == severity]


def test_a_clean_build_passes_but_for_the_package_warning(tmp_path):
    cfg = _config(tmp_path)
    _target(cfg, run_results="run_results_build_pass.json")
    findings = checks.dbt_results(None, cfg)
    assert _count(findings) == {"pass": 32, "skip": 0, "warn": 1, "fail": 0, "error": 0}
    assert {f.check for f in findings} == {"dbt"}
    assert _sentences(findings, "warn") == [
        "1 row of orders fails the check sales events make net total."
    ]
    assert "order_id is set in every row of orders." in _sentences(findings, "pass")
    assert "The unit test refund_lines_of_one_line_item of refunds passes." in _sentences(
        findings, "pass"
    )
    assert "orders was built." in _sentences(findings, "pass")
    assert not checks.failed(findings)


def test_failing_tests_become_sentences_with_counts(tmp_path):
    cfg = _config(tmp_path)
    _target(cfg, run_results="run_results_test_fail.json")
    findings = checks.dbt_results(None, cfg)
    assert _count(findings) == {"pass": 25, "skip": 0, "warn": 1, "fail": 3, "error": 0}
    assert sorted(_sentences(findings, "fail")) == [
        "1 value of order_id appears in more than one row of orders.",
        "2 rows of order_lines have no matching order_id in orders.",
        "2 values of line_id appear in more than one row of order_lines.",
    ]
    duplicate = next(f for f in findings if f.object == "orders.order_id" and f.severity == "fail")
    assert (duplicate.observed, duplicate.expected) == ("1 rows", "0 rows")
    assert checks.failed(findings)


def test_a_build_stopped_by_a_failure_reports_what_did_not_run(tmp_path):
    cfg = _config(tmp_path)
    _target(cfg, run_results="run_results_build_skipped.json")
    findings = checks.dbt_results(None, cfg)
    counted = _count(findings)
    assert counted["fail"] == 1 and counted["skip"] == 28
    assert "orders was not built because an earlier step failed." in _sentences(findings, "skip")
    assert (
        "The not_null test on orders.order_id did not run because an earlier step failed."
        in _sentences(findings, "skip")
    )


def test_a_model_error_names_the_table_and_dbt_message(tmp_path):
    cfg = _config(tmp_path)
    _target(cfg, run_results="run_results_build_error.json")
    findings = checks.dbt_results(None, cfg)
    [error] = [f for f in findings if f.severity == "error"]
    assert error.object == "orders"
    assert error.sentence.startswith("orders could not be built: Database Error in model orders")


def test_a_model_whose_columns_changed_names_the_full_refresh(tmp_path):
    cfg = _config(tmp_path)
    _target(cfg, run_results="run_results_build_schema_change.json")
    findings = checks.dbt_results(None, cfg)
    [error] = [f for f in findings if f.severity == "error"]
    assert error.object == "orders"
    assert error.sentence == (
        "orders no longer has the columns its model builds (a release of the models "
        "library changed them, or the table was changed by hand); run `dactopus run` "
        "once with `--full-refresh shopify` to rebuild it from the input."
    )
    assert _count(findings)["skip"] == 27
    assert checks.failed(findings)


def test_results_that_ran_nothing_are_left_out(tmp_path):
    cfg = _config(tmp_path)
    _target(cfg)
    results = json.loads((FIXTURES / "run_results_build_pass.json").read_text())
    results["results"].append(
        {"unique_id": "exposure.dactopus_shopify.dashboard", "status": "no-op", "message": None}
    )
    (cfg.workdir / "shopify" / "target" / "run_results.json").write_text(json.dumps(results))
    findings = checks.dbt_results(None, cfg)
    assert _count(findings) == {"pass": 32, "skip": 0, "warn": 1, "fail": 0, "error": 0}


def test_a_ref_in_double_quotes_names_the_table(tmp_path):
    cfg = _config(tmp_path)
    _target(cfg, run_results="run_results_test_fail.json")
    path = cfg.workdir / "shopify" / "target" / "manifest.json"
    manifest = json.loads(path.read_text())
    for node in manifest["nodes"].values():
        metadata = node.get("test_metadata") or {}
        if metadata.get("name") == "relationships":
            metadata["kwargs"]["to"] = 'ref("orders")'
    path.write_text(json.dumps(manifest))
    findings = checks.dbt_results(None, cfg)
    assert "2 rows of order_lines have no matching order_id in orders." in _sentences(
        findings, "fail"
    )


def test_a_package_without_a_run_is_an_error(tmp_path):
    cfg = _config(tmp_path, "ga4", "shopify")
    _target(cfg, run_results="run_results_build_pass.json")
    findings = checks.dbt_results(None, cfg)
    [error] = [f for f in findings if f.severity == "error"]
    assert error.object == "ga4"
    assert "has not been built yet" in error.sentence


def test_freshness_within_the_threshold(tmp_path):
    cfg = _config(tmp_path)
    _target(cfg, sources="sources_pass.json")
    [finding] = checks.freshness(None, cfg)
    assert (finding.check, finding.object, finding.severity) == ("freshness", "orders", "pass")
    assert finding.observed == "latest 2026-10-21 23:29 UTC, a minute ago"
    assert finding.expected == "within 1 day, 3 days at most"
    assert finding.sentence == (
        "The shopify input is current: the latest row of orders is a minute old (2026-10-21)."
    )


def test_freshness_past_the_error_threshold(tmp_path):
    cfg = _config(tmp_path)
    _target(cfg, sources="sources_stale.json")
    [finding] = checks.freshness(None, cfg)
    assert finding.severity == "error"
    assert finding.sentence == (
        "No new rows of orders (shopify) for 18 days, the latest from 2026-10-04; the "
        "package allows 3 days at most. Check that the source still exports and the load runs."
    )


def test_freshness_of_a_missing_input_table(tmp_path):
    cfg = _config(tmp_path)
    _target(cfg, sources="sources_error.json")
    [finding] = checks.freshness(None, cfg)
    assert finding.severity == "error"
    assert "its input table is missing" in finding.sentence


def test_freshness_without_a_run(tmp_path):
    cfg = _config(tmp_path)
    [finding] = checks.freshness(None, cfg)
    assert finding.severity == "error"
    assert "run `dactopus run`" in finding.sentence


@pytest.mark.parametrize(
    ("seconds", "words"),
    [
        (14, "a minute"),
        (90, "a minute"),
        (600, "10 minutes"),
        (7200, "2 hours"),
        (200000, "2 days"),
    ],
)
def test_age_in_words(seconds, words):
    assert checks.ago(seconds) == words


class _Result:
    def __init__(self, rows):
        self.result_rows = rows


class _Client:
    """Answers the two queries of a volume for one table of daily counts."""

    def __init__(self, counts: dict[date, int]):
        self.counts = counts

    def query(self, sql, parameters=None):
        if "missing" in sql:
            raise DatabaseError("Code: 60. Unknown table")
        if sql.startswith("SELECT count()"):
            if not self.counts:
                return _Result([(0, date(1970, 1, 1), date(1970, 1, 1))])
            return _Result([(sum(self.counts.values()), min(self.counts), max(self.counts))])
        return _Result(
            [
                (day, n)
                for day, n in self.counts.items()
                if parameters["end"] - timedelta(days=35) < day <= parameters["end"]
            ]
        )


def _daily(latest, days, gap=None):
    counts = {}
    for i in range(days):
        day = latest - timedelta(days=i)
        if day != gap:
            counts[day] = 10 + i % 7
    return counts


def test_volumes_give_the_last_week_against_past_weeks(tmp_path):
    cfg = _config(tmp_path)
    latest = date(2026, 10, 20)
    client = _Client(_daily(latest, 60))
    volumes = checks.volumes(client, cfg, NOW)
    assert [(v.package, v.table) for v in volumes] == [
        ("shopify", "orders"),
        ("shopify", "refunds"),
    ]
    orders = volumes[0]
    assert (orders.first, orders.latest) == (date(2026, 8, 22), latest)
    assert list(orders.days) == [latest - timedelta(days=i) for i in range(6, -1, -1)]
    assert orders.days[latest] == 10
    # The same weekday a week, two, three and four weeks before had 10 too.
    assert orders.typical[latest] == 10
    assert orders.gaps == []


def test_volumes_of_a_short_history_have_no_typical_values(tmp_path):
    cfg = _config(tmp_path)
    latest = date(2026, 10, 20)
    [orders, _] = checks.volumes(_Client(_daily(latest, 3)), cfg, NOW)
    assert list(orders.days) == [latest - timedelta(days=2), latest - timedelta(days=1), latest]
    assert set(orders.typical.values()) == {None}


def test_a_day_without_rows_in_the_first_table_warns(tmp_path):
    cfg = _config(tmp_path)
    latest = date(2026, 10, 20)
    gap = latest - timedelta(days=3)
    [finding] = checks.rows_per_day(_Client(_daily(latest, 60, gap=gap)), cfg)
    assert (finding.check, finding.object, finding.severity) == ("rows per day", "orders", "warn")
    assert finding.sentence == (
        "orders (shopify) has no rows for 2026-10-17; the other days of that week have 10 to 16."
    )


def test_rows_every_day_passes(tmp_path):
    cfg = _config(tmp_path)
    [finding] = checks.rows_per_day(_Client(_daily(date(2026, 10, 20), 60)), cfg)
    assert finding.severity == "pass"
    assert (
        finding.sentence == "orders (shopify) has rows on every day from 2026-10-14 to 2026-10-20."
    )


def test_an_empty_table_warns(tmp_path):
    cfg = _config(tmp_path)
    [finding] = checks.rows_per_day(_Client({}), cfg)
    assert finding.severity == "warn"
    assert finding.sentence == "orders (shopify) has no rows yet."
    [orders, _] = checks.volumes(_Client({}), cfg, NOW)
    assert orders.latest is None and orders.error is None


def test_the_day_not_over_yet_is_left_out(tmp_path):
    cfg = _config(tmp_path)
    counts = _daily(date(2026, 10, 20), 60)
    counts[date(2026, 10, 21)] = 2
    [orders, _] = checks.volumes(_Client(counts), cfg, NOW)
    assert orders.latest == date(2026, 10, 20)
    assert date(2026, 10, 21) not in orders.days


def test_days_are_over_in_the_store_time_zone(tmp_path):
    cfg = _config(tmp_path, zone="America/New_York")
    # 02:00 UTC on 21 October is 22:00 on 20 October in New York.
    at = datetime(2026, 10, 21, 2, 0, tzinfo=UTC)
    [orders, _] = checks.volumes(_Client(_daily(date(2026, 10, 20), 60)), cfg, at)
    assert orders.latest == date(2026, 10, 19)


def test_a_day_without_rows_up_to_yesterday_is_a_gap(tmp_path):
    cfg = _config(tmp_path)
    [finding] = checks.rows_per_day(_Client(_daily(date(2026, 10, 19), 60)), cfg)
    assert finding.severity == "warn"
    assert finding.sentence.startswith("orders (shopify) has no rows for 2026-10-20; the other")


def test_a_week_without_rows_says_so_without_a_spread(tmp_path):
    cfg = _config(tmp_path)
    [finding] = checks.rows_per_day(_Client(_daily(date(2026, 10, 1), 30)), cfg)
    assert finding.severity == "warn"
    assert finding.sentence == (
        "orders (shopify) has no rows for 2026-10-14, 2026-10-15, 2026-10-16, 2026-10-17, "
        "2026-10-18, 2026-10-19 and 2026-10-20."
    )


def test_a_weekday_without_rows_as_usual_is_not_a_gap(tmp_path):
    cfg = _config(tmp_path)
    # No orders on Sundays; 18 October 2026 is a Sunday.
    counts = {d: n for d, n in _daily(date(2026, 10, 20), 60).items() if d.weekday() != 6}
    [finding] = checks.rows_per_day(_Client(counts), cfg)
    assert finding.severity == "pass"
    assert finding.sentence == (
        "orders (shopify) has no rows for 2026-10-18, as on the same weekday in the 4 weeks before."
    )


def test_a_quiet_shop_does_not_warn(tmp_path):
    cfg = _config(tmp_path)
    [finding] = checks.rows_per_day(_Client({date(2026, 9, 1): 3}), cfg)
    assert finding.severity == "pass"
    assert finding.observed.startswith("no rows on 2026-10-14, ")


def test_a_day_without_rows_and_without_history_is_a_gap(tmp_path):
    cfg = _config(tmp_path)
    counts = {date(2026, 10, 18): 5, date(2026, 10, 20): 4}
    [finding] = checks.rows_per_day(_Client(counts), cfg)
    assert finding.severity == "warn"
    assert finding.sentence == (
        "orders (shopify) has no rows for 2026-10-19; the other days of that week have 4 to 5."
    )


def test_rows_only_for_today_wait_for_the_day_to_end(tmp_path):
    cfg = _config(tmp_path)
    [finding] = checks.rows_per_day(_Client({date(2026, 10, 21): 3}), cfg)
    assert finding.severity == "pass"
    assert finding.sentence == (
        "orders (shopify) has rows only for today so far; a day is counted once it is over."
    )
    [orders, _] = checks.volumes(_Client({date(2026, 10, 21): 3}), cfg, NOW)
    assert (orders.first, orders.latest) == (date(2026, 10, 21), None)


def test_no_findings_fail_the_run():
    assert checks.failed([])


def _declare(monkeypatch, *daily, time_zone=None):
    """Every package declares these daily tables, and the time zone variable given."""
    variables = {"input_database": "x_input_database"}
    if time_zone:
        variables["time_zone"] = time_zone
    declared = library.Deployment("entities/x.yaml", variables, tuple(daily))
    monkeypatch.setattr(checks, "_deployment", lambda config, package: declared)


def test_a_missing_table_is_an_error_not_a_crash(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    _declare(monkeypatch, library.Daily("missing", "order_date", gap=True))
    [finding] = checks.rows_per_day(_Client({}), cfg)
    assert finding.severity == "error"
    assert (
        finding.sentence == "missing (shopify) could not be counted; build it with `dactopus run`."
    )
    [volume] = checks.volumes(_Client({}), cfg, NOW)
    assert volume.error.startswith("Code: 60")


def test_every_table_marked_gap_is_checked_and_only_those(tmp_path, monkeypatch):
    cfg = _config(tmp_path, "ga4")
    _declare(
        monkeypatch,
        library.Daily("first", "d", gap=True),
        library.Daily("second", "d"),
        library.Daily("third", "d", gap=True),
    )
    findings = checks.rows_per_day(_Client(_daily(date(2026, 10, 20), 60)), cfg)
    assert [(f.object, f.severity) for f in findings] == [("first", "pass"), ("third", "pass")]
    volumes = checks.volumes(_Client(_daily(date(2026, 10, 20), 60)), cfg, NOW)
    assert [v.table for v in volumes] == ["first", "second", "third"]


def test_a_package_without_a_time_zone_counts_to_its_latest_day(tmp_path, monkeypatch):
    """GA4: every daily table read is a day that is over, even the latest."""
    cfg = _config(tmp_path, "ga4")
    _declare(monkeypatch, library.Daily("events", "event_date", gap=True))
    [events] = checks.volumes(_Client(_daily(date(2026, 10, 21), 60)), cfg, NOW)
    assert events.latest == date(2026, 10, 21)


def test_a_package_with_a_time_zone_variable_has_the_store_days(tmp_path, monkeypatch):
    cfg = _config(tmp_path, zone="Asia/Tokyo")
    _declare(monkeypatch, library.Daily("orders", "order_date", gap=True), time_zone="store_zone")
    # 20:00 UTC on 21 October is already 22 October in Tokyo.
    at = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
    [orders] = checks.volumes(_Client(_daily(date(2026, 10, 21), 60)), cfg, at)
    assert orders.latest == date(2026, 10, 21)


class _EntryPoint:
    def __init__(self, name, check):
        self.name, self.value, self.check = name, f"plugin:{name}", check

    def load(self):
        return self.check


def test_run_all_keeps_going_when_a_plugin_breaks(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    ok = checks.Finding("mine", "orders", "pass", "1", "1", "Fine.")

    def boom(client, config):
        raise ValueError("no such column")

    monkeypatch.setattr(
        checks,
        "entry_points",
        lambda group: [_EntryPoint("zzz", boom), _EntryPoint("mine", lambda c, g: [ok])],
    )
    findings = checks.run_all(None, cfg)
    assert findings[0] == ok
    assert (findings[1].check, findings[1].severity) == ("zzz", "error")
    assert findings[1].sentence == "The check zzz could not run: no such column"


def test_built_in_checks_are_registered_like_plugins():
    names = sorted(e.name for e in checks.entry_points(group=checks.ENTRY_POINT_GROUP))
    assert names == ["dbt", "freshness", "rows_per_day", "time_zone"]
    assert (
        json.loads((FIXTURES / "manifest.json").read_text())["metadata"]["dbt_version"] == "1.11.16"
    )


def _misdated(monkeypatch, **counts):
    """build.misdated answers these counts per table, and records the days it is given."""
    asked = {}

    def fake(client, database, counted, zone, days=None):
        asked[counted.table] = (zone, days)
        found = counts[counted.table]
        if isinstance(found, Exception):
            raise found
        return found

    monkeypatch.setattr(checks, "misdated", fake)
    return asked


def test_time_zone_passes_for_tables_built_in_the_store_zone(tmp_path, monkeypatch):
    cfg = _config(tmp_path, zone="America/New_York")
    asked = _misdated(monkeypatch, orders=Misdated(0, 16), refunds=Misdated(0, 6))
    findings = checks.time_zone(None, cfg)
    assert [(f.object, f.severity) for f in findings] == [("orders", "pass"), ("refunds", "pass")]
    assert (
        findings[0].sentence == "Every row of orders (shopify) is on its day in America/New_York."
    )
    # A package built in the zone is counted whole.
    assert asked == {"orders": ("America/New_York", None), "refunds": ("America/New_York", None)}


def test_time_zone_fails_for_tables_built_in_another_zone(tmp_path, monkeypatch):
    cfg = _config(tmp_path, zone="America/New_York")
    _misdated(monkeypatch, orders=Misdated(1, 16), refunds=Misdated(0, 0))
    orders, refunds = checks.time_zone(None, cfg)
    assert (orders.severity, orders.observed) == ("fail", "1 of 16 rows on another day")
    assert orders.sentence == (
        "1 row of orders (shopify) is on another day than in America/New_York: the table was "
        "built in another time zone. Run `dactopus run --full-refresh shopify` once to "
        "rebuild it."
    )
    assert (refunds.severity, refunds.sentence) == (
        "skip",
        "refunds (shopify) has no rows to check the days of.",
    )


def test_time_zone_warns_for_a_source_in_another_zone(tmp_path, monkeypatch):
    """GA4: the days are the property's; counted over the days rows_per_day reads."""
    cfg = _config(tmp_path, "ga4", zone="America/New_York")
    first, last = date(2020, 12, 28), date(2021, 1, 31)
    asked = _misdated(
        monkeypatch,
        events=Misdated(892_970, 4_295_584, first, last),
        sessions=Misdated(0, 10, first, last),
        purchases=DatabaseError("Code: 497. Not enough privileges"),
    )
    events, sessions, purchases = checks.time_zone(None, cfg)
    assert asked["events"] == ("America/New_York", 35)
    assert events.severity == "warn"
    assert (
        events.observed == "892,970 of 4,295,584 rows on another day, from 2020-12-28 to 2021-01-31"
    )
    assert events.sentence.startswith(
        "20.8% of the rows of events (ga4) from 2020-12-28 to 2021-01-31 are on another day in "
        "America/New_York: the source counts its days in another time zone"
    )
    assert sessions.severity == "pass"
    assert (purchases.severity, purchases.observed) == ("error", "Code: 497. Not enough privileges")
    assert purchases.sentence.endswith("check that the ClickHouse user can read it and run again.")


def test_a_few_rows_on_another_day_are_not_zero_percent(tmp_path, monkeypatch):
    cfg = _config(tmp_path, "ga4", zone="UTC")
    first, last = date(2020, 12, 28), date(2021, 1, 31)
    _misdated(
        monkeypatch,
        events=Misdated(3, 4_295_584, first, last),
        sessions=Misdated(0, 10, first, last),
        purchases=Misdated(0, 10, first, last),
    )
    events, _, _ = checks.time_zone(None, cfg)
    assert events.sentence.startswith("under 0.1% of the rows of events (ga4)")


def test_almost_every_row_on_another_day_is_not_a_hundred_percent(tmp_path, monkeypatch):
    cfg = _config(tmp_path, "ga4", zone="UTC")
    first, last = date(2020, 12, 28), date(2021, 1, 31)
    _misdated(
        monkeypatch,
        events=Misdated(4_295_581, 4_295_584, first, last),
        sessions=Misdated(10, 10, first, last),
        purchases=Misdated(0, 10, first, last),
    )
    events, sessions, _ = checks.time_zone(None, cfg)
    assert events.sentence.startswith("over 99.9% of the rows of events (ga4)")
    assert sessions.sentence.startswith("100.0% of the rows of sessions (ga4)")


def test_time_zone_leaves_out_tables_without_a_time_column(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    _declare(monkeypatch, library.Daily("orders", "order_date", gap=True), time_zone="z")
    _misdated(monkeypatch)
    assert checks.time_zone(None, cfg) == []
