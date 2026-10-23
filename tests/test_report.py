from datetime import UTC, date, datetime, timedelta

import yaml

from dactopus import config, report
from dactopus.checks import Finding, Volume

NOW = datetime(2026, 10, 22, 7, 30, tzinfo=UTC)


def _config(tmp_path):
    path = tmp_path / "dactopus.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "clickhouse": {
                    "url": "http://db.local:8123",
                    "input_database": "raw",
                    "database": "canonical",
                },
                "models": {"version": "v0.6.0"},
                "packages": {"shopify": {"shop": "s"}},
            }
        )
    )
    return config.load(path, environ={})


def _volume(package, table, latest, rows, typical=None, error=None):
    days = {latest - timedelta(days=i): rows for i in range(6, -1, -1)}
    return Volume(
        package,
        table,
        latest - timedelta(days=40),
        latest,
        days,
        dict.fromkeys(days, typical),
        error,
    )


FRESH = Finding(
    "freshness", "orders", "pass", "latest", "within 1 day", "The shopify input is current."
)
OK = Finding("dbt", "orders.order_id", "pass", "0 rows", "0 rows", "No order_id repeats in orders.")
FAIL = Finding("dbt", "orders.order_id", "fail", "2 rows", "0 rows", "2 values of order_id repeat.")
WARN = Finding(
    "rows per day",
    "orders",
    "warn",
    "no rows on 2026-10-20",
    "rows every day",
    "No orders on 2026-10-20.",
)
SKIP = Finding(
    "dbt",
    "refunds",
    "skip",
    "skipped",
    "built",
    "refunds was not built because an earlier step failed.",
)


def test_three_answers_come_first_when_all_is_well(tmp_path):
    cfg = _config(tmp_path)
    latest = date(2026, 10, 21)
    text = report.render(cfg, [FRESH, OK], [_volume("shopify", "orders", latest, 12, 10)], NOW)
    lines = text.splitlines()
    assert lines[0] == "# Dactopus report"
    assert lines[2] == f"2026-10-22 07:30 UTC, {cfg.path}"
    assert [line for line in lines if line.startswith("## ")] == [
        "## How fresh the data is",
        "## Did every check pass",
        "## Rows per day",
        "## Details",
    ]
    assert "- The shopify input is current." in lines
    assert "Yes: all 2 checks passed." in lines
    assert "### Shopify" in lines
    assert "| Day | orders |" in lines
    assert "| 2026-10-21 Wed | 12 (10) |" in lines
    assert "- pass: orders.order_id: No order_id repeats in orders." in lines


def test_a_single_check_is_said_in_the_singular(tmp_path):
    text = report.render(_config(tmp_path), [OK], [], NOW)
    assert "Yes: the check passed." in text.splitlines()


def test_what_did_not_pass_is_listed_worst_first(tmp_path):
    cfg = _config(tmp_path)
    text = report.render(cfg, [FRESH, OK, WARN, FAIL, SKIP], [], NOW)
    lines = text.splitlines()
    i = lines.index(
        "No: of 5 checks, 1 failed, 1 warned, 1 did not run because an earlier step failed."
    )
    assert lines[i + 2 : i + 4] == [
        "- fail: 2 values of order_id repeat.",
        "- warn: No orders on 2026-10-20.",
    ]
    assert "No tables to count." in lines
    assert (
        "- fail: orders.order_id: 2 values of order_id repeat. (observed 2 rows; expected 0 rows)"
        in lines
    )


def test_tables_without_rows_or_missing_are_said_so(tmp_path):
    cfg = _config(tmp_path)
    latest = date(2026, 10, 21)
    volumes = [
        _volume("shopify", "orders", latest, 3),
        Volume("shopify", "refunds", None, None, {}, {}),
        Volume("shopify", "sales", latest, None, {}, {}),
        Volume("ga4", "events", None, None, {}, {}, "Code: 60. Unknown table"),
    ]
    text = report.render(cfg, [], volumes, NOW)
    assert "refunds has no rows." in text
    assert "sales has rows only for today so far." in text
    assert "events could not be counted: Code: 60. Unknown table" in text
    assert "| 2026-10-21 Wed | 3 |" in text
    assert "No checks ran: reinstall dactopus." in text


def test_the_report_is_written_next_to_dbt_output(tmp_path):
    cfg = _config(tmp_path)
    path = report.write(cfg, "# Dactopus report\n")
    assert path == cfg.workdir / "report.md"
    assert path.read_text() == "# Dactopus report\n"
